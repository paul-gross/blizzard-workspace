"""Provision handler spans through the real OpenTelemetry adapter and the real subprocess runner.

The handler process must see the handler span in `TRACEPARENT`, and nothing a handler prints or
a launch failure says may reach the export.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from opentelemetry import trace
from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status

from tests.conftest import FakeFilesystem, FakeSubprocessRunner
from tests.otlp_receiver import OtlpReceiver
from winter_cli.config.models import AdoptExtensions, WorkspaceConfig
from winter_cli.core.internal.local_subprocess_runner import LocalSubprocessRunner
from winter_cli.core.internal.otel_command_tracer import OtelCommandTracer
from winter_cli.core.tracing import TracingSettings
from winter_cli.modules.provision.execution_service import ProvisionExecutionService
from winter_cli.modules.provision.manifest import ProvisionAction, ProvisionHandler, ProvisionScope
from winter_cli.modules.workspace.extension_manifest import ExtensionManifestLoader
from winter_cli.modules.workspace.repository_factory import RepositoryFactory

ENV_NAME = "alpha"


@pytest.fixture(autouse=True)
def isolated_otel_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test with no inherited trace context and no registered global provider."""
    monkeypatch.delenv("TRACEPARENT", raising=False)
    monkeypatch.delenv("TRACESTATE", raising=False)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace._TRACER_PROVIDER_SET_ONCE, "_done", False)


class _Sink:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.errors: list[str] = []

    def execution_started(self, label: str, action: str, cwd: Path) -> None:
        pass

    def execution_output_line(self, label: str, line: str) -> None:
        self.lines.append(line)

    def execution_completed(self, label: str, action: str, exit_code: int) -> None:
        pass

    def execution_error(self, label: str, error: str) -> None:
        self.errors.append(error)


class _LaunchFailingRunner(FakeSubprocessRunner):
    def popen(self, cmd, **kwargs):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("cannot spawn secret-launch-text")


def _service(tmp_path: Path, tracer: OtelCommandTracer, runner: object) -> ProvisionExecutionService:
    config = WorkspaceConfig(
        workspace_root=tmp_path,
        service_prefix="t",
        main_branch="main",
        adopt_extensions=AdoptExtensions.winter,
    )
    return ProvisionExecutionService(
        config=config,
        fs=FakeFilesystem(),
        subprocess_runner=runner,  # type: ignore[arg-type]
        manifest_loader=ExtensionManifestLoader(config_file_reader=None),  # type: ignore[arg-type]
        repo_factory=RepositoryFactory(config=config),
        tracer=tracer,
    )


def _handler(command: str) -> ProvisionHandler:
    return ProvisionHandler(subtarget="dependency", scope=ProvisionScope.workspace, apply=(command,), source="project")


def _handler_span(request_spans: list[Span]) -> Span:
    (span,) = [span for span in request_spans if span.name == "provision handler apply"]
    return span


def test_a_handler_printing_traceparent_shows_the_handler_span_id(tmp_path: Path, otlp_receiver: OtlpReceiver) -> None:
    tracer = OtelCommandTracer(TracingSettings(otlp_endpoint=otlp_receiver.endpoint))
    service = _service(tmp_path, tracer, LocalSubprocessRunner(trace_propagator=tracer))
    sink = _Sink()

    tracer.start_command("winter provision")
    result = service.run_handler(_handler('echo "$TRACEPARENT"'), ProvisionAction.apply, ENV_NAME, sink)
    tracer.end_command(None)
    tracer.export()

    assert result.ok
    (request,) = otlp_receiver.requests
    command = next(span for span in request.spans if span.name == "winter provision")
    span = _handler_span(request.spans)
    assert span.parent_span_id == command.span_id
    assert span.trace_id == command.trace_id
    (printed,) = sink.lines
    _version, trace_id, span_id, _flags = printed.split("-")
    assert span_id == span.span_id.hex() != command.span_id.hex()
    assert trace_id == span.trace_id.hex()


def test_a_failing_handler_exports_error_status_and_none_of_its_output(
    tmp_path: Path, otlp_receiver: OtlpReceiver
) -> None:
    tracer = OtelCommandTracer(TracingSettings(otlp_endpoint=otlp_receiver.endpoint))
    service = _service(tmp_path, tracer, LocalSubprocessRunner(trace_propagator=tracer))

    tracer.start_command("winter provision")
    result = service.run_handler(
        _handler("echo secret-handler-output; echo secret-stderr-output >&2; exit 3"),
        ProvisionAction.apply,
        ENV_NAME,
        _Sink(),
    )
    tracer.end_command(None)
    tracer.export()

    assert not result.ok
    (request,) = otlp_receiver.requests
    span = _handler_span(request.spans)
    assert span.status.code == Status.STATUS_CODE_ERROR
    assert span.status.message == ""
    assert {attribute.key: attribute.value for attribute in span.attributes}["winter.exit_code"].int_value == 3
    for text in (b"secret-handler-output", b"secret-stderr-output"):
        assert text not in request.body


def test_a_launch_oserror_exports_its_class_name_as_error_type_and_none_of_the_message(
    tmp_path: Path, otlp_receiver: OtlpReceiver
) -> None:
    tracer = OtelCommandTracer(TracingSettings(otlp_endpoint=otlp_receiver.endpoint))
    service = _service(tmp_path, tracer, _LaunchFailingRunner())
    sink = _Sink()

    tracer.start_command("winter provision")
    result = service.run_handler(_handler("true"), ProvisionAction.apply, ENV_NAME, sink)
    tracer.end_command(None)
    tracer.export()

    assert result.error is not None and "secret-launch-text" in result.error
    (request,) = otlp_receiver.requests
    span = _handler_span(request.spans)
    assert span.status.code == Status.STATUS_CODE_ERROR
    assert span.status.message == ""
    assert {attribute.key: attribute.value.string_value for attribute in span.attributes}[
        "error.type"
    ] == "FileNotFoundError"
    assert list(span.events) == []
    assert b"secret-launch-text" not in request.body
