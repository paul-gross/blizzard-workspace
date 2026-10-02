"""`OtelCommandTracer.operation` against a real localhost receiver.

Each test opens operation spans through the `IOperationTracer` surface and reads what actually
arrived at a stdlib receiver that decodes the OTLP protobuf request.
"""

from __future__ import annotations

import statistics
import sys
import threading
import time
from pathlib import Path

import pytest
from opentelemetry import trace
from opentelemetry.proto.common.v1.common_pb2 import KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from tests.otlp_receiver import HangingEndpoint, OtlpReceiver
from winter_cli.core.internal.local_subprocess_runner import LocalSubprocessRunner
from winter_cli.core.internal.otel_command_tracer import EXPORT_TIMEOUT_SECONDS, OtelCommandTracer
from winter_cli.core.tracing import TracingSettings

_CALLER_TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
_CALLER_SPAN_ID = "b7ad6b7169203331"

# A `ws status` over 10 envs x 20 repos opens roughly this many git spans.
_REPRESENTATIVE_SPAN_COUNT = 500

_WRITE_TRACEPARENT = "import os; open(os.environ['TRACE_OUT'], 'w').write(os.environ.get('TRACEPARENT', '<unset>'))"


@pytest.fixture(autouse=True)
def isolated_otel_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test with no inherited trace context and no registered global provider."""
    monkeypatch.delenv("TRACEPARENT", raising=False)
    monkeypatch.delenv("TRACESTATE", raising=False)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace._TRACER_PROVIDER_SET_ONCE, "_done", False)


def _tracer(endpoint: str) -> OtelCommandTracer:
    return OtelCommandTracer(TracingSettings(otlp_endpoint=endpoint))


def _attributes(span: Span) -> dict[str, object]:
    def value(attribute: KeyValue) -> object:
        return getattr(attribute.value, str(attribute.value.WhichOneof("value")))

    return {attribute.key: value(attribute) for attribute in span.attributes}


def _exported_spans(receiver: OtlpReceiver) -> dict[str, Span]:
    """The spans of the one request that arrived, by name."""
    assert len(receiver.requests) == 1
    spans = receiver.requests[0].spans
    by_name = {span.name: span for span in spans}
    assert len(by_name) == len(spans), "span names are expected to be distinct in these tests"
    return by_name


class _Boom(Exception):
    pass


# ── Name, attributes, trace and parent ───────────────────────────────────────


def test_operation_span_has_the_given_name_and_typed_attributes_and_shares_the_command_trace(
    otlp_receiver: OtlpReceiver,
) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws status")
    with tracer.operation("git status", {"winter.repo": "winter", "winter.count": 3, "winter.ready": True}):
        pass
    tracer.end_command(None)
    tracer.export()

    spans = _exported_spans(otlp_receiver)
    operation, command = spans["git status"], spans["winter ws status"]
    assert _attributes(operation) == {"winter.repo": "winter", "winter.count": 3, "winter.ready": True}
    assert operation.trace_id == command.trace_id
    assert operation.parent_span_id == command.span_id
    assert operation.status.code == Status.STATUS_CODE_UNSET


def test_handle_adds_attributes_after_the_span_opens(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter provision")
    with tracer.operation("provision handler apply") as operation:
        operation.set_attribute("winter.exit_code", 0)
    tracer.end_command(None)
    tracer.export()

    assert _attributes(_exported_spans(otlp_receiver)["provision handler apply"]) == {"winter.exit_code": 0}


def test_nested_operation_is_parented_on_the_operation_current_when_it_opens(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws sync")
    with tracer.operation("outer"):
        with tracer.operation("inner"):
            pass
        with tracer.operation("sibling"):
            pass
    tracer.end_command(None)
    tracer.export()

    spans = _exported_spans(otlp_receiver)
    assert spans["inner"].parent_span_id == spans["outer"].span_id
    assert spans["sibling"].parent_span_id == spans["outer"].span_id
    assert spans["outer"].parent_span_id == spans["winter ws sync"].span_id


def test_operation_is_current_for_its_body_and_the_previous_span_again_afterwards(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws sync")
    command_span_id = trace.get_current_span().get_span_context().span_id

    with tracer.operation("outer"):
        in_body = trace.get_current_span().get_span_context().span_id
    after = trace.get_current_span().get_span_context().span_id
    tracer.end_command(None)

    assert in_body != command_span_id
    assert after == command_span_id


def test_operation_on_a_thread_without_span_context_is_parented_on_the_command_span(
    otlp_receiver: OtlpReceiver,
) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws status")

    def worker() -> None:
        assert not trace.get_current_span().get_span_context().is_valid
        with tracer.operation("git status"):
            pass

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    tracer.end_command(None)
    tracer.export()

    spans = _exported_spans(otlp_receiver)
    assert spans["git status"].trace_id == spans["winter ws status"].trace_id
    assert spans["git status"].parent_span_id == spans["winter ws status"].span_id


def test_operation_under_a_foreign_current_span_is_parented_on_it(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws status")
    foreign = NonRecordingSpan(
        SpanContext(
            trace_id=int(_CALLER_TRACE_ID, 16),
            span_id=int(_CALLER_SPAN_ID, 16),
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
    )
    with trace.use_span(foreign), tracer.operation("git status"):
        pass
    tracer.end_command(None)
    tracer.export()

    operation = _exported_spans(otlp_receiver)["git status"]
    assert operation.trace_id.hex() == _CALLER_TRACE_ID
    assert operation.parent_span_id.hex() == _CALLER_SPAN_ID


def test_operation_under_an_unsampled_caller_exports_nothing(
    otlp_receiver: OtlpReceiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRACEPARENT", f"00-{_CALLER_TRACE_ID}-{_CALLER_SPAN_ID}-00")
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws status")
    with tracer.operation("git status"):
        pass
    tracer.end_command(None)
    tracer.export()

    assert otlp_receiver.requests == []


def test_operation_without_a_command_span_is_not_traced_and_runs_its_body(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    ran: list[str] = []

    with tracer.operation("git status") as operation:
        operation.set_attribute("winter.repo", "winter")
        operation.mark_failed("RepoError")
        ran.append("body")
    tracer.export()

    assert ran == ["body"]
    assert otlp_receiver.requests == []


# ── Failure ──────────────────────────────────────────────────────────────────


def test_an_escaping_exception_fails_the_span_with_its_class_name_and_reraises_unchanged(
    otlp_receiver: OtlpReceiver,
) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws fetch")
    raised = _Boom("secret stderr text")

    with pytest.raises(_Boom) as caught, tracer.operation("git fetch"):
        raise raised
    tracer.end_command(None)
    tracer.export()

    assert caught.value is raised
    operation = _exported_spans(otlp_receiver)["git fetch"]
    assert operation.status.code == Status.STATUS_CODE_ERROR
    assert operation.status.message == ""
    assert list(operation.events) == []
    assert _attributes(operation) == {"error.type": "_Boom"}
    assert b"secret stderr text" not in otlp_receiver.requests[0].body


def test_an_exception_escaping_nested_operations_fails_each_of_them(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws fetch")

    with pytest.raises(_Boom), tracer.operation("outer"), tracer.operation("inner"):
        raise _Boom
    tracer.end_command("_Boom")
    tracer.export()

    spans = _exported_spans(otlp_receiver)
    for name in ("outer", "inner"):
        assert spans[name].status.code == Status.STATUS_CODE_ERROR
        assert _attributes(spans[name]) == {"error.type": "_Boom"}


def test_marking_failure_with_an_error_type_sets_error_status_and_the_type(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter provision")
    with tracer.operation("provision handler apply") as operation:
        operation.mark_failed("OSError")
    tracer.end_command(None)
    tracer.export()

    span = _exported_spans(otlp_receiver)["provision handler apply"]
    assert span.status.code == Status.STATUS_CODE_ERROR
    assert span.status.message == ""
    assert _attributes(span) == {"error.type": "OSError"}


def test_marking_failure_without_an_error_type_sets_error_status_and_no_type(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter provision")
    with tracer.operation("provision handler apply") as operation:
        operation.mark_failed()
    tracer.end_command(None)
    tracer.export()

    span = _exported_spans(otlp_receiver)["provision handler apply"]
    assert span.status.code == Status.STATUS_CODE_ERROR
    assert _attributes(span) == {}


# ── Propagation to a child ───────────────────────────────────────────────────


def test_child_started_inside_an_operation_gets_a_traceparent_naming_that_span(
    otlp_receiver: OtlpReceiver, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_file = tmp_path / "traceparent.out"
    monkeypatch.setenv("TRACE_OUT", str(out_file))
    tracer = _tracer(otlp_receiver.endpoint)
    runner = LocalSubprocessRunner(tracer)
    tracer.start_command("winter provision")
    with tracer.operation("provision handler apply"):
        assert runner.run([sys.executable, "-c", _WRITE_TRACEPARENT]).returncode == 0
    tracer.end_command(None)
    tracer.export()

    _version, trace_id, span_id, _flags = out_file.read_text().split("-")
    operation = _exported_spans(otlp_receiver)["provision handler apply"]
    assert (trace_id, span_id) == (operation.trace_id.hex(), operation.span_id.hex())


# ── Export ───────────────────────────────────────────────────────────────────


def _open_many(tracer: OtelCommandTracer, count: int) -> None:
    tracer.start_command("winter ws status")
    for index in range(count):
        with tracer.operation("git status", {"winter.repo": f"repo-{index % 20}", "winter.env": f"env-{index % 10}"}):
            pass
    tracer.end_command(None)


def test_export_sends_exactly_one_request_carrying_every_collected_span(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    _open_many(tracer, _REPRESENTATIVE_SPAN_COUNT)

    tracer.export()

    assert len(otlp_receiver.requests) == 1
    assert len(otlp_receiver.requests[0].spans) == _REPRESENTATIVE_SPAN_COUNT + 1


def test_export_of_a_representative_command_returns_within_the_cap_against_a_hanging_endpoint(
    hanging_endpoint: HangingEndpoint,
) -> None:
    """The cap covers the request; encoding 500 spans before it must fit inside the 50 ms margin.

    The median of five runs absorbs one scheduler stall but still fails when most runs overrun.
    """
    elapsed: list[float] = []
    for _ in range(5):
        tracer = _tracer(hanging_endpoint.endpoint)
        _open_many(tracer, _REPRESENTATIVE_SPAN_COUNT)
        started = time.perf_counter()
        tracer.export()
        elapsed.append(time.perf_counter() - started)

    assert statistics.median(elapsed) < EXPORT_TIMEOUT_SECONDS + 0.05, f"export took {elapsed}"


# ── Tracing never breaks the body ────────────────────────────────────────────


def _raise(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("tracing blew up")


def test_a_failure_starting_the_span_still_runs_the_body(
    monkeypatch: pytest.MonkeyPatch, otlp_receiver: OtlpReceiver
) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws status")
    monkeypatch.setattr(tracer._tracer, "start_span", _raise)
    ran: list[str] = []

    with tracer.operation("git status") as operation:
        operation.set_attribute("winter.repo", "winter")
        operation.mark_failed()
        ran.append("body")
    tracer.end_command(None)

    assert ran == ["body"]


def test_a_failure_in_the_handle_or_at_span_end_does_not_reach_the_body(
    monkeypatch: pytest.MonkeyPatch, otlp_receiver: OtlpReceiver
) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws status")
    ran: list[str] = []

    with tracer.operation("git status") as operation:
        span = trace.get_current_span()
        monkeypatch.setattr(span, "set_attribute", _raise)
        monkeypatch.setattr(span, "set_status", _raise)
        monkeypatch.setattr(span, "end", _raise)
        operation.set_attribute("winter.repo", "winter")
        operation.mark_failed("OSError")
        ran.append("body")

    assert ran == ["body"]
    assert trace.get_current_span().get_span_context().is_valid
    tracer.end_command(None)
