from __future__ import annotations

import re
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

# Span attribute keys. Values are names, counts, exit codes and booleans, never free text.
ATTR_COMMAND = "winter.command"
ATTR_ENV = "winter.env"
ATTR_REPO = "winter.repo"
ATTR_PROVIDER = "winter.provider"
ATTR_SCOPE = "winter.scope"
ATTR_SERVICE_PATTERNS = "winter.service.patterns"
ATTR_READY = "winter.ready"
ATTR_HANDLER = "winter.handler"
ATTR_EXIT_CODE = "winter.exit_code"
ATTR_ERROR_TYPE = "error.type"

AttributeValue = str | int | bool


@dataclass(frozen=True)
class TracingSettings:
    """Tracing settings, read from the environment once at the CLI boundary.

    `otlp_endpoint` is the base URL of an OTLP/HTTP receiver (`WINTER_OTEL_EXPORTER_OTLP_ENDPOINT`);
    tracing is on only when it is set. `otlp_headers` is the raw `WINTER_OTEL_EXPORTER_OTLP_HEADERS`
    value, in the standard `k1=v1,k2=v2` format. `sdk_disabled` is the `OTEL_SDK_DISABLED=true`
    kill switch. `launch_time_ns` is the instant the launcher shim started, in nanoseconds since
    the epoch; `None` means no usable launch instant, so the command span starts at dispatch.
    """

    otlp_endpoint: str | None = None
    otlp_headers: str | None = None
    sdk_disabled: bool = False
    launch_time_ns: int | None = None

    @property
    def enabled(self) -> bool:
        """Whether this process traces: an endpoint is set and the kill switch is not."""
        return self.otlp_endpoint is not None and not self.sdk_disabled


def parse_sdk_disabled(raw: str | None) -> bool:
    """Parse `OTEL_SDK_DISABLED`: only the value `true` (any case) disables the SDK."""
    return raw is not None and raw.strip().lower() == "true"


def non_blank(raw: str | None) -> str | None:
    """A set, non-blank environment value stripped of surrounding whitespace, else `None`."""
    if raw is None:
        return None
    return raw.strip() or None


_LAUNCH_TIME_PATTERN = re.compile(r"(?P<seconds>\d+)(?:\.(?P<fraction>\d+))?")
_NANOSECONDS_PER_SECOND = 1_000_000_000


def parse_launch_time_ns(raw: str | None, now_ns: int) -> int | None:
    """Parse a `WINTER_LAUNCH_TIME` value (`$EPOCHREALTIME`) into epoch nanoseconds.

    Accepts `.` or `,` as the decimal separator, since bash's `$EPOCHREALTIME` follows the
    locale. An absent, unparseable, non-positive, or future (later than `now_ns`) value
    returns `None`, which means "start the span at dispatch time".
    """
    if raw is None:
        return None
    match = _LAUNCH_TIME_PATTERN.fullmatch(raw.strip().replace(",", "."))
    if match is None:
        return None
    fraction = (match["fraction"] or "").ljust(9, "0")[:9]
    launch_time_ns = int(match["seconds"]) * _NANOSECONDS_PER_SECOND + int(fraction)
    if launch_time_ns <= 0 or launch_time_ns > now_ns:
        return None
    return launch_time_ns


class ITracePropagator(Protocol):
    """Writes the active trace context into a child process environment."""

    def inject(self, env: dict[str, str]) -> None:
        """Set `TRACEPARENT` in `env` to the active span's context. A no-op when tracing is off."""
        ...


class ICommandAnnotator(Protocol):
    """Annotates the running command's span."""

    def annotate_env(self, env_name: str) -> None:
        """Record the one feature environment the command targets as `winter.env`."""
        ...


class IOperationHandle(Protocol):
    """The open operation span, as its body sees it."""

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        """Add an attribute to the operation: a name, count, exit code or boolean, never free text."""
        ...

    def mark_failed(self, error_type: str | None = None) -> None:
        """Mark the operation failed without raising; `error_type`, an exception class name, is set as `error.type`."""
        ...


class IOperationTracer(Protocol):
    """Opens spans for the operations that run inside a command.

    This is the seam every span site depends on. Tracing off makes it a pass-through.
    """

    def operation(
        self, name: str, attributes: Mapping[str, AttributeValue] | None = None
    ) -> AbstractContextManager[IOperationHandle]:
        """Open a span named `name` for the `with` body, nested under the span active when it opens.

        The span is the active one for the body, so child processes and nested operations parent
        on it. An exception that escapes the body marks the operation failed with its class name
        as `error.type`, then propagates unchanged. Tracing never raises into the body.
        """
        ...


class ISessionTracer(Protocol):
    """Traces a long-running session as a series of short traces, exporting them while it runs.

    The command span is the session span: it lasts as long as the session and is exported when
    the process exits. Each unit of work inside the session, such as one refresh, runs under a
    session root, a trace of its own that links back to the session span. Roots are exported as
    they end, in the background, so a session that runs for hours is visible while it runs.
    """

    def start_background_export(self) -> None:
        """Export ended spans about every five seconds from a daemon thread, until `export` runs.

        A session calls this once, before its work begins. A one-shot command never calls it and
        keeps the single export at exit. The thread never keeps the process alive, never blocks
        a call that ends a span, and never surfaces a failure: a failed flush drops its batch.
        """
        ...

    def session_root(self, name: str) -> AbstractContextManager[IOperationHandle]:
        """Open a new trace named `name` for the `with` body, linked to the session span.

        For work that starts on a thread with no span context. Spans opened inside the body, and
        child processes started from it, parent on this root. The root carries `winter.command`
        with the session's command path. It is recorded only when the session span is, and the
        body runs either way. An exception that escapes the body fails the root like an
        operation, then propagates unchanged. Tracing never raises into the body.
        """
        ...


class ICommandTracer(ITracePropagator, ICommandAnnotator, IOperationTracer, ISessionTracer, Protocol):
    """The command span's lifecycle, owned by the CLI boundary.

    One tracer instance serves a whole process: `start_command` opens the single command
    span, `end_command` closes it, and `export` ships whatever was collected, once, at exit.
    """

    def start_command(self, command_path: str) -> None:
        """Open the command span, named and attributed by its full command path (`winter ws init`)."""
        ...

    def end_command(self, error_type: str | None) -> None:
        """Close the command span: `None` for a successful exit, else the failure's exception class name."""
        ...

    def export(self) -> None:
        """Export the spans still collected, once, within the exit cap in total. Never raises.

        Stops background export first. A flush already in flight is waited for, and the final
        batch, which holds the command span, is sent only with the budget that remains.
        """
        ...
