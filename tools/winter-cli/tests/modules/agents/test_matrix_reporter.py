"""Unit tests for the `winter agents` Stream and Json reporters."""

from __future__ import annotations

import json
from typing import Any

import pytest

from winter_cli.core.internal.click_cli_output_service import ClickCliOutputService
from winter_cli.modules.agents import matrix_reporter
from winter_cli.modules.agents.matrix_reporter import JsonAgentMatrixReporter, StreamAgentMatrixReporter
from winter_cli.modules.agents.models import (
    LAYER_COLORS,
    AgentMatrix,
    AgentMatrixEntry,
    AgentOverrideEntry,
    AgentOverrideHarnessValue,
    TierMatrixEntry,
)
from winter_cli.modules.workspace.agent_transform.agent_copy_inspector import CopyStatus
from winter_cli.modules.workspace.agent_transform.model_tiers import build_effective_tier_table
from winter_cli.modules.workspace.agent_transform.models import (
    AgentModelOverrideValue,
    AgentResolution,
    ConfigSource,
    EffortLayer,
    EffortResolution,
    ModelLayer,
    ModelResolution,
    SourcedValue,
)


class FakeClick:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.err_lines: list[str] = []

    def echo(self, message: str = "", err: bool = False, **_: Any) -> None:
        (self.err_lines if err else self.lines).append(message)

    def style(self, text: str, **_: Any) -> str:
        return text


_TIER_TABLE = build_effective_tier_table({"big-thinker": {"claude": "claude-opus-5-5"}})


def _model_resolution(**overrides: Any) -> ModelResolution:
    defaults: dict[str, Any] = {
        "declared_tier": "sonnet",
        "code_default": "sonnet",
        "tier_override": None,
        "harness_block": None,
        "agent_override": None,
        "effective": "sonnet",
        "effective_layer": ModelLayer.code_default,
    }
    defaults.update(overrides)
    return ModelResolution(**defaults)


def _effort_resolution(**overrides: Any) -> EffortResolution:
    defaults: dict[str, Any] = {
        "harness_block": None,
        "agent_override": None,
        "effective": None,
        "effective_layer": EffortLayer.inherited,
    }
    defaults.update(overrides)
    return EffortResolution(**defaults)


def _agent_entry(**overrides: Any) -> AgentMatrixEntry:
    defaults: dict[str, Any] = {
        "agent": "reviewer",
        "extension": "wf",
        "installed_name": "wf-reviewer",
        "harness": "claude",
        "resolution": AgentResolution(model=_model_resolution(), effort=_effort_resolution()),
        "error": None,
        "on_disk": CopyStatus.in_sync,
    }
    defaults.update(overrides)
    return AgentMatrixEntry(**defaults)


def _matrix(
    tiers: list[TierMatrixEntry] | None = None,
    agent_overrides: list[AgentOverrideEntry] | None = None,
    agents: list[AgentMatrixEntry] | None = None,
) -> AgentMatrix:
    return AgentMatrix(tiers=tiers or [], agent_overrides=agent_overrides or [], agents=agents or [])


# ── Stream reporter ───────────────────────────────────────────────────────────


def test_stream_renders_three_tables_and_legend() -> None:
    click = FakeClick()
    cli_output = ClickCliOutputService()
    matrix = _matrix(
        tiers=[TierMatrixEntry(label="sonnet", harness="claude", cell=_TIER_TABLE.cell("sonnet", "claude"))],
        agent_overrides=[
            AgentOverrideEntry(
                agent="reviewer",
                source=ConfigSource.config_toml,
                matches_installed=True,
                tier="haiku",
                harnesses={
                    "claude": AgentOverrideHarnessValue(model="haiku", effort=None),
                    "codex": None,
                    "opencode": None,
                },
            )
        ],
        agents=[_agent_entry()],
    )
    StreamAgentMatrixReporter(click, cli_output).render(matrix)
    output = "\n".join(click.lines)
    assert "Code defaults" in output
    assert "Global overrides" in output
    assert "Agent overrides" not in output
    assert "Effective matrix" in output
    assert "Legend" in output
    assert "reviewer" in output
    assert "sonnet" in output


