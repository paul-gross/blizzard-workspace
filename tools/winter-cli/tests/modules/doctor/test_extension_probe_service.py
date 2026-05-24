from __future__ import annotations

from pathlib import Path

from tests.conftest import FakeConfigFileReader, FakeFilesystem, FakeSubprocessRunner
from winter_cli.config.models import AdoptExtensions, WorkspaceConfig
from winter_cli.core.subprocess_runner import SubprocessResult
from winter_cli.modules.doctor.extension_probe_service import ExtensionProbeService
from winter_cli.modules.doctor.models import ProbeStatus
from winter_cli.modules.workspace.extension_manifest import EXT_MANIFEST, ExtensionManifestLoader
from winter_cli.modules.workspace.models import StandaloneRepository

WORKSPACE_ROOT = Path("/ws")
EXT_PATH = WORKSPACE_ROOT / "my-ext"
SCRIPT_PATH = EXT_PATH / "probe.sh"


def _build_config(adopt: AdoptExtensions = AdoptExtensions.winter) -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        session_prefix="t",
        main_branch="main",
        adopt_extensions=adopt,
    )


def _build_service(
    *,
    fs_files: dict[Path, str] | None = None,
    fs_executables: set[Path] | None = None,
    manifest_data: dict | None = None,
    run_response: SubprocessResult | None = None,
    adopt: AdoptExtensions = AdoptExtensions.winter,
) -> tuple[ExtensionProbeService, FakeSubprocessRunner, StandaloneRepository]:
    files = dict(fs_files or {})
    files.setdefault(EXT_PATH / EXT_MANIFEST, "")
    files.setdefault(SCRIPT_PATH, "")
    fs = FakeFilesystem(
        files=files,
        directories={EXT_PATH},
        executables=fs_executables if fs_executables is not None else {SCRIPT_PATH},
    )
    config_files = {
        EXT_PATH / EXT_MANIFEST: dict(manifest_data if manifest_data is not None else {"doctor": "probe.sh"}),
    }
    config_reader = FakeConfigFileReader(config_files)
    loader = ExtensionManifestLoader(config_file_reader=config_reader)

    run_responses: dict[str, SubprocessResult] = {}
    if run_response is not None:
        run_responses[str(SCRIPT_PATH.resolve())] = run_response
    runner = FakeSubprocessRunner(run_responses=run_responses)

    svc = ExtensionProbeService(
        config=_build_config(adopt),
        fs=fs,
        subprocess_runner=runner,
        manifest_loader=loader,
    )
    repo = StandaloneRepository(name="my-ext", path=EXT_PATH)
    return svc, runner, repo


def test_parses_each_ndjson_line_into_probe_result() -> None:
    stdout = (
        '{"name": "tea auth", "status": "pass", "message": "logged in"}\n'
        '{"name": "tmux", "status": "warn", "message": "v2.9", "remediation": "upgrade"}\n'
    )
    svc, _, repo = _build_service(run_response=SubprocessResult(0, stdout, ""))

    results = svc.run([repo])

    assert [r.name for r in results] == ["tea auth", "tmux"]
    assert results[0].status == ProbeStatus.pass_
    assert results[0].message == "logged in"
    assert results[1].status == ProbeStatus.warn
    assert results[1].remediation == "upgrade"
    assert all(r.source == "my-ext" for r in results)


def test_non_zero_exit_appends_synthetic_fail_with_stderr() -> None:
    svc, _, repo = _build_service(run_response=SubprocessResult(2, "", "boom"))

    results = svc.run([repo])

    assert len(results) == 1
    assert results[0].status == ProbeStatus.fail
    assert results[0].message == "boom"
    assert results[0].name == "doctor"


def test_missing_doctor_field_is_silently_skipped() -> None:
    svc, runner, repo = _build_service(manifest_data={})
    assert svc.run([repo]) == []
    assert runner.run_calls == []


def test_missing_manifest_skips_repo() -> None:
    # No manifest file on disk → no probes, no errors.
    fs = FakeFilesystem(files={}, directories={EXT_PATH})
    loader = ExtensionManifestLoader(config_file_reader=FakeConfigFileReader({}))
    svc = ExtensionProbeService(
        config=_build_config(),
        fs=fs,
        subprocess_runner=FakeSubprocessRunner(),
        manifest_loader=loader,
    )
    repo = StandaloneRepository(name="my-ext", path=EXT_PATH)
    assert svc.run([repo]) == []


def test_non_executable_script_reports_actionable_failure() -> None:
    svc, runner, repo = _build_service(fs_executables=set())
    results = svc.run([repo])
    assert len(results) == 1
    assert results[0].status == ProbeStatus.fail
    assert "not executable" in results[0].message
    assert runner.run_calls == []


def test_unparseable_line_becomes_warn() -> None:
    stdout = "not json\n"
    svc, _, repo = _build_service(run_response=SubprocessResult(0, stdout, ""))
    results = svc.run([repo])
    assert len(results) == 1
    assert results[0].status == ProbeStatus.warn
    assert "unparseable" in results[0].message


def test_unknown_status_becomes_warn() -> None:
    stdout = '{"name": "probe-x", "status": "bogus"}\n'
    svc, _, repo = _build_service(run_response=SubprocessResult(0, stdout, ""))
    results = svc.run([repo])
    assert len(results) == 1
    assert results[0].status == ProbeStatus.warn
    assert "unknown status" in results[0].message


def test_adopt_extensions_none_skips_everything() -> None:
    svc, runner, repo = _build_service(adopt=AdoptExtensions.none)
    assert svc.run([repo]) == []
    assert runner.run_calls == []
