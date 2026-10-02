"""`WorkspaceInitRunner` runs the bare `ws init` through a silent reporter and summarizes failures."""

from __future__ import annotations

import sys
import threading
from collections.abc import Mapping
from pathlib import Path

import git
import pytest

from winter_cli.container import Container
from winter_cli.modules.tui.ws_init_runner import WorkspaceInitRunner, WsInitResult
from winter_cli.modules.workspace.init_reporter import IInitReporter
from winter_cli.modules.workspace.init_service import InitService


class FakeInitService:
    def __init__(self, success: bool) -> None:
        self._success = success

    def reconcile_workspace(self, reporter: IInitReporter) -> bool:
        reporter.target_started("projects/")
        reporter.repo_action("winter", "projects/winter", "exists")
        reporter.cmd_output_line("winter", "noise that must not reach the TUI")
        if not self._success:
            reporter.repo_error("winter", "clone failed")
        reporter.target_completed("projects/", self._success)
        return self._success


def test_successful_run_reports_no_errors() -> None:
    runner = WorkspaceInitRunner(lambda: FakeInitService(success=True))  # type: ignore[arg-type,return-value]
    assert runner.run() == WsInitResult(success=True, errors=[])


def test_a_second_run_while_one_is_in_flight_is_refused() -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingInitService:
        def reconcile_workspace(self, reporter: IInitReporter) -> bool:
            started.set()
            release.wait(5)
            return True

    runner = WorkspaceInitRunner(init_svc_factory=lambda: BlockingInitService())  # type: ignore[arg-type,return-value]
    first = threading.Thread(target=runner.run)
    first.start()
    try:
        assert started.wait(5)
        assert runner.is_running
        second = runner.run()
        assert second == WsInitResult(success=False, errors=["winter ws init is already running"])
    finally:
        release.set()
        first.join(5)
    assert not runner.is_running


def test_failed_run_collects_only_the_failures() -> None:
    runner = WorkspaceInitRunner(lambda: FakeInitService(success=False))  # type: ignore[arg-type,return-value]
    assert runner.run() == WsInitResult(success=False, errors=["[winter] clone failed", "projects/ failed"])


def _dashboard_init_service() -> InitService:
    """An `InitService` from the factory the container injects into the dashboard's runner."""
    return Container().ws_init_runner()._init_svc_factory()


def test_container_factory_resolves_a_fresh_init_service_per_run(tmp_workspace_root: Path) -> None:
    first = _dashboard_init_service()
    second = _dashboard_init_service()

    assert isinstance(first, InitService)
    assert first._config is not second._config


def test_fresh_container_binds_the_launching_containers_tracer_instance(tmp_workspace_root: Path) -> None:
    """A process has one tracer: the dashboard's fresh `ws init` Container reuses the launching one's."""
    from dependency_injector import providers

    from winter_cli.core.internal.noop_command_tracer import NoopCommandTracer

    launching = Container()
    launching_tracer = NoopCommandTracer()
    launching.command_tracer.override(providers.Object(launching_tracer))

    factory = launching.ws_init_runner()._init_svc_factory
    first, second = factory.build_container(), factory.build_container()

    assert first is not second
    assert first.command_tracer() is launching_tracer
    assert second.command_tracer() is launching_tracer


def test_fresh_container_does_not_select_its_own_tracer(tmp_workspace_root: Path) -> None:
    """With no override on the launching Container the fresh one still holds the launching instance."""
    launching = Container()

    fresh = launching.ws_init_runner()._init_svc_factory.build_container()

    assert fresh.command_tracer() is launching.command_tracer()


def test_a_child_of_the_fresh_container_gets_the_launching_command_spans_traceparent(
    tmp_workspace_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With tracing on, an init child carries the trace id and span id of the boundary's command span."""
    from dependency_injector import providers
    from opentelemetry import trace

    from winter_cli.core.tracing import TracingSettings

    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace._TRACER_PROVIDER_SET_ONCE, "_done", False)
    monkeypatch.delenv("TRACEPARENT", raising=False)
    launching = Container()
    launching.tracing_settings.override(providers.Object(TracingSettings(otlp_endpoint="http://127.0.0.1:9")))
    tracer = launching.command_tracer()
    tracer.start_command("winter dashboard")
    try:
        context = trace.get_current_span().get_span_context()
        fresh = launching.ws_init_runner()._init_svc_factory.build_container()

        result = fresh.subprocess_runner().run(
            [sys.executable, "-c", "import os; print(os.environ.get('TRACEPARENT', ''))"]
        )
    finally:
        tracer.end_command(None)

    _version, trace_id, span_id, _flags = result.stdout.strip().split("-")
    assert (trace_id, span_id) == (trace.format_trace_id(context.trace_id), trace.format_span_id(context.span_id))


# A child that reports whether it could prompt: stdin at EOF and the git prompt switch it sees.
_PROBE = "import os, sys; print(sys.stdin.read() == '', os.environ.get('GIT_TERMINAL_PROMPT'))"


def test_init_child_processes_see_eof_stdin_and_no_git_prompt(tmp_workspace_root: Path) -> None:
    runner = _dashboard_init_service()._subprocess

    assert runner.run([sys.executable, "-c", _PROBE]).stdout.split() == ["True", "0"]


def test_init_clones_with_the_git_prompt_disabled(tmp_workspace_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Mapping[str, str] | None] = []
    monkeypatch.setattr(git.Repo, "clone_from", lambda url, dest, env=None, **_: seen.append(env))

    _dashboard_init_service()._git_repo.clone("https://example.invalid/r.git", tmp_workspace_root / "r", repo_name="r")

    assert len(seen) == 1
    assert seen[0] is not None
    assert seen[0]["GIT_TERMINAL_PROMPT"] == "0"
