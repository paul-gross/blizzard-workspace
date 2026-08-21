from __future__ import annotations

from winter_cli.modules.lint.core_lint_service import CoreLintService
from winter_cli.modules.lint.extension_lint_service import ExtensionLintService
from winter_cli.modules.lint.ignore_service import LintIgnoreService
from winter_cli.modules.lint.lint_reporter import ILintReporter
from winter_cli.modules.lint.models import LintCheckOutcome, LintScope, LintStatus, LintSummary
from winter_cli.modules.lint.workspace_lint_service import WorkspaceLintService
from winter_cli.modules.workspace.repository_factory import RepositoryFactory


class LintService:
    """Aggregates lint findings from built-in core checks, the workspace script, and every extension's script.

    A pure dispatcher: it owns discovery, ordering, and aggregation, but never
    inspects content itself — every finding originates in a check it dispatches.
    Built-in core checks run first, then the workspace script, then each
    extension-eligible repo (standalones plus project repos carrying a root
    `winter-ext.toml`) — mirroring the doctor
    `[core]`-then-`[project]`-then-extensions ordering.

    Every finding from every source passes through the single flatten below, so
    that is where `[lint.ignore]` filtering happens — one filter covering core
    checks, the workspace script, and every contributed extension check alike.
    """

    def __init__(
        self,
        core_lint_svc: CoreLintService,
        workspace_lint_svc: WorkspaceLintService,
        extension_lint_svc: ExtensionLintService,
        ignore_svc: LintIgnoreService,
        repo_factory: RepositoryFactory,
    ) -> None:
        self._core_lint_svc = core_lint_svc
        self._workspace_lint_svc = workspace_lint_svc
        self._extension_lint_svc = extension_lint_svc
        self._ignore_svc = ignore_svc
        self._repo_factory = repo_factory

    def run(self, scope: LintScope, reporter: ILintReporter) -> LintSummary:
        reporter.started(scope)

        outcomes: list[LintCheckOutcome] = []
        outcomes.extend(self._core_lint_svc.run(scope))
        workspace_outcome = self._workspace_lint_svc.run(scope)
        if workspace_outcome is not None:
            outcomes.append(workspace_outcome)

        extension_repos = self._repo_factory.get_extension_repos()
        outcomes.extend(self._extension_lint_svc.run(scope, extension_repos))

        findings = [finding for outcome in outcomes for finding in outcome.findings]
        # The one seam every finding passes through — see the class docstring.
        # `diagnostics` are findings the filter itself raises about the ignore
        # configuration — a rule that suppressed nothing, a key it could not
        # use. They are reported like any other finding, so an ignore that has
        # outlived its purpose, or never worked at all, is as visible as the
        # content it was meant to hide.
        ignore_outcome = self._ignore_svc.apply(findings, scope)
        reported = [*ignore_outcome.kept, *ignore_outcome.diagnostics]

        for finding in reported:
            reporter.finding(finding)
        for suppressed in ignore_outcome.ignored:
            reporter.ignored(suppressed.finding, suppressed.rule)

        fails = sum(1 for f in reported if f.status == LintStatus.fail)
        warns = sum(1 for f in reported if f.status == LintStatus.warn)
        summary = LintSummary(
            contributors=len(outcomes),
            total=len(reported),
            fails=fails,
            warns=warns,
            ignored=len(ignore_outcome.ignored),
        )
        reporter.finished(summary)
        return summary
