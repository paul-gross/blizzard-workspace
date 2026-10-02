"""`OtelCommandTracer`'s session surface: roots, background export and the exit budget.

Tests run against the stdlib receivers in `tests/otlp_receiver.py`, with a short flush interval
so a flush happens within the test rather than every five seconds.
"""

from __future__ import annotations

import statistics
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest
from opentelemetry import trace
from opentelemetry.proto.common.v1.common_pb2 import KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult

from tests.otlp_receiver import HangingEndpoint, OtlpReceiver, refused_endpoint
from winter_cli.core.internal.local_subprocess_runner import LocalSubprocessRunner
from winter_cli.core.internal.otel_command_tracer import (
    BACKGROUND_FLUSH_INTERVAL_SECONDS,
    EXPORT_TIMEOUT_SECONDS,
    OtelCommandTracer,
)
from winter_cli.core.tracing import TracingSettings

_CALLER_TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
_CALLER_SPAN_ID = "b7ad6b7169203331"

_FLUSH_INTERVAL_SECONDS = 0.05
_FLUSHER_NAME = "winter-trace-flush"

_WRITE_TRACEPARENT = "import os; open(os.environ['TRACE_OUT'], 'w').write(os.environ.get('TRACEPARENT', '<unset>'))"

_SESSION = "winter dashboard"
_ROOT = "dashboard refresh workspace"


