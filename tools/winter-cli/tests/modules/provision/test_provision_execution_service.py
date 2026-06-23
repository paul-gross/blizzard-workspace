from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import FakeFilesystem, FakeSubprocessRunner
from winter_cli.config.models import AdoptExtensions, ProjectRepositoryConfig, WorkspaceConfig
from winter_cli.modules.provision.execution_service import (
    IProvisionOutputSink,
    ProvisionExecutionService,
)
from winter_cli.modules.provision.manifest import ProvisionHandler, ProvisionScope
from winter_cli.modules.workspace.extension_manifest import ExtensionManifestLoader
from winter_cli.modules.workspace.repository_factory import RepositoryFactory

WORKSPACE_ROOT = Path("/ws")
ENV_NAME = "alpha"
ENV_ROOT = WORKSPACE_ROOT / ENV_NAME


# ── Fakes & helpers ───────────────────────────────────────────────────────────


class _InMemoryRegistry:
    """Minimal IEnvIndexRegistry for tests."""

    def __init__(self, assignments: dict[str, int] | None = None) -> None:
        self._data: dict[str, int] = dict(assignments or {})

    def get_index(self, name: str) -> int | None:
        return self._data.get(name)

    def all_assignments(self) -> dict[str, int]:
        return dict(self._data)

    def assign(self, name: str, index: int) -> None:
        self._data[name] = index

    def remove(self, name: str) -> None:
        self._data.pop(name, None)


class _FakeConfigFileReader:
    def __init__(self, contents: dict[Path, dict]) -> None:
        self._contents = contents

    def load(self, path: Path) -> dict:
        if path not in self._contents:
            raise FileNotFoundError(path)
        return self._contents[path]


class FakeProvisionOutputSink:
    """Records every IProvisionOutputSink event for assertion."""

    def __init__(self) -> None:
        self.started: list[tuple[str, str, Path]] = []
        self.output_lines: list[tuple[str, str]] = []
        self.completed: list[tuple[str, str, int]] = []
        self.errors: list[tuple[str, str]] = []

    def execution_started(self, label: str, action: str, cwd: Path) -> None:
        self.started.append((label, action, cwd))

    def execution_output_line(self, label: str, line: str) -> None:
        self.output_lines.append((label, line))

    def execution_completed(self, label: str, action: str, exit_code: int) -> None:
        self.completed.append((label, action, exit_code))

    def execution_error(self, label: str, error: str) -> None:
        self.errors.append((label, error))


def _make_config(
    *,
    project_repos: list[ProjectRepositoryConfig] | None = None,
    base_port: int = 4000,
    ports_per_env: int = 20,
) -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        session_prefix="t",
        main_branch="main",
        adopt_extensions=AdoptExtensions.winter,
        base_port=base_port,
        ports_per_env=ports_per_env,
        project_repos=project_repos or [],
    )


def _make_service(
    config: WorkspaceConfig,
    fs: FakeFilesystem,
    config_files: dict[Path, dict],
    subprocess: FakeSubprocessRunner,
    registry: _InMemoryRegistry | None = None,
) -> ProvisionExecutionService:
    loader = ExtensionManifestLoader(config_file_reader=_FakeConfigFileReader(config_files))
    repo_factory = RepositoryFactory(config=config)
    return ProvisionExecutionService(
        config=config,
        fs=fs,
        subprocess_runner=subprocess,
        manifest_loader=loader,
        repo_factory=repo_factory,
        registry=registry,
    )


def _project_handler(
    subtarget: str = "dependency",
    scope: ProvisionScope = ProvisionScope.workspace,
    apply: str = "scripts/apply.sh",
    destroy: str | None = None,
    reset: str | None = None,
) -> ProvisionHandler:
    return ProvisionHandler(
        subtarget=subtarget,
        scope=scope,
        apply=apply,
        source="project",
        destroy=destroy,
        reset=reset,
    )


def _setup_apply_script(
    fs: FakeFilesystem,
    source_root: Path,
    script_rel: str,
    *,
    executable: bool = True,
) -> Path:
    """Register a script in the fake filesystem under source_root."""
    script_path = (source_root / script_rel).resolve()
    fs.files[script_path] = ""
    fs.directories.add(script_path.parent)
    if executable:
        fs.executables.add(script_path)
    return script_path


# ── workspace scope ───────────────────────────────────────────────────────────


