"""Tests for EnvProvisionerService — the single source of truth for runtime env maps.

Covers:
- Feature-env scope: WINTER_ENV / WINTER_ENV_INDEX / WINTER_PORT_BASE /
  WINTER_WORKSPACE_PORT_BASE are computed from the registry-assigned index.
- Workspace scope: index 0, port_base_for_index(0).
- [env.vars] rendering: ${NAME}, ${NAME+N} expansion, sibling references.
- [env.vars] error cases: undefined variable, unsupported token, non-integer +N.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from winter_cli.config.models import ProjectRepositoryConfig, SingletonRepository, SingletonType, WorkspaceConfig
from winter_cli.modules.workspace.env_provisioner import EnvProvisionerService

WORKSPACE_ROOT = Path("/ws")


# ---------------------------------------------------------------------------
# In-memory registry
# ---------------------------------------------------------------------------


class _InMemoryRegistry:
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


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def _config(
    base_port: int = 4000,
    ports_per_env: int = 20,
    env_vars: dict[str, str] | None = None,
) -> WorkspaceConfig:
    kwargs: dict = {
        "workspace_root": WORKSPACE_ROOT,
        "session_prefix": "t",
        "main_branch": "main",
        "base_port": base_port,
        "ports_per_env": ports_per_env,
        "singleton_repos": [SingletonRepository(name="ws", type=SingletonType.workspace)],
        "project_repos": [ProjectRepositoryConfig(name="demo", url="git@example.com:demo.git")],
    }
    if env_vars is not None:
        kwargs["env_vars"] = env_vars
    return WorkspaceConfig(**kwargs)


def _svc(
    assignments: dict[str, int] | None = None,
    base_port: int = 4000,
    ports_per_env: int = 20,
    env_vars: dict[str, str] | None = None,
) -> EnvProvisionerService:
    cfg = _config(base_port=base_port, ports_per_env=ports_per_env, env_vars=env_vars)
    reg = _InMemoryRegistry(assignments)
    return EnvProvisionerService(config=cfg, registry=reg)


# ---------------------------------------------------------------------------
# Feature-env scope
# ---------------------------------------------------------------------------


class TestFeatureEnvScope:
    def test_winter_env_is_scope_name(self) -> None:
        """WINTER_ENV equals the scope name passed to compute()."""
        result = _svc(assignments={"alpha": 1}).compute("alpha")
        assert result["WINTER_ENV"] == "alpha"

    def test_winter_env_index_from_registry(self) -> None:
        """WINTER_ENV_INDEX matches the registry-assigned index."""
        result = _svc(assignments={"alpha": 1}).compute("alpha")
        assert result["WINTER_ENV_INDEX"] == "1"

    def test_winter_port_base_alpha(self) -> None:
        """WINTER_PORT_BASE is base_port + index * ports_per_env for alpha (index 1)."""
        result = _svc(assignments={"alpha": 1}, base_port=4000, ports_per_env=20).compute("alpha")
        assert result["WINTER_PORT_BASE"] == "4020"  # 4000 + 1 * 20

    def test_winter_port_base_beta(self) -> None:
        """WINTER_PORT_BASE is correct for beta (index 2)."""
        result = _svc(assignments={"beta": 2}, base_port=4000, ports_per_env=20).compute("beta")
        assert result["WINTER_PORT_BASE"] == "4040"  # 4000 + 2 * 20

    def test_winter_workspace_port_base_is_index_zero(self) -> None:
        """WINTER_WORKSPACE_PORT_BASE is always port_base_for_index(0) = base_port."""
        result = _svc(assignments={"alpha": 1}, base_port=4000, ports_per_env=20).compute("alpha")
        assert result["WINTER_WORKSPACE_PORT_BASE"] == "4000"

    def test_persisted_index_used_over_formula(self) -> None:
        """A non-alias env with a persisted index uses that index, not the hash formula."""
        # "myenv" is not in env_aliases; persist index 15 out-of-band.
        result = _svc(assignments={"myenv": 15}, base_port=4000, ports_per_env=20).compute("myenv")
        assert result["WINTER_ENV_INDEX"] == "15"
        assert result["WINTER_PORT_BASE"] == "4300"  # 4000 + 15 * 20


# ---------------------------------------------------------------------------
# Workspace scope
# ---------------------------------------------------------------------------


class TestWorkspaceScope:
    def test_winter_env_is_workspace(self) -> None:
        result = _svc().compute("workspace")
        assert result["WINTER_ENV"] == "workspace"

    def test_winter_env_index_is_zero(self) -> None:
        result = _svc().compute("workspace")
        assert result["WINTER_ENV_INDEX"] == "0"

    def test_winter_workspace_port_base_is_index_zero(self) -> None:
        """WINTER_WORKSPACE_PORT_BASE is base_port for workspace scope (index 0)."""
        result = _svc(base_port=4000, ports_per_env=20).compute("workspace")
        assert result["WINTER_WORKSPACE_PORT_BASE"] == "4000"

    def test_winter_port_base_not_emitted_for_workspace(self) -> None:
        """WINTER_PORT_BASE is NOT in the workspace scope result — workspace only gets WINTER_WORKSPACE_PORT_BASE."""
        result = _svc(base_port=4000, ports_per_env=20).compute("workspace")
        assert "WINTER_PORT_BASE" not in result


# ---------------------------------------------------------------------------
# [env.vars] rendering
# ---------------------------------------------------------------------------


class TestEnvVarsRendering:
    def test_port_offset_token(self) -> None:
        """${WINTER_PORT_BASE+10} resolves to port_base + 10."""
        result = _svc(
            assignments={"alpha": 1},
            base_port=4000,
            ports_per_env=20,
            env_vars={"WEB_PORT": "${WINTER_PORT_BASE+10}"},
        ).compute("alpha")
        assert result["WEB_PORT"] == "4030"  # 4020 + 10

    def test_zero_offset(self) -> None:
        """${WINTER_PORT_BASE+0} resolves to exactly port_base."""
        result = _svc(
            assignments={"alpha": 1},
            env_vars={"MY_PORT": "${WINTER_PORT_BASE+0}"},
        ).compute("alpha")
        assert result["MY_PORT"] == "4020"

    def test_literal_passthrough(self) -> None:
        """Values with no ${...} token pass through unchanged."""
        result = _svc(
            assignments={"alpha": 1},
            env_vars={"DATABASE_URL": "postgresql://user:pass@localhost/mydb"},
        ).compute("alpha")
        assert result["DATABASE_URL"] == "postgresql://user:pass@localhost/mydb"

    def test_bare_reference_resolves(self) -> None:
        """${WINTER_PORT_BASE} without offset resolves to the base var's string value."""
        result = _svc(
            assignments={"alpha": 1},
            env_vars={"MY_PORT": "${WINTER_PORT_BASE}"},
        ).compute("alpha")
        assert result["MY_PORT"] == "4020"

    def test_sibling_reference_resolves(self) -> None:
        """A later [env.vars] entry can reference an earlier one by name."""
        result = _svc(
            assignments={"alpha": 1},
            env_vars={
                "DB_PORT": "${WINTER_PORT_BASE+12}",
                "DATABASE_URL": "postgresql://localhost:${DB_PORT}/mydb",
            },
        ).compute("alpha")
        assert result["DB_PORT"] == "4032"
        assert result["DATABASE_URL"] == "postgresql://localhost:4032/mydb"

    def test_workspace_port_base_arithmetic(self) -> None:
        """${WINTER_WORKSPACE_PORT_BASE+N} resolves against index-0 base."""
        result = _svc(
            assignments={"alpha": 1},
            base_port=4000,
            ports_per_env=20,
            env_vars={"RABBITMQ_PORT": "${WINTER_WORKSPACE_PORT_BASE+1}"},
        ).compute("alpha")
        assert result["RABBITMQ_PORT"] == "4001"

    def test_string_base_var_reference(self) -> None:
        """${WINTER_ENV} resolves to the env name string."""
        result = _svc(
            assignments={"alpha": 1},
            env_vars={"TAG": "${WINTER_ENV}-build"},
        ).compute("alpha")
        assert result["TAG"] == "alpha-build"

    def test_mixed_token_and_literal(self) -> None:
        """A value mixing token with surrounding text resolves correctly."""
        result = _svc(
            assignments={"alpha": 1},
            base_port=4000,
            ports_per_env=20,
            env_vars={"DB_URL": "postgres://localhost:${WINTER_PORT_BASE+12}/db"},
        ).compute("alpha")
        assert result["DB_URL"] == "postgres://localhost:4032/db"

    def test_multiple_port_offsets(self) -> None:
        """Multiple [env.vars] entries are all rendered."""
        result = _svc(
            assignments={"alpha": 1},
            env_vars={
                "WEB_PORT": "${WINTER_PORT_BASE+10}",
                "API_PORT": "${WINTER_PORT_BASE+11}",
                "LITERAL": "no-token",
            },
        ).compute("alpha")
        assert result["WEB_PORT"] == "4030"
        assert result["API_PORT"] == "4031"
        assert result["LITERAL"] == "no-token"

    def test_no_env_vars_table_returns_base_vars_only(self) -> None:
        """Absent [env.vars] table returns only the four base WINTER_* vars."""
        result = _svc(assignments={"alpha": 1}, env_vars=None).compute("alpha")
        assert set(result.keys()) == {
            "WINTER_ENV",
            "WINTER_ENV_INDEX",
            "WINTER_PORT_BASE",
            "WINTER_WORKSPACE_PORT_BASE",
        }

    def test_workspace_scope_env_vars(self) -> None:
        """[env.vars] entries are also rendered for workspace scope."""
        result = _svc(
            env_vars={"WS_PORT": "${WINTER_WORKSPACE_PORT_BASE+1}"},
        ).compute("workspace")
        assert result["WS_PORT"] == "4001"  # 4000 + 1


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestEnvVarsErrors:
    def test_undefined_reference_raises(self) -> None:
        """A ${NAME} reference to an undefined variable raises ValueError."""
        with pytest.raises(ValueError, match=r"undefined variable.*UNKNOWN_VAR"):
            _svc(
                assignments={"alpha": 1},
                env_vars={"BAD": "${UNKNOWN_VAR}"},
            ).compute("alpha")

    def test_unsupported_token_raises(self) -> None:
        """A ${...} that is not a valid reference pattern raises ValueError."""
        with pytest.raises(ValueError, match="unsupported substitution token"):
            _svc(
                assignments={"alpha": 1},
                env_vars={"BAD": "${not-an-identifier}"},
            ).compute("alpha")

    def test_non_integer_offset_raises(self) -> None:
        """${NAME+N} where NAME is not an integer raises ValueError."""
        with pytest.raises(ValueError, match="non-integer"):
            _svc(
                assignments={"alpha": 1},
                env_vars={
                    "HOSTNAME": "db.example.com",
                    "BAD": "${HOSTNAME+1}",
                },
            ).compute("alpha")

    def test_forward_reference_raises(self) -> None:
        """Referencing an entry declared later (not yet in scope) raises ValueError."""
        with pytest.raises(ValueError, match=r"undefined variable.*WTS_DB_PORT"):
            _svc(
                assignments={"alpha": 1},
                env_vars={
                    "DATABASE_URL": "postgres://localhost:${WTS_DB_PORT}/db",
                    "WTS_DB_PORT": "${WINTER_PORT_BASE+12}",
                },
            ).compute("alpha")
