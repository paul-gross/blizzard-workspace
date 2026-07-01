from __future__ import annotations

from pathlib import Path

from tests.conftest import FakeFilesystem, FakeSubprocessRunner, make_workspace_config
from winter_cli.config.models import WorkspaceConfig
from winter_cli.core.subprocess_runner import SubprocessResult
from winter_cli.modules.doctor.models import ProbeStatus
from winter_cli.modules.doctor.workspace_probe_service import WORKSPACE_SOURCE, WorkspaceProbeService

WORKSPACE_ROOT = Path("/ws")
SCRIPT_PATH = WORKSPACE_ROOT / "context" / "project" / "doctor.sh"


def _build_config(doctor: str | None) -> WorkspaceConfig:
    return make_workspace_config(workspace_root=WORKSPACE_ROOT, doctor=doctor)


def _build_service(
    *,
    doctor: str | None = "context/project/doctor.sh",
    fs_files: dict[Path, str] | None = None,
    fs_executables: set[Path] | None = None,
    run_response: SubprocessResult | None = None,
) -> tuple[WorkspaceProbeService, FakeSubprocessRunner]:
    files = dict(fs_files or {})
    if doctor:
        files.setdefault(SCRIPT_PATH, "")
    fs = FakeFilesystem(
        files=files,
        directories={WORKSPACE_ROOT, SCRIPT_PATH.parent},
        executables=fs_executables if fs_executables is not None else ({SCRIPT_PATH} if doctor else set()),
    )
    run_responses = {str(SCRIPT_PATH.resolve()): run_response} if run_response is not None else {}
    runner = FakeSubprocessRunner(run_responses=run_responses)
    svc = WorkspaceProbeService(
        config=_build_config(doctor),
        fs=fs,
        subprocess_runner=runner,
    )
    return svc, runner


def test_returns_empty_when_doctor_field_unset() -> None:
    svc, runner = _build_service(doctor=None)
    assert svc.run() == []
    assert runner.run_calls == []


def test_parses_ndjson_lines_into_probe_results() -> None:
    stdout = '{"name": "postgres", "status": "pass", "message": "running"}\n'
    svc, _ = _build_service(run_response=SubprocessResult(0, stdout, ""))
    results = svc.run()
    assert len(results) == 1
    assert results[0].source == WORKSPACE_SOURCE
    assert results[0].name == "postgres"
    assert results[0].status == ProbeStatus.pass_


def test_missing_script_surfaces_as_fail() -> None:
    fs = FakeFilesystem(files={}, directories={WORKSPACE_ROOT})
    svc = WorkspaceProbeService(
        config=_build_config("context/project/doctor.sh"),
        fs=fs,
        subprocess_runner=FakeSubprocessRunner(),
    )
    results = svc.run()
    assert len(results) == 1
    assert results[0].status == ProbeStatus.fail
    assert "not found" in results[0].message


def test_non_executable_script_surfaces_as_fail() -> None:
    svc, runner = _build_service(fs_executables=set())
    results = svc.run()
    assert len(results) == 1
    assert results[0].status == ProbeStatus.fail
    assert "not executable" in results[0].message
    assert runner.run_calls == []


def test_non_zero_exit_appends_synthetic_fail() -> None:
    svc, _ = _build_service(run_response=SubprocessResult(2, "", "boom"))
    results = svc.run()
    assert len(results) == 1
    assert results[0].status == ProbeStatus.fail
    assert results[0].message == "boom"