@pytest.fixture(autouse=True)
def isolated_otel_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test with no inherited trace context and no registered global provider."""
    monkeypatch.delenv("TRACEPARENT", raising=False)
    monkeypatch.delenv("TRACESTATE", raising=False)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace._TRACER_PROVIDER_SET_ONCE, "_done", False)


def _tracer(endpoint: str, flush_interval_seconds: float = _FLUSH_INTERVAL_SECONDS) -> OtelCommandTracer:
    return OtelCommandTracer(TracingSettings(otlp_endpoint=endpoint), flush_interval_seconds=flush_interval_seconds)


def _attributes(span: Span) -> dict[str, object]:
    def value(attribute: KeyValue) -> object:
        return getattr(attribute.value, str(attribute.value.WhichOneof("value")))

    return {attribute.key: value(attribute) for attribute in span.attributes}


def _on_a_fresh_thread(work: Callable[[], None]) -> None:
    errors: list[BaseException] = []

    def run() -> None:
        try:
            work()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]


def _wait_until(condition: Callable[[], bool], timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.005)


def _flusher_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == _FLUSHER_NAME]


def _names(spans: Sequence[Span]) -> list[str]:
    return [span.name for span in spans]


def _one_root(tracer: OtelCommandTracer, name: str = _ROOT) -> None:
    """A root with one operation inside it, opened on a fresh thread as a Textual worker does."""

    def work() -> None:
        with tracer.session_root(name), tracer.operation("git status", {"winter.repo": "demo"}):
            pass

    _on_a_fresh_thread(work)


@pytest.fixture(autouse=True)
def no_flusher_outlives_its_test() -> Iterator[None]:
    yield
    _wait_until(lambda: not _flusher_threads())


# ── Roots ────────────────────────────────────────────────────────────────────


def test_root_on_a_fresh_thread_starts_a_new_trace_linked_to_the_command_span(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command(_SESSION)
    _one_root(tracer)
    tracer.end_command(None)
    tracer.export()

    spans = {span.name: span for span in otlp_receiver.requests[0].spans}
    command, root, git = spans[_SESSION], spans[_ROOT], spans["git status"]
    assert root.trace_id != command.trace_id
    assert root.parent_span_id == b""
    assert [(link.trace_id, link.span_id) for link in root.links] == [(command.trace_id, command.span_id)]
    assert _attributes(root) == {"winter.command": _SESSION}
    assert root.status.code == Status.STATUS_CODE_UNSET
    assert (git.trace_id, git.parent_span_id) == (root.trace_id, root.span_id)


def test_a_child_started_inside_a_root_gets_a_traceparent_naming_the_root(
    otlp_receiver: OtlpReceiver, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_file = tmp_path / "traceparent.out"
    monkeypatch.setenv("TRACE_OUT", str(out_file))
    tracer = _tracer(otlp_receiver.endpoint)
    runner = LocalSubprocessRunner(tracer)
    tracer.start_command(_SESSION)

    def work() -> None:
        with tracer.session_root(_ROOT):
            assert runner.run([sys.executable, "-c", _WRITE_TRACEPARENT]).returncode == 0

    _on_a_fresh_thread(work)
    tracer.end_command(None)
    tracer.export()

    _version, trace_id, span_id, _flags = out_file.read_text().split("-")
    root = next(span for span in otlp_receiver.requests[0].spans if span.name == _ROOT)
    assert (trace_id, span_id) == (root.trace_id.hex(), root.span_id.hex())


def test_each_root_is_a_trace_of_its_own(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command(_SESSION)

    _one_root(tracer)
    _one_root(tracer)
    tracer.end_command(None)
    tracer.export()

    roots = [span for span in otlp_receiver.requests[0].spans if span.name == _ROOT]
    assert len(roots) == 2
    assert roots[0].trace_id != roots[1].trace_id


def test_a_root_is_current_for_its_body_and_the_previous_span_again_afterwards(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command(_SESSION)
    command_span_id = trace.get_current_span().get_span_context().span_id

    with tracer.session_root(_ROOT):
        in_body = trace.get_current_span().get_span_context().span_id
    after = trace.get_current_span().get_span_context().span_id
    tracer.end_command(None)

    assert in_body != command_span_id
    assert after == command_span_id


def test_a_root_opened_inside_another_span_still_starts_a_trace_of_its_own(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command(_SESSION)

    with tracer.operation("outer"), tracer.session_root(_ROOT):
        pass
    tracer.end_command(None)
    tracer.export()

    spans = {span.name: span for span in otlp_receiver.requests[0].spans}
    command, root = spans[_SESSION], spans[_ROOT]
    assert root.parent_span_id == b""
    assert root.trace_id != command.trace_id
    assert [(link.trace_id, link.span_id) for link in root.links] == [(command.trace_id, command.span_id)]


def test_a_root_is_not_recorded_when_the_command_span_is_not(
    otlp_receiver: OtlpReceiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRACEPARENT", f"00-{_CALLER_TRACE_ID}-{_CALLER_SPAN_ID}-00")
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command(_SESSION)
    ran: list[str] = []

    def work() -> None:
        with tracer.session_root(_ROOT), tracer.operation("git status"):
            ran.append("body")

    _on_a_fresh_thread(work)
    tracer.end_command(None)
    tracer.export()

    assert ran == ["body"]
    assert otlp_receiver.requests == []


def test_a_root_without_a_command_span_runs_its_body_and_records_nothing(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    ran: list[str] = []

    with tracer.session_root(_ROOT) as root:
        root.set_attribute("winter.repo", "demo")
        ran.append("body")
    tracer.export()

    assert ran == ["body"]
    assert otlp_receiver.requests == []


def test_an_exception_escaping_a_root_fails_it_with_its_class_name_and_reraises_unchanged(
    otlp_receiver: OtlpReceiver,
) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command(_SESSION)
    raised = ValueError("secret stderr text")

    def work() -> None:
        with tracer.session_root(_ROOT):
            raise raised

    with pytest.raises(ValueError) as caught:
        _on_a_fresh_thread(work)
    tracer.end_command(None)
    tracer.export()

    assert caught.value is raised
    root = next(span for span in otlp_receiver.requests[0].spans if span.name == _ROOT)
    assert root.status.code == Status.STATUS_CODE_ERROR
    assert _attributes(root) == {"winter.command": _SESSION, "error.type": "ValueError"}
    assert b"secret stderr text" not in otlp_receiver.requests[0].body


# ── Background export ────────────────────────────────────────────────────────


def test_the_default_flush_interval_is_five_seconds() -> None:
    assert BACKGROUND_FLUSH_INTERVAL_SECONDS == 5.0


def test_spans_ended_after_background_export_starts_reach_the_receiver_with_no_export_call(
    otlp_receiver: OtlpReceiver,
) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command(_SESSION)
    tracer.start_background_export()

    _one_root(tracer)
    _wait_until(lambda: len(otlp_receiver.requests) == 1)

    assert sorted(_names(otlp_receiver.requests[0].spans)) == sorted([_ROOT, "git status"])
    tracer.end_command(None)
    tracer.export()


def test_each_flush_is_one_request_and_an_idle_interval_sends_nothing(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command(_SESSION)
    tracer.start_background_export()

    _one_root(tracer)
    _wait_until(lambda: len(otlp_receiver.requests) == 1)
    time.sleep(_FLUSH_INTERVAL_SECONDS * 4)
    assert len(otlp_receiver.requests) == 1

    _one_root(tracer)
    _wait_until(lambda: len(otlp_receiver.requests) == 2)
    assert sorted(_names(otlp_receiver.requests[1].spans)) == sorted([_ROOT, "git status"])
    tracer.end_command(None)
    tracer.export()


def test_the_session_span_is_not_flushed_before_it_ends(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command(_SESSION)
    tracer.start_background_export()

    _one_root(tracer)
    _wait_until(lambda: len(otlp_receiver.requests) == 1)

    assert _SESSION not in _names(otlp_receiver.requests[0].spans)
    tracer.end_command(None)
    tracer.export()


def test_the_flusher_is_a_daemon_thread_and_starting_twice_starts_one(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)

    tracer.start_background_export()
    tracer.start_background_export()

    flushers = _flusher_threads()
    assert len(flushers) == 1
    assert flushers[0].daemon
    tracer.export()


def test_a_command_that_never_starts_background_export_sends_once_at_exit(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command("winter ws status")
    with tracer.operation("git status"):
        pass
    tracer.end_command(None)

    time.sleep(_FLUSH_INTERVAL_SECONDS * 4)
    assert _flusher_threads() == []
    assert otlp_receiver.requests == []

    tracer.export()
    assert len(otlp_receiver.requests) == 1
    assert sorted(_names(otlp_receiver.requests[0].spans)) == sorted(["winter ws status", "git status"])


def test_ending_spans_never_waits_on_a_flush_in_flight_against_a_hanging_endpoint(
    hanging_endpoint: HangingEndpoint,
) -> None:
    tracer = _tracer(hanging_endpoint.endpoint, flush_interval_seconds=0.01)
    tracer.start_command(_SESSION)
    tracer.start_background_export()
    _one_root(tracer)
    _wait_until(lambda: hanging_endpoint.accepted >= 1)

    slowest = 0.0
    for _ in range(200):
        started = time.perf_counter()
        _one_root(tracer)
        slowest = max(slowest, time.perf_counter() - started)

    # A thread start and two spans cost well under a flush's 100 ms request timeout.
    assert slowest < EXPORT_TIMEOUT_SECONDS / 2, f"ending spans took {slowest}s while a flush was in flight"
    tracer.end_command(None)
    tracer.export()


class _OverlapDetectingExporter:
    """Stands in for the exporter: counts how many `export` calls are in flight at once."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.most_active = 0
        self.calls = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            self.active += 1
            self.calls += 1
            self.most_active = max(self.most_active, self.active)
        time.sleep(0.03)
        with self._lock:
            self.active -= 1
        return SpanExportResult.SUCCESS


