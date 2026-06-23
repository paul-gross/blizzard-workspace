from __future__ import annotations

import logging
from pathlib import Path

from winter_cli.config.models import AdoptExtensions, CodeAgentVendor, SkillInstall, WorkspaceConfig
from winter_cli.core.filesystem import IFilesystemWriter
from winter_cli.modules.workspace.extension_manifest import (
    EXT_MANIFEST,
    ExtensionManifestLoader,
)
from winter_cli.modules.workspace.extension_skill_install import (
    CopySkillStrategy,
    InstallSkillStrategy,
    SymlinkInstaller,
    SymlinkSkillStrategy,
)
from winter_cli.modules.workspace.init_reporter import IInitReporter
from winter_cli.modules.workspace.models import RepoError, StandaloneRepository

logger = logging.getLogger(__name__)


class ExtensionSymlinkService:
    """Installs per-vendor `<prefix>-*` skills and `.claude/agents/<prefix>-*` symlinks for an extension repo.

    For each standalone repo, decides whether it should contribute skills/agents
    (per `adopt_extensions` mode and the presence of `winter-ext.toml`),
    validates SKILL.md frontmatter conforms to the prefix-by-directory
    convention, and installs per-entry skills and agents.

    Skills are projected into every `CodeAgentVendor`'s skills dir using the
    install strategy that vendor's `skill_install` capability selects (symlink
    for ClaudeCode/Codex, copy for OpenCode) — see
    `extension_skill_install.py`. Agents are Claude-only flat `.md` symlinks
    under `.claude/agents/<prefix>-<name>`, installed via the shared symlink
    primitives in that same module.

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
            agents_root = self._resolve_existing_dir(repo.path, manifest.agents_dirs)

            self._validate_frontmatter(repo, skills_root, reporter, strict=mode == AdoptExtensions.winter)

            # Skills are directories containing SKILL.md. Project them into every
            # code-agent vendor's skills dir using the install strategy its
            # `skill_install` capability selects: a relative symlink for ClaudeCode
            # (`.claude/skills`) and Codex (`.codex/skills`), and a real-directory
            # copy for OpenCode (`.opencode/skill`). OpenCode globs `skill/**/SKILL.md`
            # and does NOT traverse symlinked directories, so a symlink there would be
            # invisible to it; the copy lives only under `.opencode/skill`, which no
            # other harness reads, so there's no double-loading.
            skill_names: list[str] = []
            for vendor in CodeAgentVendor:
                target_root = self._config.workspace_root / vendor.skills_subpath
                skill_names = self._skill_strategy(vendor).install(
                    source_root=skills_root,
                    target_root=target_root,
                    prefix=manifest.prefix,
                )

            # Agents are flat .md files (one per agent). Directories are
            # reserved for the nested-agent convention and must carry an
            # AGENT.md marker; bare doc directories (e.g. `agents/docs/`) and
            # `README.md` files at the agents root are skipped. Agents stay
            # Claude-only symlinks for now (no per-vendor abstraction).
            symlinks = SymlinkInstaller(self._fs)
            agents_target = self._config.workspace_root / ".claude" / "agents"
            agent_links = symlinks.install_entries(
                source_root=agents_root,
                target_root=agents_target,
                prefix=manifest.prefix,
                kind="agent",
                include_dirs=True,
                include_files=True,
                file_suffix=".md",
                exclude_filenames=("README.md",),
                require_marker_file="AGENT.md",
            )
            symlinks.prune_stale(agents_target, manifest.prefix, set(agent_links), kind="agent")
        except (RepoError, OSError) as exc:
            logger.warning("process symlinks: failed for %s — %s", repo.name, exc)
            reporter.repo_error(repo.name, str(exc))
            return False

        if skill_names or agent_links:
            detail = f"prefix={manifest.prefix} skills={len(skill_names)} agents={len(agent_links)}"
            reporter.repo_action(repo.name, str(repo.path), "extension_installed", detail)

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
        if skills_root is None or not self._fs.is_dir(skills_root):
            return

        offenders: list[str] = []
        for entry in sorted(self._fs.iterdir(skills_root)):
            if not self._fs.is_dir(entry):
                continue
            skill_md = entry / "SKILL.md"
            if not self._fs.is_file(skill_md):
                continue
            name_field = self._extract_frontmatter_name(skill_md)
            if name_field is None:
                continue
            offenders.append(f"{entry.name}/SKILL.md sets `name: {name_field}`")

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

    def _extract_frontmatter_name(self, skill_md: Path) -> str | None:
        """Return the `name` field from YAML frontmatter, or None if not set.

        Looks only at the top-level frontmatter delimited by `---`. Returns None
        if there's no frontmatter, no `name` key, or any read error.
        """
        try:
            text = self._fs.read_text(skill_md)
        except OSError:
            return None
        if not text.startswith("---"):
            return None
        # Find closing delimiter.
        lines = text.split("\n")
        if len(lines) < 2:
            return None
        end_idx = None
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                end_idx = i
                break
        if end_idx is None:
            return None
        for line in lines[1:end_idx]:
            stripped = line.strip()
            if stripped.startswith("name:"):
                value = stripped.split(":", 1)[1].strip()
                # Strip optional surrounding quotes.
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if value:
                    return value
        return None

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
