"""Pure arithmetic for the keys winter delegates into a nested workspace's local overlay."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from winter_cli.config.models import WorkspaceConfig

if TYPE_CHECKING:
    from collections.abc import Mapping

_DEFAULT_PORTS_PER_ENV = WorkspaceConfig.model_fields["ports_per_env"].default
_DEFAULT_ENVS_PER_WORKSPACE = WorkspaceConfig.model_fields["envs_per_workspace"].default


def delegated_keys(port_base: int, service_prefix: str, envs: int | None) -> dict[str, Any]:
    """The overlay keys winter writes into the nested workspace for one outer env.

    `base_port` and `service_prefix` always. With *envs* (the usable nested
    feature-env count) also `env_aliases = []` and `envs_per_workspace = envs +
    1`: with no aliases, index 1 is the reserved buffer slot, so `envs` usable
    indices need `envs + 1` slots.
    """
    keys: dict[str, Any] = {"base_port": port_base, "service_prefix": service_prefix}
    if envs is not None:
        keys["env_aliases"] = []
        keys["envs_per_workspace"] = envs + 1
    return keys


def _int(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def effective_ports_per_env(committed: Mapping[str, Any], local: Mapping[str, Any]) -> int:
    """The nested workspace's `ports_per_env` after the overlay, at winter's default when unset."""
    return _int(local.get("ports_per_env", committed.get("ports_per_env")), _DEFAULT_PORTS_PER_ENV)


def effective_envs_per_workspace(committed: Mapping[str, Any], local: Mapping[str, Any]) -> int:
    """The nested workspace's `envs_per_workspace` after the overlay, at winter's default when unset."""
    return _int(local.get("envs_per_workspace", committed.get("envs_per_workspace")), _DEFAULT_ENVS_PER_WORKSPACE)


def footprint(committed: Mapping[str, Any], local_after: Mapping[str, Any]) -> int:
    """Ports the nested workspace can use: `(envs_per_workspace + 1) * ports_per_env`.

    Indices run `0..envs_per_workspace`, each owning `ports_per_env` ports.
    *committed* is the nested `config.toml`; *local_after* is its
    `config.local.toml` with the delegated keys applied, which wins.
    """
    return (effective_envs_per_workspace(committed, local_after) + 1) * effective_ports_per_env(committed, local_after)
