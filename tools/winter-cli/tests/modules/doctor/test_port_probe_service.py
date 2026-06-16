"""Tests for PortProbeService: invariant and registry drift probes.

Covers:
  1. Config invariant probe: PASS when envs_per_workspace >= len(aliases) + 2,
     FAIL otherwise.
  2. Registry drift:
     (a) stale entry — registered name with no env directory on disk
     (b) unregistered dir — env directory exists with no registry entry
     (c) out-of-range / reserved index in the registry
     (d) duplicate index — two envs sharing the same slot
     (e) clean state — all consistent → single PASS "registry drift" result
"""
from __future__ import annotations

from pathlib import Path
from typing import cast

from tests.conftest import FakeFilesystem
from winter_cli.config.models import WorkspaceConfig
from winter_cli.core.filesystem import IFilesystemReader
from winter_cli.modules.doctor.models import ProbeStatus
from winter_cli.modules.doctor.port_probe_service import PORT_SOURCE, PortProbeService

WORKSPACE_ROOT = Path("/ws")


# ---------------------------------------------------------------------------
# In-memory registry
# ---------------------------------------------------------------------------


class _InMemoryRegistry:
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


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def _config(
    env_aliases: list[str] | None = None,
    envs_per_workspace: int = 48,
) -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        session_prefix="t",
        main_branch="main",
        env_aliases=env_aliases if env_aliases is not None else ["alpha", "beta"],
        envs_per_workspace=envs_per_workspace,
    )


def _svc(
    config: WorkspaceConfig,
    fs: FakeFilesystem,
    registry: _InMemoryRegistry | None,
) -> PortProbeService:
    return PortProbeService(
        config=config,
        fs=cast(IFilesystemReader, fs),
        registry=registry,
    )


def _empty_fs() -> FakeFilesystem:
    return FakeFilesystem(directories={WORKSPACE_ROOT})


def _env_fs(*env_names: str) -> FakeFilesystem:
    """Build a FakeFilesystem with env directories (each with a .winter.env)."""
    files: dict[Path, str] = {}
    directories = {WORKSPACE_ROOT}
    for name in env_names:
        env_dir = WORKSPACE_ROOT / name
        directories.add(env_dir)
        files[env_dir / ".winter.env"] = f"WINTER_ENV={name}\n"
    return FakeFilesystem(files=files, directories=directories)


# ---------------------------------------------------------------------------
# 1. Config invariant
# ---------------------------------------------------------------------------


class TestInvariantProbe:
    def test_passes_when_envs_per_workspace_is_sufficient(self) -> None:
        """PASS when envs_per_workspace >= len(aliases) + 2."""
        cfg = _config(env_aliases=["alpha", "beta"], envs_per_workspace=4)
        svc = _svc(cfg, _empty_fs(), registry=None)

        result = svc._probe_invariant()

        assert result.status == ProbeStatus.pass_
        assert result.source == PORT_SOURCE
        assert result.name == "port config invariant"

    def test_passes_with_default_config(self) -> None:
        """PASS with default config (10 aliases, 48 envs_per_workspace)."""
        cfg = _config(envs_per_workspace=48)
        svc = _svc(cfg, _empty_fs(), registry=None)

        result = svc._probe_invariant()

        assert result.status == ProbeStatus.pass_

    def test_fails_when_envs_per_workspace_is_too_small(self) -> None:
        """FAIL when envs_per_workspace < len(aliases) + 2."""
        # 3 aliases requires at least 5 envs_per_workspace; we set 4.
        cfg = _config(env_aliases=["alpha", "beta", "gamma"], envs_per_workspace=4)
        svc = _svc(cfg, _empty_fs(), registry=None)

        result = svc._probe_invariant()

        assert result.status == ProbeStatus.fail
        assert result.source == PORT_SOURCE
        assert result.remediation is not None

    def test_fails_at_exact_boundary(self) -> None:
        """FAIL at exactly len(aliases) + 1 (one below the required minimum)."""
        cfg = _config(env_aliases=["alpha", "beta"], envs_per_workspace=3)
        svc = _svc(cfg, _empty_fs(), registry=None)

        result = svc._probe_invariant()

        assert result.status == ProbeStatus.fail

    def test_passes_at_exact_minimum(self) -> None:
        """PASS at exactly len(aliases) + 2 (the minimum valid value)."""
        cfg = _config(env_aliases=["alpha", "beta"], envs_per_workspace=4)
        svc = _svc(cfg, _empty_fs(), registry=None)

        result = svc._probe_invariant()

        assert result.status == ProbeStatus.pass_


# ---------------------------------------------------------------------------
# 2. Registry drift
# ---------------------------------------------------------------------------


class TestRegistryDriftNoRegistry:
    def test_returns_empty_when_no_registry(self) -> None:
        """When registry is None, no drift probes are emitted."""
        cfg = _config()
        svc = _svc(cfg, _empty_fs(), registry=None)

        results = svc._probe_registry_drift()

        assert results == []


