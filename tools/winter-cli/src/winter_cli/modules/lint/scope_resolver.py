from __future__ import annotations

import logging
from pathlib import Path

from winter_cli.config.models import WorkspaceConfig
from winter_cli.core.subprocess_runner import ISubprocessRunner, SubprocessResult
from winter_cli.modules.lint.models import (
    LintScope,
    LintScopeError,
    LintScopeKind,
    LintScopeRequest,
)
from winter_cli.modules.workspace.models import RepoError
from winter_cli.modules.workspace.repo_repository import IWriteRepoRepository
from winter_cli.modules.workspace.repository_factory import RepositoryFactory
from winter_cli.modules.workspace.workspace_repository import IReadWorkspaceRepository

logger = logging.getLogger(__name__)


def parse_porcelain_z(output: str) -> list[str]:
    """Parse the NUL-delimited output of ``git status --porcelain -z``.

    ``-z`` emits raw, NUL-terminated, unquoted paths — so paths with spaces or
    non-ASCII survive intact (plain ``--porcelain`` C-quotes them).  Each entry
    starts with a two-character XY status code followed by a space and the path.
    A rename or copy entry (``R`` or ``C`` anywhere in XY) spans **two** NUL
    fields: ``XY new_path\\0orig_path``; we keep the new path and skip the
    original.  All other entries are a single field.

    Returns the new/current paths in order, one per status entry.
    """
    tokens = output.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if len(token) < 4:
            i += 1
            continue
        status, path = token[:2], token[3:]
        paths.append(path)
        # R/C entries carry the original path in the following NUL field.
        i += 2 if ("R" in status or "C" in status) else 1
    return paths


class LintScopeResolver:
    """Turns a CLI scope request into the concrete content a lint run covers.

    Owns scope selection only — it resolves names and the changed set to a list
    of paths, never inspecting *what* those paths contain. The four scopes:

      - `--all` (the default): the whole workspace tree, rooted at the
        workspace root.
      - a repo name: that project / standalone / singleton repo's directory.
      - an env name: every project worktree directory inside the env.
      - `--changed`: files that are dirty or in un-pushed commits in the git
        repository containing the invocation directory.
    """

    def __init__(
        self,
        config: WorkspaceConfig,
        repo_factory: RepositoryFactory,
        worktree_repo: IReadWorkspaceRepository,
        repo_repo: IWriteRepoRepository,
        subprocess_runner: ISubprocessRunner,
    ) -> None:
        self._config = config
        self._repo_factory = repo_factory
        self._worktree_repo = worktree_repo
        self._repo_repo = repo_repo
        self._subprocess = subprocess_runner

    def resolve(self, request: LintScopeRequest) -> LintScope:
        sources = (
            ("a scope name", request.name is not None),
            ("--all", request.all),
            ("--changed", request.changed),
        )
        chosen = [label for label, on in sources if on]
        if len(chosen) > 1:
            raise LintScopeError(f"{', '.join(chosen)} are mutually exclusive")

        if request.changed:
            return self._resolve_changed(request.cwd or self._config.workspace_root)
        if request.name is not None:
            return self._resolve_name(request.name)
        return self._resolve_all()

    # ── --all ──────────────────────────────────────────────────────────────

    def _resolve_all(self) -> LintScope:
        return LintScope(
            kind=LintScopeKind.all,
            label="all",
            paths=[self._config.workspace_root],
        )

    # ── named repo or env ────────────────────────────────────────────────────

    def _resolve_name(self, name: str) -> LintScope:
        repo_path = self._repo_path(name)
        env_paths = self._env_worktree_paths(name)
        if repo_path is not None and env_paths is not None:
            raise LintScopeError(f"`{name}` matches both a repo and an env — rename one to disambiguate")
        if repo_path is not None:
            return LintScope(kind=LintScopeKind.repo, label=f"repo: {name}", paths=[repo_path])
        if env_paths is not None:
            return LintScope(kind=LintScopeKind.env, label=f"env: {name}", paths=env_paths)
        raise LintScopeError(f"unknown scope `{name}` — expected a repo name, an env name, --all, or --changed")

    def _repo_path(self, name: str) -> Path | None:
        for repo in self._repo_factory.get_project_repos():
            if repo.name == name:
                return repo.main_path
        standalone = self._repo_factory.find_standalone(name)
        return standalone.path if standalone is not None else None

    def _env_worktree_paths(self, name: str) -> list[Path] | None:
        """Resolve an env name to its per-repo worktree directories, or None if no such env."""
        project_repos = self._repo_factory.get_project_repos()
        try:
            workspace = self._repo_repo.get_workspace(
                self._config.workspace_root,
                self._config.session_prefix,
                self._config.main_branch,
            )
            envs = self._worktree_repo.get_environments(workspace, project_repos)
        except RepoError as exc:
            raise LintScopeError(f"failed to enumerate envs: {exc}") from exc
        match = next((env for env in envs if env.name == name), None)
        if match is None:
            return None
        return [match.path / repo.name for repo in project_repos]

    # ── --changed ────────────────────────────────────────────────────────────

    def _resolve_changed(self, cwd: Path) -> LintScope:
        root = self._git_toplevel(cwd)
        if root is None:
            raise LintScopeError(f"--changed must run inside a git repository (cwd: {cwd})")

        rel_paths: list[str] = []
        rel_paths.extend(self._dirty_paths(root))
        rel_paths.extend(self._unpushed_paths(root))

        seen: set[str] = set()
        paths: list[Path] = []
        for rel in rel_paths:
            if rel in seen:
                continue
            seen.add(rel)
            paths.append(root / rel)
        return LintScope(kind=LintScopeKind.changed, label=f"changed ({root.name})", paths=paths)

    def _git_toplevel(self, cwd: Path) -> Path | None:
        result = self._git(cwd, "rev-parse", "--show-toplevel")
        if result is None or result.returncode != 0:
            return None
        top = result.stdout.strip()
        return Path(top) if top else None

    def _dirty_paths(self, root: Path) -> list[str]:
        """Working-tree + staged + untracked paths via `git status --porcelain -z`."""
        result = self._git(root, "status", "--porcelain", "-z")
        if result is None or result.returncode != 0:
            return []
        return parse_porcelain_z(result.stdout)

    def _unpushed_paths(self, root: Path) -> list[str]:
        """Files changed in commits ahead of the upstream (or origin/<main>)."""
        base = self._upstream_ref(root)
        if base is None:
            return []
        result = self._git(root, "diff", "--name-only", "-z", f"{base}..HEAD")
        if result is None or result.returncode != 0:
            return []
        return [token for token in result.stdout.split("\0") if token]

    def _upstream_ref(self, root: Path) -> str | None:
        tracking = self._git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if tracking is not None and tracking.returncode == 0 and tracking.stdout.strip():
            return tracking.stdout.strip()
        fallback = f"origin/{self._config.main_branch}"
        verify = self._git(root, "rev-parse", "--verify", "--quiet", fallback)
        if verify is not None and verify.returncode == 0:
            return fallback
        return None

    def _git(self, cwd: Path, *args: str) -> SubprocessResult | None:
        try:
            return self._subprocess.run(["git", "-C", str(cwd), *args])
        except OSError as exc:
            logger.debug("git %s failed in %s: %s", " ".join(args), cwd, exc)
            return None
