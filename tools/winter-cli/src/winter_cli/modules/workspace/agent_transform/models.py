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


@dataclasses.dataclass(frozen=True)
class CanonicalAgent:
    """Parsed canonical agent — harness-neutral representation.

    ``model_tier`` is the tier label string from the agent's ``model:`` field
    (e.g. ``"sonnet"``, ``"haiku"``, ``"opus"``, or a workspace-defined custom
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

    ``is_concrete`` distinguishes the two override forms so a renderer never
    has to guess by string-matching against the tier table:

    - Bare-string form (``reviewer = "haiku"``) — ``is_concrete=False``;
      ``value`` is a tier label resolved against the effective tier table.
    - Per-vendor inline-table form (``coder = { opencode = "haiku" }``) —
      ``is_concrete=True``; ``value`` is a concrete model id passed through
      verbatim even when it happens to collide with a tier label string.
    """

    value: str
    is_concrete: bool


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