class TestRegistryDriftCleanState:
    def test_passes_when_registry_and_dirs_match(self) -> None:
        """A consistent state (registry matches on-disk dirs) emits a single PASS."""
        registry = _InMemoryRegistry()
        registry.assign("alpha", 1)

        cfg = _config()
        fs = _env_fs("alpha")
        svc = _svc(cfg, fs, registry)

        results = svc._probe_registry_drift()

        assert len(results) == 1
        assert results[0].status == ProbeStatus.pass_
        assert results[0].source == PORT_SOURCE
        assert results[0].name == "registry drift"

    def test_passes_with_empty_registry_and_no_env_dirs(self) -> None:
        """Empty registry + no env dirs on disk → single PASS."""
        registry = _InMemoryRegistry()
        cfg = _config()
        fs = _empty_fs()
        svc = _svc(cfg, fs, registry)

        results = svc._probe_registry_drift()

        assert len(results) == 1
        assert results[0].status == ProbeStatus.pass_


class TestRegistryDriftStaleEntry:
    def test_warns_on_stale_registry_entry(self) -> None:
        """(a) Registry has 'alpha' but its env directory does not exist → WARN."""
        registry = _InMemoryRegistry()
        registry.assign("alpha", 1)

        cfg = _config()
        fs = _empty_fs()  # no env dirs
        svc = _svc(cfg, fs, registry)

        results = svc._probe_registry_drift()

        warns = [r for r in results if r.status == ProbeStatus.warn]
        assert len(warns) >= 1
        stale = next((r for r in warns if "stale" in r.message), None)
        assert stale is not None
        assert stale.source == PORT_SOURCE


class TestRegistryDriftUnregisteredDir:
    def test_warns_on_unregistered_env_dir(self) -> None:
        """(b) Env directory exists but has no registry entry → WARN."""
        registry = _InMemoryRegistry()  # empty

        cfg = _config()
        fs = _env_fs("alpha")
        svc = _svc(cfg, fs, registry)

        results = svc._probe_registry_drift()

        warns = [r for r in results if r.status == ProbeStatus.warn]
        assert len(warns) >= 1
        unregistered = next((r for r in warns if "no registry entry" in r.message), None)
        assert unregistered is not None
        assert unregistered.source == PORT_SOURCE

    def test_unregistered_dir_warn_names_the_env(self) -> None:
        """The warning for an unregistered dir names the env."""
        registry = _InMemoryRegistry()
        cfg = _config()
        fs = _env_fs("beta")
        svc = _svc(cfg, fs, registry)

        results = svc._probe_registry_drift()

        warns = [r for r in results if r.status == ProbeStatus.warn]
        assert any("beta" in r.name for r in warns)


class TestRegistryDriftOutOfRange:
    def test_warns_on_reserved_index_zero(self) -> None:
        """(c) Index 0 is reserved and must never appear in the registry → WARN."""
        registry = _InMemoryRegistry()
        registry.assign("alpha", 0)

        cfg = _config()
        fs = _env_fs("alpha")
        svc = _svc(cfg, fs, registry)

        results = svc._probe_registry_drift()

        warns = [r for r in results if r.status == ProbeStatus.warn]
        reserved = next((r for r in warns if "reserved" in r.message), None)
        assert reserved is not None

    def test_warns_on_index_above_envs_per_workspace(self) -> None:
        """(c) Index > envs_per_workspace is out of range → WARN."""
        registry = _InMemoryRegistry()
        registry.assign("alpha", 100)  # envs_per_workspace defaults to 48

        cfg = _config(envs_per_workspace=48)
        fs = _env_fs("alpha")
        svc = _svc(cfg, fs, registry)

        results = svc._probe_registry_drift()

        warns = [r for r in results if r.status == ProbeStatus.warn]
        oor = next((r for r in warns if "outside the valid range" in r.message), None)
        assert oor is not None


class TestRegistryDriftDuplicateIndex:
    def test_warns_on_duplicate_index(self) -> None:
        """(d) Two envs sharing the same index → WARN for the second (alphabetically)."""
        registry = _InMemoryRegistry()
        registry.assign("alpha", 5)
        registry.assign("beta", 5)  # same index as alpha

        cfg = _config()
        fs = _env_fs("alpha", "beta")
        svc = _svc(cfg, fs, registry)

        results = svc._probe_registry_drift()

        warns = [r for r in results if r.status == ProbeStatus.warn]
        dup = next((r for r in warns if "shared" in r.message or "duplicate" in r.message), None)
        assert dup is not None
        assert dup.source == PORT_SOURCE


class TestRunMethod:
    def test_run_returns_invariant_and_drift_results(self) -> None:
        """run() returns both invariant and drift probe results."""
        registry = _InMemoryRegistry()
        cfg = _config()
        fs = _empty_fs()
        svc = _svc(cfg, fs, registry)

        results = svc.run()

        names = {r.name for r in results}
        assert "port config invariant" in names
        assert "registry drift" in names

    def test_run_all_pass_on_clean_state(self) -> None:
        """All probes pass on a default config with a consistent registry."""
        registry = _InMemoryRegistry()
        registry.assign("alpha", 1)

        cfg = _config()
        fs = _env_fs("alpha")
        svc = _svc(cfg, fs, registry)

        results = svc.run()

        assert all(r.status == ProbeStatus.pass_ for r in results)
