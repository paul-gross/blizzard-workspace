from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from winter_cli.config.models import AdoptExtensions, CodeAgentVendor, WorkspaceConfig
from winter_cli.core.filesystem import IFilesystemWriter
from winter_cli.modules.workspace.agent_transform.agent_enumerator import CanonicalAgentEnumerator
from winter_cli.modules.workspace.agent_transform.model_tiers import build_effective_tier_table
from winter_cli.modules.workspace.agent_transform.registry import PARSER, RENDERERS
from winter_cli.modules.workspace.agent_transform.renderers import resolve_workspace_model_override
from winter_cli.modules.workspace.extension_manifest import (
    EXT_MANIFEST,
    ExtensionManifestLoader,
)
from winter_cli.modules.workspace.init_reporter import IInitReporter
from winter_cli.modules.workspace.models import RepoError, StandaloneRepository

logger = logging.getLogger(__name__)


class ExtensionAgentService:
    """Renders canonical agent files into per-vendor copies under each harness's agents dir.

    For each standalone repo that contributes agents (per `adopt_extensions`
    mode and the presence of `winter-ext.toml`), finds every canonical ``.md``
    agent file in the extension's agents directory, parses it, renders it for
    each ``CodeAgentVendor`` using the vendor's assigned renderer, and writes
    the result idempotently to ``<workspace>/<vendor.agents_subpath>/<prefix>-<name><suffix>``.

    Agent discovery is **flat ``.md``-only**: the service scans only the top-level
    files in the agents directory and ignores subdirectories entirely.  Nested
    agent directories (e.g. with an ``AGENT.md`` marker) are not supported and
    are silently skipped.  Author one ``.md`` file per agent at the root of the
    extension's agents directory.

    Idempotency is by byte-comparison: if the rendered text encodes to the same
    bytes as the current on-disk file, the write is skipped. Stale ``<prefix>-*``
    files (and any residual symlinks with that prefix) in each vendor agents dir
    are pruned when their canonical source no longer exists.

    Renderers are sourced from the shared ``agent_transform.RENDERERS`` dict and
    the parser from ``agent_transform.PARSER`` — the same instances used by
    ``AgentProbeService`` so "stale" is defined identically in both.

    Error-handling shape: ``process`` is the wrap site. Leaves raise
    ``RepoError`` / ``OSError``; one try/except at the boundary routes the
    failure through the reporter.
    """

    def __init__(
        self,
        config: WorkspaceConfig,
        fs: IFilesystemWriter,
        manifest_loader: ExtensionManifestLoader,
        agent_enumerator: CanonicalAgentEnumerator,
    ) -> None:
        self._config = config
        self._fs = fs
        self._manifest_loader = manifest_loader
        self._agent_enumerator = agent_enumerator

    def process(
        self,
        repo: StandaloneRepository,
        reporter: IInitReporter,
    ) -> bool:
        logger.info("process agents: repo=%s", repo.name)
        mode = self._config.adopt_extensions
        if mode == AdoptExtensions.none:
            return True

        manifest_path = repo.path / EXT_MANIFEST
        manifest_present = self._fs.is_file(manifest_path)

        if mode == AdoptExtensions.winter and not manifest_present:
            logger.info("process agents: %s skipped (winter mode, no manifest)", repo.name)
            return True

        try:
            manifest = self._manifest_loader.load(repo, manifest_path if manifest_present else None)
            agents_root = self._agent_enumerator.resolve_agents_dir(repo.path, manifest.agents_dirs)

            live_names: dict[CodeAgentVendor, set[str]] = {v: set() for v in CodeAgentVendor}

            if agents_root is not None:
                for entry in self._agent_enumerator.iter_candidate_agent_files(agents_root):
                    try:
                        text = self._fs.read_text(entry)
                        agent = PARSER.parse(text, default_name=entry.stem)
                    except RepoError as exc:
                        logger.warning(
                            "process agents: %s — parse error for %s: %s",
                            repo.name,
                            entry.name,
                            exc,
                        )
                        reporter.repo_action(
                            repo.name,
                            str(entry),
                            "agent_parse_warning",
                            str(exc),
                        )
                        continue

                    effective_tier_table = build_effective_tier_table(self._config.model_tiers.tiers)
                    warn = self._make_warn(repo.name, reporter)
                    # Render every vendor before writing any of them: a render
                    # failure partway through must not leave this agent
                    # updated for some vendors and stale for others.
                    try:
                        rendered_by_vendor = {
                            vendor: RENDERERS[vendor.agent_format].render(
                                agent,
                                warn=warn,
                                workspace_model_override=resolve_workspace_model_override(
                                    self._config.agent_model_overrides.overrides,
                                    agent.name,
                                    vendor.vendor_label,
                                ),
                                effective_tier_table=effective_tier_table,
                            )
                            for vendor in CodeAgentVendor
                        }
                    except RepoError as exc:
                        logger.warning(
                            "process agents: %s — tier resolution error for %s: %s",
                            repo.name,
                            entry.name,
                            exc,
                        )
                        reporter.repo_action(
                            repo.name,
                            str(entry),
                            "agent_render_warning",
                            str(exc),
                        )
                        continue

                    for vendor, rendered in rendered_by_vendor.items():
                        target_dir = self._config.workspace_root / vendor.agents_subpath
                        self._fs.mkdir(target_dir, parents=True, exist_ok=True)

                        filename = f"{manifest.prefix}-{rendered.filename_stem}{rendered.suffix}"
                        target_path = target_dir / filename

                        self._sync_file(target_path, rendered.text)
                        live_names[vendor].add(filename)

            # Prune stale <prefix>-* artifacts in all vendor agent dirs.
            for vendor in CodeAgentVendor:
                target_dir = self._config.workspace_root / vendor.agents_subpath
                self._prune(target_dir, manifest.prefix, live_names[vendor])

        except (RepoError, OSError) as exc:
            logger.warning("process agents: failed for %s — %s", repo.name, exc)
            reporter.repo_error(repo.name, str(exc))
            return False

        return True

    def check_unknown_overrides(
        self,
        repos: list[StandaloneRepository],
        reporter: IInitReporter,
    ) -> bool:
        """Warn via ``reporter`` for any ``[agent_model_overrides]`` key that matches no installed agent.

        Collects every canonical agent name across all qualifying repos (via
        the injected ``CanonicalAgentEnumerator`` — the same traversal
        ``AgentProbeService`` uses, so the two never disagree on which agents
        are known) and emits a non-fatal warning for each override key that
        names an agent that does not exist.  An override for an unknown name
        is almost always a typo and will silently have no effect on the
        rendered artifacts — surfacing it at ``winter ws init`` time keeps the
        config honest without requiring a separate ``winter doctor`` run.

        Returns True always; the warning does not cause init to fail.

        Note: a typo'd/invalid *tier* name (as opposed to an unknown agent
        name) is validated separately, at config-load time, by
        ``WorkspaceConfigService`` — see ``context/winter-cli/configuration/agents.md``.
        """
        overrides = self._config.agent_model_overrides.overrides
        if not overrides:
            return True

        known_names = {
            agent.name
            for _, agent in self._agent_enumerator.iter_known_agents(repos, mode=self._config.adopt_extensions)
        }

        unknown = sorted(name for name in overrides if name not in known_names)
        if not unknown:
            return True

        names_str = ", ".join(f"'{n}'" for n in unknown)
        reporter.repo_action(
            "agent_model_overrides",
            "",
            "agent_override_warning",
            f"unknown agent name(s) in [agent_model_overrides]: {names_str} "
            "(no matching installed agent — check for a typo or remove the entry)",
        )
        return True

    # ── Filesystem helpers ────────────────────────────────────────────────

    def _sync_file(self, path: Path, text: str) -> None:
        """Write ``text`` to ``path`` only when the on-disk content differs.

        Compares encoded bytes so a file written by a previous run with the
        same content is left untouched (no timestamp churn, no spurious diffs).

        A residual symlink from the old symlink-based install (e.g.
        ``.claude/agents/wf-foo.md`` -> the canonical source) is **unlinked
        first** so the rendered copy replaces it. Writing through the symlink
        would follow it and overwrite the canonical source file — corrupting
        the agent the transform reads from — and would leave the symlink (not a
        copy) in place. ``is_file`` follows symlinks, so the symlink check must
        come first.
        """
        new_bytes = text.encode("utf-8")
        if self._fs.is_symlink(path):
            self._fs.unlink(path)
        elif self._fs.is_file(path):
            try:
                existing = self._fs.read_bytes(path)
                if existing == new_bytes:
                    return
            except OSError:
                pass
        self._fs.write_text(path, text)

    def _prune(self, target_dir: Path, prefix: str, live_names: set[str]) -> None:
        """Remove ``<prefix>-*`` files and symlinks in ``target_dir`` not in ``live_names``.

        Prunes both regular files (rendered copies) and any residual symlinks
        from a previous symlink-based install so migration from the old
        symlink scheme to rendered copies is handled transparently.
        """
        if not self._fs.is_dir(target_dir):
            return
        prefix_with_dash = f"{prefix}-"
        for entry in sorted(self._fs.iterdir(target_dir)):
            if not entry.name.startswith(prefix_with_dash):
                continue
            if entry.name in live_names:
                continue
            if not (self._fs.is_file(entry) or self._fs.is_symlink(entry)):
                continue
            try:
                self._fs.unlink(entry)
            except OSError as exc:
                raise RepoError(f"prune stale agent artifact {entry.name}: {exc}") from exc

    @staticmethod
    def _make_warn(repo_name: str, reporter: IInitReporter) -> Callable[[str, str, str], None]:
        """Return a warn callable that logs and forwards to the reporter."""

        def warn(field: str, agent_name: str, vendor_label: str) -> None:
            msg = (
                f"agent {agent_name!r}: common field {field!r} has no equivalent "
                f"for vendor {vendor_label!r} and was dropped"
            )
            logger.warning("%s: %s", repo_name, msg)
            reporter.repo_action(repo_name, "", "agent_render_warning", msg)

        return warn
