"""Model tier enum and the canonical tier → vendor model-id lookup table.

``ModelTier`` carries the four built-in abstraction levels (fable / opus / sonnet / haiku).
``MODEL_TIER_IDS`` maps ``(ModelTier, vendor_label)`` to the concrete model-id
string each harness expects. Claude accepts tier aliases directly; each Codex and
OpenCode id carries its own verification note inline — read those before treating
a value as current, and re-verify against the vendor's live catalog when editing.

``build_effective_tier_table`` produces the runtime ``EffectiveTierTable`` by
merging the built-in defaults with workspace-configured overrides/extensions
from ``[model_tiers]`` in ``.winter/config.toml``.  All tier resolution should
use the effective table; ``MODEL_TIER_IDS`` is the source of truth for the
built-in defaults and for test assertions against the canonical ids. It is
the only tier-merge implementation — ``agent_transform.renderers.resolve_agent``,
the installer, the doctor copy inspector, and load-time tier-label validation
all consume it.

A per-harness ``model:`` key in the agent's override block wins over this
table — callers must apply that override before (or instead of) consulting it.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Mapping

from winter_cli.modules.workspace.agent_transform.models import ConfigSource, ModelLayer, SourcedValue


class ModelTier(enum.Enum):
    """Four built-in capability tiers; names match the Claude Code tier alias vocabulary.

    Declared most- to least-capable. ``fable`` is the top tier: agents that
    declare ``model: fable`` resolve through this table like any other tier, so
    no workspace ``[model_tiers]`` entry is needed to install them.
    """

    fable = "fable"
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
    (ModelTier.fable, "claude"): "fable",
    (ModelTier.opus, "claude"): "opus",
    (ModelTier.sonnet, "claude"): "sonnet",
    (ModelTier.haiku, "claude"): "haiku",
    # Codex: the astra > sol > terra > luna ranking and this tier mapping are
    # workspace-declared, not vendor-published; Codex exposes no machine-readable
    # capability order. Each tier maps to its own rank, most- to least-capable.
    # Each id was verified present in the local Codex catalog (2026-09-27, codex
    # 0.157.1). The gpt-6 series has no terra release, so the sonnet tier stays on 5.6.
    (ModelTier.fable, "codex"): "gpt-6-astra",
    (ModelTier.opus, "codex"): "gpt-6-sol",
    (ModelTier.sonnet, "codex"): "gpt-5.6-terra",
    (ModelTier.haiku, "codex"): "gpt-6-luna",
    # OpenCode: format per opencode.ai/docs/agents (provider/model-id).
    # The 5-series ids carry no date suffix; verifiable via 'opencode models'.
    # Haiku has no 5-series release, so that tier stays on 4.5.
    (ModelTier.fable, "opencode"): "anthropic/claude-fable-5-1",
    (ModelTier.opus, "opencode"): "anthropic/claude-opus-5-5",
    (ModelTier.sonnet, "opencode"): "anthropic/claude-sonnet-5-5",
    (ModelTier.haiku, "opencode"): "anthropic/claude-haiku-4-5",
}

# Built-in tier table as ``{tier_label: {vendor_label: model_id}}``, the base
# layer ``build_effective_tier_table`` merges ``[model_tiers]`` onto. Derived
# from ``MODEL_TIER_IDS`` — the two must remain in sync.
_BUILTIN_TIER_TABLE: dict[str, dict[str, str]] = {}
for (_tier, _vendor), _model_id in MODEL_TIER_IDS.items():
    _BUILTIN_TIER_TABLE.setdefault(_tier.value, {})[_vendor] = _model_id


@dataclasses.dataclass(frozen=True)
class TierCell:
    """One ``(tier label, vendor)`` cell of the effective tier table.

    ``code_default`` is the built-in ``MODEL_TIER_IDS`` value, or ``None`` for
    a custom label the built-in table does not define. ``tier_override`` is
    the workspace ``[model_tiers]`` value for this cell, if any, together with
    the file it was read from. ``effective``/``effective_layer`` name the
    winning value: ``tier_override`` when present, else ``code_default`` — a
    cell only exists when at least one of the two is set, so ``effective`` is
    never absent for a cell that exists.
    """

    code_default: str | None
    tier_override: SourcedValue | None
    effective: str
    effective_layer: ModelLayer


class EffectiveTierTable:
    """The merged tier table: built-in defaults ⊕ workspace ``[model_tiers]`` config.

    Structured as ``{tier_label: {vendor_label: TierCell}}``. A ``(label,
    vendor)`` pair absent from the table means neither the built-ins nor the
    workspace config map that vendor for that label — callers decide how to
    surface that gap (e.g. a ``null`` JSON cell); this type does not
    synthesize one.
    """

    def __init__(self, cells: Mapping[str, Mapping[str, TierCell]]) -> None:
        self._cells = cells

    def labels(self) -> frozenset[str]:
        """Every tier label known to the table — built-in or workspace-defined."""
        return frozenset(self._cells)

    def cell(self, label: str, vendor_label: str) -> TierCell | None:
        """The cell for ``(label, vendor_label)``, or ``None`` when unmapped."""
        return self._cells.get(label, {}).get(vendor_label)

    def model_id(self, label: str, vendor_label: str) -> str | None:
        """The effective model id for ``(label, vendor_label)``, or ``None`` when unmapped."""
        cell = self.cell(label, vendor_label)
        return cell.effective if cell is not None else None

    def __contains__(self, label: object) -> bool:
        return label in self._cells


def build_effective_tier_table(
    tiers: Mapping[str, Mapping[str, str]],
    tier_sources: Mapping[str, ConfigSource] | None = None,
) -> EffectiveTierTable:
    """Return the effective tier table: built-in defaults ⊕ workspace config.

    The built-in tiers (fable / opus / sonnet / haiku) are the base.  Entries in
    ``tiers`` (parsed from ``[model_tiers]``, i.e. ``ModelTiersConfig.tiers``)
    layer on top:

    - An entry for an **existing built-in label** overrides only the listed
      vendor ids; unlisted vendors inherit their built-in default value.
    - An entry for a **new label** adds a new tier; all required vendor ids
      must be provided by the caller (validated by the config parser).

    ``tiers`` and ``tier_sources`` are taken as plain mappings — not a
    ``ModelTiersConfig`` — so this module, which sits below ``config/models.py``
    in the import graph, never imports it. A label absent from ``tier_sources``
    (or when ``tier_sources`` is ``None``) defaults to ``ConfigSource.config_toml``,
    matching ``ModelTiersConfig.source_for``.
    """
    sources = tier_sources or {}
    result: dict[str, dict[str, TierCell]] = {}
    for label in set(_BUILTIN_TIER_TABLE) | set(tiers):
        builtin_vendor_ids = _BUILTIN_TIER_TABLE.get(label, {})
        override_vendor_ids = tiers.get(label, {})
        source = sources.get(label, ConfigSource.config_toml)
        cells: dict[str, TierCell] = {}
        for vendor in set(builtin_vendor_ids) | set(override_vendor_ids):
            code_default = builtin_vendor_ids.get(vendor)
            override_value = override_vendor_ids.get(vendor)
            if override_value is not None:
                cells[vendor] = TierCell(
                    code_default=code_default,
                    tier_override=SourcedValue(value=override_value, source=source),
                    effective=override_value,
                    effective_layer=ModelLayer.tier_override,
                )
            else:
                assert code_default is not None
                cells[vendor] = TierCell(
                    code_default=code_default,
                    tier_override=None,
                    effective=code_default,
                    effective_layer=ModelLayer.code_default,
                )
        result[label] = cells
    return EffectiveTierTable(result)