def test_stream_warns_on_stderr_about_an_unmatched_override() -> None:
    click = FakeClick()
    cli_output = ClickCliOutputService()
    matrix = _matrix(
        agent_overrides=[
            AgentOverrideEntry(
                agent="ghost",
                source=ConfigSource.config_toml,
                matches_installed=False,
                tier="haiku",
                harnesses={"claude": None, "codex": None, "opencode": None},
            )
        ],
    )
    StreamAgentMatrixReporter(click, cli_output).render(matrix)
    assert "ghost" not in "\n".join(click.lines)
    (warning,) = click.err_lines
    assert warning == cli_output.style("warning: overrides matching no installed agent: ghost", "yellow")


def test_stream_prints_no_warning_when_every_override_matches() -> None:
    click = FakeClick()
    matrix = _matrix(
        agent_overrides=[
            AgentOverrideEntry(
                agent="reviewer",
                source=ConfigSource.config_toml,
                matches_installed=True,
                tier="haiku",
                harnesses={"claude": None, "codex": None, "opencode": None},
            )
        ],
    )
    StreamAgentMatrixReporter(click, ClickCliOutputService()).render(matrix)
    assert click.err_lines == []


def test_stream_marks_stale_and_error_cells() -> None:
    click = FakeClick()
    cli_output = ClickCliOutputService()
    stale_entry = _agent_entry(agent="stale-agent", on_disk=CopyStatus.stale)
    error_entry = _agent_entry(
        agent="broken-agent", extension="wf", harness="codex", resolution=None, error="boom", on_disk=None
    )
    StreamAgentMatrixReporter(click, cli_output).render(_matrix(agents=[stale_entry, error_entry]))
    output = "\n".join(click.lines)
    assert "stale" in output
    assert "error" in output


def test_stream_empty_matrix_does_not_crash() -> None:
    click = FakeClick()
    cli_output = ClickCliOutputService()
    StreamAgentMatrixReporter(click, cli_output).render(_matrix())
    output = "\n".join(click.lines)
    assert "no installed agents" in output


# ── Json reporter ─────────────────────────────────────────────────────────────


def test_json_emits_single_line_with_three_sections() -> None:
    click = FakeClick()
    JsonAgentMatrixReporter(click).render(_matrix())
    assert len(click.lines) == 1
    payload = json.loads(click.lines[0])
    assert set(payload) == {"tiers", "agent_overrides", "agents"}


def test_json_tier_entry_shape() -> None:
    click = FakeClick()
    cell = _TIER_TABLE.cell("big-thinker", "claude")
    matrix = _matrix(tiers=[TierMatrixEntry(label="big-thinker", harness="claude", cell=cell)])
    JsonAgentMatrixReporter(click).render(matrix)
    payload = json.loads(click.lines[0])
    (entry,) = payload["tiers"]
    assert entry["label"] == "big-thinker"
    assert entry["harness"] == "claude"
    assert entry["code_default"] is None
    assert entry["tier_override"] == {"value": "claude-opus-5-5", "source": "config.toml"}
    assert entry["effective"] == "claude-opus-5-5"
    assert entry["effective_layer"] == "tier_override"


def test_json_tier_entry_all_null_for_unmapped_custom_label() -> None:
    click = FakeClick()
    matrix = _matrix(tiers=[TierMatrixEntry(label="custom", harness="codex", cell=None)])
    JsonAgentMatrixReporter(click).render(matrix)
    payload = json.loads(click.lines[0])
    (entry,) = payload["tiers"]
    assert entry["code_default"] is None
    assert entry["tier_override"] is None
    assert entry["effective"] is None
    assert entry["effective_layer"] is None


def test_json_agent_override_entry_shape() -> None:
    click = FakeClick()
    matrix = _matrix(
        agent_overrides=[
            AgentOverrideEntry(
                agent="reviewer",
                source=ConfigSource.config_local_toml,
                matches_installed=False,
                tier="haiku",
                harnesses={
                    "claude": AgentOverrideHarnessValue(model="haiku", effort=None),
                    "codex": None,
                    "opencode": None,
                },
            )
        ]
    )
    JsonAgentMatrixReporter(click).render(matrix)
    payload = json.loads(click.lines[0])
    (entry,) = payload["agent_overrides"]
    assert entry["agent"] == "reviewer"
    assert entry["source"] == "config.local.toml"
    assert entry["matches_installed"] is False
    assert entry["tier"] == "haiku"
    assert entry["harnesses"]["claude"] == {"model": "haiku", "effort": None, "error": None}
    assert entry["harnesses"]["codex"] is None
    assert entry["harnesses"]["opencode"] is None


