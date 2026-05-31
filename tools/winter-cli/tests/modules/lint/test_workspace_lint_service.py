from __future__ import annotations

from pathlib import Path

from tests.conftest import FakeFilesystem, FakeSubprocessRunner
from winter_cli.config.models import WorkspaceConfig
from winter_cli.core.subprocess_runner import SubprocessResult
from winter_cli.modules.lint.models import LintScope, LintScopeKind, LintStatus
from winter_cli.modules.lint.workspace_lint_service import WORKSPACE_SOURCE, WorkspaceLintService

WORKSPACE_ROOT = Path("/ws")
SCRIPT_PATH = WORKSPACE_ROOT / "lint.sh"

SCOPE = LintScope(kind=LintScopeKind.all, label="all", paths=[WORKSPACE_ROOT])


def _build_config(lint: list[str]) -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        session_prefix="t",
        main_branch="main",
        lint=lint,
    )


def _build_service(
    *,
    lint: list[str] | None = None,
    fs_executables: set[Path] | None = None,
    files: dict[Path, str] | None = None,
    run_response: SubprocessResult | None = None,
) -> tuple[WorkspaceLintService, FakeSubprocessRunner]:
    lint = ["lint.sh"] if lint is None else lint
    fs = FakeFilesystem(
        files=files if files is not None else {SCRIPT_PATH: ""},
        directories={WORKSPACE_ROOT},
        executables=fs_executables if fs_executables is not None else {SCRIPT_PATH},
    )
    responses: dict[str, SubprocessResult] = {}
    if run_response is not None:
        responses[str(SCRIPT_PATH.resolve())] = run_response
    runner = FakeSubprocessRunner(run_responses=responses)
    svc = WorkspaceLintService(
        config=_build_config(lint), fs=fs, subprocess_runner=runner, winter_cli_path="/usr/bin/winter"
    )
    return svc, runner


def test_passes_winter_cli_path_in_env() -> None:
    svc, runner = _build_service(run_response=SubprocessResult(0, "", ""))
    svc.run(SCOPE)
    assert runner.run_envs[-1] is not None
    assert runner.run_envs[-1]["WINTER_CLI"] == "/usr/bin/winter"


def test_no_lint_field_contributes_nothing() -> None:
    svc, runner = _build_service(lint=[])
    assert svc.run(SCOPE) is None
    assert runner.run_calls == []


def test_parses_findings_under_project_source() -> None:
    svc, _ = _build_service(run_response=SubprocessResult(0, '{"check": "c", "status": "warn"}\n', ""))
    outcome = svc.run(SCOPE)
    assert outcome is not None
    assert outcome.source == WORKSPACE_SOURCE
    assert outcome.findings[0].check == "c"
    assert outcome.findings[0].status == LintStatus.warn
    assert outcome.findings[0].source == WORKSPACE_SOURCE


def test_non_zero_exit_becomes_synthetic_fail() -> None:
    svc, _ = _build_service(run_response=SubprocessResult(1, "", "broke"))
    outcome = svc.run(SCOPE)
    assert outcome is not None
    assert outcome.findings[0].status == LintStatus.fail
    assert outcome.findings[0].message == "broke"


def test_missing_script_reports_failure() -> None:
    svc, runner = _build_service(files={})
    outcome = svc.run(SCOPE)
    assert outcome is not None
    assert outcome.findings[0].status == LintStatus.fail
    assert "not found" in outcome.findings[0].message
    assert runner.run_calls == []


def test_non_executable_script_reports_actionable_failure() -> None:
    svc, runner = _build_service(fs_executables=set())
    outcome = svc.run(SCOPE)
    assert outcome is not None
    assert "not executable" in outcome.findings[0].message
    assert runner.run_calls == []


def test_path_escaping_workspace_is_refused() -> None:
    svc, runner = _build_service(lint=["../evil.sh"])
    outcome = svc.run(SCOPE)
    assert outcome is not None
    assert outcome.findings[0].status == LintStatus.fail
    assert "escapes" in outcome.findings[0].message
    assert runner.run_calls == []


def test_runs_every_listed_script_and_aggregates_findings() -> None:
    first, second = WORKSPACE_ROOT / "a.sh", WORKSPACE_ROOT / "b.sh"
    fs = FakeFilesystem(files={first: "", second: ""}, directories={WORKSPACE_ROOT}, executables={first, second})
    runner = FakeSubprocessRunner(
        run_responses={
            str(first.resolve()): SubprocessResult(0, '{"check": "a", "status": "warn"}\n', ""),
            str(second.resolve()): SubprocessResult(0, '{"check": "b", "status": "fail"}\n', ""),
        }
    )
    svc = WorkspaceLintService(
        config=_build_config(["a.sh", "b.sh"]), fs=fs, subprocess_runner=runner, winter_cli_path="/usr/bin/winter"
    )

    outcome = svc.run(SCOPE)

    assert outcome is not None
    assert len(runner.run_calls) == 2
    assert {f.check for f in outcome.findings} == {"a", "b"}
