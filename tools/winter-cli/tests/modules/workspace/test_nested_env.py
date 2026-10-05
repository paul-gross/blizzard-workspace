from __future__ import annotations

from pathlib import Path

from winter_cli.modules.workspace.nested_env import NESTED_CHAIN_VAR, nested_chain, nested_child_env, scrubbed_env

ROOT = Path("/ws")


def test_scrubbed_env_keeps_only_the_allowed_winter_vars() -> None:
    env = scrubbed_env(
        {
            "WINTER_ENV": "alpha",
            "WINTER_INVOCATION_CWD": "/outer",
            "WINTER_PORT_BASE": "4020",
            NESTED_CHAIN_VAR: "/up",
            "WINTER_LOG_LEVEL": "info",
            "WINTER_OTEL_X": "1",
            "VIRTUAL_ENV": "/v",
            "HOME": "/h",
        }
    )

    assert env == {"WINTER_LOG_LEVEL": "info", "WINTER_OTEL_X": "1", "HOME": "/h"}


def test_scrubbed_env_drops_the_outer_virtualenv_from_path_so_winter_resolves_the_shim() -> None:
    env = scrubbed_env({"VIRTUAL_ENV": "/outer/.venv", "PATH": "/outer/.venv/bin:/home/u/.local/bin:/usr/bin"})

    assert env["PATH"] == "/home/u/.local/bin:/usr/bin"
    assert "VIRTUAL_ENV" not in env


def test_nested_child_env_extends_the_chain_with_the_callers_own_root() -> None:
    assert nested_child_env({}, ROOT)[NESTED_CHAIN_VAR] == str(ROOT)
    assert nested_child_env({NESTED_CHAIN_VAR: "/up"}, ROOT)[NESTED_CHAIN_VAR] == f"/up:{ROOT}"


def test_nested_child_env_is_the_scrubbed_env_plus_the_chain() -> None:
    environ = {"WINTER_PORT_BASE": "4020", "VIRTUAL_ENV": "/v", "PATH": "/v/bin:/usr/bin", "HOME": "/h"}

    assert nested_child_env(environ, ROOT) == {**scrubbed_env(environ), NESTED_CHAIN_VAR: str(ROOT)}


def test_nested_chain_ignores_empty_entries() -> None:
    assert nested_chain({NESTED_CHAIN_VAR: ":/a::/b:"}) == ("/a", "/b")
    assert nested_chain({}) == ()