def test_flushes_never_overlap(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint, flush_interval_seconds=0.005)
    exporter = _OverlapDetectingExporter()
    tracer._exporter = exporter  # type: ignore[assignment]
    tracer.start_command(_SESSION)
    tracer.start_background_export()

    for _ in range(15):
        _one_root(tracer)
        time.sleep(0.01)
    tracer.end_command(None)
    tracer.export()

    assert exporter.calls >= 2
    assert exporter.most_active == 1


def test_a_failed_flush_drops_its_batch_surfaces_nothing_and_leaves_the_flusher_running(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    tracer = _tracer(refused_endpoint(), flush_interval_seconds=0.01)
    unhandled: list[object] = []
    monkeypatch.setattr(threading, "excepthook", unhandled.append)
    attempts: list[int] = []

    def exploding_export(spans: Sequence[ReadableSpan]) -> SpanExportResult:
        attempts.append(len(spans))
        raise RuntimeError("collector exploded")

    tracer._exporter.export = exploding_export  # type: ignore[method-assign]
    tracer.start_command(_SESSION)
    tracer.start_background_export()

    _one_root(tracer)
    _wait_until(lambda: len(attempts) >= 1)
    _one_root(tracer)
    _wait_until(lambda: len(attempts) >= 2)

    assert len(_flusher_threads()) == 1
    assert attempts == [2, 2]  # each batch was sent once and dropped, never re-sent
    tracer.end_command(None)
    tracer.export()
    captured = capfd.readouterr()
    assert (captured.out, captured.err) == ("", "")
    assert unhandled == []


def test_a_refused_endpoint_never_breaks_a_running_session(capfd: pytest.CaptureFixture[str]) -> None:
    tracer = _tracer(refused_endpoint(), flush_interval_seconds=0.01)
    tracer.start_command(_SESSION)
    tracer.start_background_export()

    _one_root(tracer)
    time.sleep(0.1)
    _one_root(tracer)
    tracer.end_command(None)
    tracer.export()

    captured = capfd.readouterr()
    assert (captured.out, captured.err) == ("", "")


# ── Exit ─────────────────────────────────────────────────────────────────────


def test_export_stops_background_export(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command(_SESSION)
    tracer.start_background_export()

    tracer.end_command(None)
    tracer.export()

    _wait_until(lambda: not _flusher_threads())
    _one_root(tracer)
    time.sleep(_FLUSH_INTERVAL_SECONDS * 4)
    assert len(otlp_receiver.requests) == 1  # only the export's own request


def test_a_flush_that_runs_after_export_sends_nothing(otlp_receiver: OtlpReceiver) -> None:
    """A flush the flusher began just as export ran must not send what a worker ends after it."""
    tracer = _tracer(otlp_receiver.endpoint, flush_interval_seconds=60)
    tracer.start_command(_SESSION)
    tracer.start_background_export()
    tracer.end_command(None)
    tracer.export()

    _one_root(tracer)
    tracer._flush()

    assert len(otlp_receiver.requests) == 1


def test_the_final_request_carries_the_session_span(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint)
    tracer.start_command(_SESSION)
    tracer.start_background_export()
    _one_root(tracer)
    _wait_until(lambda: len(otlp_receiver.requests) == 1)

    tracer.end_command(None)
    tracer.export()

    assert len(otlp_receiver.requests) == 2
    assert _names(otlp_receiver.requests[1].spans) == [_SESSION]


def test_export_with_a_flush_in_flight_against_a_hanging_endpoint_returns_within_the_cap(
    hanging_endpoint: HangingEndpoint,
) -> None:
    """The median of three runs absorbs one scheduler stall but still fails when most runs overrun."""
    elapsed: list[float] = []
    for _ in range(3):
        accepted_before = hanging_endpoint.accepted
        tracer = _tracer(hanging_endpoint.endpoint, flush_interval_seconds=0.01)
        tracer.start_command(_SESSION)
        tracer.start_background_export()
        _one_root(tracer)
        _wait_until(lambda before=accepted_before: hanging_endpoint.accepted > before)
        tracer.end_command(None)

        started = time.perf_counter()
        tracer.export()
        elapsed.append(time.perf_counter() - started)

    assert statistics.median(elapsed) < EXPORT_TIMEOUT_SECONDS + 0.05, f"export took {elapsed}"


class _SlowFlushExporter:
    """Stands in for the exporter during a flush: holds the request open, then delivers it for real."""

    def __init__(self, delivered_by: OtelCommandTracer, started: threading.Event) -> None:
        self._real = delivered_by._exporter
        self._started = started

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self._started.set()
        time.sleep(0.03)
        return self._real.export(spans)


def test_export_waits_for_a_flush_in_flight_then_sends_the_session_span_within_the_cap(
    otlp_receiver: OtlpReceiver,
) -> None:
    tracer = _tracer(otlp_receiver.endpoint, flush_interval_seconds=0.01)
    flush_started = threading.Event()
    tracer._exporter = _SlowFlushExporter(tracer, flush_started)  # type: ignore[assignment]
    tracer.start_command(_SESSION)
    tracer.start_background_export()
    _one_root(tracer)
    assert flush_started.wait(5)
    tracer.end_command(None)

    started = time.perf_counter()
    tracer.export()
    elapsed = time.perf_counter() - started

    assert elapsed < EXPORT_TIMEOUT_SECONDS + 0.05
    assert [_names(request.spans) for request in otlp_receiver.requests][-1] == [_SESSION]
    assert sorted(span for request in otlp_receiver.requests[:-1] for span in _names(request.spans)) == sorted(
        [_ROOT, "git status"]
    )


def test_export_gives_up_when_a_flush_outlasts_the_cap(otlp_receiver: OtlpReceiver) -> None:
    tracer = _tracer(otlp_receiver.endpoint, flush_interval_seconds=0.01)
    flush_started = threading.Event()
    release = threading.Event()

    class _StuckExporter:
        def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
            flush_started.set()
            release.wait(5)
            return SpanExportResult.FAILURE

    tracer._exporter = _StuckExporter()  # type: ignore[assignment]
    tracer.start_command(_SESSION)
    tracer.start_background_export()
    _one_root(tracer)
    assert flush_started.wait(5)
    tracer.end_command(None)

    started = time.perf_counter()
    tracer.export()
    elapsed = time.perf_counter() - started
    release.set()

    assert elapsed < EXPORT_TIMEOUT_SECONDS + 0.05
    assert otlp_receiver.requests == []
