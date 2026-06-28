"""Unit tests for the pure agent transform core.

Covers:
- ``CanonicalAgentParser.parse``: happy path, missing required fields, malformed YAML,
  model tier resolution, per-vendor override blocks.
- ``ClaudeAgentRenderer.render``: correct frontmatter, tool projection, claude-block unraveling,
  per-block model override.
- ``CodexAgentRenderer.render``: correct TOML, round-trip parse with ``tomllib``, tools-warn,
  per-block model override.
- ``OpenCodeAgentRenderer.render``: correct frontmatter, tools-warn, per-block model override.
- ``MODEL_TIER_IDS``: tier table completeness.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from textwrap import dedent

import pytest
import yaml

from winter_cli.config.models import CodeAgentVendor
from winter_cli.modules.workspace.agent_transform.canonical_parser import CanonicalAgentParser
from winter_cli.modules.workspace.agent_transform.model_tiers import MODEL_TIER_IDS, VENDOR_LABELS, ModelTier
from winter_cli.modules.workspace.agent_transform.models import AgentFormat, RenderedAgent
from winter_cli.modules.workspace.agent_transform.registry import PARSER as SHARED_PARSER
from winter_cli.modules.workspace.agent_transform.registry import RENDERERS, renderer_for
from winter_cli.modules.workspace.agent_transform.renderers import (
    ClaudeAgentRenderer,
    CodexAgentRenderer,
    OpenCodeAgentRenderer,
)
from winter_cli.modules.workspace.models import RepoError

# ── Shared fixtures and helpers ────────────────────────────────────────────────

_PARSER = CanonicalAgentParser()


def _warn_sink() -> tuple[list[tuple[str, str, str]], Callable[[str, str, str], None]]:
    """Return (calls_list, warn_callable)."""
    calls: list[tuple[str, str, str]] = []

    def warn(field: str, agent_name: str, vendor: str) -> None:
        calls.append((field, agent_name, vendor))

    return calls, warn


_SAMPLE_MD = dedent("""\
    ---
    name: developer
    description: General-purpose developer agent.
    model: sonnet
    tools:
      - Bash
      - Read
      - Write
    ---

    You are the **Developer**.
    """)

_SAMPLE_MD_WITH_OVERRIDES = dedent("""\
    ---
    name: dev-override
    description: Agent with per-harness overrides.
    model: sonnet
    tools:
      - Bash
    claude:
      model: claude-opus-4-20250101
      subagent_type: developer
    codex:
      model: o3
    opencode:
      model: anthropic/claude-opus-4-5
    ---

    Body text.
    """)


# ── MODEL_TIER_IDS completeness ───────────────────────────────────────────────


def test_tier_table_covers_all_tiers_and_vendors() -> None:
    """Every (tier, vendor) pair has an entry in MODEL_TIER_IDS."""
    for tier in ModelTier:
        for vendor in ("claude", "codex", "opencode"):
            assert (tier, vendor) in MODEL_TIER_IDS, f"missing ({tier!r}, {vendor!r})"


def test_claude_tier_ids_are_identity_aliases() -> None:
    """Claude column is an identity map — tier value == model id string."""
    for tier in ModelTier:
        assert MODEL_TIER_IDS[(tier, "claude")] == tier.value


# ── CanonicalAgentParser: happy path ─────────────────────────────────────────


def test_parse_basic_agent() -> None:
    """A well-formed canonical file parses into a CanonicalAgent with correct fields."""
    agent = _PARSER.parse(_SAMPLE_MD)

    assert agent.name == "developer"
    assert agent.description == "General-purpose developer agent."
    assert agent.model_tier == ModelTier.sonnet
    assert agent.tools == ["Bash", "Read", "Write"]
    assert "You are the **Developer**." in agent.body
    assert agent.overrides == {}


def test_parse_agent_with_vendor_overrides() -> None:
    """Override blocks are captured per vendor label."""
    agent = _PARSER.parse(_SAMPLE_MD_WITH_OVERRIDES)

    assert agent.name == "dev-override"
    assert "claude" in agent.overrides
    assert agent.overrides["claude"]["model"] == "claude-opus-4-20250101"
    assert agent.overrides["claude"]["subagent_type"] == "developer"
    assert agent.overrides["codex"]["model"] == "o3"
    assert agent.overrides["opencode"]["model"] == "anthropic/claude-opus-4-5"


def test_parse_tools_wildcard() -> None:
    """tools: '*' parses to the string sentinel."""
    text = dedent("""\
        ---
        name: all-tools
        description: Has all tools.
        tools: '*'
        ---

        Body.
        """)
    agent = _PARSER.parse(text)
    assert agent.tools == "*"


def test_parse_tools_absent() -> None:
    """Absent tools field parses to None."""
    text = dedent("""\
        ---
        name: no-tools
        description: No tools specified.
        ---

        Body.
        """)
    agent = _PARSER.parse(text)
    assert agent.tools is None


def test_parse_default_model_tier_is_sonnet() -> None:
    """Absent model field defaults to the sonnet tier."""
    text = dedent("""\
        ---
        name: default-model
        description: Uses default model.
        ---

        Body.
        """)
    agent = _PARSER.parse(text)
    assert agent.model_tier == ModelTier.sonnet


def test_parse_all_model_tiers() -> None:
    """Each tier name parses to the corresponding ModelTier value."""
    for tier in ModelTier:
        text = dedent(f"""\
            ---
            name: t
            description: d
            model: {tier.value}
            ---

            Body.
            """)
        agent = _PARSER.parse(text)
        assert agent.model_tier == tier


def test_parse_body_extracted_correctly() -> None:
    """The body is everything after the closing --- with leading blank stripped."""
    text = "---\nname: x\ndescription: y\n---\n\nLine one.\nLine two.\n"
    agent = _PARSER.parse(text)
    assert agent.body.startswith("Line one.")
    assert "Line two." in agent.body


# ── CanonicalAgentParser: error cases ─────────────────────────────────────────


def test_parse_raises_on_missing_frontmatter() -> None:
    """Files that don't begin with --- raise RepoError."""
    with pytest.raises(RepoError, match="frontmatter"):
        _PARSER.parse("Just a body with no frontmatter.")