def test_workspace_scope_apply_cwd_is_workspace_root() -> None:
    """apply at workspace scope runs with cwd = workspace root."""
    config = _make_config()
    fs = FakeFilesystem()
    script_path = _setup_apply_script(fs, WORKSPACE_ROOT, "scripts/apply.sh")

    subprocess = FakeSubprocessRunner(
        popen_responses={str(script_path): ([], 0)},
    )
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, subprocess)

    handler = _project_handler(scope=ProvisionScope.workspace)
    result = svc.run_handler(handler, "apply", ENV_NAME, sink)

    assert result.ok
    assert len(result.runs) == 1
    assert result.runs[0].cwd == WORKSPACE_ROOT
    assert subprocess.popen_calls[0][1] == WORKSPACE_ROOT


def test_workspace_scope_apply_base_env_no_env_trio() -> None:
    """workspace scope: env contains WINTER_WORKSPACE_DIR but NOT WINTER_ENV*."""
    config = _make_config()
    fs = FakeFilesystem()
    script_path = _setup_apply_script(fs, WORKSPACE_ROOT, "scripts/apply.sh")

    subprocess = FakeSubprocessRunner(
        popen_responses={str(script_path): ([], 0)},
    )
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, subprocess)

    handler = _project_handler(scope=ProvisionScope.workspace)
    svc.run_handler(handler, "apply", ENV_NAME, sink)

    env = subprocess.popen_envs[0]
    assert env["WINTER_WORKSPACE_DIR"] == str(WORKSPACE_ROOT)
    assert "WINTER_ENV" not in env
    assert "WINTER_ENV_INDEX" not in env
    assert "WINTER_PORT_BASE" not in env


def test_workspace_scope_apply_streams_output_to_sink() -> None:
    """Script stdout lines are forwarded to the sink."""
    config = _make_config()
    fs = FakeFilesystem()
    script_path = _setup_apply_script(fs, WORKSPACE_ROOT, "scripts/apply.sh")

    subprocess = FakeSubprocessRunner(
        popen_responses={str(script_path): (["line one", "line two"], 0)},
    )
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, subprocess)

    handler = _project_handler(scope=ProvisionScope.workspace)
    svc.run_handler(handler, "apply", ENV_NAME, sink)

    lines = [line for _, line in sink.output_lines]
    assert "line one" in lines
    assert "line two" in lines


# ── feature-environment scope ─────────────────────────────────────────────────


def test_feature_environment_scope_cwd_is_env_root() -> None:
    """apply at feature-environment scope: cwd = <workspace>/<env>."""
    config = _make_config()
    fs = FakeFilesystem()
    script_path = _setup_apply_script(fs, WORKSPACE_ROOT, "scripts/apply.sh")

    subprocess = FakeSubprocessRunner(
        popen_responses={str(script_path): ([], 0)},
    )
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, subprocess)

    handler = _project_handler(scope=ProvisionScope.feature_environment)
    result = svc.run_handler(handler, "apply", ENV_NAME, sink)

    assert result.ok
    assert result.runs[0].cwd == ENV_ROOT
    assert subprocess.popen_calls[0][1] == ENV_ROOT


def test_feature_environment_scope_env_trio_present() -> None:
    """apply at feature-environment scope: WINTER_ENV/WINTER_ENV_INDEX/WINTER_PORT_BASE set."""
    config = _make_config(base_port=4000, ports_per_env=20)
    fs = FakeFilesystem()
    script_path = _setup_apply_script(fs, WORKSPACE_ROOT, "scripts/apply.sh")

    # alpha is alias index 1 → port base 4000 + 1*20 = 4020
    subprocess = FakeSubprocessRunner(
        popen_responses={str(script_path): ([], 0)},
    )
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, subprocess)

    handler = _project_handler(scope=ProvisionScope.feature_environment)
    svc.run_handler(handler, "apply", ENV_NAME, sink)

    env = subprocess.popen_envs[0]
    assert env["WINTER_ENV"] == "alpha"
    assert env["WINTER_ENV_INDEX"] == "1"  # alpha is alias 1
    assert env["WINTER_PORT_BASE"] == "4020"


def test_feature_environment_scope_env_trio_uses_registry_index() -> None:
    """WINTER_ENV_INDEX agrees with the registry-persisted index, not the suggestion."""
    config = _make_config(base_port=5000, ports_per_env=30)
    fs = FakeFilesystem()
    script_path = _setup_apply_script(fs, WORKSPACE_ROOT, "scripts/apply.sh")

    registry = _InMemoryRegistry({"alpha": 7})
    subprocess = FakeSubprocessRunner(
        popen_responses={str(script_path): ([], 0)},
    )
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, subprocess, registry=registry)

    handler = _project_handler(scope=ProvisionScope.feature_environment)
    svc.run_handler(handler, "apply", ENV_NAME, sink)

    env = subprocess.popen_envs[0]
    assert env["WINTER_ENV_INDEX"] == "7"
    assert env["WINTER_PORT_BASE"] == str(5000 + 7 * 30)


