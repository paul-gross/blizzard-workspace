"""`OtelCommandTracer` against real localhost endpoints.

Each test drives the adapter through its Protocol surface and reads what actually arrived at a
stdlib receiver that decodes the OTLP protobuf request.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry.proto.trace.v1.trace_pb2 import Status
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, TraceState

from tests.otlp_receiver import HangingEndpoint, OtlpReceiver
from winter_cli.core.internal.otel_command_tracer import EXPORT_TIMEOUT_SECONDS, OtelCommandTracer
from winter_cli.core.tracing import TracingSettings

_CALLER_TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
_CALLER_SPAN_ID = "b7ad6b7169203331"

_ENV_CONTROLLED_BY_TESTS = (
    "TRACEPARENT",
    "TRACESTATE",
    "OTEL_SERVICE_NAME",
    "OTEL_RESOURCE_ATTRIBUTES",
    "OTEL_TRACES_SAMPLER",
    "OTEL_TRACES_SAMPLER_ARG",
)


@pytest.fixture(autouse=True)
def isolated_otel_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start each test with no inherited OpenTelemetry environment and no registered global provider."""
    for name in _ENV_CONTROLLED_BY_TESTS:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith(("OTEL_EXPORTER_OTLP_", "OTEL_PYTHON_EXPORTER_OTLP_")):
            monkeypatch.delenv(name)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace._TRACER_PROVIDER_SET_ONCE, "_done", False)
    yield


def _tracer(endpoint: str, **settings: object) -> OtelCommandTracer:
    return OtelCommandTracer(TracingSettings(otlp_endpoint=endpoint, **settings))  # type: ignore[arg-type]


def _run(tracer: OtelCommandTracer, command_path: str = "winter ws init", error_type: str | None = None) -> None:
    tracer.start_command(command_path)
    tracer.end_command(error_type)
    tracer.export()


def _only_span(receiver: OtlpReceiver):
    assert len(receiver.requests) == 1
    spans = receiver.requests[0].spans
    assert len(spans) == 1
    return spans[0]


# ── Root span ────────────────────────────────────────────────────────────────


def test_root_span_is_named_by_and_attributed_with_its_command_path(otlp_receiver: OtlpReceiver) -> None:
    _run(_tracer(otlp_receiver.endpoint), "winter service up")

    span = _only_span(otlp_receiver)
    attributes = {attribute.key: attribute.value.string_value for attribute in span.attributes}
    assert span.name == "winter service up"
    assert attributes == {"winter.command": "winter service up"}
    assert otlp_receiver.requests[0].path == "/v1/traces"


def test_endpoint_trailing_slash_does_not_double_the_path(otlp_receiver: OtlpReceiver) -> None:
    _run(_tracer(otlp_receiver.endpoint + "/"))

    assert [request.path for request in otlp_receiver.requests] == ["/v1/traces"]


