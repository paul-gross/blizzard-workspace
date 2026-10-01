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
