"""Shared renderer and parser singletons for the agent transform layer.

Both ``ExtensionAgentService`` (the installer) and ``AgentProbeService`` (the
staleness probe) import from this module so they always use IDENTICAL renderer
instances.  That structural guarantee means "stale" in the probe is defined
exactly as "what the next ``winter ws init`` run would overwrite" — no
independent copies that could silently diverge.
"""

from __future__ import annotations

from winter_cli.modules.workspace.agent_transform.canonical_parser import CanonicalAgentParser
from winter_cli.modules.workspace.agent_transform.models import AgentFormat
from winter_cli.modules.workspace.agent_transform.renderers import (
    ClaudeAgentRenderer,
    CodexAgentRenderer,
    IAgentRenderer,
    OpenCodeAgentRenderer,
)

# Single authoritative renderer registry — indexed by AgentFormat so callers
# can look up the right renderer from a CodeAgentVendor's agent_format attribute.
RENDERERS: dict[AgentFormat, IAgentRenderer] = {
    AgentFormat.claude_md: ClaudeAgentRenderer(),
    AgentFormat.codex_toml: CodexAgentRenderer(),
    AgentFormat.opencode_md: OpenCodeAgentRenderer(),
}

# Shared stateless parser.  CanonicalAgentParser.parse is side-effect-free so
# a single instance is safe to call from any context.
PARSER: CanonicalAgentParser = CanonicalAgentParser()


def renderer_for(fmt: AgentFormat) -> IAgentRenderer:
    """Return the canonical renderer for ``fmt``."""
    return RENDERERS[fmt]
