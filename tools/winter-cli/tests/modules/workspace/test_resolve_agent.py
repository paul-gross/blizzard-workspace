"""Unit tests for the shared model/effort resolver, ``resolve_agent``.

Covers every ``ModelLayer``/``EffortLayer`` value it can produce, a tier-string
``[agent_model_overrides]`` entry (built-in and custom label), a custom tier
label that leaves a vendor unmapped, and source attribution that reflects a
local-over-shared win rather than always defaulting to ``config.toml``.
"""

from __future__ import annotations

from textwrap import dedent

import pytest

from winter_cli.config.models import AgentModelOverridesConfig
from winter_cli.modules.workspace.agent_transform.canonical_parser import CanonicalAgentParser
from winter_cli.modules.workspace.agent_transform.model_tiers import (
    MODEL_TIER_IDS,
    ModelTier,
    build_effective_tier_table,
)
from winter_cli.modules.workspace.agent_transform.models import (
    AgentModelOverrideProfile,
    AgentModelOverrideValue,
    AgentResolution,
    ConfigSource,
    EffortLayer,
    EffortResolution,
    ModelLayer,
    ModelResolution,
    SourcedValue,
)
from winter_cli.modules.workspace.agent_transform.renderers import resolve_agent
from winter_cli.modules.workspace.models import RepoError

_PARSER = CanonicalAgentParser()
_DEFAULT_TIER_TABLE = build_effective_tier_table({})
_NO_OVERRIDES = AgentModelOverridesConfig()

_SONNET_AGENT_MD = dedent("""\
    ---
    name: reviewer
    description: Reviews code changes
    model: sonnet
    ---
    You are a code reviewer.
    """)

_SONNET_AGENT_WITH_CLAUDE_MODEL_MD = dedent("""\
    ---
    name: reviewer
    description: Reviews code changes
    model: sonnet
    claude:
      model: claude-harness-pinned
    ---
    You are a code reviewer.
    """)

_SONNET_AGENT_WITH_CLAUDE_EFFORT_MD = dedent("""\
    ---
    name: reviewer
    description: Reviews code changes
    model: sonnet
    claude:
      effort: low
    ---
    You are a code reviewer.
    """)


# ── Model layer: code_default ────────────────────────────────────────────────


def test_model_effective_layer_code_default() -> None:
    """No tier override, no harness block, no agent override → code_default wins."""
    agent = _PARSER.parse(_SONNET_AGENT_MD)
    resolution = resolve_agent(agent, "claude", _DEFAULT_TIER_TABLE, _NO_OVERRIDES)

    assert resolution.model.effective_layer is ModelLayer.code_default
    assert resolution.model.effective == MODEL_TIER_IDS[(ModelTier.sonnet, "claude")]
    assert resolution.model.code_default == MODEL_TIER_IDS[(ModelTier.sonnet, "claude")]
    assert resolution.model.tier_override is None
    assert resolution.model.harness_block is None
    assert resolution.model.agent_override is None


# ── Model layer: tier_override ───────────────────────────────────────────────


def test_model_effective_layer_tier_override() -> None:
    """A [model_tiers] remap for the agent's tier wins over the code default."""
    agent = _PARSER.parse(_SONNET_AGENT_MD)
    tier_table = build_effective_tier_table(
        {"sonnet": {"claude": "claude-sonnet-remapped"}}, {"sonnet": ConfigSource.config_toml}
    )
    resolution = resolve_agent(agent, "claude", tier_table, _NO_OVERRIDES)

    assert resolution.model.effective_layer is ModelLayer.tier_override
    assert resolution.model.effective == "claude-sonnet-remapped"
    assert resolution.model.code_default == MODEL_TIER_IDS[(ModelTier.sonnet, "claude")]
    assert resolution.model.tier_override is not None
    assert resolution.model.tier_override.value == "claude-sonnet-remapped"
    assert resolution.model.tier_override.source is ConfigSource.config_toml
    assert resolution.model.harness_block is None
    assert resolution.model.agent_override is None


# ── Model layer: harness_block ───────────────────────────────────────────────


