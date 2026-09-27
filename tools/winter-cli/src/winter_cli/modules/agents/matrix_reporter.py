"""Reporters for `winter agents`: a human-readable Stream adapter and a machine Json adapter.

Follows the `modules/capability/capability_reporter.py` precedent: one
`render(matrix)` entry point per adapter, chosen by the handler off `--json`.
The Stream adapter renders through the injected `ICliOutputService` so its
`Cell` styling stays the one CLI table-rendering seam
(`winter-context:/architecture/winter-cli.md` §Reporters); the Json adapter
emits the exact three-section contract `winter agents --json` promises.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from winter_cli.core.cli_output_service import Cell, ICliOutputService
from winter_cli.modules.agents.cell_text import code_default_segments, effective_segments, tier_segments
from winter_cli.modules.agents.models import (
    LAYER_COLORS,
    LAYER_LABELS,
    AgentMatrix,
    AgentMatrixEntry,
    AgentOverrideEntry,
    AgentOverrideHarnessValue,
    TierMatrixEntry,
)
from winter_cli.modules.workspace.agent_transform.model_tiers import ModelTier, TierCell
from winter_cli.modules.workspace.agent_transform.models import (
    AgentModelOverrideValue,
    EffortResolution,
    ModelResolution,
    SourcedValue,
)

# Harness column order for every table — matches `CodeAgentVendor` declaration
# order (ClaudeCode, Codex, OpenCode).
_HARNESS_ORDER: tuple[str, ...] = ("claude", "codex", "opencode")


class IAgentMatrixReporter(Protocol):
    """Sink for one `AgentMatrix` snapshot, rendered in a single call."""

    def render(self, matrix: AgentMatrix) -> None: ...


# ── Stream (human-readable) ────────────────────────────────────────────────


class StreamAgentMatrixReporter:
    """Renders the three `winter agents` tables plus the layer legend.

    `agent_overrides` gets no table of its own — the effective matrix already
    shows every override in its layer color — only a stderr warning naming any
    entry that matches no installed agent.
    """

    def __init__(self, click: Any, cli_output: ICliOutputService) -> None:
        self._click = click
        self._cli_output = cli_output

    def render(self, matrix: AgentMatrix) -> None:
        self._render_code_defaults()
        self._click.echo("")
        self._render_tier_overrides(matrix.tiers)
        self._click.echo("")
        self._render_effective_matrix(matrix.agents)
        self._click.echo("")
        self._render_legend()
        self._warn_unmatched(matrix.unmatched_override_agents())

    # ── Table 1: code defaults ──────────────────────────────────────────

    def _render_code_defaults(self) -> None:
        self._click.echo(self._cli_output.style("Code defaults", "bold"))
        rows: list[list[str | Cell]] = []
        for tier in ModelTier:
            row: list[str | Cell] = [tier.value]
            row.extend(Cell.compose(code_default_segments(tier, harness)) for harness in _HARNESS_ORDER)
            rows.append(row)
        for line in self._cli_output.render_table(rows, headers=["tier", *_HARNESS_ORDER]):
            self._click.echo(line)

    # ── Table 2: tier overrides (effective tier table) ──────────────────

    def _render_tier_overrides(self, tiers: list[TierMatrixEntry]) -> None:
        self._click.echo(self._cli_output.style("Global overrides", "bold"))
        by_label: dict[str, dict[str, TierCell | None]] = {}
        for entry in tiers:
            by_label.setdefault(entry.label, {})[entry.harness] = entry.cell

        rows: list[list[str | Cell]] = []
        for label in sorted(by_label):
            row: list[str | Cell] = [label]
            row.extend(Cell.compose(tier_segments(by_label[label].get(harness))) for harness in _HARNESS_ORDER)
            rows.append(row)
        for line in self._cli_output.render_table(rows, headers=["tier", *_HARNESS_ORDER]):
            self._click.echo(line)

    # ── Table 3: effective matrix ────────────────────────────────────────

    def _render_effective_matrix(self, agents: list[AgentMatrixEntry]) -> None:
        self._click.echo(self._cli_output.style("Effective matrix", "bold"))
        if not agents:
            self._click.echo("(no installed agents)")
            return

        by_agent: dict[tuple[str, str], dict[str, AgentMatrixEntry]] = {}
        for entry in agents:
            by_agent.setdefault((entry.extension, entry.agent), {})[entry.harness] = entry

        rows: list[list[str | Cell]] = []
        for extension, agent in sorted(by_agent):
            row: list[str | Cell] = [agent, extension]
            row.extend(
                Cell.compose(effective_segments(by_agent[(extension, agent)].get(harness)))
                for harness in _HARNESS_ORDER
            )
            rows.append(row)
        headers = ["agent", "extension", *_HARNESS_ORDER]
        for line in self._cli_output.render_table(rows, headers=headers):
            self._click.echo(line)

    def _warn_unmatched(self, agents: list[str]) -> None:
        if agents:
            message = f"warning: overrides matching no installed agent: {', '.join(agents)}"
            self._click.echo(self._cli_output.style(message, "yellow"), err=True)

    # ── Legend ────────────────────────────────────────────────────────────

    def _render_legend(self) -> None:
        self._click.echo(self._cli_output.style("Legend", "bold"))
        for layer_name, color in LAYER_COLORS.items():
            self._click.echo(f"  {self._cli_output.style(LAYER_LABELS[layer_name], color)}")


# ── Json (machine contract) ──────────────────────────────────────────────


def _sourced_json(value: SourcedValue | None) -> dict[str, Any] | None:
    return None if value is None else {"value": value.value, "source": value.source.value}


def _model_override_json(value: AgentModelOverrideValue | None) -> dict[str, Any] | None:
    return None if value is None else {"value": value.value, "tier": value.tier, "source": value.source.value}


def _tier_json(entry: TierMatrixEntry) -> dict[str, Any]:
    cell = entry.cell
    if cell is None:
        return {
            "label": entry.label,
            "harness": entry.harness,
            "code_default": None,
            "tier_override": None,
            "effective": None,
            "effective_layer": None,
        }
    return {
        "label": entry.label,
        "harness": entry.harness,
        "code_default": cell.code_default,
        "tier_override": _sourced_json(cell.tier_override),
        "effective": cell.effective,
        "effective_layer": cell.effective_layer.value,
    }


def _override_harness_json(value: AgentOverrideHarnessValue | None) -> dict[str, Any] | None:
    return None if value is None else {"model": value.model, "effort": value.effort, "error": value.error}


def _override_json(entry: AgentOverrideEntry) -> dict[str, Any]:
    return {
        "agent": entry.agent,
        "source": entry.source.value,
        "matches_installed": entry.matches_installed,
        "tier": entry.tier,
        "harnesses": {harness: _override_harness_json(value) for harness, value in entry.harnesses.items()},
    }


def _model_resolution_json(model: ModelResolution) -> dict[str, Any]:
    return {
        "declared_tier": model.declared_tier,
        "code_default": model.code_default,
        "tier_override": _sourced_json(model.tier_override),
        "harness_block": model.harness_block,
        "agent_override": _model_override_json(model.agent_override),
        "effective": model.effective,
        "effective_layer": model.effective_layer.value,
    }


def _effort_resolution_json(effort: EffortResolution) -> dict[str, Any]:
    return {
        "harness_block": effort.harness_block,
        "agent_override": _sourced_json(effort.agent_override),
        "effective": effort.effective,
        "effective_layer": effort.effective_layer.value,
    }


def _agent_json(entry: AgentMatrixEntry) -> dict[str, Any]:
    resolution = entry.resolution
    return {
        "agent": entry.agent,
        "extension": entry.extension,
        "installed_name": entry.installed_name,
        "harness": entry.harness,
        "model": _model_resolution_json(resolution.model) if resolution is not None else None,
        "effort": _effort_resolution_json(resolution.effort) if resolution is not None else None,
        "on_disk": entry.on_disk.value if entry.on_disk is not None else None,
        "error": entry.error,
    }


class JsonAgentMatrixReporter:
    """Emits the `tiers` / `agent_overrides` / `agents` JSON contract as a single document.

    Stdout carries only this document — diagnostics (e.g. an unresolvable
    `[agent_model_overrides]` entry) go through the logger to stderr, never
    interleaved with this line.
    """

    def __init__(self, click: Any) -> None:
        self._click = click

    def render(self, matrix: AgentMatrix) -> None:
        payload = {
            "tiers": [_tier_json(entry) for entry in matrix.tiers],
            "agent_overrides": [_override_json(entry) for entry in matrix.agent_overrides],
            "agents": [_agent_json(entry) for entry in matrix.agents],
        }
        self._click.echo(json.dumps(payload))


def _conforms_stream_reporter(x: StreamAgentMatrixReporter) -> IAgentMatrixReporter:
    return x


def _conforms_json_reporter(x: JsonAgentMatrixReporter) -> IAgentMatrixReporter:
    return x
