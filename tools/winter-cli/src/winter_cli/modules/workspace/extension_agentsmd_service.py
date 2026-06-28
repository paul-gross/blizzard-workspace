from __future__ import annotations

from winter_cli.config.models import AdoptExtensions, WorkspaceConfig
from winter_cli.core.filesystem import IFilesystemWriter
from winter_cli.modules.workspace.extension_manifest import (
    AGENTS_WINTER_FILENAME,
    CLAUDEMD_WINTER_FILENAME,
    EXTENSION_BLOCK_NAME,
    EXTENSION_INDEX_FILENAME,
)
from winter_cli.modules.workspace.init_reporter import IInitReporter
from winter_cli.modules.workspace.models import StandaloneRepository


class ExtensionAgentsMdService:
    """Aggregate-updates `AGENTS.winter.md` with the list of installed extensions.

    The workspace's `AGENTS.md` (or `CLAUDE.md` for backward-compat) is expected
    to commit a stable `# Winter Extensions` section that imports
    `@AGENTS.winter.md`; this CLI never touches those committed files.
    `AGENTS.winter.md` is gitignored, so adding or removing extensions does not
    dirty the workspace.

    A stale `CLAUDE.winter.md` at the workspace root (written by older versions of
    winter that generated a paired shim) is removed on every run as a migration
    step; this CLI no longer generates that file.
    """

    def __init__(
        self,
        config: WorkspaceConfig,
        fs: IFilesystemWriter,
    ) -> None:
        self._config = config
        self._fs = fs

    def finalize_agentsmd(
        self,
        repos: list[StandaloneRepository],
        reporter: IInitReporter,
    ) -> bool:
        """Aggregate-update `AGENTS.winter.md`.

        Called once after all standalones are reconciled. Lists every
        standalone that has an `index.md` at its repo root, with a path
        description and an `@`-import line.

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

        eligible: list[tuple[str, str]] = []
        for repo in repos:
            index_path = repo.path / EXTENSION_INDEX_FILENAME
            if not self._fs.is_file(index_path):
                continue
            try:
                relative = repo.path.relative_to(self._config.workspace_root).as_posix()
            except ValueError:
                # Standalone path lives outside the workspace; can't write a
                # workspace-relative @-import for it. Skip silently.
                continue
            eligible.append((repo.name, relative))

        agents_path = self._config.workspace_root / AGENTS_WINTER_FILENAME

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

        winter_lines = [
            f"- **{name}** at `./{rel}/` — resolves the `{name}:` path notation. @{rel}/{EXTENSION_INDEX_FILENAME}"
            for name, rel in sorted(eligible)
        ]
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
