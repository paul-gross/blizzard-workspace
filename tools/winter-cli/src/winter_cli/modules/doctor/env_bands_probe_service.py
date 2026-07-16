from __future__ import annotations

from winter_cli.config.models import WorkspaceConfig
from winter_cli.modules.doctor.env_discovery_service import EnvDiscoveryService
from winter_cli.modules.doctor.models import ProbeResult, ProbeStatus
from winter_cli.modules.workspace.env_index_registry import IEnvIndexRegistry

ENV_BANDS_SOURCE = "env-vars"


class EnvBandsProbeService:
    """Warns about `[env.<name>.vars]` bands naming an env that does not exist.

    `EnvProvisionerService.compute()` looks up a per-env band by scope name only
    (`bands.named.get(scope, {})`) — a band naming an env that is not the current
    scope is simply never consulted. This is deliberate, documented, and tested
    behavior (see `context/winter-cli/configuration/ports-and-environments.md`
    and `test_env_provisioner.py::test_named_band_for_unknown_env_is_inert`): it
    lets an override outlive the env it was written for. But it means a typo
    (`[env.alfa.vars]` meaning `alpha`) parses clean, renders nothing, and
    signals nothing anywhere — observably identical to a correct, intentionally
    dormant band. This probe surfaces that silent condition as a `warn` (never a
    `fail` — an unknown-env band is not an error) so a typo is at least visible.

    "Exists" means the union of names recorded in the `.winter/state.toml`
    registry and env directories discovered on disk, the latter via the shared
    `EnvDiscoveryService` `PortProbeService` also uses — the two probes must
    agree on what an env is. Deliberately NOT validated against `env_aliases` —
    `resolve_env_index` hashes any non-alias name into the index band, so a
    legitimately-named non-alias env (e.g. `[env.my-feature.vars]`) is valid
    config; checking against aliases would false-positive on every one of those.
    """

    def __init__(
        self,
        config: WorkspaceConfig,
        registry: IEnvIndexRegistry | None,
        env_discovery: EnvDiscoveryService,
    ) -> None:
        self._config = config
        self._registry = registry
        self._env_discovery = env_discovery

    def run(self) -> list[ProbeResult]:
        named = self._config.env_bands.named
        if not named:
            return []

        known_envs = self._known_envs()
        results: list[ProbeResult] = []
        for name in sorted(named):
            if name in known_envs:
                continue
            results.append(
                ProbeResult(
                    source=ENV_BANDS_SOURCE,
                    name=f"env band: {name}",
                    status=ProbeStatus.warn,
                    message=(f"[env.{name}.vars] names an env that does not exist — never rendered for any scope"),
                    remediation=(
                        f"If `{name}` is a typo for an existing env, fix the table name in .winter/config.toml. "
                        f"If it is intentionally kept for an env you plan to re-init later, no action is needed — "
                        f"the band stays dormant until an env named `{name}` exists again."
                    ),
                )
            )
        return results

    def _known_envs(self) -> set[str]:
        """Names an env band could legitimately target: on disk, or in the registry.

        The two sources are unioned rather than intersected — an env mid-`ws init`
        (registered, not yet on disk) or one predating the registry (on disk, not
        registered) is still a real env, and warning about a band pointing at it
        would be a false positive. `PortProbeService` owns the drift *between* the
        two views; this probe only asks whether the name is known to either.
        """
        known: set[str] = set(self._env_discovery.discover_env_dirs(self._config.workspace_root))
        if self._registry is not None:
            known.update(self._registry.all_assignments().keys())
        return known
