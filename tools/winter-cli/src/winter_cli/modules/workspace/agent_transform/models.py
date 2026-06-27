"""Domain types for the agent transform layer.

``CanonicalAgent`` is the parsed, harness-neutral representation of a canonical
agent file. ``AgentFormat`` names the three supported output formats. ``RenderedAgent``
carries one renderer's output (text + filename metadata).
"""

from __future__ import annotations

import dataclasses
import enum

from winter_cli.modules.workspace.agent_transform.model_tiers import ModelTier


class AgentFormat(enum.Enum):
    """Supported output artifact formats."""

    claude_md = "claude_md"
    codex_toml = "codex_toml"
    opencode_md = "opencode_md"


@dataclasses.dataclass(frozen=True)
class CanonicalAgent:
    """Parsed canonical agent — harness-neutral representation.

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
    model_tier: ModelTier
    tools: list[str] | str | None
    body: str
    overrides: dict[str, dict]


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
