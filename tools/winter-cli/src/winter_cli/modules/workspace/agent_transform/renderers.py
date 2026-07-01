"""Per-harness renderers that project a ``CanonicalAgent`` into native artifacts.

Each renderer implements ``IAgentRenderer``: given a ``CanonicalAgent`` and an
injected ``warn`` callable it produces a ``RenderedAgent`` (filename stem, suffix,
text).  The three concrete renderers handle Claude Code (MD + YAML frontmatter),
Codex (TOML), and OpenCode (MD + YAML frontmatter).

Lossy projection rule: any common-layer field the renderer has no mapping for is
*dropped* and surfaced via ``warn(field, agent_name, vendor_label)`` rather than
silently omitted.  The caller wires ``warn`` to ``logger.warning``.

Conformance sentinels at the bottom of this module typecheck every adapter
against ``IAgentRenderer`` without coupling the Protocol module to its adapters.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import tomlkit
import yaml

from winter_cli.modules.workspace.agent_transform.model_tiers import build_effective_tier_table
from winter_cli.modules.workspace.agent_transform.models import (
    AgentFieldMap,
    CanonicalAgent,
    RenderedAgent,
    WorkspaceModelOverride,
)
from winter_cli.modules.workspace.models import RepoError

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

    ``workspace_model_override`` is the resolved workspace-level model override
    for this ``(agent, vendor)`` pair — a ``WorkspaceModelOverride`` carrying
    either a tier label (bare string from ``[agent_model_overrides]``,
    validated at config load time) or a concrete model id (from a per-vendor
    inline-table entry, passed through without tier resolution) — or ``None``
    when no workspace override applies.  Callers compute this via
    ``resolve_workspace_model_override`` before invoking ``render``.

    ``effective_tier_table`` is the merged tier table (built-in defaults ⊕
    workspace ``[model_tiers]`` config) as ``{tier_label: {vendor: model_id}}``.
    When ``None`` the renderer falls back to the built-in defaults only.
    """

    def render(
        self,
        agent: CanonicalAgent,
        *,
        warn: Callable[[str, str, str], None],
        workspace_model_override: WorkspaceModelOverride | None = None,
        effective_tier_table: dict[str, dict[str, str]] | None = None,
    ) -> RenderedAgent: ...


# ── Shared helpers ────────────────────────────────────────────────────────────


def resolve_workspace_model_override(
    overrides: dict[str, str | dict[str, str]],
    agent_name: str,
    vendor_label: str,
) -> WorkspaceModelOverride | None:
    """Return the workspace-level model override for ``(agent_name, vendor_label)``.

    Returns a ``WorkspaceModelOverride`` when an override is configured, or
    ``None`` when no workspace override applies. A string value in the map
    applies to all vendors and is returned as a tier label (``is_concrete=False``);
    a dict value selects the entry for ``vendor_label`` and is returned as a
    concrete model id (``is_concrete=True``), or ``None`` when that vendor is
    not listed. The two forms are never conflated, even when a per-vendor
    value happens to collide with a tier label string.

    This function is the single lookup point used by both
    ``ExtensionAgentService`` (the installer) and ``AgentProbeService`` (the
    staleness probe) so the two always agree on the resolved override.
    """
    entry = overrides.get(agent_name)
    if entry is None:
        return None
    if isinstance(entry, str):
        return WorkspaceModelOverride(value=entry, is_concrete=False)
    vendor_value = entry.get(vendor_label)
    if vendor_value is None:
        return None
    return WorkspaceModelOverride(value=vendor_value, is_concrete=True)


