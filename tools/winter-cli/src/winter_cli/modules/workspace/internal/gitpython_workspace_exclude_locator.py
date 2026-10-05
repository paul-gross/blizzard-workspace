from __future__ import annotations

from pathlib import Path

import git

from winter_cli.core.tracing import IOperationTracer
from winter_cli.modules.workspace.internal.git_operation import GitOperationDeclaration
from winter_cli.modules.workspace.internal.repo_error_factory import RepoErrorFactory
from winter_cli.modules.workspace.models import RepoError
from winter_cli.modules.workspace.workspace_exclude import IWorkspaceExcludeWriteLocator


class GitPythonWorkspaceExcludeLocator:
    """GitPython-backed adapter for IWorkspaceExcludeWriteLocator (and so IWorkspaceExcludeLocator).

    Confines `git.*` usage to this file.

    Asks git for the workspace root's git directory and common directory. They
    are the same directory for a normal clone and differ for a linked worktree,
    which is the one case that needs a per-worktree exclude file.
    """

    def __init__(self, workspace_root: Path, error_factory: RepoErrorFactory, tracer: IOperationTracer) -> None:
        self._workspace_root = workspace_root
        self._error_factory = error_factory
        self._tracer = tracer

    @GitOperationDeclaration("rev-parse")
    def locate(self) -> Path | None:
        try:
            with git.Repo(str(self._workspace_root)) as repo:
                return self._exclude_path(repo)
        except (git.InvalidGitRepositoryError, git.NoSuchPathError):
            return None
        except git.GitCommandError as exc:
            raise self._resolve_error(exc) from exc

    @GitOperationDeclaration("config")
    def locate_for_write(self) -> Path | None:
        try:
            with git.Repo(str(self._workspace_root)) as repo:
                exclude_path = self._exclude_path(repo)
                if self._is_linked_worktree(repo):
                    self._enable_worktree_exclude(repo, self._common_dir(repo), exclude_path)
                return exclude_path
        except (git.InvalidGitRepositoryError, git.NoSuchPathError):
            return None
        except git.GitCommandError as exc:
            raise self._resolve_error(exc) from exc

    @staticmethod
    def _exclude_path(repo: git.Repo) -> Path:
        return Path(repo.git.rev_parse("--absolute-git-dir")) / "info" / "exclude"

    def _common_dir(self, repo: git.Repo) -> Path:
        return (self._workspace_root / repo.git.rev_parse("--git-common-dir")).resolve()

    def _is_linked_worktree(self, repo: git.Repo) -> bool:
        git_dir = Path(repo.git.rev_parse("--absolute-git-dir")).resolve()
        return git_dir != self._common_dir(repo)

    def _resolve_error(self, exc: git.GitCommandError) -> RepoError:
        return self._error_factory.from_git(
            exc,
            message=f"could not resolve the workspace exclude file at {self._workspace_root}",
            cwd=self._workspace_root,
        )

    @classmethod
    def _enable_worktree_exclude(cls, repo: git.Repo, common_dir: Path, exclude_path: Path) -> None:
        """Make `exclude_path` the linked worktree's `core.excludesFile`, writing only what differs.

        Every read comes before its write: once the shared config is set up, a
        repeat `ws init` (or a parallel one in a sibling worktree) writes nothing
        and so never contends for the shared config's lock.
        """
        # `--local`: the extension only counts in the repository's own config, not a user-level one.
        if cls._read_config(repo, "--local", "--type=bool", "--get", "extensions.worktreeConfig") != "true":
            cls._relocate_core_layout(repo, common_dir)
            repo.git.config("extensions.worktreeConfig", "true")
        if cls._read_config(repo, "--worktree", "--get", "core.excludesFile") != str(exclude_path):
            repo.git.config("--worktree", "core.excludesFile", str(exclude_path))

    @classmethod
    def _relocate_core_layout(cls, repo: git.Repo, common_dir: Path) -> None:
        """Move `core.bare` and `core.worktree` from the shared config to the common dir's `config.worktree`.

        With `extensions.worktreeConfig` on, git reads these two settings per
        worktree. Left in the shared config they apply to every linked worktree,
        and for a bare common dir `core.bare=true` makes each sibling fail with
        "this operation must be run in a work tree". The common dir's
        `config.worktree` is read only by its own (main or bare) worktree.
        See https://git-scm.com/docs/git-worktree#_configuration_file. The new
        copy is written first and the old one removed before the extension is
        enabled, so no worktree ever sees a layout setting that is not its own.
        """
        shared = str(common_dir / "config")
        own = str(common_dir / "config.worktree")
        for key in ("core.bare", "core.worktree"):
            value = cls._read_config(repo, "--file", shared, "--get", key)
            if value is None:
                continue
            repo.git.config("--file", own, key, value)
            repo.git.config("--file", shared, "--unset", key)

    @staticmethod
    def _read_config(repo: git.Repo, *args: str) -> str | None:
        """The value `git config <args>` prints, or `None` when the key is unset (git exits 1)."""
        try:
            return repo.git.config(*args)
        except git.GitCommandError as exc:
            if exc.status == 1:
                return None
            raise


def _conforms_gitpython_workspace_exclude_locator(x: GitPythonWorkspaceExcludeLocator) -> IWorkspaceExcludeWriteLocator:
    return x