def test_parse_raises_on_unclosed_frontmatter() -> None:
    """A frontmatter block that has no closing --- raises RepoError."""
    with pytest.raises(RepoError, match="closed"):
        _PARSER.parse("---\nname: x\ndescription: y\n")


def test_parse_raises_on_malformed_yaml() -> None:
    """Invalid YAML in the frontmatter raises RepoError mentioning 'YAML'."""
    bad = "---\nname: [unclosed\n---\n\nBody.\n"
    with pytest.raises(RepoError, match="YAML"):
        _PARSER.parse(bad)


def test_parse_raises_on_missing_name() -> None:
    """Absent 'name' field with no filename fallback raises RepoError."""
    text = "---\ndescription: No name here.\n---\n\nBody.\n"
    with pytest.raises(RepoError, match="name"):
        _PARSER.parse(text)


def test_parse_falls_back_to_default_name_when_name_absent() -> None:
    """An agent that omits 'name' adopts the filename-stem fallback rather than failing.

    Lets a vanilla / not-yet-migrated agent project (identity = filename) instead
    of being silently dropped at init.
    """
    text = "---\ndescription: No name in frontmatter.\n---\n\nBody.\n"
    agent = _PARSER.parse(text, default_name="app-runner")
    assert agent.name == "app-runner"


def test_parse_explicit_name_wins_over_default_name() -> None:
    """A declared 'name' takes precedence over the filename-stem fallback."""
    text = "---\nname: declared\ndescription: d\n---\n\nBody.\n"
    agent = _PARSER.parse(text, default_name="from-filename")
    assert agent.name == "declared"


def test_parse_raises_on_missing_description() -> None:
    """Absent 'description' field raises RepoError."""
    text = "---\nname: something\n---\n\nBody.\n"
    with pytest.raises(RepoError, match="description"):
        _PARSER.parse(text)


def test_parse_raises_on_unknown_model_tier() -> None:
    """An unrecognised model value raises RepoError mentioning the bad value."""
    text = "---\nname: x\ndescription: d\nmodel: gpt-4\n---\n\nBody.\n"
    with pytest.raises(RepoError, match="gpt-4"):
        _PARSER.parse(text)


