"""Per-harness renderers that project a ``CanonicalAgent`` into native artifacts.

Each renderer implements ``IAgentRenderer``: given a ``CanonicalAgent``, an
injected ``warn`` callable, and a precomputed ``AgentResolution`` it produces a
``RenderedAgent`` (filename stem, suffix, text).  The three concrete renderers
handle Claude Code (MD + YAML frontmatter), Codex (TOML), and OpenCode
(MD + YAML frontmatter). Renderers never resolve a model or effort themselves —
``resolve_agent`` is the single resolution path, shared by the renderers, the
installer, and the doctor copy inspector.

Lossy projection rule: any common-layer field the renderer has no mapping for is
*dropped* and surfaced via ``warn(field, agent_name, vendor_label)`` rather than
silently omitted.  The caller wires ``warn`` to ``logger.warning``.

``resolve_agent_model_override`` resolves just the ``[agent_model_overrides]``
layer, independent of a specific agent's own frontmatter. ``resolve_agent``
builds its override layer by calling it, so a consumer that displays the
override table on its own — including entries that name no installed agent —
reads the exact values the renderers project.

Conformance sentinels at the bottom of this module typecheck every adapter
against ``IAgentRenderer`` without coupling the Protocol module to its adapters.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

import tomlkit
import yaml

from winter_cli.config.models import AgentModelOverridesConfig
from winter_cli.modules.workspace.agent_transform.model_tiers import EffectiveTierTable
from winter_cli.modules.workspace.agent_transform.models import (
    AgentFieldMap,
    AgentModelOverrideProfile,
    AgentModelOverrideValue,
    AgentResolution,
    CanonicalAgent,
    EffortLayer,
    EffortResolution,
    ModelLayer,
    ModelResolution,
    RenderedAgent,
    SourcedValue,
    WorkspaceModelOverride,
)
from winter_cli.modules.workspace.models import RepoError

# The frontmatter/override-block key that carries the native reasoning-effort
# setting for each vendor. Claude and OpenCode use their own vocabulary;
# Codex's is the published subagent schema key.
_EFFORT_KEYS: dict[str, str] = {
    "claude": "effort",
    "codex": "model_reasoning_effort",
    "opencode": "reasoningEffort",
}

# Common-layer fields subject to per-renderer lossiness checking.
# ``name`` is excluded: every renderer always uses it as ``filename_stem`` and
# never needs to warn about dropping it — the identity is preserved in the
# artifact filename regardless of whether the vendor frontmatter format
# includes a ``name`` key (Claude/Codex do; OpenCode does not).
_COMMON_FIELDS: frozenset[str] = frozenset({"description", "model", "tools"})


class IAgentRenderer(Protocol):
    """Renders a ``CanonicalAgent`` into a vendor-native artifact.

    ``warn(field, agent_name, vendor_label)`` is injected by the caller and
    invoked for every common-layer field the renderer cannot project.  Callers
    typically wire it to ``logger.warning`` so losses are observable without
    halting the build.

    ``resolution`` is this ``(agent, vendor)`` pair's precomputed
    ``AgentResolution`` — the effective model and effort, plus every
    contributing layer. Callers compute it via ``resolve_agent`` before
    invoking ``render``; the renderer only projects ``resolution.model.effective``
    and ``resolution.effort.effective``, it never resolves on its own.
    """

    def render(
        self,
        agent: CanonicalAgent,
        *,
        warn: Callable[[str, str, str], None],
        resolution: AgentResolution,
    ) -> RenderedAgent: ...


# ── Shared resolver ─────────────────────────────────────────────────────────


def resolve_agent(
    agent: CanonicalAgent,
    vendor_label: str,
    tier_table: EffectiveTierTable,
    agent_model_overrides: AgentModelOverridesConfig,
) -> AgentResolution:
    """Return the fully-instrumented model and effort resolution for ``(agent, vendor_label)``.

    The single resolution path shared by the three renderers, the installer
    (``ExtensionAgentService``), and the doctor copy inspector, so "what model
    and effort does this agent run on this harness" is answered identically
    everywhere. Raises ``RepoError`` (not ``ConfigError``) when the agent's own
    ``model_tier`` label, or an ``[agent_model_overrides]`` tier-label entry,
    cannot be resolved against ``tier_table`` — these failures root in the
    agent's own frontmatter or a workspace tier definition resolved against
    it, not in malformed ``.winter/config.toml``, the same vocabulary
    ``CanonicalAgentParser`` already uses for frontmatter problems in this
    pipeline.
    """
    override_block = agent.overrides.get(vendor_label, {})
    model_override, effort_override = resolve_agent_model_override(
        agent_model_overrides, agent.name, vendor_label, tier_table
    )
    model = _resolve_model_layers(
        agent,
        vendor_label,
        override_block,
        tier_table=tier_table,
        agent_override=model_override,
    )
    effort = _resolve_effort_layers(vendor_label, override_block, agent_override=effort_override)
    return AgentResolution(model=model, effort=effort)


def resolve_agent_model_override(
    agent_model_overrides: AgentModelOverridesConfig,
    agent_name: str,
    vendor_label: str,
    tier_table: EffectiveTierTable,
) -> tuple[AgentModelOverrideValue | None, SourcedValue | None]:
    """Return the ``[agent_model_overrides]`` model/effort layer for one
    ``(agent_name, vendor_label)`` pair, independent of any agent's own frontmatter.

    The only producer of the agent-override layer: ``resolve_agent`` calls it
    and blends the result with an agent's own tier/harness-block layers, which
    requires a parsed ``CanonicalAgent``. Calling it directly serves a consumer
    that displays the ``[agent_model_overrides]`` table on its own — including
    entries that name no installed agent, so there is no ``CanonicalAgent`` to
    resolve against.

    Both return values are ``None`` when no override is configured for
    ``agent_name``, or when a per-vendor entry does not list ``vendor_label``.
    Raises ``RepoError`` when a bare-string entry names a tier label
    ``tier_table`` does not know, or one with no mapping for ``vendor_label``.
    """
    workspace_override = _workspace_override_for(agent_model_overrides, agent_name, vendor_label)
    model = _resolve_agent_override_model_layer(workspace_override, vendor_label, tier_table)
    effort: SourcedValue | None = None
    if workspace_override is not None and workspace_override.effort is not None:
        effort = SourcedValue(value=workspace_override.effort, source=workspace_override.source)
    return model, effort


def _workspace_override_for(
    agent_model_overrides: AgentModelOverridesConfig,
    agent_name: str,
    vendor_label: str,
) -> WorkspaceModelOverride | None:
    """Return the workspace-level model override for ``(agent_name, vendor_label)``.

    Returns a ``WorkspaceModelOverride`` when an override is configured, or
    ``None`` when no workspace override applies. A string value applies to all
    vendors as a tier label (``is_concrete=False``, ``tier`` set to that label).
    A per-vendor string is a concrete model id; a profile carries its optional
    concrete model and effort. Missing profile models deliberately return
    ``value=None`` so model resolution falls through while effort still
    projects.
    """
    entry = agent_model_overrides.overrides.get(agent_name)
    if entry is None:
        return None
    source = agent_model_overrides.source_for(agent_name)
    if isinstance(entry, str):
        return WorkspaceModelOverride(value=entry, is_concrete=False, source=source, tier=entry)
    vendor_value = entry.get(vendor_label)
    if vendor_value is None:
        return None
    if isinstance(vendor_value, AgentModelOverrideProfile):
        return WorkspaceModelOverride(
            value=vendor_value.model, is_concrete=True, source=source, effort=vendor_value.effort
        )
    return WorkspaceModelOverride(value=vendor_value, is_concrete=True, source=source)


def _resolve_agent_override_model_layer(
    workspace_override: WorkspaceModelOverride | None,
    vendor_label: str,
    tier_table: EffectiveTierTable,
) -> AgentModelOverrideValue | None:
    """Return the ``[agent_model_overrides]`` model layer, resolving a bare tier label.

    Raises ``RepoError`` when a bare-string override names a tier label that
    ``tier_table`` does not know, or that has no mapping for ``vendor_label``.
    """
    if workspace_override is None:
        return None
    if workspace_override.is_concrete:
        # Per-vendor inline-table entry — always a concrete model id. An
        # effort-only profile has no model at this layer.
        if workspace_override.value is None:
            return None
        return AgentModelOverrideValue(value=workspace_override.value, tier=None, source=workspace_override.source)
    # Bare-string entry — a tier label, resolve to concrete id.
    label = workspace_override.value or ""
    if label not in tier_table:
        valid = ", ".join(repr(t) for t in sorted(tier_table.labels()))
        raise RepoError(f"unknown model tier {label!r} in [agent_model_overrides]; valid tier labels: {valid}")
    resolved = tier_table.model_id(label, vendor_label)
    if resolved is None:
        raise RepoError(
            f"model tier {label!r} has no mapping for vendor {vendor_label!r}; "
            f"add a {vendor_label!r} entry under [model_tiers.{label}]"
        )
    return AgentModelOverrideValue(value=resolved, tier=workspace_override.tier, source=workspace_override.source)


def _resolve_model_layers(
    agent: CanonicalAgent,
    vendor_label: str,
    override_block: Mapping[str, object],
    *,
    tier_table: EffectiveTierTable,
    agent_override: AgentModelOverrideValue | None,
) -> ModelResolution:
    """Return the full ``ModelResolution`` for ``(agent, vendor_label)``.

    Precedence (highest to lowest): ``[agent_model_overrides]`` (the
    ``agent_override`` layer ``resolve_agent_model_override`` produced) >
    per-harness override block's ``model`` key > the agent's own
    ``model_tier`` label resolved via ``tier_table``.

    Raises ``RepoError`` when the agent's ``model_tier`` label is not present
    in ``tier_table``, or has no mapping for ``vendor_label`` — see
    ``resolve_agent``.
    """
    declared_tier = agent.model_tier
    tier_cell = tier_table.cell(declared_tier, vendor_label)
    code_default = tier_cell.code_default if tier_cell is not None else None
    tier_override = tier_cell.tier_override if tier_cell is not None else None

    harness_value = override_block.get("model")
    harness_block = harness_value if isinstance(harness_value, str) else None

    if agent_override is not None:
        effective, effective_layer = agent_override.value, ModelLayer.agent_override
    elif harness_block is not None:
        effective, effective_layer = harness_block, ModelLayer.harness_block
    elif declared_tier not in tier_table:
        valid = ", ".join(repr(t) for t in sorted(tier_table.labels()))
        raise RepoError(f"agent {agent.name!r}: unknown model tier {declared_tier!r}; valid tier labels: {valid}")
    else:
        resolved = tier_table.model_id(declared_tier, vendor_label)
        if resolved is None:
            raise RepoError(
                f"agent {agent.name!r}: model tier {declared_tier!r} has no mapping for vendor {vendor_label!r}; "
                f"add a {vendor_label!r} entry under [model_tiers.{declared_tier}]"
            )
        assert tier_cell is not None
        effective, effective_layer = resolved, tier_cell.effective_layer

    return ModelResolution(
        declared_tier=declared_tier,
        code_default=code_default,
        tier_override=tier_override,
        harness_block=harness_block,
        agent_override=agent_override,
        effective=effective,
        effective_layer=effective_layer,
    )


def _resolve_effort_layers(
    vendor_label: str,
    override_block: Mapping[str, object],
    *,
    agent_override: SourcedValue | None,
) -> EffortResolution:
    """Return the full ``EffortResolution`` for ``vendor_label``.

    Precedence (highest to lowest): ``[agent_model_overrides]`` profile effort
    > the vendor's native effort key in the agent's override block > inherited
    (no explicit effort — the harness uses its own default).
    """
    native_key = _EFFORT_KEYS[vendor_label]
    harness_value = override_block.get(native_key)
    harness_block = harness_value if isinstance(harness_value, str) else None

    if agent_override is not None:
        effective, effective_layer = agent_override.value, EffortLayer.agent_override
    elif harness_block is not None:
        effective, effective_layer = harness_block, EffortLayer.harness_block
    else:
        effective, effective_layer = None, EffortLayer.inherited

    return EffortResolution(
        harness_block=harness_block,
        agent_override=agent_override,
        effective=effective,
        effective_layer=effective_layer,
    )


def _warn_unknown_common_fields(
    agent: CanonicalAgent,
    field_map: AgentFieldMap,
    vendor_label: str,
    warn: Callable[[str, str, str], None],
    *,
    suppress: frozenset[str] = frozenset(),
) -> None:
    """Invoke ``warn`` for every common-layer field not in ``field_map.common``.

    ``suppress`` lists field names that should be silently skipped even when
    they are not in ``field_map.common``.  Used by Codex and OpenCode renderers
    to suppress the ``tools``-drop warning when the vendor's own override block
    already declares the equivalent access-control key (``sandbox_mode`` for
    Codex, ``permission`` for OpenCode) — in that case the author has explicitly
    handled tool access for this vendor and the warning would be noise.
    """
    for field in _COMMON_FIELDS:
        if field in field_map.common:
            continue
        if field in suppress:
            continue
        # Only warn when the field actually carries a value (skip absent optionals).
        if field == "tools" and agent.tools is None:
            continue
        warn(field, agent.name, vendor_label)


def _emit_yaml_frontmatter(fields: dict) -> str:
    """Serialize ``fields`` as a ``---``-delimited YAML frontmatter block.

    ``sort_keys=False`` preserves the insertion order so name / description /
    model appear before any per-vendor extra keys.
    """
    yaml_text = yaml.dump(
        fields,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )
    return f"---\n{yaml_text}---\n"


# ── Claude Code renderer ──────────────────────────────────────────────────────


class ClaudeAgentRenderer:
    """Renders a ``CanonicalAgent`` to a Claude Code agent ``.md`` file.

    Output is a Markdown file with YAML frontmatter.  The ``claude:`` override
    block is *unraveled* — its key/value pairs are merged into the top-level
    frontmatter, with the override block winning on conflicts.  The body is
    copied verbatim.

    All common-layer fields are known to Claude Code so no common fields
    trigger warnings.  Override block fields, including Claude's native
    ``effort`` key, are passed through as-is.
    """

    VENDOR = "claude"
    SUFFIX = ".md"
    _FIELD_MAP = AgentFieldMap(common=frozenset({"description", "model", "tools"}))

    def render(
        self,
        agent: CanonicalAgent,
        *,
        warn: Callable[[str, str, str], None],
        resolution: AgentResolution,
    ) -> RenderedAgent:
        override = agent.overrides.get(self.VENDOR, {})
        _warn_unknown_common_fields(agent, self._FIELD_MAP, self.VENDOR, warn)

        # Build the frontmatter dict: common fields first, then override extras.
        fields: dict = {"name": agent.name, "description": agent.description, "model": resolution.model.effective}
        if agent.tools is not None:
            fields["tools"] = agent.tools if agent.tools == "*" else list(agent.tools)

        # Unravel the claude: block on top — overrides win, extras are added.
        for key, value in override.items():
            if key == "model":
                # Already resolved into `resolution.model.effective`.
                continue
            fields[key] = value
        if resolution.effort.effective is not None:
            fields["effort"] = resolution.effort.effective

        frontmatter = _emit_yaml_frontmatter(fields)
        body_sep = "\n" if agent.body else ""
        text = frontmatter + body_sep + agent.body
        return RenderedAgent(filename_stem=agent.name, suffix=self.SUFFIX, text=text)


# ── Codex renderer ────────────────────────────────────────────────────────────


class CodexAgentRenderer:
    """Renders a ``CanonicalAgent`` to a Codex subagent ``.toml`` file.

    Codex subagents are TOML documents.  The body becomes the
    ``developer_instructions`` key (verified against
    developers.openai.com/codex/subagents 2026-06).  The ``codex:`` override
    block is merged; its ``model`` key overrides the tier table.

    Non-lossy Codex TOML keys (may be set via the ``codex:`` override block):
    ``name``, ``description``, ``developer_instructions``, ``model``,
    ``model_reasoning_effort``, ``sandbox_mode``, ``nickname_candidates``,
    ``mcp_servers``.  The ``model`` key is optional in Codex and inherits from
    the parent when omitted; we always emit it for explicitness.

    ``tools`` is a Claude-centric field with no direct Codex equivalent — it
    is dropped with a warning when present on the agent.
    """

    VENDOR = "codex"
    SUFFIX = ".toml"
    _FIELD_MAP = AgentFieldMap(common=frozenset({"description", "model"}))

    def render(
        self,
        agent: CanonicalAgent,
        *,
        warn: Callable[[str, str, str], None],
        resolution: AgentResolution,
    ) -> RenderedAgent:
        override = agent.overrides.get(self.VENDOR, {})
        # Suppress the tools-drop warning when the codex: block already declares
        # sandbox_mode — the author has expressed the access-control intent in
        # Codex-native vocabulary.  A surviving tools-drop warning means no
        # harness-native access equivalent was declared.
        suppress = frozenset({"tools"}) if "sandbox_mode" in override else frozenset()
        _warn_unknown_common_fields(agent, self._FIELD_MAP, self.VENDOR, warn, suppress=suppress)

        doc = tomlkit.document()
        doc["name"] = agent.name
        doc["description"] = agent.description
        doc["model"] = resolution.model.effective

        # Merge codex: override block (skip model — already resolved).
        for key, value in override.items():
            if key == "model":
                continue
            doc[key] = value
        if resolution.effort.effective is not None:
            doc["model_reasoning_effort"] = resolution.effort.effective

        # Body maps to the `developer_instructions` key per the Codex subagent schema.
        if agent.body:
            doc["developer_instructions"] = agent.body

        text = tomlkit.dumps(doc)
        return RenderedAgent(filename_stem=agent.name, suffix=self.SUFFIX, text=text)


# ── OpenCode renderer ─────────────────────────────────────────────────────────


class OpenCodeAgentRenderer:
    """Renders a ``CanonicalAgent`` to an OpenCode agent ``.md`` file.

    Output is a Markdown file with YAML frontmatter (verified against
    opencode.ai/docs/agents).  The ``opencode:`` override block is merged into
    the top-level frontmatter, with the override block winning on conflicts.
    The body is copied verbatim as the system prompt.

    Recognized OpenCode frontmatter keys: ``description``, ``mode``
    (primary|subagent|all), ``model``, ``reasoningEffort``, ``temperature``, ``top_p``,
    ``permission``, ``steps``, ``disable``, ``hidden``, ``color``.  The agent
    identity is carried solely by the filename (``filename_stem``); OpenCode
    does NOT have a ``name`` frontmatter field, so the canonical ``name`` is
    used as the output filename only and is never emitted to the frontmatter.

    ``tools`` is a Claude-centric field with no OpenCode equivalent (OpenCode
    uses per-tool ``permission`` keys) — it is dropped with a warning when
    present on the agent.
    """

    VENDOR = "opencode"
    SUFFIX = ".md"
    _FIELD_MAP = AgentFieldMap(common=frozenset({"description", "model"}))

    def render(
        self,
        agent: CanonicalAgent,
        *,
        warn: Callable[[str, str, str], None],
        resolution: AgentResolution,
    ) -> RenderedAgent:
        override = agent.overrides.get(self.VENDOR, {})
        # Suppress the tools-drop warning when the opencode: block already declares
        # permission — the author has expressed the access-control intent in
        # OpenCode-native vocabulary.  A surviving tools-drop warning means no
        # harness-native access equivalent was declared.
        suppress = frozenset({"tools"}) if "permission" in override else frozenset()
        _warn_unknown_common_fields(agent, self._FIELD_MAP, self.VENDOR, warn, suppress=suppress)

        # OpenCode frontmatter carries description, model, and mode; name is NOT
        # a recognized OpenCode field — the agent identity lives in the filename.
        # mode defaults to "subagent" so the artifact is spawnable as a subagent
        # per opencode.ai/docs/agents/; a per-block override wins via the merge loop.
        fields: dict = {"description": agent.description, "model": resolution.model.effective, "mode": "subagent"}

        # Merge the opencode: override block on top (model already resolved).
        for key, value in override.items():
            if key == "model":
                continue
            fields[key] = value
        if resolution.effort.effective is not None:
            fields["reasoningEffort"] = resolution.effort.effective

        frontmatter = _emit_yaml_frontmatter(fields)
        body_sep = "\n" if agent.body else ""
        text = frontmatter + body_sep + agent.body
        return RenderedAgent(filename_stem=agent.name, suffix=self.SUFFIX, text=text)


# ── Conformance sentinels ──────────────────────────────────────────────────────
# One sentinel per Protocol/adapter pair: Pyright rejects the `return x` if an
# adapter drifts from IAgentRenderer (e.g. renamed method, wrong signature).
# See winter-context:/standards/protocol-conformance.md for the full convention.


def _conforms_claude_renderer(x: ClaudeAgentRenderer) -> IAgentRenderer:
    return x


def _conforms_codex_renderer(x: CodexAgentRenderer) -> IAgentRenderer:
    return x


def _conforms_opencode_renderer(x: OpenCodeAgentRenderer) -> IAgentRenderer:
    return x
