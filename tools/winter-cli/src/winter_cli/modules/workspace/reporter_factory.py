from __future__ import annotations

from typing import TYPE_CHECKING

from winter_cli.modules.workspace.fetch_reporter import FetchReporter
from winter_cli.modules.workspace.init_reporter import InitReporter
from winter_cli.modules.workspace.pull_reporter import PullReporter

if TYPE_CHECKING:
    from winter_cli.container import Container


class ReporterFactory:
    """Selects the right reporter implementation at runtime based on caller arguments.

    Holds a reference to the DI container so it can resolve reporter providers on
    demand — handlers depend on this factory rather than a fixed set of reporters,
    keeping the choice (stream vs. JSON, etc.) close to where it's actually made.
    """

    def __init__(self, container: "Container") -> None:
        self._container = container

    def get_init_reporter(self, output_json: bool) -> InitReporter:
        if output_json:
            return self._container.json_reporter()
        return self._container.stream_reporter()

    def get_fetch_reporter(self, output_json: bool) -> FetchReporter:
        if output_json:
            return self._container.json_fetch_reporter()
        return self._container.stream_fetch_reporter()

    def get_pull_reporter(self, output_json: bool) -> PullReporter:
        if output_json:
            return self._container.json_pull_reporter()
        return self._container.stream_pull_reporter()
