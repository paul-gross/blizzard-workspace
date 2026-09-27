"""The shared agent-matrix cell builders `winter agents` and the TUI both render from."""

from __future__ import annotations

from winter_cli.modules.agents.cell_text import code_default_segments, effective_segments, tier_segments
from winter_cli.modules.agents.models import LAYER_COLORS, AgentMatrixEntry
from winter_cli.modules.workspace.agent_transform.agent_copy_inspector import CopyStatus
from winter_cli.modules.workspace.agent_transform.model_tiers import ModelTier, build_effective_tier_table
from winter_cli.modules.workspace.agent_transform.models import (
    AgentResolution,
    EffortLayer,
    EffortResolution,
    ModelLayer,
    ModelResolution,
)


def _resolution(
    model_layer: ModelLayer = ModelLayer.code_default,
    effort: str | None = None,
    effort_layer: EffortLayer = EffortLayer.inherited,
) -> AgentResolution:
    return AgentResolution(
        model=ModelResolution(
            declared_tier="sonnet",
            code_default="sonnet",
            tier_override=None,
            harness_block=None,
            agent_override=None,
            effective="sonnet",
            effective_layer=model_layer,
        ),
        effort=EffortResolution(
            harness_block=effort, agent_override=None, effective=effort, effective_layer=effort_layer
        ),
    )


def _entry(
    resolution: AgentResolution | None, on_disk: CopyStatus | None = CopyStatus.in_sync, error: str | None = None
) -> AgentMatrixEntry:
    return AgentMatrixEntry(
        agent="reviewer",
        extension="wf",
        installed_name="wf-reviewer",
        harness="claude",
        resolution=resolution,
        error=error,
        on_disk=on_disk,
    )


def test_code_default_cell_is_the_built_in_id_or_a_dim_dash() -> None:
    ((text, style),) = code_default_segments(ModelTier.opus, "claude")
    assert text and style == LAYER_COLORS["code_default"]
    assert code_default_segments(ModelTier.fable, "no-such-harness") == (("-", "dim"),)


def test_tier_cell_names_the_source_file_of_a_remap() -> None:
    table = build_effective_tier_table({"sonnet": {"codex": "remapped"}})
    assert tier_segments(table.cell("sonnet", "codex")) == (
        ("remapped", LAYER_COLORS["tier_override"]),
        ("  (config.toml)", "dim"),
    )
    assert tier_segments(None) == (("-", "dim"),)


def test_effective_cell_colors_each_half_by_its_layer_and_marks_a_stale_copy() -> None:
    resolution = _resolution(ModelLayer.harness_block, effort="high", effort_layer=EffortLayer.harness_block)
    assert effective_segments(_entry(resolution, on_disk=CopyStatus.stale)) == (
        ("sonnet", LAYER_COLORS["harness_block"]),
        ("·high", LAYER_COLORS["harness_block"]),
        ("  [stale]", "yellow"),
    )


def test_effective_cell_inherited_effort_in_sync_error_and_absent() -> None:
    assert effective_segments(_entry(_resolution())) == (("sonnet", LAYER_COLORS["code_default"]),)
    assert effective_segments(_entry(None, on_disk=None, error="boom")) == (("error", "red"),)
    assert effective_segments(None) == (("-", "dim"),)
