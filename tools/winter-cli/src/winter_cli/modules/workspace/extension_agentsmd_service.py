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
    ExtensionLoad,
    ExtensionManifest,
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
    path-notation prefix) to the location that resolves it. A bullet renders in
    one of three shapes, chosen by the extension's load mode and repo kind:

    - **eager** — `@`-import of the entry point. Always-on context: the import
      graph is injected into every session and counts against `injected_bytes`.
    - **lazy standalone** — the extension name linked to its entry point, plus
      the manifest description when one is declared. The agent follows the link
      only when the row's subject is in scope, so the entry point costs one
      routing line rather than its whole transitive import graph.
    - **lazy project-repo** — the literal `<env>/<name>/` entry-point path
      template (plus the description) in backticks rather than a link, because
      `<env>` is not a real path. A project-repo extension (a `projects/<name>/`
      checkout carrying a root `winter-ext.toml` with no matching
      `[[standalone_repository]]` declaration — see
      `RepositoryFactory.get_extension_repos`) has N copies on disk — the source
      checkout plus one worktree per feature env — so the template points an
      agent at the worktree it is actually in rather than an injected `master`
      copy. When at least one such bullet is rendered, a one-line note precedes
      them stating how `<env>` binds and what a workspace-root reader (no env in
      scope) opens instead.

    The load mode comes from the manifest's `load` key. Undeclared, it defaults
    per repo kind — standalones eager, project repos lazy — so the rendering
    predates the key and is unchanged by adding it. `load = "eager"` on a
    project repo is refused (reported, then rendered lazily): an eager import
    there would inject the `master` copy that goes stale against whatever
    feature branch the reading agent is on, which is the reason project repos
    render lazily in the first place.

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
        Each entry renders as an eager `@`-import, a lazy link, or a lazy
        path-template bullet — see the class docstring for how the manifest's
        `load` key and the repo kind select between them. A repo of any kind is
        eligible only when an entry point (`index.md`, `AGENTS.md`, then
        `context/index.md`, in that order) exists at its root.

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

        eager_lines: list[tuple[str, str]] = []
        lazy_lines: list[tuple[str, str]] = []
        project_rows: list[tuple[str, str]] = []
        for repo in repos:
            entry_point = self._find_entry_point(repo.path)
            if entry_point is None:
                continue
            manifest = self._load_manifest(repo, reporter)
            description = manifest.description if manifest is not None else None
            is_project_repo = self._is_project_repo_extension(repo)
            load = self._resolve_load(repo, manifest, is_project_repo=is_project_repo, reporter=reporter)
            if is_project_repo:
                project_rows.append((repo.name, self._render_project_row(repo, entry_point, description)))
                continue
            try:
                relative = repo.path.relative_to(self._config.workspace_root).as_posix()
            except ValueError:
                # Standalone path lives outside the workspace; can't write a
                # workspace-relative @-import or link for it. Skip silently.
                continue
            target = f"{relative}/{entry_point}"
            if load is ExtensionLoad.lazy:
                lazy_lines.append((repo.name, self._render_lazy_row(repo.name, target, description)))
            else:
                eager_lines.append((repo.name, f"- **{repo.name}**: @{target}"))

        agents_path = self._config.workspace_root / AGENTS_WINTER_FILENAME
        eligible = [*eager_lines, *lazy_lines, *project_rows]

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
        winter_lines.extend(line for _, line in sorted(eager_lines))
        if lazy_lines:
            # Lazy bullets carry no `@`, so a blank line keeps them from reading as a
            # continuation of the eager import block above.
            if eager_lines:
                winter_lines.append("")
            winter_lines.extend(line for _, line in sorted(lazy_lines))
        if project_rows:
            # `<env>` in the bullets below is unbound for a reader with no feature env
            # in scope (e.g. a subagent spawned from the workspace root per
            # `context/workspace-layout.md` rule 4) — state the binding and the
            # workspace-root fallback once here rather than repeating it per bullet.
            if eager_lines or lazy_lines:
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

    def _resolve_load(
        self,
        repo: StandaloneRepository,
        manifest: ExtensionManifest | None,
        *,
        is_project_repo: bool,
        reporter: IInitReporter,
    ) -> ExtensionLoad:
        """Resolve the effective load mode for `repo`.

        An undeclared `load` falls back to the default for the repo kind —
        standalones eager, project repos lazy — which is the behavior that
        predates the key, so existing manifests render unchanged.

        `load = "eager"` on a project repo is refused and downgraded to lazy
        rather than failing the run: the `@`-import it asks for would inject the
        `projects/<name>/` master copy, which goes stale against whatever
        feature branch the reading agent is on. Reported so the manifest gets
        fixed, non-fatal so one bad key doesn't trap a reconcile.
        """
        declared = manifest.load if manifest is not None else None
        if declared is None:
            return ExtensionLoad.lazy if is_project_repo else ExtensionLoad.eager
        if is_project_repo and declared is ExtensionLoad.eager:
            reporter.repo_error(
                repo.name,
                f'{EXT_MANIFEST} — `load = "eager"` is not available to a project-repo extension '
                f"(it has one copy per feature env; an eager import would inject the stale master "
                f"copy). Rendering it lazily.",
            )
            return ExtensionLoad.lazy
        return declared

    def _render_lazy_row(self, name: str, target: str, description: str | None) -> str:
        """Render a lazily-loaded standalone's bullet.

        A markdown link, not an `@`-import: the entry point sits at one fixed
        workspace-relative path, so the link resolves for a reader anywhere in
        the workspace and the agent opens it only when it needs to. The
        extension name *is* the link — naming the path a second time as link
        text would spend tokens on every row to say what the href already says.
        Appends the manifest `description` when present; without one the row is
        the link alone, which says where to read but not when.
        """
        line = f"- **[{name}]({target})**"
        if description:
            line = f"{line} — {description}"
        return line

    def _render_project_row(
        self,
        repo: StandaloneRepository,
        entry_point: str,
        description: str | None,
    ) -> str:
        """Render a project-repo extension's bullet.

        The `<env>/<name>/` path template in backticks rather than a link —
        `<env>` binds at read time to the worktree the agent is in, so there is
        no single path to link to. Names the full entry-point path (so the
        bullet says *what* to read, not just where); appends the manifest
        `description` after it when present.
        """
        line = f"- **{repo.name}**: `<env>/{repo.name}/{entry_point}`"
        if description:
            line = f"{line} — {description}"
        return line

    def _load_manifest(self, repo: StandaloneRepository, reporter: IInitReporter) -> ExtensionManifest | None:
        """Load `repo`'s `winter-ext.toml`, or None when absent or malformed.

        A malformed manifest is reported and treated as absent so the extension
        still renders — under its repo-kind defaults — instead of vanishing from
        the generated file.
        """
        manifest_path = repo.path / EXT_MANIFEST
        if not self._fs.is_file(manifest_path):
            return None
        try:
            return self._manifest_loader.load(repo, manifest_path)
        except RepoError as exc:
            reporter.repo_error(repo.name, str(exc))
            return None
