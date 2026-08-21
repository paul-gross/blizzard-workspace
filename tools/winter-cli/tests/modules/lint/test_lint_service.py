from __future__ import annotations

from pathlib import Path
from typing import Any

from winter_cli.modules.lint.lint_service import LintService
from winter_cli.modules.lint.models import (
    IgnoredFinding,
    LintCheckOutcome,
    LintFinding,
    LintIgnoreOutcome,
    LintIgnoreRule,
    LintScope,
    LintScopeKind,
    LintStatus,
    LintSummary,
)

SCOPE = LintScope(kind=LintScopeKind.all, label="all", paths=[])


def _finding(source: str, status: LintStatus, check: str = "c") -> LintFinding:
    return LintFinding(source=source, check=check, status=status)


class _FakeCoreLint:
    """A core lint service fake: `run(scope) -> list[LintCheckOutcome]`."""

    def __init__(self, outcomes: list[LintCheckOutcome]) -> None:
        self._outcomes = outcomes
        self.calls: list[LintScope] = []

    def run(self, scope: LintScope) -> list[LintCheckOutcome]:
        self.calls.append(scope)
        return list(self._outcomes)


class _FakeScalarLint:
    """A workspace-style lint service: `run(scope) -> outcome | None`."""

    def __init__(self, outcome: LintCheckOutcome | None) -> None:
        self._outcome = outcome
        self.calls: list[LintScope] = []

    def run(self, scope: LintScope) -> LintCheckOutcome | None:
        self.calls.append(scope)
        return self._outcome


class _FakeExtensionLint:
    def __init__(self, outcomes: list[LintCheckOutcome]) -> None:
        self._outcomes = outcomes
        self.calls: list[Any] = []

    def run(self, scope: LintScope, standalone_repos: Any) -> list[LintCheckOutcome]:
        self.calls.append((scope, standalone_repos))
        return list(self._outcomes)


class _FakeRepoFactory:
    def get_extension_repos(self) -> list[str]:
        return ["repo-a"]


