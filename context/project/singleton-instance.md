# The singleton instance

This workspace **dogfoods blizzard**: a real blizzard hub and one runner host of two runners drive blizzard's own
development (the `r-claude-1`–`r-claude-6` and `r-chatgpt-1`–`r-chatgpt-4` envs), and winter's from a sibling workspace,
against the **real** GitHub forge. **The instance** is what this file calls that deployment, and what "redeploy and
restart the instance" means — **not** a feature env, and not the per-feature-env verification stacks.

The instance is a **singleton**, and no checkout's own: every clone of this workspace works against the same one hub and
the same one runner host. Both run one pinned **build** — a release, or a dogfood build that
[feeding-the-dog](./workflows/feeding-the-dog.md) moves them onto — never whatever `master` holds: a landing reaches the
instance only once the user moves it onto a build that carries it.

## Where it runs

Its two halves run in different places, and that governs everything below.

| Half                  | Where                                                                                | Who moves it                                  | Reachable from                       |
| --------------------- | ------------------------------------------------------------------------------------ | --------------------------------------------- | ------------------------------------ |
| **Hub**               | hosted, `https://blizzard.grosscode.net`                                             | the operator, by moving its pin               | any checkout, through its API        |
| **Runner host `xps`** | the machine `xps`, a Dell XPS, beside its checkout — one daemon running both runners | **you**, by hand, onto the build the hub runs | the checkout on `xps`, and none else |

