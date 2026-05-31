from __future__ import annotations

import json
import threading
from typing import Any, Protocol

from winter_cli.modules.lint.models import LintFinding, LintScope, LintStatus, LintSummary

_STATUS_GLYPH = {
    LintStatus.pass_: "✓",
    LintStatus.warn: "!",
    LintStatus.fail: "✗",
}

_STATUS_COLOR = {
    LintStatus.pass_: "green",
    LintStatus.warn: "yellow",
    LintStatus.fail: "red",
}


class ILintReporter(Protocol):
    """Sink for lint events.

    `started` fires once with the resolved scope; each finding emits `finding`;
    `finished` fires once with the aggregated summary so the reporter can print
    a footer and the handler can choose its exit code.
    """

    def started(self, scope: LintScope) -> None: ...
    def finding(self, finding: LintFinding) -> None: ...
    def finished(self, summary: LintSummary) -> None: ...


def _location(finding: LintFinding) -> str:
    """Render a `file:line` suffix for a finding, or empty when no file is set."""
    if not finding.file:
        return ""
    return f"{finding.file}:{finding.line}" if finding.line is not None else finding.file


class StreamLintReporter:
    """Renders findings as a grouped, color-coded table, keyed by source.

    Findings buffer in memory and render once at `finished` so the table groups
    by contributing source. When no script contributed at all, it says so
    explicitly rather than printing a misleading "0 findings" pass.
    """

    def __init__(self, click: Any) -> None:
        self._click = click
        self._lock = threading.Lock()
        self._findings: list[LintFinding] = []
        self._scope: LintScope | None = None

    def started(self, scope: LintScope) -> None:
        with self._lock:
            self._scope = scope

    def finding(self, finding: LintFinding) -> None:
        with self._lock:
            self._findings.append(finding)

    def finished(self, summary: LintSummary) -> None:
        with self._lock:
            findings = list(self._findings)
            scope = self._scope

        scope_label = scope.label if scope is not None else "?"
        self._click.echo(f"lint scope: {scope_label}")

        if summary.contributors == 0:
            self._click.echo(
                self._click.style(
                    "no lint checks are contributed by any installed extension",
                    fg="yellow",
                )
            )
            return

        for source in self._ordered_sources(findings):
            self._click.echo(f"\n[{source}]")
            for finding in findings:
                if finding.source == source:
                    self._echo_finding(finding)

        self._echo_footer(summary)

    def _ordered_sources(self, findings: list[LintFinding]) -> list[str]:
        sources: list[str] = []
        seen: set[str] = set()
        for finding in findings:
            if finding.source not in seen:
                sources.append(finding.source)
                seen.add(finding.source)
        return sources

    def _echo_finding(self, finding: LintFinding) -> None:
        glyph = self._click.style(_STATUS_GLYPH[finding.status], fg=_STATUS_COLOR[finding.status])
        line = f"  {glyph} {finding.check}"
        location = _location(finding)
        if location:
            line += f" {location}"
        if finding.message:
            line += f" — {finding.message}"
        self._click.echo(line)
        if finding.status == LintStatus.fail and finding.remediation:
            self._click.echo(f"      → {finding.remediation}")

    def _echo_footer(self, summary: LintSummary) -> None:
        if summary.fails:
            self._click.echo(
                self._click.style(
                    f"\n✗ {summary.fails} fail / {summary.warns} warn / {summary.total} finding(s)",
                    fg="red",
                ),
                err=True,
            )
        elif summary.warns:
            self._click.echo(self._click.style(f"\n! {summary.warns} warn / {summary.total} finding(s)", fg="yellow"))
        else:
            self._click.echo(self._click.style("\n✓ no findings", fg="green"))


class JsonLintReporter:
    """Emits each lint event as a NDJSON line to stdout.

    `LintService` aggregates every contributed script's outcomes before emitting,
    so findings flush after the run rather than streaming live mid-run — but each
    is still written as its own line. Thread-safe: serialization happens under a
    lock so events don't interleave. Mirrors the doctor `--json` event stream
    shape (`started` / per-result / `finished`), with `file`/`line` per finding.
    """

    def __init__(self, click: Any) -> None:
        self._click = click
        self._lock = threading.Lock()

    def _emit(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._click.echo(json.dumps(payload))

    def started(self, scope: LintScope) -> None:
        self._emit(
            {
                "type": "started",
                "scope": scope.kind.value,
                "label": scope.label,
                "paths": [str(p) for p in scope.paths],
            }
        )

    def finding(self, finding: LintFinding) -> None:
        self._emit(
            {
                "type": "finding",
                "source": finding.source,
                "check": finding.check,
                "status": finding.status.value,
                "message": finding.message,
                "file": finding.file,
                "line": finding.line,
                "remediation": finding.remediation,
            }
        )

    def finished(self, summary: LintSummary) -> None:
        self._emit(
            {
                "type": "finished",
                "contributors": summary.contributors,
                "total": summary.total,
                "fails": summary.fails,
                "warns": summary.warns,
            }
        )
