from __future__ import annotations

from pathlib import Path

from winter_cli.config.models import AdoptExtensions, WorkspaceConfig
from winter_cli.core.filesystem import IFilesystemWriter
from winter_cli.modules.workspace.extension_manifest import (
    AGENTS_WINTER_FILENAME,
    CLAUDEMD_WINTER_FILENAME,
    DEFAULT_ENTRY_POINT_PATHS,
    EXT_MANIFEST,
    EXTENSION_BLOCK_NAME,
    IExtensionManifestLoader,
)
from winter_cli.modules.workspace.init_reporter import IInitReporter
from winter_cli.modules.workspace.models import RepoError, StandaloneRepository


class ExtensionAgentsMdService:
    """Aggregate-updates `AGENTS.winter.md` with the list of installed extensions.

    The workspace's `AGENTS.md` (or `CLAUDE.md` for backward-compat) is expected
    to commit a stable `# Winter Extensions` section that imports
    `@AGENTS.winter.md`; this CLI never touches those committed files.
    `AGENTS.winter.md` is gitignored, so adding or removing extensions does not
    dirty the workspace.

    The generated file opens with a single `# Path notation resolution`
    heading; every bullet under it binds an extension name (the `<name>:`
    path-notation prefix) to the location that resolves it. Rendering forks
    by repo kind. A standalone extension lives at one fixed workspace path,
    so its bullet is an eager `@`-import of its entry point. A project-repo
    extension (a `projects/<name>/` checkout carrying a root `winter-ext.toml`
    with no matching `[[standalone_repository]]` declaration — see
    `RepositoryFactory.get_extension_repos`) has N copies — the source checkout
    plus one worktree per feature env — so its bullet carries the literal
    `<env>/<name>/` entry-point path template (plus the manifest description
    when one is declared) with no `@`. This points an agent at the worktree it
    is actually in rather than an injected `master` copy, and keeps project
    entry points off the `injected_bytes` budget the `@`-import graph is
    measured against. When at least one project-repo bullet is rendered, a
    one-line note precedes them stating how `<env>` binds and what a
    workspace-root reader (no env in scope) opens instead.

    A stale `CLAUDE.winter.md` at the workspace root (written by older versions of
    winter that generated a paired shim) is removed on every run as a migration
    step; this CLI no longer generates that file.
    """

    def __init__(
        self,
        config: WorkspaceConfig,
        fs: IFilesystemWriter,
        manifest_loader: IExtensionManifestLoader,
    ) -> None:
        self._config = config
        self._fs = fs
        self._manifest_loader = manifest_loader

    def finalize_agentsmd(
        self,
        repos: list[StandaloneRepository],
        reporter: IInitReporter,
    ) -> bool:
        """Aggregate-update `AGENTS.winter.md`.

        Called once after all standalones are reconciled, with every extension
        repo (standalones plus project-repo extensions) that exists on disk.
        Standalone entries get an eager `@`-import bullet; project-repo entries
        get a path-template bullet. A repo of either kind is eligible only when an entry
        point (`index.md`, `AGENTS.md`, then `context/index.md`, in that order)
        exists at its root.

        Also removes any stale `CLAUDE.winter.md` left by an older version of
        winter (migration cleanup). When no extensions are eligible,
        `AGENTS.winter.md` is deleted if present.
        """
        # Migration cleanup: remove a stale CLAUDE.winter.md from a previous run.
        # Runs before the adopt-extensions check so it fires on every run.
        stale_shim_path = self._config.workspace_root / CLAUDEMD_WINTER_FILENAME
        if self._fs.exists(stale_shim_path):
            try:
                self._fs.unlink(stale_shim_path)
                reporter.repo_action(
                    EXTENSION_BLOCK_NAME,
                    str(stale_shim_path),
                    "claude_winter_stale_removed",
                    "",
                )
            except OSError as exc:
                reporter.repo_error(EXTENSION_BLOCK_NAME, f"{CLAUDEMD_WINTER_FILENAME} — {exc}")

        if self._config.adopt_extensions == AdoptExtensions.none:
            return True

        standalone_lines: list[tuple[str, str]] = []
        project_rows: list[tuple[str, str]] = []
        for repo in repos:
            entry_point = self._find_entry_point(repo.path)
            if entry_point is None:
                continue
            if self._is_project_repo_extension(repo):
                project_rows.append((repo.name, self._render_project_row(repo, entry_point, reporter)))
                continue
            try:
                relative = repo.path.relative_to(self._config.workspace_root).as_posix()
            except ValueError:
                # Standalone path lives outside the workspace; can't write a
                # workspace-relative @-import for it. Skip silently.
                continue
            standalone_lines.append((repo.name, f"- **{repo.name}**: @{relative}/{entry_point}"))

        agents_path = self._config.workspace_root / AGENTS_WINTER_FILENAME
        eligible = [*standalone_lines, *project_rows]

        if not eligible:
            if not self._fs.exists(agents_path):
                return True
            try:
                self._fs.unlink(agents_path)
            except OSError as exc:
                reporter.repo_error(EXTENSION_BLOCK_NAME, f"{AGENTS_WINTER_FILENAME} — {exc}")
                return False
            reporter.repo_action(
                EXTENSION_BLOCK_NAME,
                str(agents_path),
                "agents_winter_removed",
                "no eligible extensions",
            )
            return True

        winter_lines = ["# Path notation resolution", ""]
        winter_lines.extend(line for _, line in sorted(standalone_lines))
        if project_rows:
            # `<env>` in the bullets below is unbound for a reader with no feature env
            # in scope (e.g. a subagent spawned from the workspace root per
            # `context/workspace-layout.md` rule 4) — state the binding and the
            # workspace-root fallback once here rather than repeating it per bullet.
            if standalone_lines:
                winter_lines.append("")
            winter_lines.append(
                "`<env>` below binds to the feature-env directory you are working in; "
                "from the workspace root, where no env is bound, read the source "
                "checkout at `projects/<name>/` instead."
            )
            winter_lines.append("")
            winter_lines.extend(line for _, line in sorted(project_rows))
        new_agents = "\n".join(winter_lines) + "\n"
        detail = ", ".join(name for name, _ in sorted(eligible))

        try:
            existing_agents = self._fs.read_text(agents_path) if self._fs.exists(agents_path) else ""
            if new_agents != existing_agents:
                self._fs.write_text(agents_path, new_agents)
                reporter.repo_action(
                    EXTENSION_BLOCK_NAME,
                    str(agents_path),
                    "agents_winter_updated",
                    detail,
                )
        except OSError as exc:
            reporter.repo_error(EXTENSION_BLOCK_NAME, f"{AGENTS_WINTER_FILENAME} — {exc}")
            return False

        return True

    def _find_entry_point(self, repo_path: Path) -> str | None:
        """Return the first candidate entry-point path that exists at `repo_path`'s root.

        Tries `index.md`, `AGENTS.md`, then `context/index.md`, in order —
        mirrors the `DEFAULT_SKILLS_DIRS` first-match fallback pattern used to
        locate a manifest's skills/agents dirs. Returns None when none exist.
        """
        for candidate in DEFAULT_ENTRY_POINT_PATHS:
            if self._fs.is_file(repo_path / candidate):
                return candidate
        return None

    def _is_project_repo_extension(self, repo: StandaloneRepository) -> bool:
        """A project-repo extension is rooted under `<workspace_root>/projects/`.

        Standalones are cloned at the workspace root (or a configured `path`)
        outside `projects/`, so this path shape reliably distinguishes the two
        kinds without threading extra state through `RepositoryFactory.get_extension_repos()`.
        """
        try:
            repo.path.relative_to(self._config.workspace_root / "projects")
        except ValueError:
            return False
        return True

    def _render_project_row(
        self,
        repo: StandaloneRepository,
        entry_point: str,
        reporter: IInitReporter,
    ) -> str:
        """Render a project-repo extension's bullet.

        No `@`-import: an agent working in a feature env reads the worktree
        it's actually in (`<env>/<name>/`) rather than this injected `master`
        copy. Names the full entry-point path (so the bullet says *what* to
        read, not just where); appends the manifest `description` after it
        when present.
        """
        line = f"- **{repo.name}**: `<env>/{repo.name}/{entry_point}`"
        description = self._load_description(repo, reporter)
        if description:
            line = f"{line} — {description}"
        return line

    def _load_description(self, repo: StandaloneRepository, reporter: IInitReporter) -> str | None:
        manifest_path = repo.path / EXT_MANIFEST
        if not self._fs.is_file(manifest_path):
            return None
        try:
            manifest = self._manifest_loader.load(repo, manifest_path)
        except RepoError as exc:
            reporter.repo_error(repo.name, str(exc))
            return None
        return manifest.description
