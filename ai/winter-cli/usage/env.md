# `winter env` — print the runtime environment for a scope

```
winter env <scope>
```

Print the complete runtime environment for *scope* as sourceable `export KEY=value` lines, one per variable, in the order the provisioner returns them:

```
export WINTER_ENV=alpha
export WINTER_ENV_INDEX=1
export WINTER_PORT_BASE=4060
export WINTER_WORKSPACE_PORT_BASE=4000
export MY_APP_PORT=4061
```

*scope* is either a feature-env name (e.g. `alpha`, `beta`) or the reserved word `workspace` for the workspace-level singleton scope.

## Usage

**Source into the current shell:**

```bash
source <(winter env alpha)
```

**Source in a script or Dockerfile:**

```bash
eval "$(winter env alpha)"
```

**Inspect the environment for a scope:**

```bash
winter env alpha          # feature env
winter env workspace      # workspace singleton scope
```

## Variables printed

Every scope always includes the four base vars:

| Variable | Meaning |
|----------|---------|
| `WINTER_ENV` | Scope name (e.g. `alpha`; `workspace` for the singleton scope) |
| `WINTER_ENV_INDEX` | Stable index used for port allocation (0 for workspace) |
| `WINTER_PORT_BASE` | Port-band start for this scope (`base_port + index * ports_per_env`) |
| `WINTER_WORKSPACE_PORT_BASE` | Port-band start for index 0 (the workspace port base) |

Followed by any `[env.vars]` entries declared in `.winter/config.toml`, rendered in declaration order. Each entry may reference the four base vars or any earlier `[env.vars]` entry via `${NAME}` / `${NAME+N}` tokens — see [ports-and-environments.md](../configuration/ports-and-environments.md#per-env-derived-variables) for the full token grammar.

## Exit codes

| Exit code | Meaning |
|-----------|---------|
| 0 | Success — every line written to stdout. |
| 1 | Scope unknown or env-vars template error — message on stderr, no output. |

## Notes

- Output is shell-safe: values are quoted with `shlex.quote` so special characters do not break the `source`/`eval` recipe.
- `winter env` is the canonical way to load an env's variables into a shell. Services run by `winter service up` receive the same variable set injected directly into the provider subprocess environment — no file sourcing needed.
