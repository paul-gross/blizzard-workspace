from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import (
    FakeConfigFileReader,
    FakeFilesystem,
    FakeInitReporter,
    FakeSubprocessRunner,
)
from winter_cli.config.models import AdoptExtensions, WorkspaceConfig
from winter_cli.modules.workspace.extension_hook_service import ExtensionHookService
from winter_cli.modules.workspace.extension_manifest import ExtensionManifestLoader
from winter_cli.modules.workspace.models import StandaloneRepository

WORKSPACE_ROOT = Path("/ws")


class _InMemoryRegistry:
    """Minimal in-memory IEnvIndexRegistry for tests."""

    def __init__(self) -> None:
        self._data: dict[str, int] = {}

    def get_index(self, name: str) -> int | None:
        return self._data.get(name)

    def all_assignments(self) -> dict[str, int]:
        return dict(self._data)

    def assign(self, name: str, index: int) -> None:
        self._data[name] = index

    def remove(self, name: str) -> None:
        self._data.pop(name, None)


@pytest.fixture
def workspace_config() -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        session_prefix="t",
        main_branch="main",
        adopt_extensions=AdoptExtensions.winter,
    )


def _service(
    workspace_config: WorkspaceConfig,
    fs: FakeFilesystem,
    config_files: dict[Path, dict],
    subprocess: FakeSubprocessRunner,
    registry: _InMemoryRegistry | None = None,
) -> ExtensionHookService:
    loader = ExtensionManifestLoader(config_file_reader=FakeConfigFileReader(config_files))
    return ExtensionHookService(
        config=workspace_config,
        fs=fs,
        subprocess_runner=subprocess,
        manifest_loader=loader,
        registry=registry,
    )


def _setup_hook_ext(
    fs: FakeFilesystem,
    config_files: dict[Path, dict],
    hook_name: str = "on_env_init",
    script_rel: str = "hooks/init.sh",
) -> tuple[StandaloneRepository, Path]:
    """Helper: register a single extension with one hook script; returns repo + hook path."""
    ext_path = WORKSPACE_ROOT / "my-ext"
    fs.directories.add(ext_path)
    manifest_path = ext_path / "winter-ext.toml"
    fs.files[manifest_path] = ""
    config_files[manifest_path] = {"name": "my-ext", "hooks": {hook_name: script_rel}}
    hook_path = (ext_path / script_rel).resolve()
    fs.files[hook_path] = ""
    fs.executables.add(hook_path)
    fs.directories.add(hook_path.parent)
    return StandaloneRepository(name="my-ext", path=ext_path), hook_path


