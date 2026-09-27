"""Assembles the read-only agent model/effort resolution snapshot `winter agents` renders."""

from __future__ import annotations

import logging
from collections.abc import Callable

from winter_cli.config.models import AdoptExtensions, AgentModelOverridesConfig, CodeAgentVendor, WorkspaceConfig
from winter_cli.config.workspace import WorkspaceConfigService
from winter_cli.modules.agents.models import (
    AgentMatrix,
    AgentMatrixEntry,
    AgentOverrideEntry,
    AgentOverrideHarnessValue,
    TierMatrixEntry,
)
from winter_cli.modules.workspace.agent_transform.agent_copy_inspector import AgentCopyInspector
from winter_cli.modules.workspace.agent_transform.model_tiers import EffectiveTierTable, build_effective_tier_table
from winter_cli.modules.workspace.agent_transform.renderers import resolve_agent_model_override
from winter_cli.modules.workspace.models import RepoError, StandaloneRepository
from winter_cli.modules.workspace.repository_factory import RepositoryFactory

logger = logging.getLogger(__name__)


class AgentMatrixService:
    """Builds an `AgentMatrix` fresh from `.winter/config.toml` on every call.

    Every `build()` re-reads config through the injected `WorkspaceConfigService`
    and derives its repo list from a `RepositoryFactory` built for that config
    by the injected `repo_factory_for` (wired in the container), rather
    than holding a launch-time `WorkspaceConfig` singleton — the same
    re-read-per-call shape as `DashboardSnapshotService`
    (`winter-context:/architecture/dependency-injection.md` §When app config IS
    the right injection, carve-out 1: a translation service that turns app
    config into runtime types). A `.winter/config.toml` edit is visible on the
    next `build()` without restarting a long-lived caller.

    `tiers` and `agents` are read straight off `build_effective_tier_table` and
    the injected `AgentCopyInspector` — the same collaborators `winter doctor`'s
    agent probe and `winter ws init` use — so this service never runs its own
    parse/resolve/render loop: "what does this agent resolve to" always answers
    identically everywhere. `matches_installed` is judged against the agent
    names the inspector yields, and repos come from `RepositoryFactory.get_extension_repos()`.
    Under `adopt_extensions = "none"` no agent is installed, so `agents` is
    empty — the installer and the doctor agent probe skip the same way.
    """

    def __init__(
        self,
        workspace_config_svc: WorkspaceConfigService,
        agent_copy_inspector: AgentCopyInspector,
        repo_factory_for: Callable[[WorkspaceConfig], RepositoryFactory],
    ) -> None:
        self._workspace_config_svc = workspace_config_svc
        self._agent_copy_inspector = agent_copy_inspector
        self._repo_factory_for = repo_factory_for

    def build(self) -> AgentMatrix:
        return self._build(self._workspace_config_svc.load())

    def _build(self, config: WorkspaceConfig) -> AgentMatrix:
        tier_table = build_effective_tier_table(config.model_tiers.tiers, config.model_tiers.tier_sources)
        repos = self._repo_factory_for(config).get_extension_repos()

        agents, installed_names = self._build_agents(config, repos, tier_table)
        tiers = self._build_tiers(tier_table)
        agent_overrides = self._build_agent_overrides(config.agent_model_overrides, tier_table, installed_names)

        return AgentMatrix(tiers=tiers, agent_overrides=agent_overrides, agents=agents)

    def _build_agents(
        self,
        config: WorkspaceConfig,
        repos: list[StandaloneRepository],
        tier_table: EffectiveTierTable,
    ) -> tuple[list[AgentMatrixEntry], set[str]]:
        agents: list[AgentMatrixEntry] = []
        installed_names: set[str] = set()
        if config.adopt_extensions == AdoptExtensions.none:
            return agents, installed_names
        known = self._agent_copy_inspector.known_agents(repos, mode=config.adopt_extensions)
        for vendor in CodeAgentVendor:
            agents_dir = config.workspace_root / vendor.agents_subpath
            for copy in self._agent_copy_inspector.inspect(
                known,
                vendor,
                tier_table=tier_table,
                agent_model_overrides=config.agent_model_overrides,
                agents_dir=agents_dir,
            ):
                installed_names.add(copy.agent_name)
                agents.append(
                    AgentMatrixEntry(
                        agent=copy.agent_name,
                        extension=copy.extension,
                        installed_name=copy.installed_path.stem,
                        harness=vendor.vendor_label,
                        resolution=copy.resolution,
                        error=str(copy.error) if copy.error is not None else None,
                        on_disk=copy.copy_status,
                    )
                )
        return agents, installed_names

    def _build_tiers(self, tier_table: EffectiveTierTable) -> list[TierMatrixEntry]:
        return [
            TierMatrixEntry(label=label, harness=vendor.vendor_label, cell=tier_table.cell(label, vendor.vendor_label))
            for label in sorted(tier_table.labels())
            for vendor in CodeAgentVendor
        ]

    def _build_agent_overrides(
        self,
        agent_model_overrides: AgentModelOverridesConfig,
        tier_table: EffectiveTierTable,
        installed_names: set[str],
    ) -> list[AgentOverrideEntry]:
        return [
            self._build_override_entry(agent_name, agent_model_overrides, tier_table, installed_names)
            for agent_name in sorted(agent_model_overrides.overrides)
        ]

    def _build_override_entry(
        self,
        agent_name: str,
        agent_model_overrides: AgentModelOverridesConfig,
        tier_table: EffectiveTierTable,
        installed_names: set[str],
    ) -> AgentOverrideEntry:
        raw_entry = agent_model_overrides.overrides[agent_name]
        tier = raw_entry if isinstance(raw_entry, str) else None

        harnesses: dict[str, AgentOverrideHarnessValue | None] = {}
        for vendor in CodeAgentVendor:
            vendor_label = vendor.vendor_label
            try:
                model, effort = resolve_agent_model_override(
                    agent_model_overrides, agent_name, vendor_label, tier_table
                )
            except RepoError as exc:
                logger.warning(
                    "agent matrix: [agent_model_overrides] entry %r has no usable value for vendor %r: %s",
                    agent_name,
                    vendor_label,
                    exc,
                )
                harnesses[vendor_label] = AgentOverrideHarnessValue(model=None, effort=None, error=str(exc))
                continue
            if model is None and effort is None:
                harnesses[vendor_label] = None
            else:
                harnesses[vendor_label] = AgentOverrideHarnessValue(
                    model=model.value if model is not None else None,
                    effort=effort.value if effort is not None else None,
                    error=None,
                )

        return AgentOverrideEntry(
            agent=agent_name,
            source=agent_model_overrides.source_for(agent_name),
            matches_installed=agent_name in installed_names,
            tier=tier,
            harnesses=harnesses,
        )


__all__ = ["AgentMatrixService"]
