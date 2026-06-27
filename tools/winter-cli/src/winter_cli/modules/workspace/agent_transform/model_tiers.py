"""Model tier enum and the canonical tier → vendor model-id lookup table.

``ModelTier`` carries the three abstraction levels (opus / sonnet / haiku).
``MODEL_TIER_IDS`` maps ``(ModelTier, vendor_label)`` to the concrete model-id
string each harness expects. Claude accepts tier aliases directly; Codex and
OpenCode ids are verified against vendor documentation (see inline comments).

A per-harness ``model:`` key in the agent's override block wins over this
table — callers must apply that override before (or instead of) consulting it.
"""

from __future__ import annotations

import enum


class ModelTier(enum.Enum):
    """Three capability tiers; names match the Claude Code tier alias vocabulary."""

    opus = "opus"
    sonnet = "sonnet"
    haiku = "haiku"


# Canonical vendor labels that appear as override-block keys in canonical agent
# frontmatter (``claude:``, ``codex:``, ``opencode:``) and as the second key in
# ``MODEL_TIER_IDS``.  This set is the single source of truth for the vocabulary
# used by ``CanonicalAgentParser._VENDOR_LABELS``.
#
# ``CodeAgentVendor.vendor_label`` (in ``config/models.py``) must match these
# strings exactly; the test suite verifies the invariant via
# ``test_vendor_label_matches_vendor_labels_set``.
VENDOR_LABELS: frozenset[str] = frozenset({"claude", "codex", "opencode"})

# Vendor labels match CodeAgentVendor.vendor_label ("claude-code" value → "claude"
# label; "codex" → "codex"; "opencode" → "opencode").
#
# Resolution rule: MODEL_TIER_IDS[(tier, vendor)] is the *fallback*. A per-harness
# `model:` key in the agent's `<vendor>:` override block takes precedence.
MODEL_TIER_IDS: dict[tuple[ModelTier, str], str] = {
    # Claude Code accepts the tier alias directly as the model identifier.
    (ModelTier.opus, "claude"): "opus",
    (ModelTier.sonnet, "claude"): "sonnet",
    (ModelTier.haiku, "claude"): "haiku",
    # Codex: verified against developers.openai.com/codex/subagents (2026-06).
    # Both opus and sonnet tiers map to gpt-5.4; haiku maps to gpt-5.4-mini.
    (ModelTier.opus, "codex"): "gpt-5.4",
    (ModelTier.sonnet, "codex"): "gpt-5.4",
    (ModelTier.haiku, "codex"): "gpt-5.4-mini",
    # OpenCode: format per opencode.ai/docs/agents (provider/model-id);
    # pin dates verifiable via 'opencode models'.
    (ModelTier.opus, "opencode"): "anthropic/claude-opus-4-20250514",
    (ModelTier.sonnet, "opencode"): "anthropic/claude-sonnet-4-20250514",
    (ModelTier.haiku, "opencode"): "anthropic/claude-haiku-4-20250514",
}