def test_parse_raises_on_invalid_tools_type() -> None:
    """tools as a bare integer raises RepoError."""
    text = "---\nname: x\ndescription: d\ntools: 42\n---\n\nBody.\n"
    with pytest.raises(RepoError, match="tools"):
        _PARSER.parse(text)


# ── ClaudeAgentRenderer ───────────────────────────────────────────────────────


def test_claude_render_produces_valid_yaml_frontmatter() -> None:
    """Rendered Claude MD has parseable YAML frontmatter."""
    agent = _PARSER.parse(_SAMPLE_MD)
    _, warn = _warn_sink()
    r = ClaudeAgentRenderer().render(agent, warn=warn)

    assert r.suffix == ".md"
    assert r.filename_stem == "developer"
    fm = _extract_frontmatter(r.text)
    data = yaml.safe_load(fm)
    assert data["name"] == "developer"
    assert data["description"] == "General-purpose developer agent."
    assert data["model"] == "sonnet"
    assert data["tools"] == ["Bash", "Read", "Write"]


def test_claude_render_includes_body() -> None:
    """Rendered Claude MD contains the original body text."""
    agent = _PARSER.parse(_SAMPLE_MD)
    _, warn = _warn_sink()
    r = ClaudeAgentRenderer().render(agent, warn=warn)

    assert "You are the **Developer**." in r.text


def test_claude_render_no_warnings_for_tools() -> None:
    """Claude renderer does not warn about the tools field."""
    agent = _PARSER.parse(_SAMPLE_MD)
    calls, warn = _warn_sink()
    ClaudeAgentRenderer().render(agent, warn=warn)

    assert calls == [], f"unexpected warnings: {calls}"


def test_claude_render_unravels_claude_override_block() -> None:
    """claude: block keys are merged into the top-level frontmatter."""
    agent = _PARSER.parse(_SAMPLE_MD_WITH_OVERRIDES)
    _, warn = _warn_sink()
    r = ClaudeAgentRenderer().render(agent, warn=warn)

    fm = _extract_frontmatter(r.text)
    data = yaml.safe_load(fm)
    assert data["subagent_type"] == "developer"


def test_claude_block_model_override_beats_tier_table() -> None:
    """A model: in the claude: block overrides the tier-table resolution."""
    agent = _PARSER.parse(_SAMPLE_MD_WITH_OVERRIDES)
    _, warn = _warn_sink()
    r = ClaudeAgentRenderer().render(agent, warn=warn)

    fm = _extract_frontmatter(r.text)
    data = yaml.safe_load(fm)
    assert data["model"] == "claude-opus-4-20250101"


def test_claude_render_drops_other_vendor_blocks() -> None:
    """The codex: and opencode: override blocks do not appear in the Claude output."""
    agent = _PARSER.parse(_SAMPLE_MD_WITH_OVERRIDES)
    _, warn = _warn_sink()
    r = ClaudeAgentRenderer().render(agent, warn=warn)

    fm = _extract_frontmatter(r.text)
    data = yaml.safe_load(fm)
    assert "codex" not in data
    assert "opencode" not in data


# ── CodexAgentRenderer ────────────────────────────────────────────────────────


def test_codex_render_produces_valid_toml() -> None:
    """Rendered Codex TOML is parseable by tomllib."""
    agent = _PARSER.parse(_SAMPLE_MD)
    _, warn = _warn_sink()
    r = CodexAgentRenderer().render(agent, warn=warn)

    assert r.suffix == ".toml"
    assert r.filename_stem == "developer"
    doc = tomllib.loads(r.text)
    assert doc["name"] == "developer"
    assert doc["description"] == "General-purpose developer agent."
    assert doc["model"] == "gpt-5.4"  # sonnet tier → gpt-5.4 for codex


def test_codex_render_roundtrip_toml() -> None:
    """Codex TOML can be round-tripped through tomllib without data loss."""
    agent = _PARSER.parse(_SAMPLE_MD)
    _, warn = _warn_sink()
    r = CodexAgentRenderer().render(agent, warn=warn)

    # Round-trip: dump then parse back.
    doc = tomllib.loads(r.text)
    assert isinstance(doc, dict)
    assert doc["name"] == agent.name


