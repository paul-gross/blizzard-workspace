"""Domain types for the `winter agents` model/effort resolution matrix.

`AgentMatrix` is the read-only snapshot `AgentMatrixService.build()` assembles
and `matrix_reporter.py` renders. `LAYER_COLORS` is the single layer -> color
legend every surface that renders the matrix colors its cells from, and
`LAYER_LABELS` the matching layer -> legend label — its colors
are restricted to names both `ICliOutputService` `Cell` styling and rich styles
accept (see `core/cli_output_service.py::ICliOutputService`).
"""

from __future__ import annotations

import dataclasses

from winter_cli.modules.workspace.agent_transform.agent_copy_inspector import CopyStatus
from winter_cli.modules.workspace.agent_transform.model_tiers import TierCell
from winter_cli.modules.workspace.agent_transform.models import AgentResolution, ConfigSource, EffortLayer, ModelLayer

# Layer -> style name, keyed by the layer's string value so `ModelLayer` and
# `EffortLayer` share one entry for the layer names they hold in common
# (`harness_block`, `agent_override`). Restricted to style names
# `ICliOutputService.style`/`Cell` accept today (see
# `core/internal/click_cli_output_service.py::_style_kwargs`); any other
# surface reuses this same table rather than declaring its own.
LAYER_COLORS: dict[str, str] = {
    "code_default": "dim",
    "tier_override": "cyan",
    "harness_block": "white",
    "agent_override": "green",
    "inherited": "dim",
}

# Layer -> human-readable legend label, keyed like `LAYER_COLORS`. Display
# only: the JSON output keeps the raw layer names.
LAYER_LABELS: dict[str, str] = {
    "code_default": "Code Default",
    "tier_override": "Global Override",
    "harness_block": "Agent Definition",
    "agent_override": "Agent Override",
    "inherited": "Inherited",
}


def layer_color(layer: ModelLayer | EffortLayer) -> str:
    """Return the legend color for a model or effort layer."""
    return LAYER_COLORS[layer.value]


@dataclasses.dataclass(frozen=True)
class TierMatrixEntry:
    """One `(tier label, harness)` cell of the `tiers` JSON section.

    `cell` is `None` when the effective tier table has no mapping for this
    `(label, harness)` pair — a custom label a workspace config entry leaves
    incomplete for one vendor. `harness` is a `CodeAgentVendor.vendor_label`
    string (`"claude"` / `"codex"` / `"opencode"`).
    """

    label: str
    harness: str
    cell: TierCell | None


@dataclasses.dataclass(frozen=True)
class AgentOverrideHarnessValue:
    """One harness's `{model, effort, error}` breakdown within an `agent_overrides` entry.

    `error` is set (with `model`/`effort` both `None`) when this harness could
    not be resolved — e.g. a bare-string tier label with no mapping for this
    harness. That failure is distinct from "this harness does not apply",
    which stays the enclosing `None` in `AgentOverrideEntry.harnesses`: a
    resolved harness always carries `error=None`, never an absent key.
    """

    model: str | None
    effort: str | None
    error: str | None = None


@dataclasses.dataclass(frozen=True)
class AgentOverrideEntry:
    """One `[agent_model_overrides]` entry as configured.

    `harnesses` always carries all three `CodeAgentVendor.vendor_label` keys;
    a harness the override does not apply to (a per-vendor dict form that
    omits it) maps to `None` rather than being absent. `tier` is the tier
    label for the bare-string override form, `None` for the per-vendor dict
    form. `matches_installed` is judged against the agent names the injected
    `AgentCopyInspector` yields — an entry naming no installed agent is still
    listed here, flagged `False`.
    """

    agent: str
    source: ConfigSource
    matches_installed: bool
    tier: str | None
    harnesses: dict[str, AgentOverrideHarnessValue | None]


@dataclasses.dataclass(frozen=True)
class AgentMatrixEntry:
    """One agent x harness row of the `agents` JSON section.

    `resolution` and `error` are mutually exclusive, mirroring
    `AgentCopyEntry`: a `RepoError` raised while resolving the agent's
    model/effort means `resolution` is `None` and `on_disk` is `None` too —
    there is nothing to compare against the on-disk copy.
    """

    agent: str
    extension: str
    installed_name: str
    harness: str
    resolution: AgentResolution | None
    error: str | None
    on_disk: CopyStatus | None


@dataclasses.dataclass(frozen=True)
class AgentMatrix:
    """The full read-only snapshot `winter agents` renders."""

    tiers: list[TierMatrixEntry]
    agent_overrides: list[AgentOverrideEntry]
    agents: list[AgentMatrixEntry]

    def unmatched_override_agents(self) -> list[str]:
        """Agent names of `[agent_model_overrides]` entries that match no installed agent."""
        return [entry.agent for entry in self.agent_overrides if not entry.matches_installed]