def test_model_effective_layer_harness_block() -> None:
    """The agent's own per-harness model: key beats a tier_override and code_default."""
    agent = _PARSER.parse(_SONNET_AGENT_WITH_CLAUDE_MODEL_MD)
    tier_table = build_effective_tier_table({"sonnet": {"claude": "claude-sonnet-remapped"}})
    resolution = resolve_agent(agent, "claude", tier_table, _NO_OVERRIDES)

    assert resolution.model.effective_layer is ModelLayer.harness_block
    assert resolution.model.effective == "claude-harness-pinned"
    assert resolution.model.harness_block == "claude-harness-pinned"
    # The lower layers are still recorded even though they didn't win.
    assert resolution.model.code_default == MODEL_TIER_IDS[(ModelTier.sonnet, "claude")]
    assert resolution.model.tier_override is not None
    assert resolution.model.tier_override.value == "claude-sonnet-remapped"
    assert resolution.model.agent_override is None


# ── Model layer: agent_override ──────────────────────────────────────────────


def test_model_effective_layer_agent_override_concrete() -> None:
    """A per-vendor [agent_model_overrides] concrete id beats the harness block."""
    agent = _PARSER.parse(_SONNET_AGENT_WITH_CLAUDE_MODEL_MD)
    overrides = AgentModelOverridesConfig(
        overrides={"reviewer": {"claude": "claude-override-id"}},
        override_sources={"reviewer": ConfigSource.config_toml},
    )
    resolution = resolve_agent(agent, "claude", _DEFAULT_TIER_TABLE, overrides)

    assert resolution.model.effective_layer is ModelLayer.agent_override
    assert resolution.model.effective == "claude-override-id"
    assert resolution.model.harness_block == "claude-harness-pinned"
    assert resolution.model.agent_override is not None
    assert resolution.model.agent_override.value == "claude-override-id"
    assert resolution.model.agent_override.tier is None
    assert resolution.model.agent_override.source is ConfigSource.config_toml


def test_partial_tier_remap_leaves_unlisted_vendor_on_code_default() -> None:
    """A [model_tiers] entry listing one vendor remaps only that vendor; the others stay on code_default."""
    agent = _PARSER.parse(_SONNET_AGENT_MD)
    tier_table = build_effective_tier_table({"sonnet": {"opencode": "openai/gpt-6-luna"}})

    opencode = resolve_agent(agent, "opencode", tier_table, _NO_OVERRIDES)
    codex = resolve_agent(agent, "codex", tier_table, _NO_OVERRIDES)

    assert opencode.model.effective_layer is ModelLayer.tier_override
    assert opencode.model.effective == "openai/gpt-6-luna"
    assert codex.model.effective_layer is ModelLayer.code_default
    assert codex.model.effective == MODEL_TIER_IDS[(ModelTier.sonnet, "codex")]
    assert codex.model.tier_override is None


# ── Tier-string agent override: built-in and custom label ───────────────────


def test_model_tier_string_override_builtin_label() -> None:
    """A bare-string override naming a built-in tier resolves to that tier's id and records the label."""
    agent = _PARSER.parse(_SONNET_AGENT_MD)
    overrides = AgentModelOverridesConfig(overrides={"reviewer": "haiku"})
    resolution = resolve_agent(agent, "opencode", _DEFAULT_TIER_TABLE, overrides)

    assert resolution.model.effective_layer is ModelLayer.agent_override
    assert resolution.model.effective == MODEL_TIER_IDS[(ModelTier.haiku, "opencode")]
    assert resolution.model.agent_override is not None
    assert resolution.model.agent_override.tier == "haiku"
    assert resolution.model.agent_override.value == MODEL_TIER_IDS[(ModelTier.haiku, "opencode")]


def test_model_tier_string_override_custom_label() -> None:
    """A bare-string override naming a workspace-defined custom tier resolves via the effective table."""
    agent = _PARSER.parse(_SONNET_AGENT_MD)
    tier_table = build_effective_tier_table({"big-thinker": {"claude": "opus-id", "opencode": "opus-oc-id"}})
    overrides = AgentModelOverridesConfig(overrides={"reviewer": "big-thinker"})
    resolution = resolve_agent(agent, "opencode", tier_table, overrides)

    assert resolution.model.effective_layer is ModelLayer.agent_override
    assert resolution.model.effective == "opus-oc-id"
    assert resolution.model.agent_override is not None
    assert resolution.model.agent_override.tier == "big-thinker"
    assert resolution.model.agent_override.value == "opus-oc-id"
    # The declared tier's own code_default/tier_override are independent of the
    # override's tier — the agent declared "sonnet", not "big-thinker".
    assert resolution.model.code_default == MODEL_TIER_IDS[(ModelTier.sonnet, "opencode")]