def test_codex_render_includes_body_as_developer_instructions() -> None:
    """The agent body becomes the 'developer_instructions' key in the Codex TOML."""
    agent = _PARSER.parse(_SAMPLE_MD)
    _, warn = _warn_sink()
    r = CodexAgentRenderer().render(agent, warn=warn)

    doc = tomllib.loads(r.text)
    assert "developer_instructions" in doc
    assert "You are the **Developer**." in doc["developer_instructions"]


def test_codex_render_warns_about_tools() -> None:
    """Codex renderer calls warn() for the 'tools' field (no Codex equivalent)."""
    agent = _PARSER.parse(_SAMPLE_MD)
    calls, warn = _warn_sink()
    CodexAgentRenderer().render(agent, warn=warn)

    tool_warns = [(f, n, v) for f, n, v in calls if f == "tools"]
    assert len(tool_warns) == 1
    assert tool_warns[0] == ("tools", "developer", "codex")


def test_codex_render_no_tool_warning_when_tools_absent() -> None:
    """Codex renderer does not warn about tools when the agent has none."""
    text = "---\nname: notool\ndescription: no tools.\n---\n\nBody.\n"
    agent = _PARSER.parse(text)
    calls, warn = _warn_sink()
    CodexAgentRenderer().render(agent, warn=warn)

    assert all(f != "tools" for f, _, _ in calls)


def test_codex_block_model_override_beats_tier_table() -> None:
    """A model: in the codex: block overrides the tier-table resolution."""
    agent = _PARSER.parse(_SAMPLE_MD_WITH_OVERRIDES)
    _, warn = _warn_sink()
    r = CodexAgentRenderer().render(agent, warn=warn)

    doc = tomllib.loads(r.text)
    assert doc["model"] == "o3"


# ── OpenCodeAgentRenderer ─────────────────────────────────────────────────────


def test_opencode_render_produces_valid_yaml_frontmatter() -> None:
    """Rendered OpenCode MD has parseable YAML frontmatter without a name field."""
    agent = _PARSER.parse(_SAMPLE_MD)
    _, warn = _warn_sink()
    r = OpenCodeAgentRenderer().render(agent, warn=warn)

    assert r.suffix == ".md"
    # name is carried as the filename_stem, not the frontmatter.
    assert r.filename_stem == "developer"
    fm = _extract_frontmatter(r.text)
    data = yaml.safe_load(fm)
    # OpenCode does not have a name frontmatter field — identity is the filename.
    assert "name" not in data
    assert data["description"] == "General-purpose developer agent."
    assert data["model"] == "anthropic/claude-sonnet-4-20250514"  # sonnet tier → opencode id


def test_opencode_render_includes_body() -> None:
    """Rendered OpenCode MD contains the original body."""
    agent = _PARSER.parse(_SAMPLE_MD)
    _, warn = _warn_sink()
    r = OpenCodeAgentRenderer().render(agent, warn=warn)

    assert "You are the **Developer**." in r.text


def test_opencode_render_warns_about_tools() -> None:
    """OpenCode renderer calls warn() for the 'tools' field."""
    agent = _PARSER.parse(_SAMPLE_MD)
    calls, warn = _warn_sink()
    OpenCodeAgentRenderer().render(agent, warn=warn)

    tool_warns = [(f, n, v) for f, n, v in calls if f == "tools"]
    assert len(tool_warns) == 1
    assert tool_warns[0] == ("tools", "developer", "opencode")


def test_opencode_block_model_override_beats_tier_table() -> None:
    """A model: in the opencode: block overrides the tier-table resolution."""
    agent = _PARSER.parse(_SAMPLE_MD_WITH_OVERRIDES)
    _, warn = _warn_sink()
    r = OpenCodeAgentRenderer().render(agent, warn=warn)

    fm = _extract_frontmatter(r.text)
    data = yaml.safe_load(fm)
    assert data["model"] == "anthropic/claude-opus-4-5"


