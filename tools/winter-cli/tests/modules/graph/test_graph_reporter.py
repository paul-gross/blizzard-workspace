from __future__ import annotations

import json

from winter_cli.modules.graph.graph_reporter import JsonGraphReporter, StreamGraphReporter
from winter_cli.modules.graph.models import ModuleNode


class FakeClick:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def echo(self, message: str = "", err: bool = False) -> None:
        self.lines.append(message)

    def style(self, text: str, **kwargs: object) -> str:
        return text


def test_json_emits_sorted_adjacency_map() -> None:
    click = FakeClick()
    JsonGraphReporter(click).render([ModuleNode("winter-b", ("x",)), ModuleNode("winter-a", ())])
    assert json.loads(click.lines[0]) == {"winter-a": [], "winter-b": ["x"]}


def test_stream_lists_modules_and_deps() -> None:
    click = FakeClick()
    StreamGraphReporter(click).render([ModuleNode("winter-a", ("x", "y"))])
    assert click.lines == ["winter-a → x, y"]


def test_stream_reports_no_modules() -> None:
    click = FakeClick()
    StreamGraphReporter(click).render([])
    assert "no modules" in click.lines[0]
