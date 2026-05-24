from __future__ import annotations

import json
import threading
from typing import Any, Protocol

from winter_cli.modules.doctor.models import ProbeResult, ProbeStatus

_STATUS_GLYPH = {
    ProbeStatus.pass_: "✓",
    ProbeStatus.warn: "!",
    ProbeStatus.fail: "✗",
}

_STATUS_COLOR = {
    ProbeStatus.pass_: "green",
    ProbeStatus.warn: "yellow",
    ProbeStatus.fail: "red",
}


class IDoctorReporter(Protocol):
    """Sink for doctor probe events.

    `started` fires once per run; each completed probe emits `probe_result`;
    `finished` fires once with the aggregated counts so the reporter can print
    a summary and the handler can choose its exit code.
    """

    def started(self) -> None: ...
    def probe_result(self, result: ProbeResult) -> None: ...
    def finished(self, total: int, fails: int, warns: int) -> None: ...


class StreamDoctorReporter:
    """Renders probe results as a grouped, color-coded table.

    Results buffer in memory and render once at `finished` so the table can
    group by source even when probes complete out of order.
    """

    def __init__(self, click: Any) -> None:
        self._click = click
        self._lock = threading.Lock()
        self._results: list[ProbeResult] = []

    def started(self) -> None:
        pass

    def probe_result(self, result: ProbeResult) -> None:
        with self._lock:
            self._results.append(result)

    def finished(self, total: int, fails: int, warns: int) -> None:
        with self._lock:
            results = list(self._results)

        sources: list[str] = []
        seen: set[str] = set()
        for r in results:
            if r.source not in seen:
                sources.append(r.source)
                seen.add(r.source)

        for source in sources:
            self._click.echo(f"\n[{source}]")
            for r in results:
                if r.source != source:
                    continue
                glyph = _STATUS_GLYPH[r.status]
                colored = self._click.style(glyph, fg=_STATUS_COLOR[r.status])
                line = f"  {colored} {r.name}"
                if r.message:
                    line += f" — {r.message}"
                self._click.echo(line)
                if r.status == ProbeStatus.fail and r.remediation:
                    self._click.echo(f"      → {r.remediation}")

        if fails:
            self._click.echo(
                self._click.style(
                    f"\n✗ {fails} fail / {warns} warn / {total} total",
                    fg="red",
                ),
                err=True,
            )
        elif warns:
            self._click.echo(
                self._click.style(
                    f"\n! {warns} warn / {total} total",
                    fg="yellow",
                ),
            )
        else:
            self._click.echo(
                self._click.style(f"\n✓ {total} pass", fg="green"),
            )


class JsonDoctorReporter:
    """Emits each probe result as NDJSON to stdout as it lands.

    Thread-safe: serialization happens under a lock so concurrent probe
    completions don't interleave JSON lines.
    """

    def __init__(self, click: Any) -> None:
        self._click = click
        self._lock = threading.Lock()

    def _emit(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._click.echo(json.dumps(payload))

    def started(self) -> None:
        self._emit({"type": "started"})

    def probe_result(self, result: ProbeResult) -> None:
        self._emit(
            {
                "type": "probe_result",
                "source": result.source,
                "name": result.name,
                "status": result.status.value,
                "message": result.message,
                "remediation": result.remediation,
            }
        )

    def finished(self, total: int, fails: int, warns: int) -> None:
        self._emit(
            {
                "type": "finished",
                "total": total,
                "fails": fails,
                "warns": warns,
            }
        )
