"""Run the bare `winter ws init` in-process from the dashboard, silently.

Every reconcile event goes to a collecting reporter instead of stdout, so
nothing writes into the TUI. Which `InitService` a run uses — a fresh,
non-interactive one per run — is the container's wiring, injected as
`init_svc_factory`.
"""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Callable

from winter_cli.modules.workspace.init_reporter import IInitReporter
from winter_cli.modules.workspace.init_service import InitService


@dataclasses.dataclass(frozen=True)
class WsInitResult:
    success: bool
    errors: list[str]


class CollectingInitReporter:
    """An `IInitReporter` that keeps only the failures, for a summary afterwards."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.errors: list[str] = []

    def _add(self, message: str) -> None:
        with self._lock:
            self.errors.append(message)

    def target_started(self, target: str) -> None:
        pass

    def target_completed(self, target: str, success: bool) -> None:
        if not success:
            self._add(f"{target} failed")

    def repo_action(self, repo: str, location: str, action: str, detail: str = "") -> None:
        pass

    def repo_error(self, repo: str, error: str) -> None:
        self._add(f"[{repo}] {error}")

    def cmd_started(self, repo: str, command: str) -> None:
        pass

    def cmd_output_line(self, repo: str, line: str) -> None:
        pass

    def cmd_completed(self, repo: str, command: str, returncode: int) -> None:
        pass


class WorkspaceInitRunner:
    """Runs `InitService.reconcile_workspace` — the bare `winter ws init` — and summarizes it.

    One instance serves the whole dashboard, so its lock is what keeps two runs
    from overlapping: a screen that is closed and reopened mid-run gets a new
    screen instance but this same runner.
    """

    def __init__(self, init_svc_factory: Callable[[], InitService]) -> None:
        self._init_svc_factory = init_svc_factory
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._lock.locked()

    def run(self) -> WsInitResult:
        if not self._lock.acquire(blocking=False):
            return WsInitResult(success=False, errors=["winter ws init is already running"])
        try:
            reporter = CollectingInitReporter()
            success = self._init_svc_factory().reconcile_workspace(reporter)
            return WsInitResult(success=success, errors=reporter.errors)
        finally:
            self._lock.release()


def _conforms_collecting_reporter(x: CollectingInitReporter) -> IInitReporter:
    return x