# ── Custom tier label leaving a vendor unmapped ──────────────────────────────


def test_custom_tier_label_unmapped_vendor_still_resolves_via_harness_block() -> None:
    """A vendor missing from a custom tier is recorded as null but a harness block still resolves it."""
    text = dedent("""\
        ---
        name: x
        description: d
        model: big-thinker
        opencode:
          model: opencode-explicit-id
        ---
        Body.
        """)
    agent = _PARSER.parse(text)
    # big-thinker only maps claude — opencode is left unmapped.
    tier_table = build_effective_tier_table({"big-thinker": {"claude": "claude-opus-id"}})
    resolution = resolve_agent(agent, "opencode", tier_table, _NO_OVERRIDES)

    assert resolution.model.effective_layer is ModelLayer.harness_block
    assert resolution.model.effective == "opencode-explicit-id"
    # The lower layers genuinely do not apply for this vendor — both null.
    assert resolution.model.code_default is None
    assert resolution.model.tier_override is None


def test_custom_tier_label_unmapped_vendor_raises_without_a_higher_layer() -> None:
    """A vendor missing from a custom tier raises RepoError when no higher layer covers the gap."""
    agent = _PARSER.parse("---\nname: x\ndescription: d\nmodel: big-thinker\n---\n\nBody.\n")
    tier_table = build_effective_tier_table({"big-thinker": {"claude": "claude-opus-id"}})

    with pytest.raises(RepoError, match="big-thinker"):
        resolve_agent(agent, "opencode", tier_table, _NO_OVERRIDES)


# ── Effort layer: inherited ──────────────────────────────────────────────────


def test_effort_effective_layer_inherited() -> None:
    """No native effort key and no workspace override → inherited, effective is None."""
    agent = _PARSER.parse(_SONNET_AGENT_MD)
    resolution = resolve_agent(agent, "claude", _DEFAULT_TIER_TABLE, _NO_OVERRIDES)

    assert resolution.effort.effective_layer is EffortLayer.inherited
    assert resolution.effort.effective is None
    assert resolution.effort.harness_block is None
    assert resolution.effort.agent_override is None


# ── Effort layer: harness_block ──────────────────────────────────────────────


def test_effort_effective_layer_harness_block() -> None:
    """The agent's own native effort key wins when no workspace override applies."""
    agent = _PARSER.parse(_SONNET_AGENT_WITH_CLAUDE_EFFORT_MD)
    resolution = resolve_agent(agent, "claude", _DEFAULT_TIER_TABLE, _NO_OVERRIDES)

    assert resolution.effort.effective_layer is EffortLayer.harness_block
    assert resolution.effort.effective == "low"
    assert resolution.effort.harness_block == "low"
    assert resolution.effort.agent_override is None


# ── Effort layer: agent_override ─────────────────────────────────────────────


def test_effort_effective_layer_agent_override() -> None:
    """A workspace profile effort beats the agent's own native effort key."""
    agent = _PARSER.parse(_SONNET_AGENT_WITH_CLAUDE_EFFORT_MD)
    overrides = AgentModelOverridesConfig(
        overrides={"reviewer": {"claude": AgentModelOverrideProfile(effort="high")}},
        override_sources={"reviewer": ConfigSource.config_toml},
    )
    resolution = resolve_agent(agent, "claude", _DEFAULT_TIER_TABLE, overrides)

    assert resolution.effort.effective_layer is EffortLayer.agent_override
    assert resolution.effort.effective == "high"
    assert resolution.effort.harness_block == "low"
    assert resolution.effort.agent_override is not None
    assert resolution.effort.agent_override.value == "high"
    assert resolution.effort.agent_override.source is ConfigSource.config_toml


# ── Source provenance: local-over-shared ─────────────────────────────────────