# ── feature-worktree scope ────────────────────────────────────────────────────


def test_feature_worktree_scope_runs_once_per_project_repo() -> None:
    """apply at feature-worktree scope: one invocation per project repo."""
    config = _make_config(
        project_repos=[
            ProjectRepositoryConfig(name="app", url="git@example.com:org/app.git"),
            ProjectRepositoryConfig(name="api", url="git@example.com:org/api.git"),
        ]
    )
    fs = FakeFilesystem()
    script_path = _setup_apply_script(fs, WORKSPACE_ROOT, "scripts/apply.sh")

    subprocess = FakeSubprocessRunner(
        popen_responses={str(script_path): ([], 0)},
    )
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, subprocess)

    handler = _project_handler(scope=ProvisionScope.feature_worktree)
    result = svc.run_handler(handler, "apply", ENV_NAME, sink)

    assert result.ok
    assert len(result.runs) == 2

    cwds = {r.cwd for r in result.runs}
    assert cwds == {
        WORKSPACE_ROOT / ENV_NAME / "app",
        WORKSPACE_ROOT / ENV_NAME / "api",
    }


def test_feature_worktree_scope_correct_cwd_per_repo() -> None:
    """Each worktree run uses <workspace>/<env>/<repo.name> as cwd."""
    config = _make_config(
        project_repos=[
            ProjectRepositoryConfig(name="myrepo", url="git@example.com:org/myrepo.git"),
        ]
    )
    fs = FakeFilesystem()
    script_path = _setup_apply_script(fs, WORKSPACE_ROOT, "scripts/apply.sh")

    subprocess = FakeSubprocessRunner(
        popen_responses={str(script_path): ([], 0)},
    )
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, subprocess)

    handler = _project_handler(scope=ProvisionScope.feature_worktree)
    result = svc.run_handler(handler, "apply", ENV_NAME, sink)

    assert result.runs[0].cwd == WORKSPACE_ROOT / ENV_NAME / "myrepo"
    assert subprocess.popen_calls[0][1] == WORKSPACE_ROOT / ENV_NAME / "myrepo"


def test_feature_worktree_scope_env_trio_present_for_each_run() -> None:
    """WINTER_ENV is set for each worktree invocation."""
    config = _make_config(
        project_repos=[
            ProjectRepositoryConfig(name="repo-a", url="git@example.com:org/repo-a.git"),
            ProjectRepositoryConfig(name="repo-b", url="git@example.com:org/repo-b.git"),
        ]
    )
    fs = FakeFilesystem()
    script_path = _setup_apply_script(fs, WORKSPACE_ROOT, "scripts/apply.sh")

    subprocess = FakeSubprocessRunner(
        popen_responses={str(script_path): ([], 0)},
    )
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, subprocess)

    handler = _project_handler(scope=ProvisionScope.feature_worktree)
    svc.run_handler(handler, "apply", ENV_NAME, sink)

    assert len(subprocess.popen_envs) == 2
    for env in subprocess.popen_envs:
        assert env["WINTER_ENV"] == ENV_NAME
        assert "WINTER_ENV_INDEX" in env
        assert "WINTER_PORT_BASE" in env


# ── exit-code propagation ─────────────────────────────────────────────────────


def test_non_zero_exit_code_captured_in_result() -> None:
    """A non-zero exit code is recorded in SingleRunResult.exit_code and ok=False."""
    config = _make_config()
    fs = FakeFilesystem()
    script_path = _setup_apply_script(fs, WORKSPACE_ROOT, "scripts/apply.sh")

    subprocess = FakeSubprocessRunner(
        popen_responses={str(script_path): (["error output"], 1)},
    )
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, subprocess)

    handler = _project_handler(scope=ProvisionScope.workspace)
    result = svc.run_handler(handler, "apply", ENV_NAME, sink)

    assert not result.ok
    assert len(result.runs) == 1
    assert result.runs[0].exit_code == 1
    assert sink.completed[0][2] == 1


