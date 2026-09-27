"""The styled text of every harness cell in the agent matrix, shared by `winter agents` and the TUI.

Each builder returns the cell as `(text, style)` segments, styled from
`LAYER_COLORS`; the CLI wraps them in a `Cell`, the TUI in a rich `Text` (and
truncates that to its column width). Neither surface formats a cell itself, so
the two can never show the same cell differently.
"""

from __future__ import annotations

from winter_cli.modules.agents.models import AgentMatrixEntry, layer_color
from winter_cli.modules.workspace.agent_transform.agent_copy_inspector import CopyStatus
from winter_cli.modules.workspace.agent_transform.model_tiers import MODEL_TIER_IDS, ModelTier, TierCell
from winter_cli.modules.workspace.agent_transform.models import ModelLayer

Segments = tuple[tuple[str, str | None], ...]

_NOT_APPLICABLE: Segments = (("-", "dim"),)
_ERROR: Segments = (("error", "red"),)


def code_default_segments(tier: ModelTier, harness: str) -> Segments:
    """A built-in `MODEL_TIER_IDS` cell, or `-` where the tier has no id for `harness`."""
    model_id = MODEL_TIER_IDS.get((tier, harness))
    return _NOT_APPLICABLE if model_id is None else ((model_id, layer_color(ModelLayer.code_default)),)


def tier_segments(cell: TierCell | None) -> Segments:
    """An effective tier-table cell; a `[model_tiers]` remap also names its source file."""
    if cell is None:
        return _NOT_APPLICABLE
    segments: list[tuple[str, str | None]] = [(cell.effective, layer_color(cell.effective_layer))]
    if cell.tier_override is not None:
        segments.append((f"  ({cell.tier_override.source.value})", "dim"))
    return tuple(segments)


def effective_segments(entry: AgentMatrixEntry | None) -> Segments:
    """An effective-matrix cell: `model·effort`, each half in its own layer's color.

    An inherited effort prints no effort half; a stale or missing on-disk copy
    appends its marker; a cell whose resolution failed prints `error`.
    """
    if entry is None:
        return _NOT_APPLICABLE
    if entry.error is not None or entry.resolution is None:
        return _ERROR

    resolution = entry.resolution
    segments: list[tuple[str, str | None]] = [
        (resolution.model.effective, layer_color(resolution.model.effective_layer))
    ]
    if resolution.effort.effective is not None:
        segments.append((f"·{resolution.effort.effective}", layer_color(resolution.effort.effective_layer)))
    if entry.on_disk is not None and entry.on_disk is not CopyStatus.in_sync:
        segments.append((f"  [{entry.on_disk.value}]", "yellow"))
    return tuple(segments)


__all__ = ["Segments", "code_default_segments", "effective_segments", "tier_segments"]
