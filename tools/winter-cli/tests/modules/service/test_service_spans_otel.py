"""Service spans through the real OpenTelemetry adapter and the real subprocess runner.

A `down` provider child must see its own cell span in `TRACEPARENT`, an `up` child must see none,
and nothing a provider prints may reach the export.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from opentelemetry import trace
from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status

from tests.otlp_receiver import OtlpReceiver
from winter_cli.core.internal.local_subprocess_runner import LocalSubprocessRunner
from winter_cli.core.internal.otel_command_tracer import OtelCommandTracer
from winter_cli.core.tracing import TracingSettings
from winter_cli.modules.capability.models import CapabilitySlot, ResolvedCapability
from winter_cli.modules.service.service_fan_out_service import FanOutCell, ServiceFanOutService
from winter_cli.modules.service.service_readiness_service import ServiceReadinessService
from winter_cli.modules.service.status_models import EnvStatus, ServiceStatus, StatusDocument


@pytest.fixture(autouse=True)
def isolated_otel_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test with no inherited trace context and no registered global provider."""
    monkeypatch.delenv("TRACEPARENT", raising=False)
    monkeypatch.delenv("TRACESTATE", raising=False)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace._TRACER_PROVIDER_SET_ONCE, "_done", False)


def _provider(tmp_path: Path, script: str) -> ResolvedCapability:
    ext_dir = tmp_path / "provider-a"
    entrypoint = ext_dir / "workflow" / "service"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text(f"#!/bin/sh\n{script}\n")
    entrypoint.chmod(entrypoint.stat().st_mode | stat.S_IXUSR)
    return ResolvedCapability(
        slot=CapabilitySlot.service,
        extension_name="provider-a",
        entrypoint=entrypoint,
        ext_dir=ext_dir,
        prefix="provider-a",
        config_dir=tmp_path / ".winter" / "config" / "provider-a",
    )


def _fan_out(tmp_path: Path, tracer: OtelCommandTracer) -> ServiceFanOutService:
    return ServiceFanOutService(
        subprocess_runner=LocalSubprocessRunner(trace_propagator=tracer),
        workspace_root=tmp_path,
        service_prefix="winter",
        tracer=tracer,
    )


def _span(spans: list[Span], name: str) -> Span:
    (span,) = [span for span in spans if span.name == name]
    return span


def test_a_down_provider_child_sees_its_cell_span_in_traceparent(tmp_path: Path, otlp_receiver: OtlpReceiver) -> None:
    seen = tmp_path / "seen"
    provider = _provider(tmp_path, f'echo "$TRACEPARENT" > {seen}')
    tracer = OtelCommandTracer(TracingSettings(otlp_endpoint=otlp_receiver.endpoint))

    tracer.start_command("winter service down")
    code = _fan_out(tmp_path, tracer).down([FanOutCell(provider=provider, scope="alpha", positional="alpha")])
    tracer.end_command(None)
    tracer.export()

    assert code == 0
    (request,) = otlp_receiver.requests
    command = _span(request.spans, "winter service down")
    cell = _span(request.spans, "service provider down")
    assert cell.parent_span_id == command.span_id
    assert seen.read_text().strip().split("-")[2] == cell.span_id.hex() != command.span_id.hex()
    attributes = {attribute.key: attribute.value.string_value for attribute in cell.attributes}
    assert attributes == {"winter.provider": "provider-a", "winter.scope": "alpha"}


def test_an_up_provider_child_sees_no_traceparent_though_its_cell_is_spanned(
    tmp_path: Path, otlp_receiver: OtlpReceiver
) -> None:
    seen = tmp_path / "seen"
    provider = _provider(tmp_path, f'echo "tp=[$TRACEPARENT]" > {seen}')
    tracer = OtelCommandTracer(TracingSettings(otlp_endpoint=otlp_receiver.endpoint))

    tracer.start_command("winter service up")
    _fan_out(tmp_path, tracer).up([FanOutCell(provider=provider, scope="workspace", positional="workspace")])
    tracer.end_command(None)
    tracer.export()

    assert seen.read_text().strip() == "tp=[]"
    (request,) = otlp_receiver.requests
    cell = _span(request.spans, "service provider up")
    assert {attribute.key: attribute.value.string_value for attribute in cell.attributes}["winter.scope"] == "workspace"


def test_a_failing_provider_exports_error_status_and_none_of_its_output(
    tmp_path: Path, otlp_receiver: OtlpReceiver
) -> None:
    provider = _provider(tmp_path, "echo secret-provider-stdout; echo secret-provider-stderr >&2; exit 4")
    tracer = OtelCommandTracer(TracingSettings(otlp_endpoint=otlp_receiver.endpoint))

    tracer.start_command("winter service down")
    code = _fan_out(tmp_path, tracer).down([FanOutCell(provider=provider, scope="alpha", positional="alpha")])
    tracer.end_command(None)
    tracer.export()

    assert code == 4
    (request,) = otlp_receiver.requests
    cell = _span(request.spans, "service provider down")
    assert cell.status.code == Status.STATUS_CODE_ERROR
    assert cell.status.message == ""
    for text in (b"secret-provider-stdout", b"secret-provider-stderr"):
        assert text not in request.body


class _StatusService:
    def __init__(self, health: str) -> None:
        self._health = health

    def collect(self, patterns: tuple[str, ...]) -> StatusDocument:
        service = ServiceStatus(
            name="api", state="running", health=self._health, ports=(), handle=None, log_path=None, since=None
        )
        return StatusDocument(envs=(EnvStatus(env="alpha", session=None, port_base=None, services=(service,)),))


@pytest.mark.parametrize(("health", "ready", "failed"), [("healthy", True, False), ("unhealthy", False, True)])
def test_readiness_span_exports_the_pattern_count_and_ready_flag(
    otlp_receiver: OtlpReceiver, health: str, ready: bool, failed: bool
) -> None:
    tracer = OtelCommandTracer(TracingSettings(otlp_endpoint=otlp_receiver.endpoint))
    readiness = ServiceReadinessService(
        status_service=_StatusService(health),  # type: ignore[arg-type]
        tracer=tracer,
        sleep=lambda _s: None,
    )

    tracer.start_command("winter service up")
    readiness.wait(("alpha", "beta"), timeout_s=0.0)
    tracer.end_command(None)
    tracer.export()

    (request,) = otlp_receiver.requests
    command = _span(request.spans, "winter service up")
    span = _span(request.spans, "service readiness wait")
    assert span.parent_span_id == command.span_id
    attributes = {attribute.key: attribute.value for attribute in span.attributes}
    assert attributes["winter.service.patterns"].int_value == 2
    assert attributes["winter.ready"].bool_value is ready
    assert (span.status.code == Status.STATUS_CODE_ERROR) is failed
