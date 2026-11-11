# The singleton instance

This workspace **dogfoods blizzard**: a real blizzard hub and one runner host of two runners drive blizzard's own
development (the `bz1`–`bz8` envs), and winter's from a sibling workspace, against the **real** GitHub forge. **The
instance** is what this file calls that deployment, and what "redeploy and restart the instance" means — **not** a
feature env, and not the per-feature-env verification stacks.

The instance is a **singleton**. Its target, access, and machine authority are owned by
[blizzard-binding.md](./blizzard-binding.md). Both halves run one pinned **build** — a release, or a dogfood build that
[feeding-the-dog](./workflows/feeding-the-dog.md) moves them onto — never whatever `master` holds: a landing reaches the
instance only once the user moves it onto a build that carries it.

## Where it runs

Resolve machine authority through [the binding](./blizzard-binding.md#machine-ownership-and-authority) before operating
anything below. All runtime paths in this file are on `xps`; they do not name local services on another checkout. Hub
hosting is owned by `paul-gross/blizzard-infra`, while the runner runtime sits beside the workspace on `xps`.

## Deployed surface

Paths below are `xps`'s. The instance directory, `~/projects/blizzard-blizzard`, holds the venv, the host directory, and
the collector; this workspace sits beside it at `~/projects/blizzard-workspace`, outside it so the env tree's unix
sockets stay under the 108-byte `sun_path` limit.

| Piece           | Location / value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hub**         | Production target and operator access: [workspace binding](./blizzard-binding.md)                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Hub deployment  | Owned end to end by the **`paul-gross/blizzard-infra`** repo (private; worktreed here only on a machine whose `config.local.toml` declares it). It holds the host, the image channel, the updater, rollback, and the operator entry points. Go there for anything about the host — none of it is restated here                                                                                                                                                                                                                    |
| Runner host     | One host, `xps`, running two runners split by harness — see [The host and its runners](#the-host-and-its-runners) for its ids, workspaces, envs, tokens, and unit                                                                                                                                                                                                                                                                                                                                                                 |
| Venv            | `~/projects/blizzard-blizzard/.venv` — the `blizzard` binary the host runs, and the operator CLI; installed from the **build's wheel** — a release's, or a dogfood build's `push`-run wheel — never a local build or an editable install                                                                                                                                                                                                                                                                                          |
| Build           | the build `blizzard-infra`'s `deploy/release.env` pins for the hub — a release, or a dogfood build's `sha-` image by digest, as [The hub's pin](#the-hubs-pin) describes — and the venv holds the same build's wheel. Nothing here records which: ask the instance. `curl -s https://blizzard.grosscode.net/api/health \| jq -r .version` for the hub, and `curl -s http://127.0.0.1:8431/api/health \| jq -r .version` on `xps` for the host; a dogfood build reports its dev version with its commit, `0.5.0.dev1420+aa786ded2` |
| Forge           | **real GitHub** — owner `paul-gross`; the hub's token lives on the host, never in this repo                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Supervision     | one systemd **user** unit, `blizzard-blizzard-runner.service` (unit file and drop-ins in `~/.config/systemd/user/`)                                                                                                                                                                                                                                                                                                                                                                                                               |
| Trace collector | `~/projects/blizzard-blizzard/otel-collector` — a docker compose OpenTelemetry Collector on `127.0.0.1:4318` that forwards both runners' spans to Honeycomb; see [The runners' trace collector](#the-runners-trace-collector)                                                                                                                                                                                                                                                                                                     |

### The host and its runners

Our runners are split by harness and subscription but share one daemon, host directory, store, port, and unit. General
runner inspection and controls belong to [usage runner guidance](../../.winter/ext/usage/context/runner-hosts.md).

|                     | Host `xps`                                                                                        |
| ------------------- | ------------------------------------------------------------------------------------------------- |
| Hub id              | `hs_01M521395MMCN3ANGMJVQPG1YK`                                                                   |
| Host directory      | `~/projects/blizzard-blizzard/runner` — `blizzard-host.toml`, store `data/host.db`, `.env`        |
| Port / `public_url` | `127.0.0.1:8431`, plus the tailnet origin                                                         |
| Systemd unit        | `blizzard-blizzard-runner.service`                                                                |
| Host token          | `BZ_HOST_TOKEN` in `~/projects/blizzard-blizzard/runner/.env` (unit drop-in `EnvironmentFile`)    |
| Trace export        | `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318` in `~/projects/blizzard-blizzard/runner/.env` |

|                                | Claude Code runner                                                    | OpenCode runner                                                                                                                                                                                                                                                             |
| ------------------------------ | --------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hub id                         | `rn_01M3MY1NGQR8WE1ZBRKNREFNE4`                                       | `rn_01M3JK4RX4BQJ74W7T3CK4NQ1B`                                                                                                                                                                                                                                             |
| Name                           | `r-claude`                                                            | `r-chatgpt`                                                                                                                                                                                                                                                                 |
| Workspace (`[[workspace]] id`) | `blizzard`, shared                                                    | `blizzard`, shared                                                                                                                                                                                                                                                          |
| Envs (`[[workspace]] envs`)    | `bz1`–`bz8`, one pool for both runners                                | `bz1`–`bz8`, one pool for both runners                                                                                                                                                                                                                                      |
| Agents (`[[runner]] agents`)   | 3, shared by `blizzard` and `winter`                                  | 3, shared by `blizzard` and `winter`                                                                                                                                                                                                                                        |
| Harness                        | `[runner.opencode] enabled = false`                                   | `[runner.claude_code] enabled = false`                                                                                                                                                                                                                                      |
| Subscription spent             | `anthropic`                                                           | `openai`                                                                                                                                                                                                                                                                    |
| Worker config                  | `~/projects/blizzard-blizzard/runner/worker-settings.json`            | `~/projects/blizzard-blizzard/runner/runners/r-chatgpt/worker-settings.json`; OpenCode 2 ignores `opencode-worker-config.json`                                                                                                                                              |
| OpenCode                       | —                                                                     | **2.2.6** at `~/.local/opt/opencode-2.2.6/opencode`, run as the operator's own managed service (`opencode service status`) under the operator's login and `~/.config/opencode/opencode.json`, whose `model` (`openai/gpt-6.1-sol`) is the fallback for a launch naming none |
| Hub token                      | `BZ_HUB_TOKEN_R_CLAUDE` in `~/projects/blizzard-blizzard/runner/.env` | `BZ_HUB_TOKEN_R_CHATGPT` in `~/projects/blizzard-blizzard/runner/.env`                                                                                                                                                                                                      |

Both runners serve the hub's **`blizzard` project** through one shared `[[workspace]]`, `blizzard`: root
`~/projects/blizzard-workspace`, envs `bz1`–`bz8`, one pool for both runners. They also serve the **`winter` project**,
each through a second binding to one shared `[[workspace]]`, `winter`: root `~/projects/winter-dev`, envs `bz1`–`bz8`,
one pool for both runners — `blizzard-host.toml` is the record of its settings. The `blizzard` pool reuses those env
names under another root, so no worktree is in both. Its `c5xlabs` repo records read their forge token from the secret
`github-forge-c5xlabs`, not `github-forge`, since a fine-grained token has one resource owner.

Both shared workspaces use `base_branch = master`. The host file declares the `blizzard` and `winter` workspaces, the
`anthropic` and `openai` subscriptions, and the two runners above. Keep each root's env pool in its one shared workspace
declaration rather than introducing another overlapping pool.

For authorized runner enrollment, renaming, or retirement, use the application's
`blizzard/docs/deployment/runner-auth.md` and `blizzard/docs/remote-runner.md` with this host's paths and identities.
Our policy for a new runner is to pause it at the hub before the host restart that starts it, and resume it only after
inspection confirms it is online. A host restart affects both projects
([post-delivery.md](./post-delivery.md#the-restart-is-fleet-wide)).

#### Recovery artifacts on `xps`

- `~/projects/blizzard-blizzard/runner/backup-host-consolidation-*/` — the store, host file, `.env`, worker config, and
  unit as they stood just before the gathering; `~/projects/blizzard-blizzard/runner/.env.pre-consolidation-*` is
  `~/projects/blizzard-blizzard/runner/.env` before the host token.
- The pre-host `blizzard-runner.toml.graduated` and `backup-host-graduation-*/`, from the graduation before that.

### The runners' trace collector

The runners use a collector on `xps`, separate from the hosted hub's private compose network. It forwards to the same
Honeycomb `blizzard` environment as the hub's. The runner spans land in the `blizzard-runner` dataset beside the hub's
`blizzard-hub`.

The tracing protocol, diagnostic fields, and replay procedure are owned by `blizzard/docs/deployment/tracing.md`; the
collector's local configuration and commands are below.

| Piece       | Value                                                                                                        |
| ----------- | ------------------------------------------------------------------------------------------------------------ |
| Runtime dir | `~/projects/blizzard-blizzard/otel-collector` — `compose.yaml`, `otel-collector.yaml`, `collector.env`       |
| Image       | `otel/opentelemetry-collector:0.162.0`, pinned to match `blizzard-infra`'s hosted collector                  |
| Listens     | `127.0.0.1:4318`, OTLP over HTTP — loopback only, nothing off `xps` reaches it                               |
| Secret      | `HONEYCOMB_API_KEY` in `collector.env` (mode `0600`), an ingest key for the Honeycomb `blizzard` environment |
| Supervision | docker `restart: unless-stopped`; `docker.service` is enabled, so it comes back after a reboot on its own    |
| Senders     | the host's `.env` sets `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318` for both runners                  |

`otel-collector.yaml` is `blizzard-infra`'s `deploy/otel-collector.yaml` with only its comments changed, and
`compose.yaml` is that repo's `otel-collector` service with `ports: ["127.0.0.1:4318:4318"]` in place of `expose` and no
network. Recreate the dir from there if it is ever lost; never commit `collector.env`.

Operate it from `~/projects/blizzard-blizzard/otel-collector`:

```bash
docker compose up -d                       # bring it up (also after editing either yaml)
docker compose restart                     # restart it (also after rotating the key in collector.env)
docker compose ps                          # is it running
docker compose logs --since 10m            # exporter errors — a bad key reads as a 401 from api.honeycomb.io
docker run --rm -e HONEYCOMB_API_KEY=x -v "$PWD/otel-collector.yaml:/c.yaml:ro" \
  otel/opentelemetry-collector:0.162.0 validate --config=/c.yaml   # check a config edit before applying it
```

Restarting the collector costs the runners nothing — no host restart, no worker interrupted. Changing the host's `.env`
is different: the host reads its environment only at startup, so it takes a host restart, which
[terminates the fleet workers both runners hold](./post-delivery.md).

**Confirm both runners are reporting:**

```bash
for r in r-claude r-chatgpt; do ~/projects/blizzard-blizzard/.venv/bin/blizzard runner traces status --dir ~/projects/blizzard-blizzard/runner --runner "$r"; done
```

Both should show tracing on, exporting to `http://127.0.0.1:4318`, with no lasting export error. An idle runner can have
an old last-export time.

### Traps on `xps`

**`blizzard-blizzard-hub.service` is stopped and `disable`d — do not start it.** `~/projects/blizzard-blizzard/hub/`
still exists on disk, but `~/projects/blizzard-blizzard/hub/data/hub.db` is a copy frozen at the migration, not what the
fleet reads. Starting it serves that frozen state on `127.0.0.1:8421` and looks entirely healthy doing so. Nothing
refreshes it, and it drifts further every day.

**`127.0.0.1:8421` answers nothing.** Any tool or dev surface still aimed there fails to connect rather than silently
reaching the wrong fleet.

Our unit uses `Restart=on-failure`; a SIGTERM can leave it down. For feature-env restarts use
`winter service restart <env>/runner`, not a process-name pattern that could also stop this deployment. General host
intervention safety is owned by [usage runner guidance](../../.winter/ext/usage/context/runner-hosts.md).

### Reaching the host's web surface

The host binds loopback and declares `http://127.0.0.1:8431` plus its tailnet origin. Federation setup is owned by
`blizzard/docs/deployment/human-auth.md` §Runner-side federation. Our local bindings are:

- **The host is already reachable on the tailnet.** `tailscale serve` fronts 8431 and preserves the browser's `Host`,
  and its `public_url` carries the tailnet origin plus `trusted_proxies = ["127.0.0.1"]` for the address `serve`
  connects from. Confirm mappings with `tailscale serve status`.
- **Changing it costs a fleet worker.** The host reads its config only at startup, so a widened set takes effect on
  restart and reaches the hub on the first host report after it — and restarting
  [terminates every running fleet worker](./post-delivery.md).

## Redeploy + restart runbook — moving the instance onto a release

Read [the workspace binding](./blizzard-binding.md) for the approved CLI, shell variables, and sign-in first. Follow
[workflows/release-deploy.md](./workflows/release-deploy.md) for sequencing, upgrade boundaries, and stop conditions.
This section supplies our pins, paths, and commands; it does not authorize a move from another machine.

### The hub's pin

The hub runs the build `blizzard-infra`'s `deploy/release.env` pins — a release by its version, or a dogfood build by
its `sha-<full sha>` tag — by index digest. Moving the hub is a change to that file on `blizzard-infra`'s `master`, then
`scripts/deploy.sh` from a `blizzard-infra` worktree: the install syncs the pin to the host and runs the updater, which
converges the hub on it, forward or back. Merging the change moves nothing until the deploy runs. A `BLIZZARD_HUB_IMAGE`
line in the host's `/srv/blizzard/deploy/.env` is a hold that wins over the pin — the updater logs
`held at …; release pin … not applied` — and is normally absent. `blizzard-infra`'s `docs/deployment.md` §The pin and
the hold and `docs/rollback.md` own the commands and the way back.

Moving the hub needs the operator's `blizzard-prod-admin` session, which no fleet worker holds: an agent driving the
move asks for `! aws sso login --profile blizzard-prod-admin` to be run, and stops if it is not. **Proof:**
`curl -s https://blizzard.grosscode.net/api/health | jq -r .version` equals the target in PEP 440 normal form —
`0.2.0rc2` for `v0.2.0-rc.2` — and `GET /api/ready` reports ready.

### The runner host

One install, migration, and restart move both runners. Use the following local commands in the release workflow's order;
`blizzard/docs/upgrade.md` and `blizzard/docs/rollback.md` own the product's migration requirements.

```bash
# 1. confirm the hub runs the release being installed, or a newer one inside the window the
#    `blizzard` repo's docs/versioning.md states. A runner host never runs ahead of its hub.
curl -s https://blizzard.grosscode.net/api/health | jq -r .version

# 2. fetch the release's own wheel — never a build from projects/blizzard
gh release download v<version> --repo paul-gross/blizzard --pattern 'blizzard-*.whl' --dir <scratch>

# 3. install it into the runtime venv
uv pip install --python ~/projects/blizzard-blizzard/.venv --reinstall <scratch>/blizzard-*.whl

# 4. migrate the host's store, data/host.db (idempotent — safe every move). There is
#    no local hub store to migrate; the hosted hub migrates itself on boot.
~/projects/blizzard-blizzard/.venv/bin/blizzard runner migrate --dir ~/projects/blizzard-blizzard/runner

# 5. check the host directory; require `host ok` before restarting
~/projects/blizzard-blizzard/.venv/bin/blizzard runner host --check --dir ~/projects/blizzard-blizzard/runner

# 6. re-mint every graph the release range changed — see below. Reads the graph from the
#    LOCAL venv and posts it to the HOSTED hub, so it needs $BZ_HUB_URL and a
#    login. Sits BEFORE the runner restarts (which terminate fleet workers).
~/projects/blizzard-blizzard/.venv/bin/blizzard hub graph mint --hub-url "$BZ_HUB_URL" --tenant "$FLEET_TENANT" \
  ~/projects/blizzard-blizzard/.venv/lib/python3*/site-packages/blizzard/hub/graphs/<graph>/graph.yaml

# 7. restart the host. A fleet-worker shell has no user session bus, so
#    systemctl --user fails there ("Failed to connect to bus") until these are set:
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
systemctl --user restart blizzard-blizzard-runner.service
```

**Skip step 6 when the release range did not touch `src/blizzard/hub/graphs/`** — check with the diff below rather than
minting blindly.

Verify the host's version equals the target release in PEP 440 normal form, not merely that HTTP answers:

```bash
curl -s http://127.0.0.1:8431/api/health | jq -r .version   # -> 0.2.0rc2 for v0.2.0-rc.2
```

Any other version is a failed proof. Then check `systemctl --user is-active blizzard-blizzard-runner.service` for the
unit, `curl -s http://127.0.0.1:8431/api/ready` for both runners `serving` under their hub ids, and that each runner is
actually reaching the hub — a runner that cannot reach it still reports healthy:

```bash
journalctl --user -u blizzard-blizzard-runner.service --since "-2 min" | grep -c '"event": "tick end"'
for r in r-claude r-chatgpt; do ~/projects/blizzard-blizzard/.venv/bin/blizzard runner status --dir ~/projects/blizzard-blizzard/runner --runner "$r" | head -5; done
```

Last, confirm both runners still export traces — `blizzard runner traces status --runner` on each, per
[Confirm both runners are reporting](#the-runners-trace-collector) — and that `docker compose ps` in
`~/projects/blizzard-blizzard/otel-collector` shows the collector up. A host restart never touches the collector, but it
is the moment a dropped `.env` line shows.

Nothing in this workspace records the move: the hub's pin in `blizzard-infra` is the deployment's record, and each
half's `/api/health` reports what it actually runs.

### Going back

Going back is the same move towards an older release, **runner host first and hub after**. The build to go back to is
the one the hub reported before the move, which `blizzard-infra`'s `deploy/release.env` history also holds. The host's
store may be ahead of what the older release expects, so before its wheel is installed, with the unit stopped
throughout:

1. Read the head revision the older release expects: install its wheel into a throwaway venv and run
   `blizzard runner migrate --head` from it.
2. Downgrade on the **current** venv, which holds the downgrade code:
   `~/projects/blizzard-blizzard/.venv/bin/blizzard runner migrate --dir ~/projects/blizzard-blizzard/runner --down <revision>`.
3. Install the older wheel, check the directory, and restart, as in a forward move.

A downgrade that fails leaves the unit stopped on the current venv, which is restarted unchanged. The hub's way back —
its store downgraded on the outgoing image before the swap — is
[workflows/release-deploy.md](./workflows/release-deploy.md)'s and the `blizzard` repo's `docs/rollback.md`'s.

### Re-minting changed graphs

Our deployment move includes minting the packaged graphs the range changed. Product graph synchronization and inlining
behavior belong to `blizzard/docs/deployment/install.md` and the installed `hub graph mint --help`. For our release
move, identify changed graph directories from the tags rather than the checkout's current `master`:

```bash
git -C projects/blizzard fetch --tags origin
git -C projects/blizzard diff --name-only v<previous>..v<version> -- src/blizzard/hub/graphs/
```

Diff the whole graph directory, including prompt files. Mint from the installed venv, never from `projects/blizzard`, so
the definition matches the deployed build. Read back each minted definition and confirm the intended graph and prompt
changes landed before declaring the move complete.

## The hub's work sources

Our forge is `github`, owned by `paul-gross`, and its PAT is stored as the hub secret `github-forge`. The PAT selects
repos explicitly; its coverage must be updated before a newly authorized source is added.

The `blizzard`, `blizzard-mock`, `blizzard-infra`, and `blizzard-context` sources and repositories are linked to the
`blizzard` project. The `winter*` sources and repositories are linked to `winter`, all with `annotate true`. These
bindings are managed at the hub, not in `blizzard-infra`'s deployment files. Configuration procedures belong to
`blizzard/docs/deployment/work-sources.md` and `blizzard/docs/deployment/install.md`.

For routine fleet operation, enter [blizzard-usage](../../.winter/ext/usage/index.md) with our
[binding](./blizzard-binding.md). Our [operator workflows](./workflows/index.md) own local policy and approval
choreography; [hub-data-modes.md](./hub-data-modes.md) owns development-data safety.