def test_non_zero_exit_among_worktrees_propagates() -> None:
    """For feature-worktree scope, a failing run makes ok=False even if others pass."""
    config = _make_config(
        project_repos=[
            ProjectRepositoryConfig(name="app", url="git@example.com:org/app.git"),
            ProjectRepositoryConfig(name="api", url="git@example.com:org/api.git"),
        ]
    )
    fs = FakeFilesystem()
    script_path = _setup_apply_script(fs, WORKSPACE_ROOT, "scripts/apply.sh")

    # Return different exit codes on successive calls
    call_count: list[int] = [0]
    original_popen = FakeSubprocessRunner(
        popen_responses={str(script_path): ([], 1)},  # always exit 1 for simplicity
    )
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, original_popen)

    handler = _project_handler(scope=ProvisionScope.feature_worktree)
    result = svc.run_handler(handler, "apply", ENV_NAME, sink)

    assert not result.ok


# ── path-escape guard ─────────────────────────────────────────────────────────


def test_path_escape_script_is_rejected() -> None:
    """A script path that escapes the source root is rejected without running."""
    config = _make_config()
    fs = FakeFilesystem()
    # Don't register the escaped path as a real file — it shouldn't be reached.
    subprocess = FakeSubprocessRunner()
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, subprocess)

    # "../../../etc/passwd" escapes from WORKSPACE_ROOT
    handler = _project_handler(apply="../../../etc/passwd", scope=ProvisionScope.workspace)
    result = svc.run_handler(handler, "apply", ENV_NAME, sink)

    assert not result.ok
    assert result.error is not None
    assert "escapes" in result.error
    assert not subprocess.popen_calls


def test_missing_script_file_is_rejected() -> None:
    """A script declared but not present on disk produces an error result."""
    config = _make_config()
    fs = FakeFilesystem()
    # Do NOT add the script to fs.files
    subprocess = FakeSubprocessRunner()
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, subprocess)

    handler = _project_handler(scope=ProvisionScope.workspace)
    result = svc.run_handler(handler, "apply", ENV_NAME, sink)

    assert not result.ok
    assert result.error is not None
    assert "not found" in result.error
    assert not subprocess.popen_calls


def test_non_executable_script_is_rejected() -> None:
    """A script that exists but is not executable produces an error result."""
    config = _make_config()
    fs = FakeFilesystem()
    script_path = _setup_apply_script(fs, WORKSPACE_ROOT, "scripts/apply.sh", executable=False)

    subprocess = FakeSubprocessRunner()
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, subprocess)

    handler = _project_handler(scope=ProvisionScope.workspace)
    result = svc.run_handler(handler, "apply", ENV_NAME, sink)

    assert not result.ok
    assert result.error is not None
    assert "not executable" in result.error
    assert not subprocess.popen_calls


# ── destroy / reset actions ───────────────────────────────────────────────────


def test_destroy_action_runs_destroy_script() -> None:
    """action='destroy' runs handler.destroy, not handler.apply."""
    config = _make_config()
    fs = FakeFilesystem()
    apply_path = _setup_apply_script(fs, WORKSPACE_ROOT, "scripts/apply.sh")
    destroy_path = _setup_apply_script(fs, WORKSPACE_ROOT, "scripts/destroy.sh")

    subprocess = FakeSubprocessRunner(
        popen_responses={
            str(apply_path): ([], 0),
            str(destroy_path): (["destroyed"], 0),
        }
    )
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, subprocess)

    handler = _project_handler(scope=ProvisionScope.workspace, destroy="scripts/destroy.sh")
    result = svc.run_handler(handler, "destroy", ENV_NAME, sink)

    assert result.ok
    # Only destroy should have been invoked
    assert str(destroy_path) in subprocess.popen_calls[0][0]


def test_reset_action_runs_reset_script() -> None:
    """action='reset' runs handler.reset, not handler.apply or handler.destroy."""
    config = _make_config()
    fs = FakeFilesystem()
    apply_path = _setup_apply_script(fs, WORKSPACE_ROOT, "scripts/apply.sh")
    reset_path = _setup_apply_script(fs, WORKSPACE_ROOT, "scripts/reset.sh")

    subprocess = FakeSubprocessRunner(
        popen_responses={
            str(apply_path): ([], 0),
            str(reset_path): (["reset"], 0),
        }
    )
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, subprocess)

    handler = _project_handler(
        scope=ProvisionScope.workspace,
        destroy="scripts/destroy.sh",
        reset="scripts/reset.sh",
    )
    result = svc.run_handler(handler, "reset", ENV_NAME, sink)

    assert result.ok
    assert str(reset_path) in subprocess.popen_calls[0][0]


