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
from winter_cli.modules.workspace.models import RepoError

logger = logging.getLogger(__name__)

WORKSPACE_SKILLS_DIR = "skills"


class WorkspaceSkillService:
    """Installs workspace-owned skills from workspace_root/skills/ into per-vendor skill dirs.

    When `prefix` is set in `.winter/config.toml`, reads every skill directory
    (a directory containing `SKILL.md`) under `workspace_root/skills/` and
    projects them into every code-agent vendor's skills directory using the
    same per-vendor install strategies as extension skills:
    - Symlink for ClaudeCode (.claude/skills) and Codex (.codex/skills)
    - Real-directory copy for OpenCode (.opencode/skill)

    Stale `<prefix>-*` entries (from a previous run where a skill was renamed
    or removed) are pruned on each reconcile pass, matching extension behavior.
    This includes the case where the whole `skills/` directory is deleted — the
    strategies receive `source_root=None` and prune any remaining `<prefix>-*`
    links or copies.

    No-op when `prefix` is absent from config.
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
        if prefix is None:
            logger.debug("workspace skills: no prefix configured, skipping")
            return True

        skills_root = self._config.workspace_root / WORKSPACE_SKILLS_DIR
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
