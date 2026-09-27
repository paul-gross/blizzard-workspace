"""Injected collaborator that owns the canonical-agent to rendered-copy walk.

Every consumer that reports on installed agent copies — ``AgentProbeService``
(the doctor staleness probe) among them — needs to re-render every canonical
agent for every vendor and compare the result to the on-disk copy at
``<workspace>/<vendor.agents_subpath>/<prefix>-<name><suffix>``. This module is
the single walk: it resolves each agent's model and effort via the shared
``resolve_agent`` resolver, renders it with the same ``RENDERERS`` singletons
``ExtensionAgentService`` (the installer) uses, and reports whether the
on-disk copy matches.

Config-derived inputs (the tier table, the agent-model-override config, which
extensions qualify) arrive as method arguments rather than by injecting the
whole ``WorkspaceConfig``, so a caller that re-reads config on every call and a
caller that reads it once at construction can share this collaborator without
either owning config lifetime.
"""

from __future__ import annotations

import dataclasses
import enum
import logging
from collections.abc import Iterator, Sequence
from pathlib import Path

from winter_cli.config.models import AdoptExtensions, AgentModelOverridesConfig, CodeAgentVendor
from winter_cli.core.filesystem import IFilesystemReader
from winter_cli.modules.workspace.agent_transform.agent_enumerator import CanonicalAgentEnumerator, KnownAgent
from winter_cli.modules.workspace.agent_transform.model_tiers import EffectiveTierTable
from winter_cli.modules.workspace.agent_transform.models import AgentResolution
from winter_cli.modules.workspace.agent_transform.registry import RENDERERS
from winter_cli.modules.workspace.agent_transform.renderers import resolve_agent
from winter_cli.modules.workspace.extension_manifest import EXT_MANIFEST, ExtensionManifestLoader
from winter_cli.modules.workspace.models import RepoError, StandaloneRepository

logger = logging.getLogger(__name__)


def _noop_warn(field: str, agent_name: str, vendor_label: str) -> None:
    """No-op warn callback: the inspector only compares bytes, never surfaces lossy-field warnings."""


def _log_unreadable(extension: str, path: Path, exc: Exception) -> None:
    logger.warning("agent copy inspector: %s — could not read or parse %s: %s", extension, path.name, exc)


class CopyStatus(enum.Enum):
    """How an installed agent copy compares to its freshly-rendered expectation."""

    in_sync = "in_sync"
    stale = "stale"
    missing = "missing"


@dataclasses.dataclass(frozen=True)
class AgentCopyEntry:
    """One agent x vendor result from :meth:`AgentCopyInspector.inspect`.

    ``resolution``/``error`` are mutually exclusive: a ``RepoError`` raised
    while resolving the agent's model/effort means ``resolution`` is ``None``,
    and ``copy_status`` cannot be computed either, so it is ``None`` too.
    """

    extension: str
    agent_name: str
    resolution: AgentResolution | None
    error: RepoError | None
    installed_path: Path
    copy_status: CopyStatus | None


class AgentCopyInspector:
    """Re-renders every canonical agent and compares it to its installed copy.

    Owns the read-side walk: manifest load, agents-dir resolution, parse,
    resolve, render, and the byte comparison against the installed copy. Every
    copy-status consumer goes through it, so "stale" means the same thing
    everywhere. The installer (``ExtensionAgentService``) keeps its own write
    walk and shares only the resolver and the candidate-file listing.
    """

    def __init__(
        self,
        fs: IFilesystemReader,
        manifest_loader: ExtensionManifestLoader,
        agent_enumerator: CanonicalAgentEnumerator,
    ) -> None:
        self._fs = fs
        self._manifest_loader = manifest_loader
        self._agent_enumerator = agent_enumerator

    def known_prefixes(self, repos: list[StandaloneRepository], *, mode: AdoptExtensions) -> set[str]:
        """Return the prefix of every extension whose manifest loads.

        Includes extensions with no agents directory — an orphan scan needs
        every prefix that could legitimately own a ``<prefix>-*`` file, not
        just the ones that currently contribute agents.
        """
        prefixes: set[str] = set()
        for repo in repos:
            manifest_path = repo.path / EXT_MANIFEST
            manifest_present = self._fs.is_file(manifest_path)
            if mode == AdoptExtensions.winter and not manifest_present:
                continue
            try:
                manifest = self._manifest_loader.load(repo, manifest_path if manifest_present else None)
            except RepoError:
                continue
            prefixes.add(manifest.prefix)
        return prefixes

    def known_agents(self, repos: list[StandaloneRepository], *, mode: AdoptExtensions) -> list[KnownAgent]:
        """Every installed canonical agent — the one walk `inspect` and every other consumer share.

        An agent source that fails to read or parse is logged and skipped. The
        walk runs once per call: a caller that needs the agents for several
        vendors or checks walks once and hands the list to each, so every
        unreadable file is logged once per run.
        """
        return list(self._agent_enumerator.iter_known_agents(repos, mode=mode, on_unreadable=_log_unreadable))

    def inspect(
        self,
        agents: Sequence[KnownAgent],
        vendor: CodeAgentVendor,
        *,
        tier_table: EffectiveTierTable,
        agent_model_overrides: AgentModelOverridesConfig,
        agents_dir: Path,
    ) -> Iterator[AgentCopyEntry]:
        """Yield one ``AgentCopyEntry`` per agent in ``agents`` (from :meth:`known_agents`) for ``vendor``.

        Resolves and renders from the already-walked list, so inspecting every
        vendor re-reads no canonical source.
        """
        renderer = RENDERERS[vendor.agent_format]
        guessed_suffix = getattr(renderer, "SUFFIX", "")
        for extension, prefix, agent in agents:
            try:
                resolution = resolve_agent(agent, vendor.vendor_label, tier_table, agent_model_overrides)
            except RepoError as exc:
                yield AgentCopyEntry(
                    extension=extension,
                    agent_name=agent.name,
                    resolution=None,
                    error=exc,
                    installed_path=agents_dir / f"{prefix}-{agent.name}{guessed_suffix}",
                    copy_status=None,
                )
                continue

            rendered = renderer.render(agent, warn=_noop_warn, resolution=resolution)
            expected_bytes = rendered.text.encode("utf-8")
            installed_path = agents_dir / f"{prefix}-{rendered.filename_stem}{rendered.suffix}"
            yield AgentCopyEntry(
                extension=extension,
                agent_name=agent.name,
                resolution=resolution,
                error=None,
                installed_path=installed_path,
                copy_status=self._copy_status(installed_path, expected_bytes),
            )

    def _copy_status(self, path: Path, expected: bytes) -> CopyStatus:
        if not self._fs.is_file(path):
            return CopyStatus.missing
        try:
            actual = self._fs.read_bytes(path)
        except OSError:
            return CopyStatus.missing
        return CopyStatus.in_sync if actual == expected else CopyStatus.stale


__all__ = ["AgentCopyEntry", "AgentCopyInspector", "CopyStatus"]
