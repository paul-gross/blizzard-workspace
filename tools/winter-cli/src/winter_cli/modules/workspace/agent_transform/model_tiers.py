"""Model tier enum and the canonical tier → vendor model-id lookup table.

``ModelTier`` carries the three built-in abstraction levels (opus / sonnet / haiku).
``MODEL_TIER_IDS`` maps ``(ModelTier, vendor_label)`` to the concrete model-id
string each harness expects. Claude accepts tier aliases directly; Codex and
OpenCode ids are verified against vendor documentation (see inline comments).

``build_effective_tier_table`` produces the runtime tier table by merging the
built-in defaults with workspace-configured overrides/extensions from
``[model_tiers]`` in ``.winter/config.toml``.  All tier resolution should
use the effective table; ``MODEL_TIER_IDS`` is the source of truth for the
built-in defaults and for test assertions against the canonical ids.

A per-harness ``model:`` key in the agent's override block wins over this
table — callers must apply that override before (or instead of) consulting it.
"""

from __future__ import annotations

import enum


class ModelTier(enum.Enum):
    """Three built-in capability tiers; names match the Claude Code tier alias vocabulary."""

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

# Built-in tier table in the dict[str, dict[str, str]] shape used by
# ``build_effective_tier_table`` and the renderers.  Derived from
# ``MODEL_TIER_IDS`` — the two must remain in sync.
_BUILTIN_TIER_TABLE: dict[str, dict[str, str]] = {}
for (_tier, _vendor), _model_id in MODEL_TIER_IDS.items():
    _BUILTIN_TIER_TABLE.setdefault(_tier.value, {})[_vendor] = _model_id


def build_effective_tier_table(
    custom_tiers: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Return the effective tier table: built-in defaults ⊕ workspace config.

    The built-in tiers (opus / sonnet / haiku) are the base.  Entries in
    ``custom_tiers`` (parsed from ``[model_tiers]``) layer on top:

    - An entry for an **existing built-in label** overrides only the listed
      vendor ids; unlisted vendors inherit their built-in default value.
    - An entry for a **new label** adds a new tier; all required vendor ids
      must be provided by the caller (validated by the config parser).

    The result is a dict mapping tier label → dict[vendor_label → model_id].
    """
    result: dict[str, dict[str, str]] = {label: dict(vendor_ids) for label, vendor_ids in _BUILTIN_TIER_TABLE.items()}
    for label, vendor_ids in custom_tiers.items():
        if label in result:
            # Built-in tier: merge per-vendor so only listed vendors are replaced.
            result[label] = {**result[label], **vendor_ids}
        else:
            # New custom tier: add directly.
            result[label] = dict(vendor_ids)
    return result
