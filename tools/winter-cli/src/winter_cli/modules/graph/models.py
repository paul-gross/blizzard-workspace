from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleNode:
    """One module in the dependency graph.

    `name` is the module's identity — the `<context>` half of a
    `<context>:/path` reference (e.g. `winter-harness`), which is the standalone
    repo's name. `requires` is its declared dependency list from `winter-ext.toml`.
    The graph is a plain set of nodes-with-edges; it carries no notion of "core"
    or layering — those are rules the consumer (the extractability linter) owns.
    """

    name: str
    requires: tuple[str, ...]