def test_run_env_init_hook_streams_output_and_succeeds(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """Happy-path: a hook script runs, lines stream to the reporter, exit 0."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext_path = WORKSPACE_ROOT / "my-ext"
    fs.directories.add(ext_path)
    manifest_path = ext_path / "winter-ext.toml"
    fs.files[manifest_path] = ""
    config_files[manifest_path] = {"name": "my-ext", "hooks": {"on_env_init": "hooks/init.sh"}}

    hook_path = (ext_path / "hooks" / "init.sh").resolve()
    fs.files[hook_path] = ""
    fs.executables.add(hook_path)
    fs.directories.add(hook_path.parent)

    repos = [StandaloneRepository(name="my-ext", path=ext_path)]
    env_root = WORKSPACE_ROOT / "alpha"

    subprocess = FakeSubprocessRunner(
        popen_responses={str(hook_path): (["doing stuff", "done"], 0)},
    )

    svc = _service(workspace_config, fs, config_files, subprocess)
    ok = svc.run_env_init_hooks(repos, env_root, "alpha", init_reporter)

    assert ok is True
    assert ("my-ext", "doing stuff") in init_reporter.cmd_output
    assert ("my-ext", "done") in init_reporter.cmd_output
    assert any(a[2] == "hook_ran" for a in init_reporter.actions)


def test_run_env_hook_failure_isolated_per_extension(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """One extension's hook failure is caught at its own wrap site — sibling extensions still run,
    the aggregator returns False, and the reporter logs exactly one error for the failing extension."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}

    # Extension A: hook exits non-zero (the failure)
    ext_a = WORKSPACE_ROOT / "ext-a"
    fs.directories.add(ext_a)
    manifest_a = ext_a / "winter-ext.toml"
    fs.files[manifest_a] = ""
    config_files[manifest_a] = {"name": "ext-a", "hooks": {"on_env_init": "hooks/a.sh"}}
    hook_a = (ext_a / "hooks" / "a.sh").resolve()
    fs.files[hook_a] = ""
    fs.executables.add(hook_a)
    fs.directories.add(hook_a.parent)

    # Extension B: hook succeeds — must still run despite A's failure
    ext_b = WORKSPACE_ROOT / "ext-b"
    fs.directories.add(ext_b)
    manifest_b = ext_b / "winter-ext.toml"
    fs.files[manifest_b] = ""
    config_files[manifest_b] = {"name": "ext-b", "hooks": {"on_env_init": "hooks/b.sh"}}
    hook_b = (ext_b / "hooks" / "b.sh").resolve()
    fs.files[hook_b] = ""
    fs.executables.add(hook_b)
    fs.directories.add(hook_b.parent)

    repos = [
        StandaloneRepository(name="ext-a", path=ext_a),
        StandaloneRepository(name="ext-b", path=ext_b),
    ]
    subprocess = FakeSubprocessRunner(
        popen_responses={
            str(hook_a): (["broke"], 1),
            str(hook_b): (["ok"], 0),
        },
    )

    svc = _service(workspace_config, fs, config_files, subprocess)
    ok = svc.run_env_init_hooks(repos, WORKSPACE_ROOT / "alpha", "alpha", init_reporter)

    assert ok is False
    # ext-a error logged exactly once
    a_errors = [msg for repo, msg in init_reporter.errors if repo == "ext-a"]
    assert len(a_errors) == 1
    assert "exited with code 1" in a_errors[0]
    # ext-b still ran successfully
    assert ("ext-b", "ok") in init_reporter.cmd_output
    assert any(a[0] == "ext-b" and a[2] == "hook_ran" for a in init_reporter.actions)


# ── on_workspace_reconcile tests ──────────────────────────────────────────────


def test_run_workspace_reconcile_hook_happy_path(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """Happy-path: on_workspace_reconcile runs from the workspace root, exits 0."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext_path = WORKSPACE_ROOT / "my-ext"
    fs.directories.add(ext_path)
    manifest_path = ext_path / "winter-ext.toml"
    fs.files[manifest_path] = ""
    config_files[manifest_path] = {
        "name": "my-ext",
        "hooks": {"on_workspace_reconcile": "hooks/ws-reconcile.sh"},
    }

    hook_path = (ext_path / "hooks" / "ws-reconcile.sh").resolve()
    fs.files[hook_path] = ""
    fs.executables.add(hook_path)
    fs.directories.add(hook_path.parent)

    repos = [StandaloneRepository(name="my-ext", path=ext_path)]

    subprocess = FakeSubprocessRunner(
        popen_responses={str(hook_path): (["workspace reconcile ran"], 0)},
    )

    svc = _service(workspace_config, fs, config_files, subprocess)
    ok = svc.run_workspace_reconcile_hooks(repos, init_reporter)

    assert ok is True
    assert ("my-ext", "workspace reconcile ran") in init_reporter.cmd_output
    assert any(a[2] == "hook_ran" for a in init_reporter.actions)


def test_run_workspace_reconcile_hook_cwd_is_workspace_root(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """The hook's cwd must be the workspace root, not an env dir."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext_path = WORKSPACE_ROOT / "my-ext"
    fs.directories.add(ext_path)
    manifest_path = ext_path / "winter-ext.toml"
    fs.files[manifest_path] = ""
    config_files[manifest_path] = {
        "name": "my-ext",
        "hooks": {"on_workspace_reconcile": "hooks/ws-reconcile.sh"},
    }

    hook_path = (ext_path / "hooks" / "ws-reconcile.sh").resolve()
    fs.files[hook_path] = ""
    fs.executables.add(hook_path)
    fs.directories.add(hook_path.parent)

    repos = [StandaloneRepository(name="my-ext", path=ext_path)]

    subprocess = FakeSubprocessRunner(
        popen_responses={str(hook_path): ([], 0)},
    )

    svc = _service(workspace_config, fs, config_files, subprocess)
    svc.run_workspace_reconcile_hooks(repos, init_reporter)

    # The popen call's cwd must equal the workspace root.
    assert subprocess.popen_calls, "expected at least one popen call"
    _, actual_cwd = subprocess.popen_calls[0]
    assert actual_cwd == WORKSPACE_ROOT


def test_run_workspace_reconcile_hook_env_contains_workspace_trio(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """The hook env must contain WINTER_WORKSPACE_DIR, WINTER_EXT_DIR,
    WINTER_EXT_PREFIX and must NOT contain WINTER_ENV, WINTER_ENV_INDEX,
    or WINTER_PORT_BASE."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext_path = WORKSPACE_ROOT / "my-ext"
    fs.directories.add(ext_path)
    manifest_path = ext_path / "winter-ext.toml"
    fs.files[manifest_path] = ""
    config_files[manifest_path] = {
        "name": "my-ext",
        "hooks": {"on_workspace_reconcile": "hooks/ws-reconcile.sh"},
    }

    hook_path = (ext_path / "hooks" / "ws-reconcile.sh").resolve()
    fs.files[hook_path] = ""
    fs.executables.add(hook_path)
    fs.directories.add(hook_path.parent)

    repos = [StandaloneRepository(name="my-ext", path=ext_path)]

    subprocess = FakeSubprocessRunner(
        popen_responses={str(hook_path): ([], 0)},
    )

    svc = _service(workspace_config, fs, config_files, subprocess)
    svc.run_workspace_reconcile_hooks(repos, init_reporter)

    assert subprocess.popen_envs, "expected at least one popen env"
    env = subprocess.popen_envs[0]

    # Workspace trio must be present.
    assert env["WINTER_WORKSPACE_DIR"] == str(WORKSPACE_ROOT)
    assert env["WINTER_EXT_DIR"] == str(ext_path)
    assert env["WINTER_EXT_PREFIX"] == "my-ext"

    # Env-scoped vars must NOT be present.
    assert "WINTER_ENV" not in env
    assert "WINTER_ENV_INDEX" not in env
    assert "WINTER_PORT_BASE" not in env


def test_run_workspace_reconcile_hook_failure_isolated_per_extension(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """One failing extension doesn't suppress others; aggregator returns False."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}

    ext_a = WORKSPACE_ROOT / "ext-a"
    fs.directories.add(ext_a)
    manifest_a = ext_a / "winter-ext.toml"
    fs.files[manifest_a] = ""
    config_files[manifest_a] = {
        "name": "ext-a",
        "hooks": {"on_workspace_reconcile": "hooks/ws.sh"},
    }
    hook_a = (ext_a / "hooks" / "ws.sh").resolve()
    fs.files[hook_a] = ""
    fs.executables.add(hook_a)
    fs.directories.add(hook_a.parent)

    ext_b = WORKSPACE_ROOT / "ext-b"
    fs.directories.add(ext_b)
    manifest_b = ext_b / "winter-ext.toml"
    fs.files[manifest_b] = ""
    config_files[manifest_b] = {
        "name": "ext-b",
        "hooks": {"on_workspace_reconcile": "hooks/ws.sh"},
    }
    hook_b = (ext_b / "hooks" / "ws.sh").resolve()
    fs.files[hook_b] = ""
    fs.executables.add(hook_b)
    fs.directories.add(hook_b.parent)

    repos = [
        StandaloneRepository(name="ext-a", path=ext_a),
        StandaloneRepository(name="ext-b", path=ext_b),
    ]
    subprocess = FakeSubprocessRunner(
        popen_responses={
            str(hook_a): (["boom"], 1),
            str(hook_b): (["ok"], 0),
        },
    )

    svc = _service(workspace_config, fs, config_files, subprocess)
    ok = svc.run_workspace_reconcile_hooks(repos, init_reporter)

    assert ok is False
    a_errors = [msg for repo, msg in init_reporter.errors if repo == "ext-a"]
    assert len(a_errors) == 1
    assert "exited with code 1" in a_errors[0]
    assert ("ext-b", "ok") in init_reporter.cmd_output
    assert any(a[0] == "ext-b" and a[2] == "hook_ran" for a in init_reporter.actions)


def test_run_workspace_reconcile_hook_missing_script(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """A hook declared in the manifest but missing from disk: per-extension error, True sibling."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext_path = WORKSPACE_ROOT / "my-ext"
    fs.directories.add(ext_path)
    manifest_path = ext_path / "winter-ext.toml"
    fs.files[manifest_path] = ""
    config_files[manifest_path] = {
        "name": "my-ext",
        "hooks": {"on_workspace_reconcile": "hooks/ws-reconcile.sh"},
    }
    # Note: hook script is NOT added to fs.files / fs.executables — it's missing.

    repos = [StandaloneRepository(name="my-ext", path=ext_path)]
    subprocess = FakeSubprocessRunner()

    svc = _service(workspace_config, fs, config_files, subprocess)
    ok = svc.run_workspace_reconcile_hooks(repos, init_reporter)

    assert ok is False
    errors = [msg for repo, msg in init_reporter.errors if repo == "my-ext"]
    assert len(errors) == 1
    assert "not found" in errors[0]


def test_run_workspace_reconcile_hook_non_executable_script(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """A hook script present but not executable reports an error."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext_path = WORKSPACE_ROOT / "my-ext"
    fs.directories.add(ext_path)
    manifest_path = ext_path / "winter-ext.toml"
    fs.files[manifest_path] = ""
    config_files[manifest_path] = {
        "name": "my-ext",
        "hooks": {"on_workspace_reconcile": "hooks/ws-reconcile.sh"},
    }

    hook_path = (ext_path / "hooks" / "ws-reconcile.sh").resolve()
    fs.files[hook_path] = ""
    fs.directories.add(hook_path.parent)
    # NOT added to fs.executables — not executable.

    repos = [StandaloneRepository(name="my-ext", path=ext_path)]
    subprocess = FakeSubprocessRunner()

    svc = _service(workspace_config, fs, config_files, subprocess)
    ok = svc.run_workspace_reconcile_hooks(repos, init_reporter)

    assert ok is False
    errors = [msg for repo, msg in init_reporter.errors if repo == "my-ext"]
    assert len(errors) == 1
    assert "not executable" in errors[0]


def test_run_workspace_reconcile_hook_skipped_when_adopt_extensions_none(
    init_reporter: FakeInitReporter,
) -> None:
    """adopt_extensions=none means no hooks fire; service returns True."""
    cfg = WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        session_prefix="t",
        main_branch="main",
        adopt_extensions=AdoptExtensions.none,
    )
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext_path = WORKSPACE_ROOT / "my-ext"
    fs.directories.add(ext_path)
    manifest_path = ext_path / "winter-ext.toml"
    fs.files[manifest_path] = ""
    config_files[manifest_path] = {
        "name": "my-ext",
        "hooks": {"on_workspace_reconcile": "hooks/ws-reconcile.sh"},
    }

    repos = [StandaloneRepository(name="my-ext", path=ext_path)]
    subprocess = FakeSubprocessRunner()

    svc = _service(cfg, fs, config_files, subprocess)
    ok = svc.run_workspace_reconcile_hooks(repos, init_reporter)

    assert ok is True
    assert not subprocess.popen_calls


def test_run_workspace_reconcile_hook_no_hook_declared(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """Extension without on_workspace_reconcile in its manifest is skipped silently."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext_path = WORKSPACE_ROOT / "my-ext"
    fs.directories.add(ext_path)
    manifest_path = ext_path / "winter-ext.toml"
    fs.files[manifest_path] = ""
    config_files[manifest_path] = {
        "name": "my-ext",
        "hooks": {"on_env_init": "hooks/init.sh"},  # only env hook, no workspace hook
    }

    repos = [StandaloneRepository(name="my-ext", path=ext_path)]
    subprocess = FakeSubprocessRunner()

    svc = _service(workspace_config, fs, config_files, subprocess)
    ok = svc.run_workspace_reconcile_hooks(repos, init_reporter)

    assert ok is True
    assert not subprocess.popen_calls


def test_env_hook_env_contains_env_scoped_vars(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """Env-scoped hooks still include WINTER_ENV, WINTER_ENV_INDEX, WINTER_PORT_BASE."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext_path = WORKSPACE_ROOT / "my-ext"
    fs.directories.add(ext_path)
    manifest_path = ext_path / "winter-ext.toml"
    fs.files[manifest_path] = ""
    config_files[manifest_path] = {"name": "my-ext", "hooks": {"on_env_init": "hooks/init.sh"}}

    hook_path = (ext_path / "hooks" / "init.sh").resolve()
    fs.files[hook_path] = ""
    fs.executables.add(hook_path)
    fs.directories.add(hook_path.parent)

    repos = [StandaloneRepository(name="my-ext", path=ext_path)]
    env_root = WORKSPACE_ROOT / "alpha"

    subprocess = FakeSubprocessRunner(
        popen_responses={str(hook_path): ([], 0)},
    )

    svc = _service(workspace_config, fs, config_files, subprocess)
    svc.run_env_init_hooks(repos, env_root, "alpha", init_reporter)

    assert subprocess.popen_envs
    env = subprocess.popen_envs[0]
    assert env["WINTER_ENV"] == "alpha"
    assert "WINTER_ENV_INDEX" in env
    assert "WINTER_PORT_BASE" in env


# ── M1: hook vars == persisted/config-derived values ─────────────────────────


def test_hook_env_index_matches_registry_persisted_index(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """WINTER_ENV_INDEX in the hook env equals the registry-persisted index, not a
    freshly-resolved suggestion.  Proves that registry-first lookup is used."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    repo, hook_path = _setup_hook_ext(fs, config_files)
    repos = [repo]
    env_root = WORKSPACE_ROOT / "alpha"

    subprocess = FakeSubprocessRunner(popen_responses={str(hook_path): ([], 0)})

    # Assign an unusual index to alpha so the test would fail if the hook
    # service ignored the registry and recomputed (which would return 1).
    registry = _InMemoryRegistry()
    registry.assign("alpha", 7)

    svc = _service(workspace_config, fs, config_files, subprocess, registry=registry)
    svc.run_env_init_hooks(repos, env_root, "alpha", init_reporter)

    assert subprocess.popen_envs
    env = subprocess.popen_envs[0]
    assert env["WINTER_ENV_INDEX"] == "7"


def test_hook_port_base_matches_registry_persisted_index(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """WINTER_PORT_BASE equals base_port + persisted_index * ports_per_env."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    repo, hook_path = _setup_hook_ext(fs, config_files)
    repos = [repo]
    env_root = WORKSPACE_ROOT / "alpha"

    subprocess = FakeSubprocessRunner(popen_responses={str(hook_path): ([], 0)})

    registry = _InMemoryRegistry()
    registry.assign("alpha", 7)

    # Custom port config to make the check non-trivial.
    cfg = WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        session_prefix="t",
        main_branch="main",
        adopt_extensions=AdoptExtensions.winter,
        base_port=5000,
        ports_per_env=30,
    )

    svc = _service(cfg, fs, config_files, subprocess, registry=registry)
    svc.run_env_init_hooks(repos, env_root, "alpha", init_reporter)

    assert subprocess.popen_envs
    env = subprocess.popen_envs[0]
    expected_port_base = 5000 + 7 * 30  # base_port + index * ports_per_env
    assert env["WINTER_PORT_BASE"] == str(expected_port_base)


def test_hook_falls_back_to_config_aware_resolve_when_no_registry(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """When no registry is wired, the hook falls back to config-aware resolve_env_index."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    repo, hook_path = _setup_hook_ext(fs, config_files)
    repos = [repo]
    env_root = WORKSPACE_ROOT / "alpha"

    subprocess = FakeSubprocessRunner(popen_responses={str(hook_path): ([], 0)})

    # No registry — should use resolve_env_index with config knobs.
    # workspace_config uses default env_aliases (first 10 Greek), alpha → index 1.
    svc = _service(workspace_config, fs, config_files, subprocess, registry=None)
    svc.run_env_init_hooks(repos, env_root, "alpha", init_reporter)

    assert subprocess.popen_envs
    env = subprocess.popen_envs[0]
    assert env["WINTER_ENV_INDEX"] == "1"


def test_hook_falls_back_to_config_aware_resolve_when_name_not_in_registry(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """When the registry is wired but has no entry for the env, config-aware resolve is used."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    repo, hook_path = _setup_hook_ext(fs, config_files)
    repos = [repo]
    env_root = WORKSPACE_ROOT / "alpha"

    subprocess = FakeSubprocessRunner(popen_responses={str(hook_path): ([], 0)})

    registry = _InMemoryRegistry()
    # alpha is NOT in the registry — should fall back to resolve_env_index → 1.

    svc = _service(workspace_config, fs, config_files, subprocess, registry=registry)
    svc.run_env_init_hooks(repos, env_root, "alpha", init_reporter)

    assert subprocess.popen_envs
    env = subprocess.popen_envs[0]
    assert env["WINTER_ENV_INDEX"] == "1"