def test_json_agent_override_harness_carries_its_error_when_unresolvable() -> None:
    click = FakeClick()
    matrix = _matrix(
        agent_overrides=[
            AgentOverrideEntry(
                agent="reviewer",
                source=ConfigSource.config_toml,
                matches_installed=True,
                tier="claude-only",
                harnesses={
                    "claude": AgentOverrideHarnessValue(model="claude-only-id", effort=None, error=None),
                    "codex": AgentOverrideHarnessValue(model=None, effort=None, error="unknown model tier mapping"),
                    "opencode": None,
                },
            )
        ]
    )
    JsonAgentMatrixReporter(click).render(matrix)
    payload = json.loads(click.lines[0])
    (entry,) = payload["agent_overrides"]
    assert entry["harnesses"]["claude"] == {"model": "claude-only-id", "effort": None, "error": None}
    assert entry["harnesses"]["codex"] == {"model": None, "effort": None, "error": "unknown model tier mapping"}
    assert entry["harnesses"]["opencode"] is None


def test_json_agent_entry_full_resolution_shape() -> None:
    click = FakeClick()
    resolution = AgentResolution(
        model=_model_resolution(
            tier_override=SourcedValue(value="openai/gpt-6-luna", source=ConfigSource.config_toml),
            agent_override=AgentModelOverrideValue(
                value="openai/gpt-6-luna", tier=None, source=ConfigSource.config_toml
            ),
            effective="openai/gpt-6-luna",
            effective_layer=ModelLayer.agent_override,
        ),
        effort=_effort_resolution(
            agent_override=SourcedValue(value="max", source=ConfigSource.config_toml),
            effective="max",
            effective_layer=EffortLayer.agent_override,
        ),
    )
    entry = _agent_entry(agent="ice-carver", harness="opencode", resolution=resolution, on_disk=CopyStatus.in_sync)
    JsonAgentMatrixReporter(click).render(_matrix(agents=[entry]))
    payload = json.loads(click.lines[0])
    (obj,) = payload["agents"]
    assert obj["agent"] == "ice-carver"
    assert obj["harness"] == "opencode"
    assert obj["model"]["agent_override"] == {"value": "openai/gpt-6-luna", "tier": None, "source": "config.toml"}
    assert obj["model"]["effective_layer"] == "agent_override"
    assert obj["effort"]["agent_override"] == {"value": "max", "source": "config.toml"}
    assert obj["effort"]["effective_layer"] == "agent_override"
    assert obj["on_disk"] == "in_sync"
    assert obj["error"] is None


def test_json_agent_entry_error_shape() -> None:
    click = FakeClick()
    entry = _agent_entry(resolution=None, error="unknown model tier 'bogus'", on_disk=None)
    JsonAgentMatrixReporter(click).render(_matrix(agents=[entry]))
    payload = json.loads(click.lines[0])
    (obj,) = payload["agents"]
    assert obj["model"] is None
    assert obj["effort"] is None
    assert obj["on_disk"] is None
    assert obj["error"] == "unknown model tier 'bogus'"


def test_json_agent_entry_emits_every_field_with_non_applicable_layers_null() -> None:
    """Every field is present on every entry; a layer that does not apply is `null`, never omitted."""
    click = FakeClick()
    JsonAgentMatrixReporter(click).render(_matrix(agents=[_agent_entry()]))
    payload = json.loads(click.lines[0])
    (obj,) = payload["agents"]
    assert obj == {
        "agent": "reviewer",
        "extension": "wf",
        "installed_name": "wf-reviewer",
        "harness": "claude",
        "model": {
            "declared_tier": "sonnet",
            "code_default": "sonnet",
            "tier_override": None,
            "harness_block": None,
            "agent_override": None,
            "effective": "sonnet",
            "effective_layer": "code_default",
        },
        "effort": {
            "harness_block": None,
            "agent_override": None,
            "effective": None,
            "effective_layer": "inherited",
        },
        "on_disk": "in_sync",
        "error": None,
    }