def test_annotate_env_sets_winter_env_on_the_root_span(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws status")
    tracer.annotate_env("alpha")
    tracer.end_command(None)
    tracer.export()

    attributes = {a.key: a.value.string_value for a in _only_span(otlp_receiver).attributes}
    assert attributes["winter.env"] == "alpha"


def test_provider_is_registered_as_the_global_tracer_provider(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws init")
    try:
        in_process_span = trace.get_tracer("an-extension").start_span("extension work")
        in_process_span.end()
        assert in_process_span.get_span_context().trace_id == trace.get_current_span().get_span_context().trace_id
    finally:
        tracer.end_command(None)
    tracer.export()

    names = {span.name for span in otlp_receiver.requests[0].spans}
    assert names == {"winter ws init", "extension work"}


# ── Exit semantics and content ───────────────────────────────────────────────


def test_successful_command_leaves_status_unset(otlp_receiver: OtlpReceiver) -> None:
    _run(_tracer(otlp_receiver.endpoint), error_type=None)

    span = _only_span(otlp_receiver)
    assert span.status.code == Status.STATUS_CODE_UNSET
    assert [attribute.key for attribute in span.attributes] == ["winter.command"]


def test_failed_command_carries_error_status_and_type_and_nothing_else(otlp_receiver: OtlpReceiver) -> None:
    _run(_tracer(otlp_receiver.endpoint), "winter ws init", error_type="RepoError")

    span = _only_span(otlp_receiver)
    attributes = {attribute.key: attribute.value.string_value for attribute in span.attributes}
    assert span.status.code == Status.STATUS_CODE_ERROR
    assert span.status.message == ""
    assert list(span.events) == []
    assert attributes == {"winter.command": "winter ws init", "error.type": "RepoError"}


# ── Propagation (read) ───────────────────────────────────────────────────────


def test_caller_traceparent_is_the_root_spans_parent(
    otlp_receiver: OtlpReceiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRACEPARENT", f"00-{_CALLER_TRACE_ID}-{_CALLER_SPAN_ID}-01")

    _run(_tracer(otlp_receiver.endpoint))

    span = _only_span(otlp_receiver)
    assert span.trace_id.hex() == _CALLER_TRACE_ID
    assert span.parent_span_id.hex() == _CALLER_SPAN_ID


def test_without_traceparent_the_span_is_a_new_root(otlp_receiver: OtlpReceiver) -> None:
    _run(_tracer(otlp_receiver.endpoint))

    span = _only_span(otlp_receiver)
    assert span.parent_span_id == b""
    assert span.trace_id.hex() != _CALLER_TRACE_ID


def test_unsampled_caller_exports_nothing(otlp_receiver: OtlpReceiver, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACEPARENT", f"00-{_CALLER_TRACE_ID}-{_CALLER_SPAN_ID}-00")

    _run(_tracer(otlp_receiver.endpoint))

    assert otlp_receiver.requests == []


def test_sampling_ignores_the_generic_sampler_setting(
    otlp_receiver: OtlpReceiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_off")

    _run(_tracer(otlp_receiver.endpoint))

    assert len(_only_span_list(otlp_receiver)) == 1


def _only_span_list(receiver: OtlpReceiver) -> list:
    assert len(receiver.requests) == 1
    return receiver.requests[0].spans


# ── Start time ───────────────────────────────────────────────────────────────


def test_span_starts_at_the_launch_time(otlp_receiver: OtlpReceiver) -> None:
    launch_ns = time.time_ns() - 250_000_000

    _run(_tracer(otlp_receiver.endpoint, launch_time_ns=launch_ns))

    assert _only_span(otlp_receiver).start_time_unix_nano == launch_ns


def test_span_starts_at_dispatch_without_a_launch_time(otlp_receiver: OtlpReceiver) -> None:
    before = time.time_ns()

    _run(_tracer(otlp_receiver.endpoint))

    assert before <= _only_span(otlp_receiver).start_time_unix_nano <= time.time_ns()


# ── Resource ─────────────────────────────────────────────────────────────────


def _service_name(receiver: OtlpReceiver) -> str:
    return receiver.requests[0].resource_attributes["service.name"]


def test_service_name_defaults_to_winter(otlp_receiver: OtlpReceiver) -> None:
    _run(_tracer(otlp_receiver.endpoint))

    assert _service_name(otlp_receiver) == "winter"


def test_service_name_in_resource_attributes_wins_over_the_default(
    otlp_receiver: OtlpReceiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "service.name=from-attributes")

    _run(_tracer(otlp_receiver.endpoint))

    assert _service_name(otlp_receiver) == "from-attributes"


def test_otel_service_name_wins_over_both(otlp_receiver: OtlpReceiver, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "service.name=from-attributes")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "from-service-name")

    _run(_tracer(otlp_receiver.endpoint))

    assert _service_name(otlp_receiver) == "from-service-name"


def test_other_resource_attributes_are_honored_alongside_the_default_name(
    otlp_receiver: OtlpReceiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "blizzard.worker=w7,blizzard.step=build")

    _run(_tracer(otlp_receiver.endpoint))

    attributes = otlp_receiver.requests[0].resource_attributes
    assert attributes["blizzard.worker"] == "w7"
    assert attributes["blizzard.step"] == "build"
    assert attributes["service.name"] == "winter"


# ── Generic variables do not reach winter's pipeline ─────────────────────────


def test_winter_headers_arrive_and_generic_headers_do_not(
    otlp_receiver: OtlpReceiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "authorization=Bearer HOSTED-SECRET,x-generic=1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "x-generic-traces=1")

    _run(_tracer(otlp_receiver.endpoint, otlp_headers="x-winter-token=abc123,x-other=two"))

    headers = otlp_receiver.requests[0].headers
    assert headers["x-winter-token"] == "abc123"
    assert headers["x-other"] == "two"
    assert "authorization" not in headers
    assert "x-generic" not in headers
    assert "x-generic-traces" not in headers
    assert "HOSTED-SECRET" not in str(headers)


def test_other_generic_exporter_variables_do_not_change_the_exporter_and_are_restored(
    otlp_receiver: OtlpReceiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    generic = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:1",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:1/v1/traces",
        "OTEL_EXPORTER_OTLP_TIMEOUT": "30",
        "OTEL_EXPORTER_OTLP_TRACES_TIMEOUT": "30",
        "OTEL_EXPORTER_OTLP_COMPRESSION": "gzip",
        "OTEL_EXPORTER_OTLP_TRACES_COMPRESSION": "gzip",
        "OTEL_EXPORTER_OTLP_CERTIFICATE": "/nonexistent/ca.pem",
        "OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE": "/nonexistent/ca.pem",
        "OTEL_EXPORTER_OTLP_CLIENT_KEY": "/nonexistent/client.key",
        "OTEL_EXPORTER_OTLP_TRACES_CLIENT_KEY": "/nonexistent/client.key",
        "OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE": "/nonexistent/client.pem",
        "OTEL_EXPORTER_OTLP_TRACES_CLIENT_CERTIFICATE": "/nonexistent/client.pem",
        "OTEL_PYTHON_EXPORTER_OTLP_HTTP_CREDENTIAL_PROVIDER": "no-such-provider",
        "OTEL_PYTHON_EXPORTER_OTLP_HTTP_TRACES_CREDENTIAL_PROVIDER": "no-such-provider",
    }
    for name, value in generic.items():
        monkeypatch.setenv(name, value)
    before = dict(os.environ)

    tracer = _tracer(otlp_receiver.endpoint)

    assert dict(os.environ) == before
    tracer.start_command("winter ws init")
    tracer.end_command(None)
    tracer.export()
    assert dict(os.environ) == before
    request = otlp_receiver.requests[0]
    assert "content-encoding" not in request.headers
    assert request.path == "/v1/traces"


def test_environment_is_restored_when_exporter_construction_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "authorization=secret")
    monkeypatch.setattr(
        "winter_cli.core.internal.otel_command_tracer.OTLPSpanExporter",
        lambda **_: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError):
        _tracer("http://127.0.0.1:1")

    assert os.environ["OTEL_EXPORTER_OTLP_HEADERS"] == "authorization=secret"


# ── Export ───────────────────────────────────────────────────────────────────


def test_export_sends_one_request_and_nothing_on_a_second_call(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws init")
    tracer.end_command(None)

    tracer.export()
    tracer.export()

    assert len(otlp_receiver.requests) == 1


def test_export_without_collected_spans_sends_no_request(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)

    tracer.export()

    assert otlp_receiver.requests == []


def test_export_against_a_hanging_endpoint_returns_within_the_cap(hanging_endpoint: HangingEndpoint) -> None:
    tracer = _tracer(hanging_endpoint.endpoint)
    tracer.start_command("winter ws init")
    tracer.end_command(None)

    started = time.perf_counter()
    tracer.export()
    elapsed = time.perf_counter() - started

    assert elapsed < EXPORT_TIMEOUT_SECONDS + 0.05


def test_export_against_a_refused_endpoint_returns_without_raising(refused_otlp_endpoint: str) -> None:
    tracer = _tracer(refused_otlp_endpoint)
    tracer.start_command("winter ws init")
    tracer.end_command(None)

    started = time.perf_counter()
    tracer.export()

    assert time.perf_counter() - started < EXPORT_TIMEOUT_SECONDS + 0.05


def test_export_swallows_an_exporter_failure(monkeypatch: pytest.MonkeyPatch, otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws init")
    tracer.end_command(None)

    def explode(spans: object) -> None:
        raise RuntimeError("exporter blew up")

    monkeypatch.setattr(tracer._exporter, "export", explode)

    tracer.export()


# ── Propagation (write) ──────────────────────────────────────────────────────


def test_inject_writes_the_command_spans_traceparent_into_the_env(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws init")
    env = {"PATH": "/bin", "TRACEPARENT": "stale"}
    tracer.inject(env)
    tracer.end_command(None)
    tracer.export()

    span = _only_span(otlp_receiver)
    assert env["TRACEPARENT"].startswith(f"00-{span.trace_id.hex()}-{span.span_id.hex()}-")
    assert env["PATH"] == "/bin"
    assert "traceparent" not in env


def test_inject_from_a_thread_without_span_context_falls_back_to_the_command_span(
    otlp_receiver: OtlpReceiver,
) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws init")
    env: dict[str, str] = {}
    worker = threading.Thread(target=tracer.inject, args=(env,))
    worker.start()
    worker.join()
    tracer.end_command(None)
    tracer.export()

    span = _only_span(otlp_receiver)
    assert env["TRACEPARENT"].startswith(f"00-{span.trace_id.hex()}-{span.span_id.hex()}-")


def test_inject_before_a_command_starts_leaves_the_env_alone(otlp_receiver: OtlpReceiver) -> None:
    env = {"TRACEPARENT": "caller"}

    _tracer(otlp_receiver.endpoint).inject(env)

    assert env == {"TRACEPARENT": "caller"}


def test_inject_removes_an_inherited_tracestate_when_the_span_context_carries_none(
    otlp_receiver: OtlpReceiver,
) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws init")
    env = {"TRACESTATE": "caller=state"}
    tracer.inject(env)
    tracer.end_command(None)

    assert "TRACEPARENT" in env
    assert "TRACESTATE" not in env


def test_inject_sets_tracestate_beside_traceparent_when_the_span_context_carries_one(
    otlp_receiver: OtlpReceiver,
) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws init")
    context = SpanContext(
        trace_id=int(_CALLER_TRACE_ID, 16),
        span_id=int(_CALLER_SPAN_ID, 16),
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=TraceState([("vendor", "state")]),
    )
    env = {"TRACESTATE": "caller=state"}
    with trace.use_span(NonRecordingSpan(context)):
        tracer.inject(env)
    tracer.end_command(None)

    assert env["TRACEPARENT"] == f"00-{_CALLER_TRACE_ID}-{_CALLER_SPAN_ID}-01"
    assert env["TRACESTATE"] == "vendor=state"


def test_inject_before_a_command_starts_leaves_an_inherited_tracestate_alone(otlp_receiver: OtlpReceiver) -> None:
    env = {"TRACEPARENT": "caller", "TRACESTATE": "caller=state"}

    _tracer(otlp_receiver.endpoint).inject(env)

    assert env == {"TRACEPARENT": "caller", "TRACESTATE": "caller=state"}


def test_end_command_without_start_does_nothing(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)

    tracer.end_command("RuntimeError")
    tracer.export()

    assert otlp_receiver.requests == []


# ── Failures never escape ────────────────────────────────────────────────────


def _raise(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("tracing blew up")


def test_start_command_failure_is_swallowed_and_leaves_no_span(
    monkeypatch: pytest.MonkeyPatch, otlp_receiver: OtlpReceiver
) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    monkeypatch.setattr(tracer._tracer, "start_span", _raise)

    tracer.start_command("winter ws init")
    tracer.annotate_env("alpha")
    env: dict[str, str] = {}
    tracer.inject(env)
    tracer.end_command(None)
    tracer.export()

    assert env == {}
    assert otlp_receiver.requests == []


def test_end_command_failure_is_swallowed_and_still_detaches_the_context(
    monkeypatch: pytest.MonkeyPatch, otlp_receiver: OtlpReceiver
) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws init")
    assert trace.get_current_span().get_span_context().is_valid
    assert tracer._span is not None
    monkeypatch.setattr(tracer._span, "end", _raise)

    tracer.end_command("RuntimeError")
    tracer.export()

    assert not trace.get_current_span().get_span_context().is_valid
    assert otlp_receiver.requests == []


def test_inject_and_annotate_env_failures_are_swallowed(
    monkeypatch: pytest.MonkeyPatch, otlp_receiver: OtlpReceiver
) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws init")
    assert tracer._span is not None
    monkeypatch.setattr(tracer._span, "set_attribute", _raise)
    monkeypatch.setattr("winter_cli.core.internal.otel_command_tracer.TraceContextTextMapPropagator.inject", _raise)
    env = {"PATH": "/bin"}

    tracer.annotate_env("alpha")
    tracer.inject(env)
    tracer.end_command(None)

    assert env == {"PATH": "/bin"}
    assert not trace.get_current_span().get_span_context().is_valid
