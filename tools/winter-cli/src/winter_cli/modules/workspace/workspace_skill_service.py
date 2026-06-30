from __future__ import annotations

import logging
from pathlib import Path

from winter_cli.config.models import CodeAgentVendor, SkillInstall, WorkspaceConfig
from winter_cli.core.filesystem import IFilesystemWriter
from winter_cli.modules.workspace.extension_skill_install import (
    CopySkillStrategy,
    InstallSkillStrategy,
    SkillFrontmatterGuard,
    SymlinkSkillStrategy,
)
from winter_cli.modules.workspace.init_reporter import IInitReporter
from winter_cli.modules.workspace.internal.managed_block import (
    GITIGNORE_BEGIN,
    GITIGNORE_END,
    replace_or_append_block,
)
from winter_cli.modules.workspace.models import RepoError

logger = logging.getLogger(__name__)

_EXCLUDE_BLOCK_NAME = "winter-workspace/workspace-skills"


class WorkspaceSkillService:
    """Installs workspace-owned skills from workspace_root/<skills_dir>/ into per-vendor skill dirs.

    Reads every skill directory (a directory containing `SKILL.md`) under
    `workspace_root/<skills_dir>/` and projects them into every code-agent
    vendor's skills directory using the per-vendor install strategies:
    - Symlink for ClaudeCode (.claude/skills) and Codex (.codex/skills)
    - Real-directory copy for OpenCode (.opencode/skill)

    Naming rule: a source directory named exactly `<prefix>` projects as the
    bare prefix (e.g. `skills/ws/` → `ws`); all others project as
    `<prefix>-<dirname>` (e.g. `skills/init/` → `ws-init`).

    Stale `<prefix>-*` and bare `<prefix>` entries (from a previous run where
    a skill was renamed or removed) are pruned on each reconcile pass, matching
    extension behavior. This includes the case where the whole `<skills_dir>/`
    directory is deleted — the strategies receive `source_root=None` and prune
    any remaining `<prefix>-*` links or copies.

    A managed git-exclude block is written to `.git/info/exclude` so the
    generated vendor entries are never accidentally committed.

    Projection is always-on: `prefix` defaults to `"ws"` and `skills_dir`
    defaults to `"skills"`.
    """

    def __init__(self, config: WorkspaceConfig, fs: IFilesystemWriter) -> None:
        self._config = config
        self._fs = fs

    def reconcile(self, reporter: IInitReporter) -> bool:
        """Project workspace skills into all vendor skill dirs.

        Returns True on success (including no-op cases). Returns False and
        routes the error through *reporter* on I/O failure or frontmatter
        violation.
        """
        prefix = self._config.skill_prefix
        skills_root = self._config.workspace_root / self._config.skills_dir
        source_root: Path | None = skills_root if self._fs.is_dir(skills_root) else None
        if source_root is None:
            logger.debug("workspace skills: %s absent, pruning stale entries", skills_root)

        try:
            if source_root is not None:
                offenders = SkillFrontmatterGuard(self._fs).collect_offenders(source_root)
                if offenders:
                    raise RepoError(
                        f"workspace skills has SKILL.md files with frontmatter `name` set, "
                        f"which would override the prefixed directory name and break namespacing. "
                        f"Remove the `name` field so the directory name (set by winter) is authoritative. "
                        f"Offenders: {'; '.join(offenders)}"
                    )

            skill_names: list[str] = []
            for vendor in CodeAgentVendor:
                target_root = self._config.workspace_root / vendor.skills_subpath
                skill_names = self._skill_strategy(vendor).install(
                    source_root=source_root,
                    target_root=target_root,
                    prefix=prefix,
                )
        except (RepoError, OSError) as exc:
            logger.warning("workspace skills: failed — %s", exc)
            reporter.repo_error("workspace", f"workspace skills — {exc}")
            return False

        if not self._write_excludes(prefix, reporter):
            return False

        if skill_names:
            detail = f"prefix={prefix} skills={len(skill_names)}"
            reporter.repo_action(
                "workspace",
                str(skills_root),
                "workspace_skills_installed",
                detail,
            )

        return True

    def _skill_strategy(self, vendor: CodeAgentVendor) -> InstallSkillStrategy:
        if vendor.skill_install is SkillInstall.copy:
            return CopySkillStrategy(self._fs, vendor)
        return SymlinkSkillStrategy(self._fs)

    def _write_excludes(self, prefix: str, reporter: IInitReporter) -> bool:
        """Write a managed exclude block for all three vendor skill dirs."""
        exclude_path = self._config.workspace_root / ".git" / "info" / "exclude"
        if not self._fs.exists(self._config.workspace_root / ".git"):
            return True

        begin = GITIGNORE_BEGIN.format(name=_EXCLUDE_BLOCK_NAME)
        end = GITIGNORE_END.format(name=_EXCLUDE_BLOCK_NAME)
        lines = [
            begin,
            f".claude/skills/{prefix}-*",
            f".claude/skills/{prefix}",
            f".codex/skills/{prefix}-*",
            f".codex/skills/{prefix}",
            f".opencode/skill/{prefix}-*",
            f".opencode/skill/{prefix}",
            end,
        ]

        try:
            existing = self._fs.read_text(exclude_path) if self._fs.exists(exclude_path) else ""
            new_content = replace_or_append_block(existing, begin, end, lines)
            if new_content == existing:
                return True
            self._fs.mkdir(exclude_path.parent, parents=True, exist_ok=True)
            self._fs.write_text(exclude_path, new_content)
        except OSError as exc:
            logger.warning("workspace skills: exclude write failed — %s", exc)
            reporter.repo_error("workspace", f"workspace skills .git/info/exclude — {exc}")
            return False

        return True
