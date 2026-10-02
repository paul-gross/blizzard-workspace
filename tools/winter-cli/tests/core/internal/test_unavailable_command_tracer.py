from __future__ import annotations

import pytest

from winter_cli.core.internal.unavailable_command_tracer import UnavailableCommandTracer


def test_unavailable_tracer_changes_nothing_and_logs_nothing_until_export(caplog: pytest.LogCaptureFixture) -> None:
    tracer = UnavailableCommandTracer(RuntimeError("sdk import failed"))
    env = {"PATH": "/bin", "TRACEPARENT": "caller"}

    with caplog.at_level("DEBUG"):
        tracer.start_command("winter ws init")
        tracer.annotate_env("alpha")
        tracer.inject(env)
        tracer.end_command(None)

    assert env == {"PATH": "/bin", "TRACEPARENT": "caller"}
    assert caplog.records == []


def test_unavailable_tracer_logs_the_failure_at_debug_on_export(caplog: pytest.LogCaptureFixture) -> None:
    failure = RuntimeError("sdk import failed")

    with caplog.at_level("DEBUG"):
        UnavailableCommandTracer(failure).export()

    assert [record.levelname for record in caplog.records] == ["DEBUG"]
    assert caplog.records[0].exc_info is not None
    assert caplog.records[0].exc_info[1] is failure


def test_unavailable_operation_runs_its_body_and_propagates_its_exception() -> None:
    tracer = UnavailableCommandTracer(RuntimeError("sdk import failed"))
    ran: list[str] = []

    with tracer.operation("git status") as operation:
        operation.set_attribute("winter.repo", "winter")
        operation.mark_failed()
        ran.append("body")
    with pytest.raises(ValueError), tracer.operation("git fetch"):
        raise ValueError("boom")

    assert ran == ["body"]


def test_unavailable_session_root_runs_its_body_and_background_export_does_nothing() -> None:
    import threading

    tracer = UnavailableCommandTracer(RuntimeError("sdk import failed"))
    before = threading.active_count()
    ran: list[str] = []

    tracer.start_background_export()
    with tracer.session_root("dashboard refresh workspace"):
        ran.append("body")
    with pytest.raises(ValueError), tracer.session_root("dashboard refresh workspace"):
        raise ValueError("boom")

    assert ran == ["body"]
    assert threading.active_count() == before
