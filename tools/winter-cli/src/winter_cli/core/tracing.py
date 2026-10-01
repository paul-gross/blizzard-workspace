from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


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


class ICommandTracer(ITracePropagator, ICommandAnnotator, Protocol):
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
        """Export the collected spans once. Never raises."""
        ...
