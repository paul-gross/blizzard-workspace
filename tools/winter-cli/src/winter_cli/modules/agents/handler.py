from __future__ import annotations

import dataclasses

from winter_cli.modules.agents.agent_matrix_service import AgentMatrixService
from winter_cli.modules.agents.matrix_reporter import (
    IAgentMatrixReporter,
    JsonAgentMatrixReporter,
    StreamAgentMatrixReporter,
)


@dataclasses.dataclass
class AgentsParams:
    output_json: bool


class AgentsHandler:
    """Dispatches `winter agents` runs: build the matrix, render with the reporter."""

    def __init__(
        self,
        matrix_svc: AgentMatrixService,
        stream_reporter: StreamAgentMatrixReporter,
        json_reporter: JsonAgentMatrixReporter,
    ) -> None:
        self._matrix_svc = matrix_svc
        self._stream_reporter = stream_reporter
        self._json_reporter = json_reporter

    def run(self, params: AgentsParams) -> None:
        matrix = self._matrix_svc.build()
        reporter: IAgentMatrixReporter = self._json_reporter if params.output_json else self._stream_reporter
        reporter.render(matrix)
