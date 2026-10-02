from __future__ import annotations

import pytest

from winter_cli.core.internal.noop_command_tracer import NoopCommandTracer


def test_noop_tracer_changes_nothing() -> None:
    tracer = NoopCommandTracer()
    env = {"PATH": "/bin", "TRACEPARENT": "caller"}

    tracer.start_command("winter ws init")
    tracer.annotate_env("alpha")
    tracer.inject(env)
    tracer.end_command(None)
    tracer.export()

    assert env == {"PATH": "/bin", "TRACEPARENT": "caller"}


def test_noop_tracer_module_imports_nothing_from_opentelemetry_and_only_its_protocol_module_from_winter() -> None:
    """The no-op must stay free of OpenTelemetry so a tracing-off process pays nothing for it."""
    import ast
    import inspect

    from winter_cli.core.internal import noop_command_tracer

    tree = ast.parse(inspect.getsource(noop_command_tracer))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not any(module.split(".")[0] == "opentelemetry" for module in imported)
    assert {module for module in imported if module.startswith("winter_cli")} == {"winter_cli.core.tracing"}


def test_noop_operation_runs_its_body_and_hands_back_a_handle_that_records_nothing() -> None:
    tracer = NoopCommandTracer()
    ran: list[str] = []

    with tracer.operation("git status", {"winter.repo": "winter"}) as operation:
        operation.set_attribute("winter.exit_code", 0)
        operation.mark_failed("OSError")
        ran.append("body")

    assert ran == ["body"]


def test_noop_operation_propagates_the_exception_of_its_body_unchanged() -> None:
    tracer = NoopCommandTracer()
    raised = RuntimeError("boom")

    with pytest.raises(RuntimeError) as caught, tracer.operation("git status"):
        raise raised

    assert caught.value is raised


def test_noop_operations_nest() -> None:
    tracer = NoopCommandTracer()

    with tracer.operation("outer"), tracer.operation("inner") as inner:
        inner.set_attribute("winter.repo", "winter")


def test_noop_session_root_runs_its_body_and_propagates_its_exception_unchanged() -> None:
    tracer = NoopCommandTracer()
    ran: list[str] = []
    raised = RuntimeError("boom")

    with tracer.session_root("dashboard refresh workspace") as root:
        root.set_attribute("winter.repo", "winter")
        root.mark_failed("OSError")
        ran.append("body")
    with pytest.raises(RuntimeError) as caught, tracer.session_root("dashboard refresh workspace"):
        raise raised

    assert ran == ["body"]
    assert caught.value is raised


def test_noop_background_export_starts_no_thread() -> None:
    import threading

    before = threading.active_count()

    NoopCommandTracer().start_background_export()

    assert threading.active_count() == before