def test_json_agent_entry_on_disk_value_is_the_copy_status() -> None:
    click = FakeClick()
    agents = [_agent_entry(on_disk=CopyStatus.stale), _agent_entry(harness="codex", on_disk=CopyStatus.missing)]
    JsonAgentMatrixReporter(click).render(_matrix(agents=agents))
    payload = json.loads(click.lines[0])
    assert [a["on_disk"] for a in payload["agents"]] == ["stale", "missing"]


# ── Stream reporter: layer colors ─────────────────────────────────────────────


def _styled(text: str, layer: str) -> str:
    return ClickCliOutputService().style(text, LAYER_COLORS[layer])


def test_stream_effective_cell_colors_model_and_effort_halves_by_their_own_layers() -> None:
    click = FakeClick()
    resolution = AgentResolution(
        model=_model_resolution(
            harness_block="claude-native", effective="claude-native", effective_layer=ModelLayer.harness_block
        ),
        effort=_effort_resolution(
            agent_override=SourcedValue(value="max", source=ConfigSource.config_toml),
            effective="max",
            effective_layer=EffortLayer.agent_override,
        ),
    )
    StreamAgentMatrixReporter(click, ClickCliOutputService()).render(
        _matrix(agents=[_agent_entry(resolution=resolution)])
    )
    output = "\n".join(click.lines)
    assert _styled("claude-native", "harness_block") in output
    assert _styled("·max", "agent_override") in output


def test_stream_effective_cell_marks_stale_and_missing_copies() -> None:
    click = FakeClick()
    agents = [_agent_entry(on_disk=CopyStatus.stale), _agent_entry(harness="codex", on_disk=CopyStatus.missing)]
    StreamAgentMatrixReporter(click, ClickCliOutputService()).render(_matrix(agents=agents))
    output = "\n".join(click.lines)
    assert "[stale]" in output
    assert "[missing]" in output
    assert "[in_sync]" not in output


def test_stream_tier_cell_colored_by_layer_with_source_file() -> None:
    click = FakeClick()
    table = build_effective_tier_table({"sonnet": {"codex": "remapped"}})
    tiers = [
        TierMatrixEntry(label="sonnet", harness="claude", cell=table.cell("sonnet", "claude")),
        TierMatrixEntry(label="sonnet", harness="codex", cell=table.cell("sonnet", "codex")),
    ]
    StreamAgentMatrixReporter(click, ClickCliOutputService()).render(_matrix(tiers=tiers))
    output = "\n".join(click.lines)
    assert _styled("sonnet", "code_default") in output
    assert _styled("remapped", "tier_override") in output
    assert "(config.toml)" in output


def test_stream_legend_prints_every_layer_in_its_color() -> None:
    click = FakeClick()
    StreamAgentMatrixReporter(click, ClickCliOutputService()).render(_matrix())
    output = "\n".join(click.lines)
    for layer, label in (
        ("code_default", "Code Default"),
        ("tier_override", "Global Override"),
        ("harness_block", "Agent Definition"),
        ("agent_override", "Agent Override"),
        ("inherited", "Inherited"),
    ):
        assert _styled(label, layer) in output
    assert "code_default" not in output


def test_layer_colors_are_distinct_for_the_model_layers() -> None:
    """Each model layer has its own color, so an effective cell's color names exactly one layer."""
    model_colors = [LAYER_COLORS[layer.value] for layer in ModelLayer]
    assert len(set(model_colors)) == len(model_colors)


def test_stream_cells_come_from_the_shared_cell_builders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(matrix_reporter, "effective_segments", lambda entry: (("EFFECTIVE-CELL", None),))
    monkeypatch.setattr(matrix_reporter, "tier_segments", lambda cell: (("TIER-CELL", None),))
    monkeypatch.setattr(matrix_reporter, "code_default_segments", lambda tier, harness: (("CODE-CELL", None),))
    tiers = [TierMatrixEntry(label="sonnet", harness="claude", cell=_TIER_TABLE.cell("sonnet", "claude"))]
    click = FakeClick()
    StreamAgentMatrixReporter(click, ClickCliOutputService()).render(_matrix(tiers=tiers, agents=[_agent_entry()]))
    output = "\n".join(click.lines)
    assert "EFFECTIVE-CELL" in output
    assert "TIER-CELL" in output
    assert "CODE-CELL" in output