**Your `AGENTS.local.md` names the machine you are on.** A checkout on `xps` holds the runner host, and all of this file
is its to act on. Any other checkout — one whose `AGENTS.local.md` names another machine, or that has none — holds no
part of the instance: the `../runner`, `../.venv`, `../hub`, and `../otel-collector` paths below name nothing there.
Refuse every runner-host task on it and hand it to the user to run on `xps`: moving the instance onto a build, by the
[runbook](#redeploy--restart-runbook--moving-the-instance-onto-a-release) or by
[feeding-the-dog](./workflows/feeding-the-dog.md); restarting or reconfiguring the host; adding, renaming, or retiring a
runner; operating its trace collector. Hub-API work — the [operator CLI](#the-operator-cli-needs-a-session),
marshalling, the sweeps — runs from any checkout.

## Deployed surface

Paths below are `xps`'s, relative to its workspace root; `../runner` is a sibling of the directory holding
`.winter/config.toml`.

| Piece           | Location / value                                                                                                                                                                                                                                                                                               |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hub**         | **`https://blizzard.grosscode.net`** — health `GET /api/health`, readiness `GET /api/ready`. GitHub OAuth; reads need a session                                                                                                                                                                                |
| Hub deployment  | Owned end to end by the **`paul-gross/blizzard-infra`** repo (private; worktreed here only on a machine whose `config.local.toml` declares it). It holds the host, the image channel, the updater, rollback, and the operator entry points. Go there for anything about the host — none of it is restated here |
| Runner host     | One host, `xps`, running two runners split by harness — see [The host and its runners](#the-host-and-its-runners) for its ids, workspaces, envs, tokens, and unit                                                                                                                                              |
| Venv            | `../.venv` — the `blizzard` binary the host runs, and the operator CLI; installed from the **build's wheel** — a release's, or a dogfood build's `push`-run wheel — never a local build or an editable install                                                                                                 |
| Release         | **dogfood `0.3.0.dev1011+e1efe1df1`** (`master` at `e1efe1df1`) — the exact build both halves run: the venv holds that commit's `push`-run wheel, and the hub runs its `sha-` image by digest, pinned as [The hub's pin](#the-hubs-pin) describes                                                              |
| Forge           | **real GitHub** — owner `paul-gross`; the hub's token lives on the host, never in this repo                                                                                                                                                                                                                    |
| Supervision     | one systemd **user** unit, `blizzard-blizzard-runner.service` (unit file and drop-ins in `~/.config/systemd/user/`)                                                                                                                                                                                            |
| Trace collector | `../otel-collector` — a docker compose OpenTelemetry Collector on `127.0.0.1:4318` that forwards both runners' spans to Honeycomb; see [The runners' trace collector](#the-runners-trace-collector)                                                                                                            |

### The host and its runners

The fleet is split by subscription: each runner binds exactly one coding harness and spends exactly one plan, so when a
plan's quota runs out the hub brakes only the runner spending it, and the other keeps working. Both runners run in **one
host**, `xps`: one daemon, one host directory, one store, one port, and one unit. The host is a hub record of its own
holding both runners, and it reports both workspaces and both subscriptions to the hub on every host tick.

|                     | Host `xps`                                                              |
| ------------------- | ----------------------------------------------------------------------- |
| Hub id              | `hs_01M521395MMCN3ANGMJVQPG1YK`                                         |
| Host directory      | `../runner` — `blizzard-host.toml`, store `data/host.db`, `.env`        |
| Port / `public_url` | `127.0.0.1:8431`, plus the tailnet origin                               |
| Systemd unit        | `blizzard-blizzard-runner.service`                                      |
| Host token          | `BZ_HOST_TOKEN` in `../runner/.env` (unit drop-in `EnvironmentFile`)    |
| Trace export        | `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318` in `../runner/.env` |

|                                | Claude Code runner                          | OpenCode runner                                                                        |
| ------------------------------ | ------------------------------------------- | -------------------------------------------------------------------------------------- |
| Hub id                         | `rn_01M3MY1NGQR8WE1ZBRKNREFNE4`             | `rn_01M3JK4RX4BQJ74W7T3CK4NQ1B`                                                        |
| Name                           | `r-claude`                                  | `r-chatgpt`                                                                            |
| Workspace (`[[workspace]] id`) | `blizzard-claude`                           | `blizzard-chatgpt`                                                                     |
| Envs (`[[workspace]] envs`)    | `r-claude-1`–`r-claude-6`                   | `r-chatgpt-1`–`r-chatgpt-4`                                                            |
| Agents (`[[runner]] agents`)   | 3, shared by `blizzard` and `winter`        | 3, shared by `blizzard` and `winter`                                                   |
| Harness                        | `[runner.opencode] enabled = false`         | `[runner.claude_code] enabled = false`                                                 |
| Subscription spent             | `anthropic`                                 | `openai`                                                                               |
| Worker config                  | `../runner/worker-settings.json`            | `../runner/runners/r-chatgpt/` — `worker-settings.json`, `opencode-worker-config.json` |
| Hub token                      | `BZ_HUB_TOKEN_R_CLAUDE` in `../runner/.env` | `BZ_HUB_TOKEN_R_CHATGPT` in `../runner/.env`                                           |

Both runners also serve the hub's **`winter` project** (every `paul-gross/winter*` source and repo), each through a
second binding to one shared `[[workspace]]`, `winter`: root `~/projects/winter-workspace`, envs `bz1`–`bz4`, one pool
for both runners. Its envs are hyphen-free because that workspace's per-env Postgres slice puts the env name into a SQL
identifier (`wts_<env>`), and its hand-used `alpha`–`epsilon` envs are never in the pool.

**A runner's agents are one count across its bindings.** A free agent takes the next ready chunk, in the hub's
ready-queue order, of any bound project whose workspace has a free env, and holds that env for its lifetime. Set it as
`[[runner]] agents`; a per-binding `agents` is deprecated, and the host logs a warning while one remains.

Both blizzard workspaces share `root` = this workspace and `base_branch = master`, and each is named for the runner
bound to it; the host sets `hub_url = "https://blizzard.grosscode.net"` for both runners, and runs the venv. The host
file holds the host keys at the top, then two `[[workspace]]`, two `[[subscription]]`, and two `[[runner]]` tables, each
`[[runner]]` carrying its hub id, its name, its subscription, its project binding, and every `[runner.*]` table. Health
is `GET /api/health` on 8431. Every per-runner `blizzard runner …` verb — `status`, `traces status`, `traces replay`,
and the rest — takes `--runner <name|id>` here, since the host runs more than one.

**Operator verbs take the hub id, never the name** — `hub runner show r-claude` answers `unknown runner`, and
`hub runner list` maps each name to its id; `hub runner-host show hs_01M521395MMCN3ANGMJVQPG1YK` lists the host with
both. The id never changes, and is the `[[runner]] id` in the host file: a host refuses to start a runner whose store
names another. The name is the `[[runner]] name`, a label that also names the runner's token variable,
`BZ_HUB_TOKEN_<NAME>` (upper-cased, every character outside `A-Z0-9` replaced by `_`).

**Keep the two workspaces' env lists disjoint.** Both name the same root, so an env listed in both lets both runners
drive one worktree.

**Which runner a chunk lands on is the harness set's call.** A runner is eligible for a chunk only when it can serve
every runner node the chunk can still reach, so a graph whose sessions accept only `claude_code` goes to `r-claude`, one
accepting only `opencode` to `r-chatgpt`, and one accepting both to whichever claims first. A session with no harness
set runs under the claiming runner's default — its one enabled harness — so it too goes to whichever claims first.

**Adding a runner** is
`blizzard runner add <name> --project blizzard --workspace <workspace-id> --agents <n> --dir
../runner`, under the
host's own token with no sign-in. It mints the runner under `xps` at the hub, writes its `BZ_HUB_TOKEN_<NAME>` to
`../runner/.env`, creates `../runner/runners/<name>/`, and appends its `[[runner]]`. A new workspace is a
`[[workspace]]` you add to the host file first. `blizzard/docs/deployment/runner-auth.md` §Adding and retiring a host's
runners owns the rest. **Pause it before the host restart that starts it** — `hub runner pause <id>` takes a runner that
has never connected, so it boots braked rather than claiming on its first tick. Resume it once `hub runner list` shows
it `online`.

**Renaming a runner** is editing its `[[runner]] name` and renaming its `BZ_HUB_TOKEN_<NAME>` in `.env` together, then
restarting the host — the id stays; rename one without the other and the host finds no token for the runner. The restart
terminates the fleet workers both runners hold ([post-delivery.md](./post-delivery.md#the-restart-is-fleet-wide)).

**Retiring a runner** is `blizzard runner retire --runner <name|id> --dir ../runner`: it retires the runner at the hub
under the host's token, forgets its identity in the host's store, and removes its `[[runner]]`. Expect it to refuse
listing held chunks even when every one is `done`: a chunk that finishes normally never releases its route. Confirm each
listed chunk is `done` or `stopped` in `hub status`, then re-run with `--force` — on a terminal chunk the release only
records itself, while a `running` one would go back to the queue. Then delete the runner's token line from `.env` rather
than leaving its revoked token on disk. **Never `hub runner-host retire` the host** to take one runner out: it retires
every runner the host holds.

#### What the consolidation left behind

The two runners ran as two hosts of one until they were gathered into `xps`. What that left is kept only to go back by:

- `../runner-opencode` — r-chatgpt's former host directory. Its store is set aside as `data/host.db.<timestamp>`, its
  token is gone from its `.env`, and its `blizzard-host.toml` still names a runner that would halt for want of a token.
- `blizzard-blizzard-runner-opencode.service` — its former unit, stopped and `disable`d. Do not start it.
- `backup-host-consolidation-*/` in both directories — each store, host file, `.env`, worker config, and unit as they
  stood just before the gathering; `../runner/.env.pre-consolidation-*` is `../runner/.env` before the host token.
- The pre-host `blizzard-runner.toml.graduated` and `backup-host-graduation-*/`, from the graduation before that.

Going back from the gathering is: stop the unit, restore both directories from `backup-host-consolidation-*/`, re-enable
and start the second unit, and release both runners from the host at the hub
(`hub runner-host release
hs_01M521395MMCN3ANGMJVQPG1YK <runner_id>`, once per runner) — never `hub runner-host retire`.
It is clean only before the runners do work in `xps`; past that, the restored stores are older than the hub's view of
them.

### The runners' trace collector

Both runners tell their closed leases into the hub's step traces (`blizzard/docs/deployment/tracing.md` §Runner spans).
The hosted hub's collector accepts only the hub on its own compose network, so the runners send to a collector of their
own on `xps`, which forwards to the same Honeycomb `blizzard` environment as the hub's. The runner spans land in the
`blizzard-runner` dataset beside the hub's `blizzard-hub`.

**Keep it up.** A runner whose collector is down keeps claiming and working — tracing never blocks a tick — so a dead
collector fails silently: spans stop arriving and nothing else changes. Each failed export reaches the hub's event log
as `trace-export-failed`, and the sweep's cursor holds until the collector is back.

| Piece       | Value                                                                                                        |
| ----------- | ------------------------------------------------------------------------------------------------------------ |
| Runtime dir | `../otel-collector` — `compose.yaml`, `otel-collector.yaml`, `collector.env`                                 |
| Image       | `otel/opentelemetry-collector:0.162.0`, pinned to match `blizzard-infra`'s hosted collector                  |
| Listens     | `127.0.0.1:4318`, OTLP over HTTP — loopback only, nothing off `xps` reaches it                               |
| Secret      | `HONEYCOMB_API_KEY` in `collector.env` (mode `0600`), an ingest key for the Honeycomb `blizzard` environment |
| Supervision | docker `restart: unless-stopped`; `docker.service` is enabled, so it comes back after a reboot on its own    |
| Senders     | the host's `.env` sets `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318` for both runners                  |

`otel-collector.yaml` is `blizzard-infra`'s `deploy/otel-collector.yaml` with only its comments changed, and
`compose.yaml` is that repo's `otel-collector` service with `ports: ["127.0.0.1:4318:4318"]` in place of `expose` and no
network. Recreate the dir from there if it is ever lost; never commit `collector.env`.

Operate it from `../otel-collector`:

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
for r in r-claude r-chatgpt; do ../.venv/bin/blizzard runner traces status --dir ../runner --runner "$r"; done
```

Each should read `tracing: on, exporting to http://127.0.0.1:4318`, with a recent `last export` and `last error: none`.
`tracing: off` means the host started without the endpoint in its `.env`. A lasting `last error` means the collector is
down or rejecting. Leases close only when work finishes, so an idle runner shows an old `last export` and is still
healthy.

**After an outage longer than the sweep's lag cap**, the runner skips the window it fell behind on and reports
`trace-window-skipped`. Tell it again with
`blizzard runner traces replay --dir ../runner --runner <name|id> --since … --until …`. The backend dedupes on span ids,
so overlapping a window that already arrived is harmless.

### Traps on `xps`

**`blizzard-blizzard-hub.service` is stopped and `disable`d — do not start it.** `../hub/` still exists on disk, but
`../hub/data/hub.db` is a copy frozen at the migration, not what the fleet reads. Starting it serves that frozen state
on `127.0.0.1:8421` and looks entirely healthy doing so. Nothing refreshes it, and it drifts further every day.

**`127.0.0.1:8421` answers nothing.** Any tool or dev surface still aimed there fails to connect rather than silently
reaching the wrong fleet.

**Never kill a runner by a pattern.** `pkill -f "blizzard runner host"` matches the instance's host as well as a feature
env's, and a host exits `0` on SIGTERM, so its unit's `Restart=on-failure` leaves it down — and every fleet worker
either runner ran dies with it, then does it again when the host restarts and resumes it. Restart a feature env's runner
with `winter service restart <env>/runner`; stop a hand-launched one by its pid.

### Reaching the host's web surface

The host serves one panel for both runners: the host's own pages at the root, and each runner's under
`/runners/<hub id>`. It signs the operator in once, as the hub's client. It binds loopback and declares two origins,
`http://127.0.0.1:8431` and the tailnet origin below. The hub returns the SSO token through the browser, so the declared
origin is what that browser follows; a phone or laptop following a loopback origin arrives at itself.

Widening it is `blizzard/docs/deployment/human-auth.md` §Runner-side federation, which owns the whole procedure — the
origin classes that can complete a bounce, the exact-match rule, and the two proxy settings an off-host origin needs.
Two facts are local to `xps` rather than that doc's:

- **The host is already reachable on the tailnet.** `tailscale serve` fronts 8431 and preserves the browser's `Host`,
  and its `public_url` carries the tailnet origin plus `trusted_proxies = ["127.0.0.1"]` for the address `serve`
  connects from. Confirm mappings with `tailscale serve status`.
- **Changing it costs a fleet worker.** The host reads its config only at startup, so a widened set takes effect on
  restart and reaches the hub on the first host report after it — and restarting
  [terminates every running fleet worker](./post-delivery.md).

## The operator CLI needs a session

The hub runs OAuth, so an unauthenticated command fails outright:

```text
$ blizzard hub status
Error: not authenticated — run `blizzard hub login`
```

**Set this up once per machine, before anything else in this file:**

```bash
export PATH="$PWD/../.venv/bin:$PATH"              # from the workspace root; the venv's binary is on no PATH
export BZ_HUB_URL=https://blizzard.grosscode.net   # every hub command defaults --hub-url to it
export FLEET_TENANT=ten_01M4N8ZW1AXGMFD9WCAFJ1FJZB  # the instance's tenant; pass it as --tenant "$FLEET_TENANT"
blizzard hub login                                  # opens a browser
```

The `PATH` line is `xps`'s. Elsewhere there is no `../.venv`: put a `blizzard` CLI of the **Release** row's build on
`PATH` instead.

**Export `BZ_HUB_URL` — treat it as a prerequisite, not a convenience.** With it unset and no explicit `--hub-url`,
every `blizzard hub …` command falls back to its built-in default of `http://127.0.0.1:8421`, which is the dead port
above.

**Pass `--tenant "$FLEET_TENANT"` to every tenant-scoped verb.** The instance's fleet — its runners, chunks, graphs,
sources, and repositories — lives in the `paul-gross` tenant, `ten_01M4N8ZW1AXGMFD9WCAFJ1FJZB`, and the operator account
also administers `demo`, so a verb without the flag refuses with `you belong to several tenants` and lists both. The CLI
keeps no saved tenant and reads no environment variable for it; `FLEET_TENANT` is only a shell convenience. Which verbs
are hub-level and take no `--tenant` is `blizzard/docs/deployment/tenants.md`'s.

**Pass `--project blizzard` to every project-scoped verb.** The tenant holds two projects, `blizzard` and `winter`, so a
verb acting on one project's records — `hub finding list` among them — refuses without the flag with
`this tenant holds several projects`. A list verb that takes `--project` only as a filter — `hub routine list`,
`hub scope list`, `hub garden-proposal list` — does not refuse: it shows every project's rows, each naming its
`project_slug`.

## Redeploy + restart runbook — moving the instance onto a release

This is the instance's binding of [workflows/release-deploy.md](./workflows/release-deploy.md), which owns the sequence
— read the range's **Upgrade notes** first, move the hub before the runner host going forward and after it going back,
and prove each half before touching the next. What follows is where each half's pin lives and `xps`'s commands for it.
**It runs on `xps` only**: anywhere else, refuse the whole move before touching the hub — the hub moved alone leaves the
instance split across two builds ([Where it runs](#where-it-runs)).

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

One host runs both runners from the venv, so one install, one migration, and one restart move both. **Gotcha:** the host
**fails fast on a stale store** when a release adds a migration — always migrate and check the host directory before
restarting it. Paths are relative to the workspace root.

```bash
# 1. confirm the hub runs the release being installed, or a newer one inside the window the
#    `blizzard` repo's docs/versioning.md states. A runner host never runs ahead of its hub.
curl -s https://blizzard.grosscode.net/api/health | jq -r .version

# 2. fetch the release's own wheel — never a build from projects/blizzard
gh release download v<version> --repo paul-gross/blizzard --pattern 'blizzard-*.whl' --dir <scratch>

# 3. install it into the runtime venv
uv pip install --python ../.venv --reinstall <scratch>/blizzard-*.whl

# 4. migrate the host's store, data/host.db (idempotent — safe every move). There is
#    no local hub store to migrate; the hosted hub migrates itself on boot.
../.venv/bin/blizzard runner migrate --dir ../runner

# 5. check the host directory: loads blizzard-host.toml, verifies the store's revision
#    and that it names each configured runner, and resolves each token at the hub, starting
#    nothing. It must print `host ok`; a refusal is a stop — restart nothing.
../.venv/bin/blizzard runner host --check --dir ../runner

# 6. re-mint every graph the release range changed — see below. Reads the graph from the
#    LOCAL venv and posts it to the HOSTED hub, so it needs $BZ_HUB_URL and a
#    login. Sits BEFORE the runner restarts (which terminate fleet workers).
../.venv/bin/blizzard hub graph mint --hub-url "$BZ_HUB_URL" --tenant "$FLEET_TENANT" \
  ../.venv/lib/python3*/site-packages/blizzard/hub/graphs/<graph>/graph.yaml

# 7. restart the host. A fleet-worker shell has no user session bus, so
#    systemctl --user fails there ("Failed to connect to bus") until these are set:
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
systemctl --user restart blizzard-blizzard-runner.service
```

**Skip step 6 when the release range did not touch `src/blizzard/hub/graphs/`** — check with the diff below rather than
minting blindly.

Verify the host: the **version stamp is the proof** — a `200` from `/api/health` alone can come from the *old* process
and has, so it confirms liveness, never the move. A release wheel carries no local segment, so the host reports the
release exactly, and it must equal the **Release** row's version in PEP 440 normal form:

```bash
curl -s http://127.0.0.1:8431/api/health | jq -r .version   # -> 0.2.0rc2 for v0.2.0-rc.2
```

Any other version — a `+` local segment above all — means the venv runs a build, not the release the row names; stop
there. Then check `systemctl --user is-active blizzard-blizzard-runner.service` for the unit,
`curl -s http://127.0.0.1:8431/api/ready` for both runners `serving` under their hub ids, and that each runner is
actually reaching the hub — a runner that cannot reach it still reports healthy:

```bash
journalctl --user -u blizzard-blizzard-runner.service --since "-2 min" | grep -c '"event": "tick end"'
for r in r-claude r-chatgpt; do ../.venv/bin/blizzard runner status --dir ../runner --runner "$r" | head -5; done
```

`blizzard runner status --runner <name|id>` opens with the runner's name and hub id; `not registered` in place of the id
means the hub has not accepted its token.

Last, confirm both runners still export traces — `blizzard runner traces status --runner` on each, per
[Confirm both runners are reporting](#the-runners-trace-collector) — and that `docker compose ps` in `../otel-collector`
shows the collector up. A host restart never touches the collector, but it is the moment a dropped `.env` line shows.

Once both halves are proven, record the move: the **Release** row above changes in a workspace commit, so the row and
the running instance never disagree for longer than one move.

### Going back

Going back is the same move towards an older release, **runner host first and hub after**. The host's store may be ahead
of what the older release expects, so before its wheel is installed, with the unit stopped throughout:

1. Read the head revision the older release expects: install its wheel into a throwaway venv and run
   `blizzard runner migrate --head` from it.
2. Downgrade on the **current** venv, which holds the downgrade code:
   `../.venv/bin/blizzard runner migrate --dir ../runner --down <revision>`.
3. Install the older wheel, check the directory, and restart, as in a forward move.

A downgrade that fails leaves the unit stopped on the current venv, which is restarted unchanged. The hub's way back —
its store downgraded on the outgoing image before the swap — is
[workflows/release-deploy.md](./workflows/release-deploy.md)'s and the `blizzard` repo's `docs/rollback.md`'s.

### Re-minting changed graphs

**A deployed wheel's graph changes are inert until they are minted.** Graphs live in the hub's store, not on disk — the
hub reads a *minted* graph per chunk and never consults the packaged YAML again. Nothing mints at boot, so a move that
ships a changed graph and stops at the restart leaves every new chunk running the previous definition, with no error
anywhere to say so.

So on every move, check whether the release range touched `src/blizzard/hub/graphs/` and mint each graph directory it
did. Read it from the tags, so the answer is the same however far `master` has moved:

```bash
git -C projects/blizzard fetch --tags origin
git -C projects/blizzard diff --name-only v<previous>..v<version> -- src/blizzard/hub/graphs/
```

Two things make this easy to get wrong:

- **A prompt-only change still needs a re-mint.** `graph mint` **inlines** every `prompt` / `prompt_addendum` file
  reference into the stored definition, so editing a `prompts/*.md` and leaving `graph.yaml` untouched is a real graph
  change. Diff the whole graph *directory*, never just `graph.yaml`.
- **Mint from the installed venv, not from `projects/blizzard`.** Minting what is deployed is the point; the source
  checkout can differ, and if it does, minting from it stores a definition no wheel is running.

Minting is additive — the new graph becomes `effective` and the prior one `superseded`, in-flight chunks stay pinned to
the definition they started on. Verify with `blizzard hub graph list` (the newest per name should be `effective`) and
`... graph show <id>`. Note `show` renders nodes and edges but **not** prompt text — to confirm inlined prose landed,
read `GET /api/graphs/<id>` instead.

[post-delivery.md](./post-delivery.md) owns what a landing owes the instance — nothing — and the rules a move onto a
release keeps: what to read before installing, never restarting onto a broken install, and the one way the restart bites
a fleet worker (restarting the host terminates the agents both runners are running).

## The hub's work sources

Which repos the hub ingests from and delivers to are records in its store — work sources and repository records, each
naming the stored secret `github-forge` that holds the forge PAT. `blizzard`, `blizzard-mock`, `blizzard-infra`, and
`blizzard-context` are configured for blizzard. Change them through the hub, with `blizzard hub source …`,
`blizzard hub repo …`, and `blizzard hub secret set github-forge` — no host operation and no restart. They are linked to
the `blizzard` project; the thirteen `winter*` sources and repos, all with `annotate true`, are linked to the `winter`
project. The hub refuses to start while its config declares a `[[work_source]]` block or its environment sets a
`BZ_FORGE_*` variable, so neither ever goes back into `blizzard-infra`'s `deploy/`.

**The PAT selects its repos explicitly.** A source whose repo the token does not cover fails at ingest with a 404 that
reads as a missing issue — re-scope the token before adding the source.

## Operating the fleet

Drive the hub with the operator CLI — `../.venv/bin/blizzard` on `xps`. **Every command below needs `$BZ_HUB_URL`
exported, a session, and `--tenant "$FLEET_TENANT"`** — see
[The operator CLI needs a session](#the-operator-cli-needs-a-session). Operator verbs are grouped under a noun —
`hub chunk …`, `hub runner …`, `hub graph …` — so the bare `hub <verb>` forms do not exist:

| Intent                                                                            | Command                                                                                                                             |
| --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| See every chunk and its derived status                                            | `hub status`                                                                                                                        |
| Ingest an issue as a chunk                                                        | `hub chunk ingest blizzard:<issue-number>`                                                                                          |
| Make an ingested chunk claimable                                                  | `hub chunk promote <chunk-id>`                                                                                                      |
| Pin a chunk to a graph (and/or model)                                             | `hub chunk set <chunk-id> --graph <graph-id>`                                                                                       |
| Inspect one chunk in full                                                         | `hub chunk show <chunk-id>`                                                                                                         |
| Every runner, its id, liveness, and brakes                                        | `hub runner list`                                                                                                                   |
| Stop a runner claiming new work — e.g. `r-chatgpt` when the OpenAI quota runs out | `hub runner pause <id>`                                                                                                             |
| Let it claim again                                                                | `hub runner resume <id>`                                                                                                            |
| One runner's liveness + paused state                                              | `hub runner show <id>`                                                                                                              |
| Retire a runner for good — revoke its token, refuse its claims                    | `blizzard runner retire --runner <id> --dir ../runner` — read [The host and its runners](#the-host-and-its-runners) for its refusal |

Every one of these is a pure hub-API client and takes `--hub-url` (default `$BZ_HUB_URL`) — not `--url`. A runner's
`<id>` is its hub id; [The host and its runners](#the-host-and-its-runners) lists both.

**Ingest mints a chunk `not_ready`.** It will never be claimed until `chunk promote` moves it to `ready`, so an ingest
on its own looks like it worked and then nothing happens. To stage a run deliberately — pinning a non-default graph
before any runner can grab it — pause both runners (`r-claude` and `r-chatgpt`) first, then ingest, set the graph,
promote, and resume last.

Ingest takes a source-native token — prefer `blizzard:26`, `blizzard#26`, or the issue's own URL pasted in. The
`github:<url>` form also works, with a warning.

### Marshalling the backlog

Marshalling, and the steps every chunk-minting verb above owes at once, is owned by
[workflows/marshalling.md](./workflows/marshalling.md).

## Developing a feature env against this instance's data

Running a feature env's web or CLI against hub data — which hub is safe to point it at, and the SQLite single-writer
constraint that rules out a second live daemon on either runner's database — is owned by
[hub-data-modes.md](./hub-data-modes.md).

The short version, because getting it wrong now reaches a public host: **do not point board or UI development at
`https://blizzard.grosscode.net`.** It is the real fleet, and a board served by `ng serve` is a real client.
