"""The dashboard's tracing, driven through a real Textual pilot and the real OpenTelemetry adapter.

A refresh runs in a Textual thread worker, which starts with no span context; the worker opens a
`dashboard <purpose>` root, and everything the refresh does parents on it. The receiver is a
localhost stdlib server, and the workspace is a temporary one, so the live workspace is untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dependency_injector import providers
from opentelemetry import trace

from tests.otlp_receiver import OtlpReceiver
from winter_cli.container import Container
from winter_cli.core.tracing import TracingSettings
from winter_cli.modules.tui.app import WinterDashboardApp
from winter_cli.modules.tui.screens.workspace import WorkspaceScreen

_SESSION = "winter dashboard"
_REFRESH_ROOT = "dashboard refresh workspace"


@pytest.fixture(autouse=True)
def isolated_otel_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test with no inherited trace context and no registered global provider."""
    monkeypatch.delenv("TRACEPARENT", raising=False)
    monkeypatch.delenv("TRACESTATE", raising=False)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace._TRACER_PROVIDER_SET_ONCE, "_done", False)


@pytest.mark.asyncio
async def test_a_pilot_refresh_of_the_workspace_screen_is_a_dashboard_root_holding_git_status_spans(
    container: Container, tmp_workspace_root: Path, otlp_receiver: OtlpReceiver
) -> None:
    (tmp_workspace_root / "alpha" / "demo-repo").mkdir(parents=True)
    container.tracing_settings.override(providers.Object(TracingSettings(otlp_endpoint=otlp_receiver.endpoint)))
    tracer = container.command_tracer()
    tracer.start_command(_SESSION)
    try:
        app = WinterDashboardApp(container)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.5)
            screen = app.screen
            assert isinstance(screen, WorkspaceScreen)
            screen.action_refresh()
            await app.workers.wait_for_complete()
            await pilot.pause(0.2)
    finally:
        tracer.end_command(None)
        tracer.export()

    spans = [span for request in otlp_receiver.requests for span in request.spans]
    session = next(span for span in spans if span.name == _SESSION)
    roots = [span for span in spans if span.name == _REFRESH_ROOT]
    assert len(roots) >= 2  # the mount refresh and the one the pilot asked for
    for root in roots:
        assert root.parent_span_id == b""
        assert root.trace_id != session.trace_id
        assert [(link.trace_id, link.span_id) for link in root.links] == [(session.trace_id, session.span_id)]
    git_status = [span for span in spans if span.name == "git status"]
    assert git_status
    root_traces = {root.span_id: root.trace_id for root in roots}
    ids_by_trace = {(span.trace_id, span.span_id) for span in spans}
    for span in git_status:
        # Every git span belongs to a refresh root's trace, nested on the root or on another span in it.
        assert span.trace_id in root_traces.values()
        assert (span.trace_id, span.parent_span_id) in ids_by_trace
    assert any(span.parent_span_id in root_traces for span in git_status)
