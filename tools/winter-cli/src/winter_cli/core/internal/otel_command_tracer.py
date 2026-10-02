from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import Token

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased
from opentelemetry.trace import Link, Span, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.util.re import parse_env_headers

from winter_cli.core.tracing import (
    ATTR_COMMAND,
    ATTR_ENV,
    ATTR_ERROR_TYPE,
    AttributeValue,
    ICommandTracer,
    IOperationHandle,
    TracingSettings,
)

logger = logging.getLogger(__name__)

# The exporter's own timeout is the exit cap: one request, never longer than this.
EXPORT_TIMEOUT_SECONDS = 0.1

# How often a session's background export flushes the spans that ended since the last flush.
BACKGROUND_FLUSH_INTERVAL_SECONDS = 5.0

_DEFAULT_SERVICE_NAME = "winter"
_UNKNOWN_SERVICE_PREFIX = "unknown_service"

# Generic OpenTelemetry exporter settings. Winter configures its own pipeline, so none of
# these may reach the exporter: it merges headers, certificates, compression and credential
# providers from them even when explicit arguments are passed.
_GENERIC_EXPORTER_ENV_PREFIXES = ("OTEL_EXPORTER_OTLP_", "OTEL_PYTHON_EXPORTER_OTLP_")


@contextmanager
def _generic_exporter_env_masked() -> Iterator[None]:
    """Hide every generic OTLP exporter variable from `os.environ`, restoring it unchanged afterwards."""
    masked = {key: value for key, value in os.environ.items() if key.startswith(_GENERIC_EXPORTER_ENV_PREFIXES)}
    for key in masked:
        del os.environ[key]
    try:
        yield
    finally:
        os.environ.update(masked)


