from __future__ import annotations

from pathlib import Path

from tests.conftest import FakeConfigFileReader, FakeFilesystem, FakeSubprocessRunner, make_workspace_config
from winter_cli.config.models import AdoptExtensions, WorkspaceConfig
from winter_cli.core.subprocess_runner import SubprocessResult
from winter_cli.modules.lint.extension_lint_service import ExtensionLintService
from winter_cli.modules.lint.models import LintScope, LintScopeKind, LintStatus
from winter_cli.modules.workspace.extension_manifest import EXT_MANIFEST, ExtensionManifestLoader
from winter_cli.modules.workspace.models import StandaloneRepository

WORKSPACE_ROOT = Path("/ws")
EXT_PATH = WORKSPACE_ROOT / "my-ext"
SCRIPT_PATH = EXT_PATH / "lint.sh"

SCOPE = LintScope(kind=LintScopeKind.all, label="all", paths=[WORKSPACE_ROOT])


def _build_config(adopt: AdoptExtensions = AdoptExtensions.winter) -> WorkspaceConfig:
    return make_workspace_config(workspace_root=WORKSPACE_ROOT, adopt_extensions=adopt)


def _build_service(
    *,
    fs_executables: set[Path] | None = None,
    manifest_data: dict | None = None,
    run_response: SubprocessResult | None = None,
    adopt: AdoptExtensions = AdoptExtensions.winter,
) -> tuple[ExtensionLintService, FakeSubprocessRunner, StandaloneRepository]:
    files = {EXT_PATH / EXT_MANIFEST: "", SCRIPT_PATH: ""}
    fs = FakeFilesystem(
        files=files,
        directories={EXT_PATH},
        executables=fs_executables if fs_executables is not None else {SCRIPT_PATH},
    )
    config_files = {
        EXT_PATH / EXT_MANIFEST: dict(manifest_data if manifest_data is not None else {"lint": "lint.sh"}),
    }
    loader = ExtensionManifestLoader(config_file_reader=FakeConfigFileReader(config_files))

    run_responses: dict[str, SubprocessResult] = {}
    if run_response is not None:
        run_responses[str(SCRIPT_PATH.resolve())] = run_response
    runner = FakeSubprocessRunner(run_responses=run_responses)

    svc = ExtensionLintService(
        config=_build_config(adopt),
        fs=fs,
        subprocess_runner=runner,
        manifest_loader=loader,
        winter_cli_path="/usr/bin/winter",
    )
    repo = StandaloneRepository(name="my-ext", path=EXT_PATH)
    return svc, runner, repo


def test_parses_findings_into_one_outcome_per_extension() -> None:
    stdout = (
        '{"check": "path-notation", "status": "pass"}\n'
        '{"check": "frontmatter", "status": "fail", "file": "a.md", "line": 3}\n'
    )
    svc, _, repo = _build_service(run_response=SubprocessResult(0, stdout, ""))

    outcomes = svc.run(SCOPE, [repo])

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.source == "my-ext"
    assert [f.check for f in outcome.findings] == ["path-notation", "frontmatter"]
    assert outcome.findings[1].file == "a.md"
    assert outcome.findings[1].line == 3
    assert all(f.source == "my-ext" for f in outcome.findings)


def test_runs_every_listed_script_in_the_manifest() -> None:
    first, second = EXT_PATH / "a.sh", EXT_PATH / "b.sh"
    fs = FakeFilesystem(
        files={EXT_PATH / EXT_MANIFEST: "", first: "", second: ""},
        directories={EXT_PATH},
        executables={first, second},
    )
    loader = ExtensionManifestLoader(
        config_file_reader=FakeConfigFileReader({EXT_PATH / EXT_MANIFEST: {"lint": ["a.sh", "b.sh"]}})
    )
    runner = FakeSubprocessRunner(
        run_responses={
            str(first.resolve()): SubprocessResult(0, '{"check": "a", "status": "warn"}\n', ""),
            str(second.resolve()): SubprocessResult(0, '{"check": "b", "status": "warn"}\n', ""),
        }
    )
    svc = ExtensionLintService(
        config=_build_config(AdoptExtensions.winter),
        fs=fs,
        subprocess_runner=runner,
        manifest_loader=loader,
        winter_cli_path="/usr/bin/winter",
    )
    repo = StandaloneRepository(name="my-ext", path=EXT_PATH)

    outcomes = svc.run(SCOPE, [repo])

    assert len(outcomes) == 1
    assert len(runner.run_calls) == 2
    assert {f.check for f in outcomes[0].findings} == {"a", "b"}


def test_passes_winter_cli_path_in_env() -> None:
    svc, runner, repo = _build_service(run_response=SubprocessResult(0, "", ""))
    svc.run(SCOPE, [repo])
    assert runner.run_envs[-1] is not None
    assert runner.run_envs[-1]["WINTER_CLI"] == "/usr/bin/winter"


def test_clean_run_with_no_findings_still_counts_as_a_contributor() -> None:
    svc, _, repo = _build_service(run_response=SubprocessResult(0, "", ""))
    outcomes = svc.run(SCOPE, [repo])
    assert len(outcomes) == 1
    assert outcomes[0].findings == []


def test_non_zero_exit_becomes_synthetic_fail() -> None:
    svc, _, repo = _build_service(run_response=SubprocessResult(2, "", "boom"))
    outcomes = svc.run(SCOPE, [repo])
    assert len(outcomes) == 1
    assert [f.status for f in outcomes[0].findings] == [LintStatus.fail]
    assert outcomes[0].findings[0].message == "boom"


def test_missing_lint_field_contributes_no_outcome() -> None:
    svc, runner, repo = _build_service(manifest_data={"doctor": "doctor.sh"})
    assert svc.run(SCOPE, [repo]) == []
    assert runner.run_calls == []


def test_non_executable_script_reports_actionable_failure() -> None:
    svc, runner, repo = _build_service(fs_executables=set())
    outcomes = svc.run(SCOPE, [repo])
    assert len(outcomes) == 1
    assert outcomes[0].findings[0].status == LintStatus.fail
    assert "not executable" in outcomes[0].findings[0].message
    assert runner.run_calls == []


def test_adopt_extensions_none_skips_everything() -> None:
    svc, runner, repo = _build_service(adopt=AdoptExtensions.none)
    assert svc.run(SCOPE, [repo]) == []
    assert runner.run_calls == []


def test_missing_manifest_skips_repo() -> None:
    fs = FakeFilesystem(files={}, directories={EXT_PATH})
    loader = ExtensionManifestLoader(config_file_reader=FakeConfigFileReader({}))
    svc = ExtensionLintService(
        config=_build_config(),
        fs=fs,
        subprocess_runner=FakeSubprocessRunner(),
        manifest_loader=loader,
        winter_cli_path="/usr/bin/winter",
    )
    repo = StandaloneRepository(name="my-ext", path=EXT_PATH)
    assert svc.run(SCOPE, [repo]) == []
