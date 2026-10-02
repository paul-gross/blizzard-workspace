"""Fixture: TUI thread workers that do not open a session root as their whole body.

`refresh_without_a_root` and `refresh_with_work_outside_the_root` are decorated workers that break
the rule; `raw_thread_without_a_root` is the target of a raw thread and breaks it too. The
compliant worker and the async worker are fine.
"""

from __future__ import annotations

import threading
from typing import Any

import textual
from textual import work


class SomeScreen:
    def __init__(self, snapshot_svc: Any, session_tracer: Any, runner: Any) -> None:
        self._snapshot_svc = snapshot_svc
        self._session_tracer = session_tracer
        self._runner = runner

    def _call_from_thread_safe(self, callback: Any) -> None:
        callback()

    def _on_refresh_start(self) -> None:
        pass

    @work(thread=True)
    def refresh_without_a_root(self) -> None:
        self._snapshot_svc.collect_for_dashboard()

    @textual.work(thread=True, exclusive=True)
    def refresh_with_work_outside_the_root(self) -> None:
        self._call_from_thread_safe(self._on_refresh_start)
        with self._session_tracer.session_root("dashboard refresh"):
            self._snapshot_svc.collect_for_dashboard()

    @work(thread=True)
    def compliant(self) -> None:
        """A docstring may precede the root."""
        with self._session_tracer.session_root("dashboard refresh"):
            self._snapshot_svc.collect_for_dashboard()

    @work
    async def async_worker(self) -> None:
        await self._snapshot_svc.collect_for_dashboard()

    def start_raw_thread(self) -> None:
        threading.Thread(target=self.raw_thread_without_a_root, daemon=True).start()

    def raw_thread_without_a_root(self) -> None:
        self._runner.run()
