from __future__ import annotations

import json
from pathlib import Path

from winter_cli.modules.lint.lint_reporter import (
    JsonLintReporter,
    StreamLintReporter,
    SuppressedFindingFilter,
)
from winter_cli.modules.lint.models import (
    LintFinding,
    LintIgnoreRule,
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


# ── suppressed findings ──────────────────────────────────────────────────────

RULE = LintIgnoreRule(repo="app", repo_root=Path("/ws/app"), glob="templates/**", check="link-anchors")
IGNORED = LintFinding(
    source="wx",
    check="link-anchors",
    status=LintStatus.fail,
    message="dead link",
    file="app/templates/t.md",
    line=13,
    remediation="Fix the path or remove the link.",
)


def test_rule_label_reads_as_the_author_wrote_it() -> None:
    assert RULE.label == 'app [lint.ignore.checks] link-anchors = "templates/**"'
    path_rule = LintIgnoreRule(repo="app", repo_root=Path("/ws/app"), glob="results/**")
    assert path_rule.label == 'app [lint.ignore] paths = "results/**"'


def test_stream_renders_suppressed_findings_under_their_rule() -> None:
    click = _FakeClick()
    reporter = StreamLintReporter(click)
    reporter.started(SCOPE)
    reporter.ignored(IGNORED, RULE)
    reporter.finished(LintSummary(contributors=1, total=0, fails=0, warns=0, ignored=1))

    out = "\n".join(click.lines)
    assert "[ignored]" in out
    assert RULE.label in out
    assert "link-anchors app/templates/t.md:13" in out
    # A suppressed fail must not wear the live `✗` glyph, nor prompt a fix the
    # run is not asking for.
    assert "✗ link-anchors" not in out
    assert "Fix the path or remove the link." not in out


def test_stream_footer_reports_the_suppressed_count() -> None:
    click = _FakeClick()
    reporter = StreamLintReporter(click)
    reporter.started(SCOPE)
    reporter.finished(LintSummary(contributors=1, total=0, fails=0, warns=0, ignored=3))

    assert "✓ no findings / 3 ignored" in "\n".join(click.lines)


def test_stream_footer_omits_the_count_when_nothing_was_suppressed() -> None:
    click = _FakeClick()
    reporter = StreamLintReporter(click)
    reporter.started(SCOPE)
    reporter.finished(LintSummary(contributors=1, total=0, fails=0, warns=0))

    assert "ignored" not in "\n".join(click.lines)


def test_json_emits_suppressed_findings_as_their_own_event_type() -> None:
    click = _FakeClick()
    reporter = JsonLintReporter(click)
    reporter.ignored(IGNORED, RULE)
    reporter.finished(LintSummary(contributors=1, total=0, fails=0, warns=0, ignored=1))

    events = [json.loads(line) for line in click.lines]
    assert events[0]["type"] == "ignored"
    assert events[0]["check"] == "link-anchors"
    assert events[0]["rule"] == RULE.label
    assert events[1]["ignored"] == 1


def test_suppression_filter_drops_ignored_events_and_passes_the_rest() -> None:
    """Without --show-ignored the reporters never see suppressed findings — but still get the count."""
    click = _FakeClick()
    inner = StreamLintReporter(click)
    reporter = SuppressedFindingFilter(inner)

    reporter.started(SCOPE)
    reporter.ignored(IGNORED, RULE)
    reporter.finished(LintSummary(contributors=1, total=0, fails=0, warns=0, ignored=1))

    out = "\n".join(click.lines)
    assert "[ignored]" not in out
    assert "✓ no findings / 1 ignored" in out


def test_zero_contributors_still_prints_ignore_diagnostics() -> None:
    """Zero contributors no longer implies zero findings — the filter audits its own rules."""
    click = _FakeClick()
    reporter = StreamLintReporter(click)
    reporter.started(SCOPE)
    reporter.finding(
        LintFinding(
            source="core",
            check="lint-ignore",
            status=LintStatus.warn,
            message='stale ignore rule — app [lint.ignore] paths = "gone/**" matches no file in the repo',
            file="app/winter-ext.toml",
        )
    )
    reporter.finished(LintSummary(contributors=0, total=1, fails=0, warns=1, ignored=0))

    out = "\n".join(click.lines)
    assert "stale ignore rule" in out
    assert "1 warn / 1 finding(s)" in out


def test_zero_contributors_and_nothing_to_say_still_short_circuits() -> None:
    click = _FakeClick()
    reporter = StreamLintReporter(click)
    reporter.started(SCOPE)
    reporter.finished(LintSummary(contributors=0, total=0, fails=0, warns=0))

    out = "\n".join(click.lines)
    assert "no lint checks are contributed" in out
    assert "finding(s)" not in out
