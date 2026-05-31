from __future__ import annotations

import json
from typing import Any, Protocol

from winter_cli.modules.graph.models import ModuleNode


class IGraphReporter(Protocol):
    """Sink for a built dependency graph — rendered in a single call."""

    def render(self, nodes: list[ModuleNode]) -> None: ...


class StreamGraphReporter:
    """Renders the graph as a human-readable `module → dep, dep` listing."""

    def __init__(self, click: Any) -> None:
        self._click = click

    def render(self, nodes: list[ModuleNode]) -> None:
        if not nodes:
            self._click.echo("no modules with a winter-ext.toml found")
            return
        for node in sorted(nodes, key=lambda n: n.name):
            deps = ", ".join(node.requires) if node.requires else "—"
            self._click.echo(f"{node.name} → {deps}")


class JsonGraphReporter:
    """Emits the graph as a single JSON adjacency map `{module: [requires...]}`.

    The stable machine contract behind `winter graph --json`: a plain object
    keyed by module name, each value the ordered `requires` list. Lint scripts
    read this (via `$WINTER_CLI graph --json`) instead of re-parsing manifests.
    Keys are sorted for deterministic output.
    """

    def __init__(self, click: Any) -> None:
        self._click = click

    def render(self, nodes: list[ModuleNode]) -> None:
        payload = {node.name: list(node.requires) for node in sorted(nodes, key=lambda n: n.name)}
        self._click.echo(json.dumps(payload))
