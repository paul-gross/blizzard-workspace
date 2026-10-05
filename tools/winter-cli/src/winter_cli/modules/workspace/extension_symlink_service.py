from __future__ import annotations

import logging
from pathlib import Path

from winter_cli.config.models import AdoptExtensions, CodeAgentVendor, SkillInstall, WorkspaceConfig
from winter_cli.core.filesystem import IFilesystemWriter
from winter_cli.modules.workspace.extension_manifest import (
    EXT_MANIFEST,
    ExtensionManifestLoader,
    prefixes_overlap,
)
from winter_cli.modules.workspace.extension_skill_install import (
    CopySkillStrategy,
    InstallSkillStrategy,
    SkillFrontmatterGuard,
    SymlinkSkillStrategy,
)
from winter_cli.modules.workspace.init_reporter import IInitReporter
from winter_cli.modules.workspace.models import RepoError, StandaloneRepository

logger = logging.getLogger(__name__)


class ExtensionSymlinkService:
    """Installs per-vendor `<prefix>-*` skills for an extension repo.

    For each standalone repo, decides whether it should contribute skills
    (per `adopt_extensions` mode and the presence of `winter-ext.toml`),
    validates SKILL.md frontmatter conforms to the prefix-by-directory
    convention, and installs per-entry skills.

    Skills are projected into every `CodeAgentVendor`'s skills dir using the
    install strategy that vendor's `skill_install` capability selects (symlink
    for ClaudeCode/Codex, copy for OpenCode) — see `extension_skill_install.py`.

    Agent installation (flat ``.md``-only rendered copies) is handled separately
    by ``ExtensionAgentService`` and is not part of this service's responsibility.

    Error-handling shape: `process` is the wrap site. Leaves raise
    `RepoError` / `OSError`; one try/except at the boundary routes the
    failure through the reporter.
    """

    def __init__(
        self,
        config: WorkspaceConfig,
        fs: IFilesystemWriter,
        manifest_loader: ExtensionManifestLoader,
    ) -> None:
        self._config = config
        self._fs = fs
        self._manifest_loader = manifest_loader

    def process(
        self,
        repo: StandaloneRepository,
        reporter: IInitReporter,
    ) -> bool:
        logger.info("process symlinks: repo=%s", repo.name)
        mode = self._config.adopt_extensions
        if mode == AdoptExtensions.none:
            return True

        manifest_path = repo.path / EXT_MANIFEST
        manifest_present = self._fs.is_file(manifest_path)

        if mode == AdoptExtensions.winter and not manifest_present:
            logger.info("process symlinks: %s skipped (winter mode, no manifest)", repo.name)
            return True

        try:
            manifest = self._manifest_loader.load(repo, manifest_path if manifest_present else None)
            skills_root = self._resolve_existing_dir(repo.path, manifest.skills_dirs)

            self._validate_frontmatter(repo, skills_root, reporter, strict=mode == AdoptExtensions.winter)

            # Skills are directories containing SKILL.md. Project them into every
            # code-agent vendor's skills dir using the install strategy its
            # `skill_install` capability selects: a relative symlink for ClaudeCode
            # (`.claude/skills`) and Codex (`.codex/skills`), and a real-directory
            # copy for OpenCode (`.opencode/skill`). OpenCode globs `skill/**/SKILL.md`
            # and does NOT traverse symlinked directories, so a symlink there would be
            # invisible to it; the copy lives only under `.opencode/skill`, which no
            # other harness reads, so there's no double-loading.
            #
            # Agent installation is handled separately by ExtensionAgentService, which
            # renders canonical agent files into per-vendor copies rather than symlinks.
            skill_names: list[str] = []
            for vendor in CodeAgentVendor:
                target_root = self._config.workspace_root / vendor.skills_subpath
                skill_names = self._skill_strategy(vendor).install(
                    source_root=skills_root,
                    target_root=target_root,
                    prefix=manifest.prefix,
                )
        except (RepoError, OSError) as exc:
            logger.warning("process symlinks: failed for %s — %s", repo.name, exc)
            reporter.repo_error(repo.name, str(exc))
            return False

        if skill_names:
            detail = f"prefix={manifest.prefix} skills={len(skill_names)}"
            reporter.repo_action(repo.name, str(repo.path), "extension_installed", detail)

        return True

    def projected_prefix(self, repo: StandaloneRepository) -> str | None:
        """Return the prefix `process` would project `repo` under, or None when it projects nothing.

        Mirrors `process`'s eligibility. A manifest that no longer parses yields
        None: there is no prefix to resolve from it.
        """
        mode = self._config.adopt_extensions
        if mode == AdoptExtensions.none:
            return None
        manifest_path = repo.path / EXT_MANIFEST
        manifest_present = self._fs.is_file(manifest_path)
        if mode == AdoptExtensions.winter and not manifest_present:
            return None
        try:
            return self._manifest_loader.load(repo, manifest_path if manifest_present else None).prefix
        except RepoError:
            return None

    def retract(self, repo: StandaloneRepository, live_prefixes: frozenset[str]) -> bool:
        """Remove the skills an earlier `process` projected for a repo that is no longer an extension.

        Called for a repo whose entry declares `extension = false`: prunes its
        `<prefix>-*` entries from every vendor skills dir by installing from no
        source. Mirrors `process`'s eligibility so it only touches what
        `process` could have written, and resolves the prefix the same way. A
        manifest that no longer parses leaves nothing to resolve a prefix from,
        so it is skipped rather than reported — the repo is data now, and what
        its manifest declares is not winter's business.

        A prefix that a live extension in `live_prefixes` also claims is left
        alone: its `<prefix>-*` entries cannot be told apart from the live
        extension's, and that extension's own install keeps them current.
        """
        prefix = self.projected_prefix(repo)
        if prefix is None:
            return True
        if any(prefixes_overlap(prefix, live) for live in live_prefixes):
            logger.info("retract skills: %s skipped — prefix %s is claimed by a live extension", repo.name, prefix)
            return True
        try:
            for vendor in CodeAgentVendor:
                self._skill_strategy(vendor).install(
                    source_root=None,
                    target_root=self._config.workspace_root / vendor.skills_subpath,
                    prefix=prefix,
                )
        except (RepoError, OSError) as exc:
            logger.warning("retract skills: failed for %s — %s", repo.name, exc)
            return False
        return True

    # ── Frontmatter validation ────────────────────────────────────────────

    def _validate_frontmatter(
        self,
        repo: StandaloneRepository,
        skills_root: Path | None,
        reporter: IInitReporter,
        strict: bool,
    ) -> None:
        """Ensure SKILL.md files don't override the symlinked directory name.

        Claude Code lets the `name` frontmatter field override the directory name
        when discovering skills — that defeats the prefix-by-symlink design. In
        strict (`winter`) mode, raise so the wrap site fails the install. In
        `all` mode, the user opts into a less-curated experience, so we only warn.
        """
        offenders = SkillFrontmatterGuard(self._fs).collect_offenders(skills_root)
        if not offenders:
            return

        msg = (
            f"extension {repo.name} has SKILL.md files with frontmatter `name` set, "
            f"which would override the prefixed directory name and break namespacing. "
            f"Remove the `name` field so the directory name (set by winter) is authoritative. "
            f"Offenders: {'; '.join(offenders)}"
        )
        if strict:
            raise RepoError(msg)
        # adopt_extensions = "all": warn via repo_action so the user sees it but install proceeds.
        reporter.repo_action(repo.name, str(repo.path), "extension_warning", msg)

    # ── Source resolution ─────────────────────────────────────────────────

    def _resolve_existing_dir(self, base: Path, candidates: tuple[str, ...]) -> Path | None:
        """Return the first candidate path under `base` that exists as a directory."""
        for candidate in candidates:
            path = base / candidate
            if self._fs.is_dir(path):
                return path
        return None

    def _skill_strategy(self, vendor: CodeAgentVendor) -> InstallSkillStrategy:
        """Select a skill-install strategy from the vendor's `skill_install` capability.

        Data-driven off the capability attribute, not a per-member branch — a
        new vendor that reuses an existing `SkillInstall` mode needs no change
        here.
        """
        if vendor.skill_install is SkillInstall.copy:
            return CopySkillStrategy(self._fs, vendor)
        return SymlinkSkillStrategy(self._fs)
