from __future__ import annotations

import json
import threading
from typing import Any, Protocol

from winter_cli.modules.lint.models import LintFinding, LintIgnoreRule, LintScope, LintStatus, LintSummary

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

    `started` fires once with the resolved scope; each surviving finding emits
    `finding` and each `[lint.ignore]`-suppressed one emits `ignored` with the
    rule that matched; `finished` fires once with the aggregated summary so the
    reporter can print a footer and the handler can choose its exit code.
    """

    def started(self, scope: LintScope) -> None: ...
    def finding(self, finding: LintFinding) -> None: ...
    def ignored(self, finding: LintFinding, rule: LintIgnoreRule) -> None: ...
    def finished(self, summary: LintSummary) -> None: ...


def _finding_payload(finding: LintFinding) -> dict[str, Any]:
    """The JSON body shared by the `finding` and `ignored` event types."""
    return {
        "source": finding.source,
        "check": finding.check,
        "status": finding.status.value,
        "message": finding.message,
        "file": finding.file,
        "line": finding.line,
        "remediation": finding.remediation,
    }


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

    Suppressed findings render last, in their own `[ignored]` block, each under
    the rule that silenced it — separate from the source groups so it is never
    ambiguous which findings the run actually stands behind. They arrive here
    only when the caller asked for them (see `SuppressedFindingFilter`); the
    footer counts them either way.
    """

    def __init__(self, click: Any) -> None:
        self._click = click
        self._lock = threading.Lock()
        self._findings: list[LintFinding] = []
        self._ignored: list[tuple[LintFinding, LintIgnoreRule]] = []
        self._scope: LintScope | None = None

    def started(self, scope: LintScope) -> None:
        with self._lock:
            self._scope = scope

    def finding(self, finding: LintFinding) -> None:
        with self._lock:
            self._findings.append(finding)

    def ignored(self, finding: LintFinding, rule: LintIgnoreRule) -> None:
        with self._lock:
            self._ignored.append((finding, rule))

    def finished(self, summary: LintSummary) -> None:
        with self._lock:
            findings = list(self._findings)
            ignored = list(self._ignored)
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
            # Zero contributors no longer implies zero findings: the ignore
            # filter audits its own rules whether or not a check ran, so a stale
            # or unusable rule is reported here too. Return only when there is
            # genuinely nothing to say, or the one output the spec calls most
            # important is the one that gets dropped.
            if not findings and not ignored and not summary.ignored:
                return

        for source in self._ordered_sources(findings):
            self._click.echo(f"\n[{source}]")
            for finding in findings:
                if finding.source == source:
                    self._echo_finding(finding)

        self._echo_ignored(ignored)
        self._echo_footer(summary)

    def _ordered_sources(self, findings: list[LintFinding]) -> list[str]:
        sources: list[str] = []
        seen: set[str] = set()
        for finding in findings:
            if finding.source not in seen:
                sources.append(finding.source)
                seen.add(finding.source)
        return sources

    def _echo_finding(self, finding: LintFinding, indent: int = 2, glyph: str | None = None) -> None:
        """Render one finding. A suppressed one passes its own glyph, so a `✗` the
        run is not acting on never appears as if it were live."""
        symbol = glyph if glyph is not None else _STATUS_GLYPH[finding.status]
        styled = self._click.style(symbol, fg=_STATUS_COLOR[finding.status])
        pad = " " * indent
        line = f"{pad}{styled} {finding.check}"
        location = _location(finding)
        if location:
            line += f" {location}"
        if finding.message:
            line += f" — {finding.message}"
        self._click.echo(line)
        if glyph is None and finding.status == LintStatus.fail and finding.remediation:
            self._click.echo(f"{pad}    → {finding.remediation}")

    def _echo_ignored(self, ignored: list[tuple[LintFinding, LintIgnoreRule]]) -> None:
        """Re-print suppressed findings, grouped under the rule that matched each."""
        if not ignored:
            return
        self._click.echo("\n[ignored]")
        for rule in self._ordered_rules(ignored):
            self._click.echo(f"  {self._click.style(rule.label, fg='cyan')}")
            for finding, matched in ignored:
                if matched == rule:
                    self._echo_finding(finding, indent=4, glyph="-")

    def _ordered_rules(self, ignored: list[tuple[LintFinding, LintIgnoreRule]]) -> list[LintIgnoreRule]:
        rules: list[LintIgnoreRule] = []
        seen: set[LintIgnoreRule] = set()
        for _, rule in ignored:
            if rule not in seen:
                rules.append(rule)
                seen.add(rule)
        return rules

    def _echo_footer(self, summary: LintSummary) -> None:
        suppressed = f" / {summary.ignored} ignored" if summary.ignored else ""
        if summary.fails:
            self._click.echo(
                self._click.style(
                    f"\n✗ {summary.fails} fail / {summary.warns} warn / {summary.total} finding(s){suppressed}",
                    fg="red",
                ),
                err=True,
            )
        elif summary.warns:
            self._click.echo(
                self._click.style(f"\n! {summary.warns} warn / {summary.total} finding(s){suppressed}", fg="yellow")
            )
        else:
            self._click.echo(self._click.style(f"\n✓ no findings{suppressed}", fg="green"))


class JsonLintReporter:
    """Emits each lint event as a NDJSON line to stdout.

    `LintService` aggregates every contributed script's outcomes before emitting,
    so findings flush after the run rather than streaming live mid-run — but each
    is still written as its own line. Thread-safe: serialization happens under a
    lock so events don't interleave. Mirrors the doctor `--json` event stream
    shape (`started` / per-result / `finished`), with `file`/`line` per finding.

    A `[lint.ignore]`-suppressed finding is emitted as its own `ignored` event
    type — same payload plus the `rule` that matched — rather than as a flag on
    `finding`, so a consumer that only knows the original event types keeps
    reading exactly the findings the run stands behind.
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
        self._emit({**_finding_payload(finding), "type": "finding"})

    def ignored(self, finding: LintFinding, rule: LintIgnoreRule) -> None:
        self._emit({**_finding_payload(finding), "type": "ignored", "rule": rule.label})

    def finished(self, summary: LintSummary) -> None:
        self._emit(
            {
                "type": "finished",
                "contributors": summary.contributors,
                "total": summary.total,
                "fails": summary.fails,
                "warns": summary.warns,
                "ignored": summary.ignored,
            }
        )


class SuppressedFindingFilter:
    """Reporter wrapper that drops `ignored` events, for runs without `--show-ignored`.

    Keeps the two reporters free of a display flag: they render whatever they
    are handed, and the handler decides whether suppressed findings are handed
    over at all. The summary still carries the ignored *count* through
    untouched, so a default run always says how much it hid even when it does
    not say what.
    """

    def __init__(self, inner: ILintReporter) -> None:
        self._inner = inner

    def started(self, scope: LintScope) -> None:
        self._inner.started(scope)

    def finding(self, finding: LintFinding) -> None:
        self._inner.finding(finding)

    def ignored(self, finding: LintFinding, rule: LintIgnoreRule) -> None:
        pass

    def finished(self, summary: LintSummary) -> None:
        self._inner.finished(summary)


def _conforms_suppressed_finding_filter(x: SuppressedFindingFilter) -> ILintReporter:
    return x