class _PassThroughIgnore:
    """The no-rules case: every finding survives, nothing is suppressed or stale."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[LintFinding], LintScope]] = []

    def apply(self, findings: list[LintFinding], scope: LintScope) -> LintIgnoreOutcome:
        self.calls.append((list(findings), scope))
        return LintIgnoreOutcome(kept=list(findings), ignored=[], diagnostics=[])


class _SuppressingIgnore:
    """Suppresses findings whose `check` matches, and raises the given diagnostics."""

    def __init__(self, check: str, diagnostics: list[LintFinding] | None = None) -> None:
        self._check = check
        self._diagnostics = diagnostics or []
        self.rule = LintIgnoreRule(repo="app", repo_root=Path("/ws/app"), glob="**", check=check)

    def apply(self, findings: list[LintFinding], scope: LintScope) -> LintIgnoreOutcome:
        kept = [f for f in findings if f.check != self._check]
        ignored = [IgnoredFinding(finding=f, rule=self.rule) for f in findings if f.check == self._check]
        return LintIgnoreOutcome(kept=kept, ignored=ignored, diagnostics=list(self._diagnostics))


class _RecordingReporter:
    def __init__(self) -> None:
        self.started_scope: LintScope | None = None
        self.findings: list[LintFinding] = []
        self.ignored_events: list[tuple[LintFinding, LintIgnoreRule]] = []
        self.summary: LintSummary | None = None

    def started(self, scope: LintScope) -> None:
        self.started_scope = scope

    def finding(self, finding: LintFinding) -> None:
        self.findings.append(finding)

    def ignored(self, finding: LintFinding, rule: LintIgnoreRule) -> None:
        self.ignored_events.append((finding, rule))

    def finished(self, summary: LintSummary) -> None:
        self.summary = summary


def _make(
    workspace: LintCheckOutcome | None,
    extensions: list[LintCheckOutcome],
    core: LintCheckOutcome | None = None,
    ignore_svc: object | None = None,
) -> tuple[LintService, _RecordingReporter]:
    core_outcomes = [core] if core is not None else []
    svc = LintService(
        core_lint_svc=_FakeCoreLint(core_outcomes),  # type: ignore[arg-type]
        workspace_lint_svc=_FakeScalarLint(workspace),  # type: ignore[arg-type]
        extension_lint_svc=_FakeExtensionLint(extensions),  # type: ignore[arg-type]
        ignore_svc=ignore_svc or _PassThroughIgnore(),  # type: ignore[arg-type]
        repo_factory=_FakeRepoFactory(),  # type: ignore[arg-type]
    )
    return svc, _RecordingReporter()


def test_aggregates_findings_and_counts_contributors() -> None:
    workspace = LintCheckOutcome("project", [_finding("project", LintStatus.pass_)])
    ext = LintCheckOutcome("wln", [_finding("wln", LintStatus.warn), _finding("wln", LintStatus.fail)])
    svc, reporter = _make(workspace, [ext])

    summary = svc.run(SCOPE, reporter)  # type: ignore[arg-type]

    assert reporter.started_scope is SCOPE
    assert len(reporter.findings) == 3
    assert summary.contributors == 2
    assert summary.total == 3
    assert summary.fails == 1
    assert summary.warns == 1
    assert summary.exit_code == 1
    assert reporter.summary == summary


def test_no_contributors_reports_zero() -> None:
    svc, reporter = _make(None, [])
    summary = svc.run(SCOPE, reporter)  # type: ignore[arg-type]
    assert summary.contributors == 0
    assert summary.total == 0
    assert summary.exit_code == 0
    assert reporter.findings == []


def test_workspace_findings_emit_before_extension_findings() -> None:
    workspace = LintCheckOutcome("project", [_finding("project", LintStatus.pass_)])
    ext = LintCheckOutcome("wln", [_finding("wln", LintStatus.pass_)])
    svc, reporter = _make(workspace, [ext])
    svc.run(SCOPE, reporter)  # type: ignore[arg-type]
    assert [f.source for f in reporter.findings] == ["project", "wln"]


def test_core_findings_emit_before_workspace_and_extension_findings() -> None:
    core = LintCheckOutcome("core", [_finding("core", LintStatus.fail)])
    workspace = LintCheckOutcome("project", [_finding("project", LintStatus.pass_)])
    ext = LintCheckOutcome("wln", [_finding("wln", LintStatus.pass_)])
    svc, reporter = _make(workspace, [ext], core=core)
    summary = svc.run(SCOPE, reporter)  # type: ignore[arg-type]
    assert [f.source for f in reporter.findings] == ["core", "project", "wln"]
    assert summary.contributors == 3


def test_core_runs_even_with_no_workspace_or_extension_checks() -> None:
    core = LintCheckOutcome("core", [_finding("core", LintStatus.fail)])
    svc, reporter = _make(None, [], core=core)
    summary = svc.run(SCOPE, reporter)  # type: ignore[arg-type]
    assert [f.source for f in reporter.findings] == ["core"]
    assert summary.contributors == 1
    assert summary.exit_code == 1


def test_only_warnings_keeps_exit_zero() -> None:
    ext = LintCheckOutcome("wln", [_finding("wln", LintStatus.warn)])
    svc, reporter = _make(None, [ext])
    summary = svc.run(SCOPE, reporter)  # type: ignore[arg-type]
    assert summary.exit_code == 0
    assert summary.warns == 1


def test_passes_standalone_repos_to_extension_service() -> None:
    ext_svc = _FakeExtensionLint([])
    svc = LintService(
        core_lint_svc=_FakeCoreLint([]),  # type: ignore[arg-type]
        workspace_lint_svc=_FakeScalarLint(None),  # type: ignore[arg-type]
        extension_lint_svc=ext_svc,  # type: ignore[arg-type]
        ignore_svc=_PassThroughIgnore(),  # type: ignore[arg-type]
        repo_factory=_FakeRepoFactory(),  # type: ignore[arg-type]
    )
    svc.run(SCOPE, _RecordingReporter())  # type: ignore[arg-type]
    assert ext_svc.calls == [(SCOPE, ["repo-a"])]


def test_ignored_findings_are_reported_separately_and_never_fail_the_run() -> None:
    """A suppressed fail is counted, handed to the reporter as `ignored`, and drops out of `fails`."""
    ext = LintCheckOutcome(
        "wln",
        [_finding("wln", LintStatus.fail, check="link-anchors"), _finding("wln", LintStatus.fail, check="file-size")],
    )
    ignore_svc = _SuppressingIgnore("link-anchors")
    svc, reporter = _make(None, [ext], ignore_svc=ignore_svc)

    summary = svc.run(SCOPE, reporter)  # type: ignore[arg-type]

    assert [f.check for f in reporter.findings] == ["file-size"]
    assert [f.check for f, _ in reporter.ignored_events] == ["link-anchors"]
    assert reporter.ignored_events[0][1] is ignore_svc.rule
    assert (summary.total, summary.fails, summary.ignored) == (1, 1, 1)
    assert summary.exit_code == 1


def test_a_run_whose_only_failure_is_ignored_exits_clean() -> None:
    ext = LintCheckOutcome("wln", [_finding("wln", LintStatus.fail, check="link-anchors")])
    svc, reporter = _make(None, [ext], ignore_svc=_SuppressingIgnore("link-anchors"))

    summary = svc.run(SCOPE, reporter)  # type: ignore[arg-type]

    assert (summary.fails, summary.ignored, summary.exit_code) == (0, 1, 0)


def test_stale_rule_warns_are_reported_as_ordinary_findings() -> None:
    """The filter that hides findings reports on itself, through the same channel."""
    stale = LintFinding(source="core", check="lint-ignore", status=LintStatus.warn, message="stale ignore rule — …")
    svc, reporter = _make(None, [], ignore_svc=_SuppressingIgnore("link-anchors", diagnostics=[stale]))

    summary = svc.run(SCOPE, reporter)  # type: ignore[arg-type]

    assert reporter.findings == [stale]
    assert (summary.warns, summary.total) == (1, 1)


def test_ignore_filter_sees_every_source_flattened_together() -> None:
    """One filter at the flatten seam covers core, workspace, and extension findings alike."""
    ignore_svc = _PassThroughIgnore()
    core = LintCheckOutcome("core", [_finding("core", LintStatus.warn)])
    workspace = LintCheckOutcome("project", [_finding("project", LintStatus.warn)])
    ext = LintCheckOutcome("wln", [_finding("wln", LintStatus.warn)])
    svc, reporter = _make(workspace, [ext], core=core, ignore_svc=ignore_svc)

    svc.run(SCOPE, reporter)  # type: ignore[arg-type]

    handed, scope = ignore_svc.calls[0]
    assert [f.source for f in handed] == ["core", "project", "wln"]
    assert scope is SCOPE
