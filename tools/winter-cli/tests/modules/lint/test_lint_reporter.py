from __future__ import annotations

import json

from winter_cli.modules.lint.lint_reporter import JsonLintReporter, StreamLintReporter
from winter_cli.modules.lint.models import (
    LintFinding,
    LintScope,
    LintScopeKind,
    LintStatus,
    LintSummary,
)

SCOPE = LintScope(kind=LintScopeKind.repo, label="repo: app", paths=[])


class _FakeClick:
    """Minimal click stand-in: records echo calls, passes styling through."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def echo(self, message: str, err: bool = False, **_: object) -> None:
        self.lines.append(message)

    def style(self, text: str, fg: str | None = None) -> str:
        return text


def test_stream_groups_by_source_and_renders_file_line() -> None:
    click = _FakeClick()
    reporter = StreamLintReporter(click)
    reporter.started(SCOPE)
    reporter.finding(
        LintFinding(
            source="wln",
            check="path-notation",
            status=LintStatus.fail,
            message="bad ref",
            file="context/x.md",
            line=12,
            remediation="use the prefix",
        )
    )
    reporter.finished(LintSummary(contributors=1, total=1, fails=1, warns=0))

    out = "\n".join(click.lines)
    assert "lint scope: repo: app" in out
    assert "[wln]" in out
    assert "path-notation context/x.md:12 — bad ref" in out
    assert "→ use the prefix" in out
    assert "1 fail" in out


def test_stream_says_so_when_nothing_contributed() -> None:
    click = _FakeClick()
    reporter = StreamLintReporter(click)
    reporter.started(SCOPE)
    reporter.finished(LintSummary(contributors=0, total=0, fails=0, warns=0))

    out = "\n".join(click.lines)
    assert "no lint checks are contributed" in out
    assert "no findings" not in out


def test_json_emits_started_finding_finished_stream() -> None:
    click = _FakeClick()
    reporter = JsonLintReporter(click)
    reporter.started(SCOPE)
    reporter.finding(LintFinding(source="wln", check="c", status=LintStatus.warn, message="m", file="f.md", line=3))
    reporter.finished(LintSummary(contributors=1, total=1, fails=0, warns=1))

    events = [json.loads(line) for line in click.lines]
    assert [e["type"] for e in events] == ["started", "finding", "finished"]
    assert events[0]["scope"] == "repo"
    assert events[1]["check"] == "c"
    assert events[1]["file"] == "f.md"
    assert events[1]["line"] == 3
    assert events[2]["contributors"] == 1
    assert events[2]["warns"] == 1
