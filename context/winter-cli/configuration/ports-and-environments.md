# Ports & environments

Winter assigns each feature environment a port band derived from its index, and computes per-env derived variables at runtime. These keys live in `.winter/config.toml`.

## Port allocation

```toml
# Port allocation — all four keys are optional; shown here with their defaults.
base_port = 4000          # start of this workspace's port band; set a different value to separate co-located workspaces
ports_per_env = 20        # ports per feature env; per-env base = base_port + index * ports_per_env
env_aliases = [           # fixed-index env names (1..N); aliases get stable slots, all other names hash into the remainder
  "alpha", "beta", "gamma", "delta", "epsilon",
  "zeta", "eta", "theta", "iota", "kappa",
]
envs_per_workspace = 48   # max feature-env index (1..envs_per_workspace); must be >= len(env_aliases) + 2
```

## Env var bands

Three kinds of scope-bound band can be declared in `.winter/config.toml`. Values support `${...}` substitution; literal text passes through unchanged. These variables are computed at runtime by `EnvProvisionerService` and injected into every provider subprocess by `winter service`. To inspect the computed values for a scope, use `winter env <scope>` (see [usage/env.md](../usage/env.md)).

```toml
[env.workspace.vars]
SHARED_DB_PORT = "${WINTER_WORKSPACE_PORT_BASE+10}"   # shared workspace service

[env.feature.vars]
WTS_WEB_PORT = "${WINTER_PORT_BASE+10}"
WTS_API_PORT = "${WINTER_PORT_BASE+11}"
WTS_DB_PORT  = "${WINTER_PORT_BASE+12}"
DATABASE_URL = "postgresql://wts:wts@localhost:${WTS_DB_PORT}/wts-${WINTER_ENV}"  # reuses WTS_DB_PORT and WINTER_ENV

[env.alpha.vars]
WTS_WEB_PORT = "8421"   # alpha only — every other env keeps the feature-band value
```

**Workspace band (`[env.workspace.vars]`)** — rendered for both the `workspace` scope and every feature env. Because `WINTER_PORT_BASE` is omitted from the workspace scope, workspace-band entries that reference a port should use `${WINTER_WORKSPACE_PORT_BASE+N}`.

**Feature band (`[env.feature.vars]`)** — rendered only for feature envs; never emitted for the `workspace` scope.

**Per-env band (`[env.<name>.vars]`)** — rendered only for the one feature env it names, on top of the feature band. Use it to point a single env at a fixed endpoint or a different backing service while its siblings keep the derived per-env value. `workspace` and `feature` are band names, so an env named either cannot carry a per-env band. A band naming an env that does not exist is inert — never looked up, never an error — so an override can outlive the env it was written for.

**Resolution per scope:**

| Scope | Variables emitted |
|-------|-----------------|
| `workspace` | `WINTER_ENV`, `WINTER_ENV_INDEX`, `WINTER_WORKSPACE_PORT_BASE`, `WINTER_SERVICE_PREFIX` + workspace-band entries only (per-env bands are never rendered here) |
| `<feature>` | `WINTER_ENV`, `WINTER_ENV_INDEX`, `WINTER_PORT_BASE`, `WINTER_WORKSPACE_PORT_BASE`, `WINTER_SERVICE_PREFIX` + workspace band rendered first, then feature band, then `[env.<name>.vars]` for that env last (each layer wins key collisions against the ones below it) |

An entry may reference keys already rendered by the bands below it: a feature-band entry may reference workspace-band keys, and a per-env entry may reference either.

**Precedence with the local overlay.** `.winter/config.local.toml` merges into the committed config before bands are resolved (see [config-files.md](./config-files.md#local-overlay-winterconfiglocaltoml)), so the effective order lowest to highest is `[env.feature.vars]` < `config.local.toml` feature overlay < `[env.<name>.vars]` — a per-env band wins over a locally-overlaid feature band.

**Migration from `[env.vars]` (hard break).** A config that still declares the legacy `[env.vars]` table is rejected with a `ConfigError` at startup. Migrate by moving entries to `[env.feature.vars]` (for feature-env variables) or `[env.workspace.vars]` (for shared workspace variables). There is no alias or fallback — the failure is intentional so no variables are silently dropped.

**Token grammar.** Two forms are supported:

- `${NAME}` — substitutes the string value of `NAME`.
- `${NAME+N}` — adds a non-negative integer `N` to `NAME` (which must parse as an integer).

`NAME` resolves against an **accumulating scope**: seeded with the base vars available for the scope (see table above) and grown by each rendered band entry **in TOML declaration order** — so a later entry can reuse an earlier one (as `DATABASE_URL` reuses `WTS_DB_PORT` above). `WINTER_PORT_BASE` is not special: `${WINTER_PORT_BASE+N}` is just the base-var case.

Resolution is computed at dispatch time by `EnvProvisionerService` — concrete values are injected into the subprocess environment. An undefined name, `+N` applied to a non-integer value, or any other malformed `${...}` token is a fatal error surfaced when the command runs.

## Index reservation

The env name → index mapping itself is recorded in [`.winter/state.toml`](./config-files.md#state-registry). Two indices are reserved and never assigned to a regular feature env:

Index 0 (`base_port`..`base_port+ports_per_env-1`) is the **workspace shared-service band**, exposed to providers as `WINTER_WORKSPACE_PORT_BASE`. It is never assigned to a feature env — feature envs start at index 1 — so workspace-scoped services (shared db, broker) get a stable port window that sits below every feature env's. The slot immediately after the aliases (`N+1`, default index 11 with the 10-alias default) is reserved as a buffer between the fixed alias band and the hash band; this is why the invariant requires `envs_per_workspace >= len(env_aliases) + 2` (not `+1`).
