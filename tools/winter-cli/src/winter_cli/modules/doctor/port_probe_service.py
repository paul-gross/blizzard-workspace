from __future__ import annotations

from winter_cli.config.models import WorkspaceConfig
from winter_cli.core.filesystem import IFilesystemReader
from winter_cli.modules.doctor.env_discovery_service import EnvDiscoveryService
from winter_cli.modules.doctor.models import ProbeResult, ProbeStatus
from winter_cli.modules.workspace.env_index import is_valid_env_index
from winter_cli.modules.workspace.env_index_registry import IEnvIndexRegistry

PORT_SOURCE = "port"

# Index 0 is reserved and must never appear in the registry.  It is
# earmarked for a future single-slot "local" environment — a pre-seeded
# shared dataset / workspace area — distinct in purpose from the N+1 buffer
# slot between aliases and the hash band.
_RESERVED_INDEX = 0


class PortProbeService:
    """Probes for port-allocation config invariants and registry drift.

    Two categories of checks:

    1. **Config invariant** — validates ``envs_per_workspace >= len(env_aliases) + 2``.
       Config load already raises on hard violations, so this probe is a belt-and-
       suspenders check that also gracefully handles the edge cases where doctor
       runs against a partially-valid or defaulted config.

    2. **Registry drift** — cross-checks the ``.winter/state.toml`` registry against
       discovered env directories on disk and the configured index range, flagging:
       (a) stale entries whose env directory no longer exists,
       (b) env directories with no registry entry (unregistered / pre-registry),
       (c) recorded indices outside ``1..envs_per_workspace`` or equal to the
           reserved index 0,
       (d) two envs sharing the same index (duplicate assignment).
    """

    def __init__(
        self,
        config: WorkspaceConfig,
        fs: IFilesystemReader,
        registry: IEnvIndexRegistry | None,
        env_discovery: EnvDiscoveryService,
    ) -> None:
        self._config = config
        self._fs = fs
        self._registry = registry
        self._env_discovery = env_discovery

    def run(self) -> list[ProbeResult]:
        results: list[ProbeResult] = []
        results.append(self._probe_invariant())
        results.extend(self._probe_registry_drift())
        return results

    # ── invariant ─────────────────────────────────────────────────────────

    def _probe_invariant(self) -> ProbeResult:
        """Validate envs_per_workspace >= len(env_aliases) + 2."""
        n_aliases = len(self._config.env_aliases)
        n_envs = self._config.envs_per_workspace
        required = n_aliases + 2
        if n_envs >= required:
            return ProbeResult(
                source=PORT_SOURCE,
                name="port config invariant",
                status=ProbeStatus.pass_,
                message=(f"envs_per_workspace={n_envs} >= len(env_aliases)+2={required}"),
            )
        return ProbeResult(
            source=PORT_SOURCE,
            name="port config invariant",
            status=ProbeStatus.fail,
            message=(
                f"envs_per_workspace={n_envs} < len(env_aliases)+2={required} "
                f"— not enough slots for all alias envs plus the hash band"
            ),
            remediation=(
                f"Increase envs_per_workspace to at least {required} or reduce env_aliases in .winter/config.toml."
            ),
        )

    # ── registry drift ────────────────────────────────────────────────────

    def _probe_registry_drift(self) -> list[ProbeResult]:
        """Cross-check registry entries, on-disk env dirs, and configured ranges."""
        if self._registry is None:
            # No registry injected — nothing to cross-check.
            return []

        assignments = self._registry.all_assignments()
        # Edge case: an env directory with no per-repo worktrees yet (none added,
        # or all removed) has no .git-file child and is therefore not discovered
        # as an env — unlike the former `.winter.env` marker, which an empty env
        # shell could still carry.  This only affects the on-disk count and the
        # unregistered-dir check (b); a registered env is still cross-checked from
        # the registry side, so a half-created env is not mistaken for stale.
        env_dirs = self._env_discovery.discover_env_dirs(self._config.workspace_root)
        n_envs = self._config.envs_per_workspace

        results: list[ProbeResult] = []

        # (a) Stale entries: registry has a name, but env dir is gone.
        for name, idx in assignments.items():
            env_path = self._config.workspace_root / name
            if not self._fs.is_dir(env_path):
                results.append(
                    ProbeResult(
                        source=PORT_SOURCE,
                        name=f"registry: {name}",
                        status=ProbeStatus.warn,
                        message=(f"registry entry (index={idx}) has no env directory ({env_path}) — stale entry"),
                        remediation=(
                            f"Run `winter ws destroy {name}` to remove the stale registry entry, "
                            f"or re-run `winter ws init {name}` to recreate the env."
                        ),
                    )
                )

        # (b) Unregistered env dirs: dir exists but not in registry.
        registered_names = set(assignments.keys())
        for env_name in env_dirs:
            if env_name not in registered_names:
                results.append(
                    ProbeResult(
                        source=PORT_SOURCE,
                        name=f"registry: {env_name}",
                        status=ProbeStatus.warn,
                        message=(
                            "env directory exists but has no registry entry — created before the registry or manually"
                        ),
                        remediation=(f"Run `winter ws init {env_name}` to record it in the registry."),
                    )
                )

        # (c) Out-of-range or reserved indices.
        env_aliases = self._config.env_aliases
        for name, idx in assignments.items():
            if not is_valid_env_index(idx, env_aliases, n_envs):
                if idx == _RESERVED_INDEX:
                    message = f"recorded index {idx} is reserved (index 0 must never be assigned)"
                elif idx == len(env_aliases) + 1:
                    message = f"recorded index {idx} is the buffer slot (N+1={idx}) — never assigned by the allocator"
                else:
                    message = (
                        f"recorded index {idx} is outside the valid range "
                        f"(alias slots 1..{len(env_aliases)}, "
                        f"hash band {len(env_aliases) + 2}..{n_envs})"
                    )
                results.append(
                    ProbeResult(
                        source=PORT_SOURCE,
                        name=f"registry: {name}",
                        status=ProbeStatus.warn,
                        message=message,
                        remediation=(
                            f"Run `winter ws destroy {name}` and re-init to get a valid index, "
                            f"or increase envs_per_workspace in .winter/config.toml."
                        ),
                    )
                )

        # (d) Duplicate indices: two envs sharing the same index.
        seen_indices: dict[int, str] = {}
        for name, idx in sorted(assignments.items()):
            if idx in seen_indices:
                first = seen_indices[idx]
                results.append(
                    ProbeResult(
                        source=PORT_SOURCE,
                        name=f"registry: {name}",
                        status=ProbeStatus.warn,
                        message=(f"index {idx} is shared with env '{first}' — duplicate assignment detected"),
                        remediation=(f"Run `winter ws destroy {name}` and re-init to get a unique index."),
                    )
                )
            else:
                seen_indices[idx] = name

        if not results:
            n_registered = len(assignments)
            n_dirs = len(env_dirs)
            results.append(
                ProbeResult(
                    source=PORT_SOURCE,
                    name="registry drift",
                    status=ProbeStatus.pass_,
                    message=f"{n_registered} registered, {n_dirs} on disk — consistent",
                )
            )

        return results