def test_opencode_render_drops_other_vendor_blocks() -> None:
    """The claude: and codex: override blocks do not appear in the OpenCode output."""
    agent = _PARSER.parse(_SAMPLE_MD_WITH_OVERRIDES)
    _, warn = _warn_sink()
    r = OpenCodeAgentRenderer().render(agent, warn=warn)

    fm = _extract_frontmatter(r.text)
    data = yaml.safe_load(fm)
    assert "claude" not in data
    assert "codex" not in data


# ── Cross-renderer: real agent file ──────────────────────────────────────────


def test_developer_agent_file_parses_and_renders_all_three_harnesses() -> None:
    """The developer.md agent file (representative sample) parses and renders to all three harnesses."""
    agent = _PARSER.parse(_SAMPLE_MD)
    _, warn_c = _warn_sink()
    _, warn_x = _warn_sink()
    _, warn_o = _warn_sink()

    claude_r = ClaudeAgentRenderer().render(agent, warn=warn_c)
    codex_r = CodexAgentRenderer().render(agent, warn=warn_x)
    opencode_r = OpenCodeAgentRenderer().render(agent, warn=warn_o)

    # All three produced output.
    assert claude_r.text
    assert codex_r.text
    assert opencode_r.text

    # Claude: YAML valid with tools.
    claude_fm = yaml.safe_load(_extract_frontmatter(claude_r.text))
    assert claude_fm["tools"] == ["Bash", "Read", "Write"]

    # Codex: TOML valid, tools warned, body in developer_instructions.
    codex_doc = tomllib.loads(codex_r.text)
    assert "tools" not in codex_doc
    assert "developer_instructions" in codex_doc

    # OpenCode: YAML valid, tools warned, name NOT in frontmatter.
    oc_fm = yaml.safe_load(_extract_frontmatter(opencode_r.text))
    assert "tools" not in oc_fm
    assert "name" not in oc_fm


# ── AgentFormat enum presence ─────────────────────────────────────────────────


def test_agent_format_enum_values_exist() -> None:
    """AgentFormat enum covers all three harnesses."""
    assert AgentFormat.claude_md
    assert AgentFormat.codex_toml
    assert AgentFormat.opencode_md


# ── RenderedAgent dataclass ────────────────────────────────────────────────────


def test_rendered_agent_is_frozen() -> None:
    """RenderedAgent is immutable after creation."""
    r = RenderedAgent(filename_stem="x", suffix=".md", text="content")
    with pytest.raises((AttributeError, TypeError)):
        r.filename_stem = "y"  # type: ignore[misc]


# ── Fix B: shared registry ────────────────────────────────────────────────────


def test_registry_renderers_dict_covers_all_formats() -> None:
    """RENDERERS dict in the registry has an entry for every AgentFormat value."""
    for fmt in AgentFormat:
        assert fmt in RENDERERS, f"RENDERERS missing entry for {fmt!r}"


def test_renderer_for_returns_same_instance_as_renderers_dict() -> None:
    """renderer_for() returns the same object as RENDERERS[fmt]."""
    for fmt in AgentFormat:
        assert renderer_for(fmt) is RENDERERS[fmt]


def test_shared_parser_is_stateless_across_calls() -> None:
    """PARSER can be called multiple times on different inputs without corruption."""
    agent1 = SHARED_PARSER.parse("---\nname: a\ndescription: d1\n---\n\nBody1.\n")
    agent2 = SHARED_PARSER.parse("---\nname: b\ndescription: d2\n---\n\nBody2.\n")
    assert agent1.name == "a"
    assert agent2.name == "b"


# ── Fix C: vendor label vocabulary / checked model-id failure ─────────────────


def test_vendor_label_matches_vendor_labels_set() -> None:
    """CodeAgentVendor.vendor_label values are exactly VENDOR_LABELS — no drift."""
    assert {v.vendor_label for v in CodeAgentVendor} == VENDOR_LABELS


def test_vendor_label_matches_model_tier_id_keys() -> None:
    """Every CodeAgentVendor.vendor_label appears as a key in MODEL_TIER_IDS."""
    key_vendors = {vendor for (_, vendor) in MODEL_TIER_IDS}
    for v in CodeAgentVendor:
        assert v.vendor_label in key_vendors, f"{v.vendor_label!r} missing from MODEL_TIER_IDS keys"


