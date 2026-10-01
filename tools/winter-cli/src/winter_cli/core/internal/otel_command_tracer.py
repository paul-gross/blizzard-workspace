from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import Token

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased
from opentelemetry.trace import Span, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.util.re import parse_env_headers

from winter_cli.core.tracing import ICommandTracer, TracingSettings

logger = logging.getLogger(__name__)

# The exporter's own timeout is the exit cap: one request, never longer than this.
EXPORT_TIMEOUT_SECONDS = 0.1

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
    """Keeps every ended span in memory until `drain` hands them to the one-shot export.

    There is no export thread and no batching: the SDK's batch processor can stack two
    exports at exit and its `force_flush` ignores its timeout.
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


class OtelCommandTracer:
    """`ICommandTracer` backed by OpenTelemetry: one root span per command, exported once at exit.

    The only module that imports `opentelemetry`; the container reaches it lazily so a process
    with tracing off never loads the SDK. Content is limited to the command path, the target
    env, and a failure's exception class name: exceptions are never recorded.
    """

    def __init__(self, settings: TracingSettings) -> None:
        if settings.otlp_endpoint is None:
            raise ValueError("OtelCommandTracer needs an OTLP endpoint")
        self._launch_time_ns = settings.launch_time_ns
        self._processor = _CollectingSpanProcessor()
        provider = TracerProvider(
            sampler=ParentBased(ALWAYS_ON),
            resource=_resource(),
            shutdown_on_exit=False,
        )
        provider.add_span_processor(self._processor)
        trace.set_tracer_provider(provider)
        self._tracer = provider.get_tracer("winter_cli")
        with _generic_exporter_env_masked():
            self._exporter = OTLPSpanExporter(
                endpoint=f"{settings.otlp_endpoint.rstrip('/')}/v1/traces",
                timeout=EXPORT_TIMEOUT_SECONDS,
                headers=parse_env_headers(settings.otlp_headers or "", liberal=True),
            )
        self._span: Span | None = None
        self._token: Token[Context] | None = None

    def start_command(self, command_path: str) -> None:
        try:
            span = self._tracer.start_span(
                command_path,
                context=_caller_context(),
                attributes={"winter.command": command_path},
                start_time=self._launch_time_ns,
                record_exception=False,
                set_status_on_exception=False,
            )
            self._token = otel_context.attach(trace.set_span_in_context(span))
            self._span = span
        except Exception:
            logger.debug("starting the command span failed", exc_info=True)

    def end_command(self, error_type: str | None) -> None:
        span, token = self._span, self._token
        if span is None:
            return
        self._token = None
        try:
            if error_type is not None:
                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error.type", error_type)
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
                self._span.set_attribute("winter.env", env_name)
        except Exception:
            logger.debug("annotating the command span failed", exc_info=True)

    def inject(self, env: dict[str, str]) -> None:
        """Set `TRACEPARENT` (and `TRACESTATE`) in `env` to the active span's context. Never raises.

        `TRACESTATE` travels with `TRACEPARENT`: it is set when the span context carries one, and
        removed otherwise, so a child never sees winter's `TRACEPARENT` beside another trace's
        `TRACESTATE`. A thread that carries no span context (OpenTelemetry context does not cross threads)
        falls back to the command span.
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

    def export(self) -> None:
        """Send every collected span in one request, capped by the exporter timeout. Never raises."""
        try:
            spans = self._processor.drain()
            if spans:
                self._exporter.export(spans)
        except Exception:
            logger.debug("trace export failed", exc_info=True)


def _conforms_otel_command_tracer(x: OtelCommandTracer) -> ICommandTracer:
    return x