def test_destroy_action_returns_error_when_no_destroy_script() -> None:
    """When handler.destroy is None, destroy action returns an error (caller violated contract)."""
    config = _make_config()
    fs = FakeFilesystem()
    _setup_apply_script(fs, WORKSPACE_ROOT, "scripts/apply.sh")

    subprocess = FakeSubprocessRunner()
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, subprocess)

    # handler has no destroy script
    handler = _project_handler(scope=ProvisionScope.workspace, destroy=None)
    result = svc.run_handler(handler, "destroy", ENV_NAME, sink)

    assert not result.ok
    assert result.error is not None
    assert not subprocess.popen_calls


# ── extension-source handlers ─────────────────────────────────────────────────


def _setup_extension(
    fs: FakeFilesystem,
    config_files: dict[Path, dict],
    ext_name: str,
    script_rel: str,
    *,
    executable: bool = True,
) -> tuple[Path, Path]:
    """Register an extension repo with a provision script; returns (ext_path, script_path)."""
    ext_path = WORKSPACE_ROOT / ext_name
    fs.directories.add(ext_path)
    manifest_path = ext_path / "winter-ext.toml"
    fs.files[manifest_path] = ""
    config_files[manifest_path] = {"name": ext_name}
    script_path = _setup_apply_script(fs, ext_path, script_rel, executable=executable)
    return ext_path, script_path


def test_extension_source_script_runs_from_extension_root() -> None:
    """Extension handler: source root is the extension directory, not workspace root."""
    from winter_cli.config.models import StandaloneRepositoryConfig

    config = WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        session_prefix="t",
        main_branch="main",
        adopt_extensions=AdoptExtensions.winter,
        standalone_repos=[
            StandaloneRepositoryConfig(name="my-ext", url="git@example.com:org/my-ext.git"),
        ],
    )
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _, script_path = _setup_extension(fs, config_files, "my-ext", "scripts/provision.sh")

    subprocess = FakeSubprocessRunner(
        popen_responses={str(script_path): (["ran"], 0)},
    )
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, config_files, subprocess)

    # Extension prefix defaults to its name ("my-ext")
    handler = ProvisionHandler(
        subtarget="dependency",
        scope=ProvisionScope.workspace,
        apply="scripts/provision.sh",
        source="my-ext",
    )
    result = svc.run_handler(handler, "apply", ENV_NAME, sink)

    assert result.ok
    env = subprocess.popen_envs[0]
    assert env["WINTER_EXT_DIR"] == str(WORKSPACE_ROOT / "my-ext")
    assert env["WINTER_EXT_PREFIX"] == "my-ext"


def test_extension_source_path_escape_is_rejected() -> None:
    """Extension handler: path escape from the extension root is rejected."""
    from winter_cli.config.models import StandaloneRepositoryConfig

    config = WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        session_prefix="t",
        main_branch="main",
        adopt_extensions=AdoptExtensions.winter,
        standalone_repos=[
            StandaloneRepositoryConfig(name="my-ext", url="git@example.com:org/my-ext.git"),
        ],
    )
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext_path = WORKSPACE_ROOT / "my-ext"
    fs.directories.add(ext_path)
    manifest_path = ext_path / "winter-ext.toml"
    fs.files[manifest_path] = ""
    config_files[manifest_path] = {"name": "my-ext"}

    subprocess = FakeSubprocessRunner()
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, config_files, subprocess)

    handler = ProvisionHandler(
        subtarget="dependency",
        scope=ProvisionScope.workspace,
        apply="../../escape.sh",
        source="my-ext",
    )
    result = svc.run_handler(handler, "apply", ENV_NAME, sink)

    assert not result.ok
    assert result.error is not None
    assert "escapes" in result.error
    assert not subprocess.popen_calls


def test_unknown_extension_source_produces_error() -> None:
    """A handler whose source doesn't match any installed extension returns an error."""
    config = _make_config()
    fs = FakeFilesystem()
    subprocess = FakeSubprocessRunner()
    sink = FakeProvisionOutputSink()
    svc = _make_service(config, fs, {}, subprocess)

    handler = ProvisionHandler(
        subtarget="dependency",
        scope=ProvisionScope.workspace,
        apply="scripts/apply.sh",
        source="no-such-ext",
    )
    result = svc.run_handler(handler, "apply", ENV_NAME, sink)

    assert not result.ok
    assert result.error is not None
    assert not subprocess.popen_calls