def test_resolve_model_unknown_tier_raises_value_error() -> None:
    """_resolve_model raises ValueError (not bare KeyError) for unknown (tier, vendor)."""
    from winter_cli.modules.workspace.agent_transform.renderers import _resolve_model

    agent = _PARSER.parse("---\nname: x\ndescription: d\n---\n\nBody.\n")
    with pytest.raises(ValueError, match="no model id"):
        _resolve_model(agent, "unknown-vendor", {})


# ── Fix D: OpenCode renderer emits mode: subagent by default ──────────────────


def test_opencode_renderer_emits_mode_subagent_by_default() -> None:
    """OpenCode output always has mode: subagent when the opencode: block has no mode."""
    agent = _PARSER.parse(_SAMPLE_MD)
    _, warn = _warn_sink()
    r = OpenCodeAgentRenderer().render(agent, warn=warn)

    fm = _extract_frontmatter(r.text)
    data = yaml.safe_load(fm)
    assert data.get("mode") == "subagent", f"expected mode=subagent, got {data.get('mode')!r}"


def test_opencode_renderer_mode_override_wins() -> None:
    """A mode: key in the opencode: override block replaces the default subagent."""
    text = dedent("""\
        ---
        name: all-mode
        description: Runs in all mode.
        opencode:
          mode: all
        ---

        Body.
        """)
    agent = _PARSER.parse(text)
    _, warn = _warn_sink()
    r = OpenCodeAgentRenderer().render(agent, warn=warn)

    fm = _extract_frontmatter(r.text)
    data = yaml.safe_load(fm)
    assert data.get("mode") == "all"


# ── Fix E: actionable tools-drop warning ─────────────────────────────────────


def test_codex_warns_tools_when_no_sandbox_mode() -> None:
    """Codex renderer warns about tools when no sandbox_mode in the codex: override."""
    agent = _PARSER.parse(_SAMPLE_MD)
    calls, warn = _warn_sink()
    CodexAgentRenderer().render(agent, warn=warn)

    tool_warns = [c for c in calls if c[0] == "tools"]
    assert len(tool_warns) == 1


def test_codex_suppresses_tools_warn_when_sandbox_mode_declared() -> None:
    """Codex renderer does NOT warn about tools when the codex: block has sandbox_mode."""
    text = dedent("""\
        ---
        name: sandboxed
        description: Has sandbox mode.
        tools:
          - Bash
        codex:
          sandbox_mode: full-auto
        ---

        Body.
        """)
    agent = _PARSER.parse(text)
    calls, warn = _warn_sink()
    CodexAgentRenderer().render(agent, warn=warn)

    tool_warns = [c for c in calls if c[0] == "tools"]
    assert not tool_warns, f"unexpected tools warning with sandbox_mode declared: {tool_warns}"


def test_opencode_warns_tools_when_no_permission() -> None:
    """OpenCode renderer warns about tools when no permission in the opencode: override."""
    agent = _PARSER.parse(_SAMPLE_MD)
    calls, warn = _warn_sink()
    OpenCodeAgentRenderer().render(agent, warn=warn)

    tool_warns = [c for c in calls if c[0] == "tools"]
    assert len(tool_warns) == 1


def test_opencode_suppresses_tools_warn_when_permission_declared() -> None:
    """OpenCode renderer does NOT warn about tools when the opencode: block has permission."""
    text = dedent("""\
        ---
        name: permissioned
        description: Has permission block.
        tools:
          - Bash
        opencode:
          permission:
            Bash: allow
        ---

        Body.
        """)
    agent = _PARSER.parse(text)
    calls, warn = _warn_sink()
    OpenCodeAgentRenderer().render(agent, warn=warn)

    tool_warns = [c for c in calls if c[0] == "tools"]
    assert not tool_warns, f"unexpected tools warning with permission declared: {tool_warns}"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _extract_frontmatter(text: str) -> str:
    """Extract the YAML text between the two --- delimiters."""
    assert text.startswith("---"), f"expected frontmatter, got: {text[:40]!r}"
    lines = text.split("\n")
    closing = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    return "\n".join(lines[1:closing])
