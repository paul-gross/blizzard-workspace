from __future__ import annotations

from collections.abc import Callable

from winter_cli.modules.workspace.fetch_reporter import IFetchReporter
from winter_cli.modules.workspace.init_reporter import IInitReporter
from winter_cli.modules.workspace.pull_reporter import IPullReporter


class ReporterFactory:
    """Selects the right reporter implementation at runtime based on caller arguments.

    Takes six provider callables — one per (channel, format) combination —
    so handlers can ask for the right reporter without seeing the DI container.
    The dependency arrow stays explicit: ReporterFactory depends on the
    Reporter Protocols, never on the container itself.
    """

    def __init__(
        self,
        stream_init_reporter: Callable[[], IInitReporter],
        json_init_reporter: Callable[[], IInitReporter],
        stream_fetch_reporter: Callable[[], IFetchReporter],
        json_fetch_reporter: Callable[[], IFetchReporter],
        stream_pull_reporter: Callable[[], IPullReporter],
        json_pull_reporter: Callable[[], IPullReporter],
    ) -> None:
        self._stream_init = stream_init_reporter
        self._json_init = json_init_reporter
        self._stream_fetch = stream_fetch_reporter
        self._json_fetch = json_fetch_reporter
        self._stream_pull = stream_pull_reporter
        self._json_pull = json_pull_reporter

    def get_init_reporter(self, output_json: bool) -> IInitReporter:
        return self._json_init() if output_json else self._stream_init()

    def get_fetch_reporter(self, output_json: bool) -> IFetchReporter:
        return self._json_fetch() if output_json else self._stream_fetch()

    def get_pull_reporter(self, output_json: bool) -> IPullReporter:
        return self._json_pull() if output_json else self._stream_pull()
