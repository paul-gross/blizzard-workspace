"""Domain types for the agent transform layer.

``CanonicalAgent`` is the parsed, harness-neutral representation of a canonical
agent file. ``AgentFormat`` names the three supported output formats. ``RenderedAgent``
carries one renderer's output (text + filename metadata).
"""

from __future__ import annotations

import dataclasses
import enum


class AgentFormat(enum.Enum):
    """Supported output artifact formats."""

    claude_md = "claude_md"
    codex_toml = "codex_toml"
    opencode_md = "opencode_md"


class ConfigSource(enum.Enum):
    """Which workspace config file a merged ``[model_tiers]`` or
    ``[agent_model_overrides]`` entry came from.

    Lives here rather than in ``config/models.py`` so that module, which
    already imports from this one, does not gain a reverse dependency.
    """

    config_toml = "config.toml"
    config_local_toml = "config.local.toml"


class ModelLayer(enum.Enum):
    """The layer that supplied an agent's effective model for one vendor.

    Ordered lowest to highest precedence: ``code_default`` (the built-in
    ``MODEL_TIER_IDS`` mapping) < ``tier_override`` (a workspace
    ``[model_tiers]`` remap) < ``harness_block`` (the agent's own per-harness
    ``model:`` key) < ``agent_override`` (a workspace
    ``[agent_model_overrides]`` entry).
    """

    code_default = "code_default"
    tier_override = "tier_override"
    harness_block = "harness_block"
    agent_override = "agent_override"


class EffortLayer(enum.Enum):
    """The layer that supplied an agent's effective reasoning effort for one vendor.

    ``inherited`` means neither layer set a value — the harness falls back to
    its own native default rather than winter emitting an explicit setting.
    """

    harness_block = "harness_block"
    agent_override = "agent_override"
    inherited = "inherited"


@dataclasses.dataclass(frozen=True)
class SourcedValue:
    """A config-derived value paired with the workspace file it was read from."""

    value: str
    source: ConfigSource


@dataclasses.dataclass(frozen=True)
class AgentModelOverrideValue:
    """The ``[agent_model_overrides]`` layer's contribution to a model resolution.

    ``value`` is always the resolved concrete model id — for the bare-string
    (tier-label) override form that means the id the label resolves to for
    this vendor, not the label itself. ``tier`` records that label for the
    bare-string form and is ``None`` for a concrete-id or profile form.
    """

    value: str
    tier: str | None
    source: ConfigSource


@dataclasses.dataclass(frozen=True)
class ModelResolution:
    """The fully-instrumented outcome of resolving one agent's model for one vendor.

    Every layer field that does not apply to this agent/vendor pair is
    ``None`` rather than omitted, so a consumer never has to guess whether a
    layer was skipped or simply absent. ``effective``/``effective_layer`` name
    the winning layer; the other fields are its full provenance trail.
    """

    declared_tier: str
    code_default: str | None
    tier_override: SourcedValue | None
    harness_block: str | None
    agent_override: AgentModelOverrideValue | None
    effective: str
    effective_layer: ModelLayer


@dataclasses.dataclass(frozen=True)
class EffortResolution:
    """The fully-instrumented outcome of resolving one agent's reasoning effort for one vendor.

    ``effective`` is ``None`` exactly when ``effective_layer`` is
    ``EffortLayer.inherited`` — no explicit effort applies for this agent/vendor.
    """

    harness_block: str | None
    agent_override: SourcedValue | None
    effective: str | None
    effective_layer: EffortLayer


@dataclasses.dataclass(frozen=True)
class AgentResolution:
    """The combined model and effort resolution for one agent x vendor pair.

    Returned by ``agent_transform.renderers.resolve_agent``, the single
    resolution path every renderer, the installer, and the doctor copy
    inspector consume.
    """

    model: ModelResolution
    effort: EffortResolution


@dataclasses.dataclass(frozen=True)
class CanonicalAgent:
    """Parsed canonical agent — harness-neutral representation.

    ``model_tier`` is the tier label string from the agent's ``model:`` field
    (e.g. ``"sonnet"``, ``"haiku"``, ``"opus"``, ``"fable"``, or a workspace-defined custom
    label like ``"big-thinker"``).  Resolution against the effective tier table
    happens at render time, not at parse time, so custom labels are stored as
    plain strings without enum conversion.

    ``tools`` carries the exact value from the frontmatter: a list of Claude
    tool-name strings, the sentinel string ``"*"`` (all tools), or ``None``
    when the field is absent. Renderers that cannot map tool lists warn and
    drop the field.

    ``overrides`` holds per-vendor override blocks keyed by vendor label
    (``"claude"``, ``"codex"``, ``"opencode"``). A renderer merges its own
    block on top of the resolved common fields; the other blocks are dropped.
    """

    name: str
    description: str
    model_tier: str
    tools: list[str] | str | None
    body: str
    overrides: dict[str, dict]


@dataclasses.dataclass(frozen=True)
class WorkspaceModelOverride:
    """A resolved ``[agent_model_overrides]`` value, with its form preserved.

    ``is_concrete`` distinguishes the two override forms so a caller never has
    to guess by string-matching against the tier table:

    - Bare-string form (``reviewer = "haiku"``) — ``is_concrete=False``;
      ``value`` is a tier label resolved against the effective tier table, and
      ``tier`` carries that same label so a resolver can record it without
      re-deriving it.
    - Per-vendor inline-table form (``coder = { opencode = "haiku" }``) —
      ``is_concrete=True``; ``value`` is a concrete model id passed through
      verbatim even when it happens to collide with a tier label string.
    - Profile form (``coder = { opencode = { effort = "high" } }``) —
      ``is_concrete=True``; ``value`` may be absent for effort-only profiles,
      and ``effort`` carries the opaque native reasoning setting.

    ``source`` is the config file the ``[agent_model_overrides]`` entry itself
    was read from, regardless of form.
    """

    value: str | None
    is_concrete: bool
    source: ConfigSource
    tier: str | None = None
    effort: str | None = None


@dataclasses.dataclass(frozen=True)
class AgentModelOverrideProfile:
    """A vendor-specific workspace profile with optional model and effort."""

    model: str | None = None
    effort: str | None = None


@dataclasses.dataclass(frozen=True)
class AgentFieldMap:
    """Common-layer field names that a renderer can project.

    Fields from the agent's own vendor override block are always passed through
    verbatim; only the *common* fields listed here are checked for projectability.
    Any common field the renderer cannot handle is dropped with a warning.
    """

    common: frozenset[str]


@dataclasses.dataclass(frozen=True)
class RenderedAgent:
    """One renderer's output artifact.

    ``filename_stem`` is the bare name without suffix (e.g. ``"developer"``);
    ``suffix`` is the file extension including the dot (e.g. ``".md"``).
    Together they produce ``<filename_stem><suffix>``.
    """

    filename_stem: str
    suffix: str
    text: str