class _CollectingSpanProcessor(SpanProcessor):
    """Keeps every ended span in memory until `drain` hands them to an export.

    There is no batching and no export thread of its own: the SDK's batch processor can stack
    two exports at exit and its `force_flush` ignores its timeout. The tracer decides when to
    drain, at exit or from a session's background flush; `on_end` only appends under a lock
    that `drain` holds just long enough to swap the list, so ending a span never waits on an
    export.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spans: list[ReadableSpan] = []

    def on_end(self, span: ReadableSpan) -> None:
        with self._lock:
            self._spans.append(span)

    def drain(self) -> list[ReadableSpan]:
        with self._lock:
            spans, self._spans = self._spans, []
        return spans


def _resource() -> Resource:
    """The SDK's own resource, with `winter` replacing only the SDK's unknown-service default.

    `OTEL_SERVICE_NAME` and a `service.name` inside `OTEL_RESOURCE_ATTRIBUTES` therefore win,
    and every other `OTEL_RESOURCE_ATTRIBUTES` entry is kept.
    """
    resource = Resource.create()
    if str(resource.attributes.get(SERVICE_NAME, "")).startswith(_UNKNOWN_SERVICE_PREFIX):
        resource = resource.merge(Resource({SERVICE_NAME: _DEFAULT_SERVICE_NAME}))
    return resource


def _caller_context() -> Context | None:
    """The caller's trace context from `TRACEPARENT`, or `None` when the caller set none.

    The propagator's carrier lookup is the lowercase `traceparent` key, so the environment
    value is handed over in an owned dict rather than `os.environ`.
    """
    traceparent = os.environ.get("TRACEPARENT")
    if not traceparent:
        return None
    return TraceContextTextMapPropagator().extract({"traceparent": traceparent})


def _mark_failed(span: Span, error_type: str | None) -> None:
    """Set ERROR status with no description, and `error.type` when the failure has a class name."""
    span.set_status(Status(StatusCode.ERROR))
    if error_type is not None:
        span.set_attribute(ATTR_ERROR_TYPE, error_type)


class _OtelOperationHandle:
    """The handle of an open operation span; with no span behind it, every call does nothing."""

    def __init__(self, span: Span | None) -> None:
        self._span = span

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        try:
            if self._span is not None:
                self._span.set_attribute(key, value)
        except Exception:
            logger.debug("annotating an operation span failed", exc_info=True)

    def mark_failed(self, error_type: str | None = None) -> None:
        try:
            if self._span is not None:
                _mark_failed(self._span, error_type)
        except Exception:
            logger.debug("marking an operation span failed", exc_info=True)


class OtelCommandTracer:
    """`ICommandTracer` backed by OpenTelemetry: one root span per command, exported once at exit.

    A session (`winter dashboard`) also exports in the background while it runs: its command span
    is the session span, held until exit, and its roots and their spans go out about every five
    seconds. The only module that imports `opentelemetry`; the container reaches it lazily so a
    process with tracing off never loads the SDK. Content is limited to the command path, the
    target env, operation names and their attributes, and a failure's exception class name:
    exceptions are never recorded.
    """

    def __init__(
        self,
        settings: TracingSettings,
        flush_interval_seconds: float = BACKGROUND_FLUSH_INTERVAL_SECONDS,
    ) -> None:
        if settings.otlp_endpoint is None:
            raise ValueError("OtelCommandTracer needs an OTLP endpoint")
        self._launch_time_ns = settings.launch_time_ns
        self._flush_interval_seconds = flush_interval_seconds
        self._processor = _CollectingSpanProcessor()
        provider = TracerProvider(
            sampler=ParentBased(ALWAYS_ON),
            resource=_resource(),
            shutdown_on_exit=False,
        )
        provider.add_span_processor(self._processor)
        trace.set_tracer_provider(provider)
        self._tracer = provider.get_tracer("winter_cli")
        self._endpoint = f"{settings.otlp_endpoint.rstrip('/')}/v1/traces"
        self._headers = parse_env_headers(settings.otlp_headers or "", liberal=True)
        self._exporter = self._build_exporter(EXPORT_TIMEOUT_SECONDS)
        self._span: Span | None = None
        self._token: Token[Context] | None = None
        self._command_path: str | None = None
        # One request at a time: held by a background flush, and by the final export.
        self._export_lock = threading.Lock()
        self._stop_flushing = threading.Event()
        self._flusher: threading.Thread | None = None

    def _build_exporter(self, timeout_seconds: float) -> OTLPSpanExporter:
        with _generic_exporter_env_masked():
            return OTLPSpanExporter(endpoint=self._endpoint, timeout=timeout_seconds, headers=self._headers)

    def start_command(self, command_path: str) -> None:
        try:
            span = self._tracer.start_span(
                command_path,
                context=_caller_context(),
                attributes={ATTR_COMMAND: command_path},
                start_time=self._launch_time_ns,
                record_exception=False,
                set_status_on_exception=False,
            )
            self._token = otel_context.attach(trace.set_span_in_context(span))
            self._span = span
            self._command_path = command_path
        except Exception:
            logger.debug("starting the command span failed", exc_info=True)

    def end_command(self, error_type: str | None) -> None:
        span, token = self._span, self._token
        if span is None:
            return
        self._token = None
        try:
            if error_type is not None:
                _mark_failed(span, error_type)
            span.end()
        except Exception:
            logger.debug("ending the command span failed", exc_info=True)
        if token is not None:
            try:
                otel_context.detach(token)
            except Exception:
                logger.debug("detaching the command span context failed", exc_info=True)

    def annotate_env(self, env_name: str) -> None:
        try:
            if self._span is not None:
                self._span.set_attribute(ATTR_ENV, env_name)
        except Exception:
            logger.debug("annotating the command span failed", exc_info=True)

    @contextmanager
    def operation(
        self, name: str, attributes: Mapping[str, AttributeValue] | None = None
    ) -> Iterator[IOperationHandle]:
        """Open a span for the body, current for its whole extent, and end it however the body exits.

        The parent is the span current when the operation opens. A thread that carries no span
        context (OpenTelemetry context does not cross plain threads, only those of a
        `ContextThreadPoolExecutor`) falls back to the command span, and with no command span either
        the operation is not traced. An exception escaping the body fails the span, with the
        class name as `error.type`, and propagates unchanged.
        """
        with self._scope(*self._open_operation(name, attributes)) as handle:
            yield handle

    @contextmanager
    def session_root(self, name: str) -> Iterator[IOperationHandle]:
        """Open a new trace for the body, linked to the session span, current for the whole body.

        The root has no parent, so it starts a trace of its own, and a link carries the session
        span's context. It is opened only when the command span is recording: with no command span,
        or one an unsampled caller suppressed, the body runs untraced. Failure handling is that of
        `operation`.
        """
        with self._scope(*self._open_session_root(name)) as handle:
            yield handle

    @contextmanager
    def _scope(self, span: Span | None, token: Token[Context] | None) -> Iterator[IOperationHandle]:
        """Hand the body its handle, fail the span on an escaping exception, and always end it."""
        try:
            yield _OtelOperationHandle(span)
        except BaseException as exc:
            if span is not None:
                try:
                    _mark_failed(span, type(exc).__name__)
                except Exception:
                    logger.debug("failing an operation span failed", exc_info=True)
            raise
        finally:
            self._close_operation(span, token)

    def _open_session_root(self, name: str) -> tuple[Span | None, Token[Context] | None]:
        command = self._span
        if command is None or not command.get_span_context().trace_flags.sampled:
            return None, None
        try:
            span = self._tracer.start_span(
                name,
                context=Context(),
                links=[Link(command.get_span_context())],
                attributes={ATTR_COMMAND: self._command_path} if self._command_path else None,
                record_exception=False,
                set_status_on_exception=False,
            )
        except Exception:
            logger.debug("starting a session root failed", exc_info=True)
            return None, None
        return self._activate(span)

    def _open_operation(
        self, name: str, attributes: Mapping[str, AttributeValue] | None
    ) -> tuple[Span | None, Token[Context] | None]:
        try:
            parent = trace.get_current_span()
            if not parent.get_span_context().is_valid:
                if self._span is None:
                    return None, None
                parent = self._span
            span = self._tracer.start_span(
                name,
                context=trace.set_span_in_context(parent),
                attributes=dict(attributes) if attributes else None,
                record_exception=False,
                set_status_on_exception=False,
            )
        except Exception:
            logger.debug("starting an operation span failed", exc_info=True)
            return None, None
        return self._activate(span)

    def _activate(self, span: Span) -> tuple[Span | None, Token[Context] | None]:
        """Make `span` current, or end it and report no span when that fails."""
        try:
            return span, otel_context.attach(trace.set_span_in_context(span))
        except Exception:
            logger.debug("activating a span failed", exc_info=True)
            self._close_operation(span, None)
            return None, None

    @staticmethod
    def _close_operation(span: Span | None, token: Token[Context] | None) -> None:
        if span is not None:
            try:
                span.end()
            except Exception:
                logger.debug("ending an operation span failed", exc_info=True)
        if token is not None:
            try:
                otel_context.detach(token)
            except Exception:
                logger.debug("detaching an operation span context failed", exc_info=True)

    def inject(self, env: dict[str, str]) -> None:
        """Set `TRACEPARENT` (and `TRACESTATE`) in `env` to the active span's context. Never raises.

        `TRACESTATE` travels with `TRACEPARENT`: it is set when the span context carries one, and
        removed otherwise, so a child never sees winter's `TRACEPARENT` beside another trace's
        `TRACESTATE`. A thread that carries no span context (OpenTelemetry context does not cross plain threads,
        only those of a `ContextThreadPoolExecutor`) falls back to the command span.
        """
        try:
            span = trace.get_current_span()
            if not span.get_span_context().is_valid:
                if self._span is None:
                    return
                span = self._span
            carrier: dict[str, str] = {}
            TraceContextTextMapPropagator().inject(carrier, context=trace.set_span_in_context(span))
            if "traceparent" in carrier:
                env["TRACEPARENT"] = carrier["traceparent"]
                if "tracestate" in carrier:
                    env["TRACESTATE"] = carrier["tracestate"]
                else:
                    env.pop("TRACESTATE", None)
        except Exception:
            logger.debug("injecting the trace context failed", exc_info=True)

    def start_background_export(self) -> None:
        """Flush ended spans about every `flush_interval_seconds` from a daemon thread. Never raises."""
        try:
            if self._flusher is not None:
                return
            flusher = threading.Thread(target=self._flush_until_stopped, name="winter-trace-flush", daemon=True)
            flusher.start()
            self._flusher = flusher
        except Exception:
            logger.debug("starting background export failed", exc_info=True)

    def _flush_until_stopped(self) -> None:
        while not self._stop_flushing.wait(self._flush_interval_seconds):
            self._flush()

    def _flush(self) -> None:
        """Send the spans ended since the last flush in one request; a failure drops the batch.

        Nothing escapes: this runs on a thread whose uncaught exception would be printed to the
        terminal, which a running dashboard owns.
        """
        if not self._export_lock.acquire(blocking=False):
            return
        try:
            if self._stop_flushing.is_set():
                return
            spans = self._processor.drain()
            if spans:
                self._exporter.export(spans)
        except Exception:
            logger.debug("background trace export failed", exc_info=True)
        finally:
            self._export_lock.release()

    def export(self) -> None:
        """Stop background export and send what is left in one request, within one cap in total. Never raises.

        A flush already in flight is waited for, at most one cap. The final batch then goes out
        through an exporter whose timeout is the budget that wait left, so the request cannot
        outlast the cap. With no flush in flight the whole cap is the request's timeout.
        """
        try:
            started = time.monotonic()
            self._stop_flushing.set()
            contended = not self._export_lock.acquire(blocking=False)
            if contended and not self._export_lock.acquire(timeout=EXPORT_TIMEOUT_SECONDS):
                return
            try:
                spans = self._processor.drain()
                if not spans:
                    return
                if not contended:
                    self._exporter.export(spans)
                    return
                remaining = EXPORT_TIMEOUT_SECONDS - (time.monotonic() - started)
                if remaining > 0:
                    self._build_exporter(remaining).export(spans)
            finally:
                self._export_lock.release()
        except Exception:
            logger.debug("trace export failed", exc_info=True)


def _conforms_otel_command_tracer(x: OtelCommandTracer) -> ICommandTracer:
    return x