def _resolve_model(
    agent: CanonicalAgent,
    vendor_label: str,
    override: dict,
    *,
    tier_table: dict[str, dict[str, str]],
    workspace_override: WorkspaceModelOverride | None = None,
) -> str:
    """Return the resolved model-id string for ``vendor_label``.

    Precedence (highest to lowest):

    1. ``workspace_override``: a tier-label form (``is_concrete=False``) is
       resolved via ``tier_table``; a concrete-id form (``is_concrete=True``,
       from a per-vendor inline-table entry in ``[agent_model_overrides]``) is
       passed through verbatim, even when its value happens to collide with a
       tier label string.  ``None`` skips this layer.
    2. Per-harness override block's ``model`` key (always a concrete id).
    3. Agent's ``model_tier`` label resolved via ``tier_table``.

    Raises ``RepoError`` when:
    - The agent's ``model_tier`` label is not present in ``tier_table``.
    - A tier label has no mapping for ``vendor_label`` in ``tier_table``.

    ``RepoError`` (not ``ConfigError``) because these failures root in the
    agent's own frontmatter or a workspace tier definition resolved against
    it, not in malformed ``.winter/config.toml`` — the same vocabulary
    ``CanonicalAgentParser`` already uses for frontmatter problems in this
    pipeline.
    """
    if workspace_override is not None:
        if workspace_override.is_concrete:
            # Per-vendor inline-table entry — always a concrete model id.
            return workspace_override.value
        # Bare-string entry — a tier label, resolve to concrete id.
        vendor_ids = tier_table.get(workspace_override.value)
        if vendor_ids is None:
            valid = ", ".join(repr(t) for t in sorted(tier_table))
            raise RepoError(
                f"unknown model tier {workspace_override.value!r} in [agent_model_overrides]; "
                f"valid tier labels: {valid}"
            )
        if vendor_label not in vendor_ids:
            raise RepoError(
                f"model tier {workspace_override.value!r} has no mapping for vendor {vendor_label!r}; "
                f"add a {vendor_label!r} entry under [model_tiers.{workspace_override.value}]"
            )
        return vendor_ids[vendor_label]
    if "model" in override and isinstance(override["model"], str):
        return override["model"]
    tier_label = agent.model_tier
    vendor_ids = tier_table.get(tier_label)
    if vendor_ids is None:
        valid = ", ".join(repr(t) for t in sorted(tier_table))
        raise RepoError(f"agent {agent.name!r}: unknown model tier {tier_label!r}; valid tier labels: {valid}")
    if vendor_label not in vendor_ids:
        raise RepoError(
            f"agent {agent.name!r}: model tier {tier_label!r} has no mapping for vendor {vendor_label!r}; "
            f"add a {vendor_label!r} entry under [model_tiers.{tier_label}]"
        )
    return vendor_ids[vendor_label]


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
    trigger warnings.  Override block fields are passed through as-is.
    """

    VENDOR = "claude"
    SUFFIX = ".md"
    _FIELD_MAP = AgentFieldMap(common=frozenset({"description", "model", "tools"}))

    def render(
        self,
        agent: CanonicalAgent,
        *,
        warn: Callable[[str, str, str], None],
        workspace_model_override: WorkspaceModelOverride | None = None,
        effective_tier_table: dict[str, dict[str, str]] | None = None,
    ) -> RenderedAgent:
        tier_table = effective_tier_table if effective_tier_table is not None else build_effective_tier_table({})
        override = agent.overrides.get(self.VENDOR, {})
        _warn_unknown_common_fields(agent, self._FIELD_MAP, self.VENDOR, warn)

        model_id = _resolve_model(
            agent,
            self.VENDOR,
            override,
            tier_table=tier_table,
            workspace_override=workspace_model_override,
        )

        # Build the frontmatter dict: common fields first, then override extras.
        fields: dict = {"name": agent.name, "description": agent.description, "model": model_id}
        if agent.tools is not None:
            fields["tools"] = agent.tools if agent.tools == "*" else list(agent.tools)

        # Unravel the claude: block on top — overrides win, extras are added.
        for key, value in override.items():
            if key == "model":
                # Already resolved into `model_id`.
                continue
            fields[key] = value

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
        workspace_model_override: WorkspaceModelOverride | None = None,
        effective_tier_table: dict[str, dict[str, str]] | None = None,
    ) -> RenderedAgent:
        tier_table = effective_tier_table if effective_tier_table is not None else build_effective_tier_table({})
        override = agent.overrides.get(self.VENDOR, {})
        # Suppress the tools-drop warning when the codex: block already declares
        # sandbox_mode — the author has expressed the access-control intent in
        # Codex-native vocabulary.  A surviving tools-drop warning means no
        # harness-native access equivalent was declared.
        suppress = frozenset({"tools"}) if "sandbox_mode" in override else frozenset()
        _warn_unknown_common_fields(agent, self._FIELD_MAP, self.VENDOR, warn, suppress=suppress)

        model_id = _resolve_model(
            agent,
            self.VENDOR,
            override,
            tier_table=tier_table,
            workspace_override=workspace_model_override,
        )

        doc = tomlkit.document()
        doc["name"] = agent.name
        doc["description"] = agent.description
        doc["model"] = model_id

        # Merge codex: override block (skip model — already resolved).
        for key, value in override.items():
            if key == "model":
                continue
            doc[key] = value

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
    (primary|subagent|all), ``model``, ``temperature``, ``top_p``,
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
        workspace_model_override: WorkspaceModelOverride | None = None,
        effective_tier_table: dict[str, dict[str, str]] | None = None,
    ) -> RenderedAgent:
        tier_table = effective_tier_table if effective_tier_table is not None else build_effective_tier_table({})
        override = agent.overrides.get(self.VENDOR, {})
        # Suppress the tools-drop warning when the opencode: block already declares
        # permission — the author has expressed the access-control intent in
        # OpenCode-native vocabulary.  A surviving tools-drop warning means no
        # harness-native access equivalent was declared.
        suppress = frozenset({"tools"}) if "permission" in override else frozenset()
        _warn_unknown_common_fields(agent, self._FIELD_MAP, self.VENDOR, warn, suppress=suppress)

        model_id = _resolve_model(
            agent,
            self.VENDOR,
            override,
            tier_table=tier_table,
            workspace_override=workspace_model_override,
        )

        # OpenCode frontmatter carries description, model, and mode; name is NOT
        # a recognized OpenCode field — the agent identity lives in the filename.
        # mode defaults to "subagent" so the artifact is spawnable as a subagent
        # per opencode.ai/docs/agents/; a per-block override wins via the merge loop.
        fields: dict = {"description": agent.description, "model": model_id, "mode": "subagent"}

        # Merge the opencode: override block on top (model already resolved).
        for key, value in override.items():
            if key == "model":
                continue
            fields[key] = value

        frontmatter = _emit_yaml_frontmatter(fields)
        body_sep = "\n" if agent.body else ""
        text = frontmatter + body_sep + agent.body
        return RenderedAgent(filename_stem=agent.name, suffix=self.SUFFIX, text=text)


# ── Conformance sentinels ──────────────────────────────────────────────────────
# One sentinel per Protocol/adapter pair: Pyright rejects the `return x` if an
# adapter drifts from IAgentRenderer (e.g. renamed method, wrong signature).
# See winter-harness:/standards/protocol-conformance.md for the full convention.


def _conforms_claude_renderer(x: ClaudeAgentRenderer) -> IAgentRenderer:
    return x


def _conforms_codex_renderer(x: CodexAgentRenderer) -> IAgentRenderer:
    return x


def _conforms_opencode_renderer(x: OpenCodeAgentRenderer) -> IAgentRenderer:
    return x
