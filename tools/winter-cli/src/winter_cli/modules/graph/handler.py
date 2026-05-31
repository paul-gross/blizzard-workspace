from __future__ import annotations

import dataclasses

from winter_cli.modules.graph.graph_reporter import IGraphReporter, JsonGraphReporter, StreamGraphReporter
from winter_cli.modules.graph.graph_service import GraphService


@dataclasses.dataclass
class GraphParams:
    output_json: bool


class GraphHandler:
    """Dispatches `winter graph` runs: build the graph, render with the reporter."""

    def __init__(
        self,
        graph_service: GraphService,
        stream_reporter: StreamGraphReporter,
        json_reporter: JsonGraphReporter,
    ) -> None:
        self._graph_service = graph_service
        self._stream_reporter = stream_reporter
        self._json_reporter = json_reporter

    def run(self, params: GraphParams) -> None:
        nodes = self._graph_service.build()
        reporter: IGraphReporter = self._json_reporter if params.output_json else self._stream_reporter
        reporter.render(nodes)