def test_tier_override_source_reflects_local_win() -> None:
    """A [model_tiers] entry that won the local-over-shared merge reports config.local.toml."""
    agent = _PARSER.parse(_SONNET_AGENT_MD)
    tier_table = build_effective_tier_table(
        {"sonnet": {"claude": "claude-local-id"}}, {"sonnet": ConfigSource.config_local_toml}
    )
    resolution = resolve_agent(agent, "claude", tier_table, _NO_OVERRIDES)

    assert resolution.model.tier_override is not None
    assert resolution.model.tier_override.source is ConfigSource.config_local_toml


def test_agent_override_source_reflects_local_win() -> None:
    """An [agent_model_overrides] entry that won the local-over-shared merge reports config.local.toml."""
    agent = _PARSER.parse(_SONNET_AGENT_MD)
    overrides = AgentModelOverridesConfig(
        overrides={"reviewer": "haiku"},
        override_sources={"reviewer": ConfigSource.config_local_toml},
    )
    resolution = resolve_agent(agent, "claude", _DEFAULT_TIER_TABLE, overrides)

    assert resolution.model.agent_override is not None
    assert resolution.model.agent_override.source is ConfigSource.config_local_toml


def test_effort_agent_override_source_reflects_local_win() -> None:
    """An effort-carrying [agent_model_overrides] entry from config.local.toml reports its own source."""
    agent = _PARSER.parse(_SONNET_AGENT_MD)
    overrides = AgentModelOverridesConfig(
        overrides={"reviewer": {"claude": AgentModelOverrideProfile(effort="high")}},
        override_sources={"reviewer": ConfigSource.config_local_toml},
    )
    resolution = resolve_agent(agent, "claude", _DEFAULT_TIER_TABLE, overrides)

    assert resolution.effort.agent_override is not None
    assert resolution.effort.agent_override.source is ConfigSource.config_local_toml


# ── Full resolution shape ────────────────────────────────────────────────────


def test_resolution_carries_every_layer_with_nulls_for_layers_that_do_not_apply() -> None:
    """A tier remap plus a per-vendor profile override records every layer; the unset harness blocks are None."""
    agent = _PARSER.parse(_SONNET_AGENT_MD.replace("name: reviewer", "name: ice-carver"))
    tier_table = build_effective_tier_table(
        {"sonnet": {"opencode": "openai/gpt-6-luna"}}, {"sonnet": ConfigSource.config_toml}
    )
    overrides = AgentModelOverridesConfig(
        overrides={"ice-carver": {"opencode": AgentModelOverrideProfile(model="openai/gpt-6-luna", effort="max")}},
        override_sources={"ice-carver": ConfigSource.config_toml},
    )

    resolution = resolve_agent(agent, "opencode", tier_table, overrides)

    assert resolution == AgentResolution(
        model=ModelResolution(
            declared_tier="sonnet",
            code_default="anthropic/claude-sonnet-5",
            tier_override=SourcedValue(value="openai/gpt-6-luna", source=ConfigSource.config_toml),
            harness_block=None,
            agent_override=AgentModelOverrideValue(
                value="openai/gpt-6-luna", tier=None, source=ConfigSource.config_toml
            ),
            effective="openai/gpt-6-luna",
            effective_layer=ModelLayer.agent_override,
        ),
        effort=EffortResolution(
            harness_block=None,
            agent_override=SourcedValue(value="max", source=ConfigSource.config_toml),
            effective="max",
            effective_layer=EffortLayer.agent_override,
        ),
    )


def test_effort_only_profile_leaves_model_layers_to_fall_through() -> None:
    """An effort-only profile contributes no model layer — the model resolves from the lower layers."""
    agent = _PARSER.parse(_SONNET_AGENT_MD)
    overrides = AgentModelOverridesConfig(
        overrides={"reviewer": {"codex": AgentModelOverrideProfile(effort="high")}},
    )

    resolution = resolve_agent(agent, "codex", _DEFAULT_TIER_TABLE, overrides)

    assert resolution.model.agent_override is None
    assert resolution.model.effective_layer is ModelLayer.code_default
    assert resolution.model.effective == MODEL_TIER_IDS[(ModelTier.sonnet, "codex")]
    assert resolution.effort.effective_layer is EffortLayer.agent_override
    assert resolution.effort.effective == "high"
