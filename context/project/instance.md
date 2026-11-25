# The instance

This workspace **dogfoods blizzard**: a real blizzard hub and its runner hosts drive blizzard's own development, and
winter's from a sibling workspace, against the **real** GitHub forge. **The instance** is what this file calls that
deployment — the hub and every runner host — and what "redeploy and restart the instance" means. It is **not** a feature
env, and not the per-feature-env verification stacks.

There is one instance. Its target, access, and authority are owned by [blizzard-binding.md](./blizzard-binding.md).
Every part of it runs one pinned **build** — a release, or a dogfood build that
[feeding-the-dog](./workflows/feeding-the-dog.md) moves it onto — never whatever `master` holds: a landing reaches the
instance only once the user moves it onto a build that carries it. A move takes the hub and **every** runner host onto
that build in one sitting.

## Where it runs

The hub is hosted (`paul-gross/blizzard-infra`). The runner hosts sit on the operator's own machines, one host per
machine; [fleet-machines.md](./fleet-machines.md) names the machines, and its table is the registry of which holds a
runner host. Any of those machines can operate every host over the tailnet, as
[fleet-machines.md](./fleet-machines.md#operating-one-machine-from-another) describes. Each path below is on the machine
named beside it.

## Deployed surface

| Piece           | Location / value                                                                                                                                                                                                                                                                                                        |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hub**         | Production target and operator access: [workspace binding](./blizzard-binding.md)                                                                                                                                                                                                                                       |
| Hub deployment  | Owned end to end by the **`paul-gross/blizzard-infra`** repo (private; worktreed only on a machine whose `config.local.toml` declares it — today `xps`). It holds the host, the image channel, the updater, rollback, and the operator entry points. Go there for anything about the host — none of it is restated here |
| Runner hosts    | One per runner-host machine, each running two runners split by harness — see [The hosts and their runners](#the-hosts-and-their-runners) for their ids, workspaces, envs, tokens, and units                                                                                                                             |
| Venv            | `<instance dir>/.venv` on each host — the `blizzard` binary the host runs, and that machine's operator CLI; installed from the **build's wheel** and its pinned requirements, never a local build or an editable install                                                                                                |
| Build           | the build `blizzard-infra`'s `deploy/release.env` pins for the hub, which every host's venv matches. Nothing here records which: read it live, as [What it runs now](#what-it-runs-now) shows                                                                                                                           |
| Forge           | **real GitHub** — owner `paul-gross`; the hub's token lives on the hub's host, never in this repo                                                                                                                                                                                                                       |
| Supervision     | one systemd **user** unit per host, kept running by linger (unit file and drop-ins in `~/.config/systemd/user/`)                                                                                                                                                                                                        |
| Trace collector | `<instance dir>/otel-collector` on each host — a docker compose OpenTelemetry Collector on `127.0.0.1:4318` that forwards that host's spans to Honeycomb; see [The runners' trace collector](#the-runners-trace-collector)                                                                                              |

### The hosts and their runners

Each host's runners are split by harness and subscription but share one daemon, host directory, store, port, and unit.
General runner inspection and controls belong to
[usage runner guidance](../../.winter/ext/usage/context/runner-hosts.md).

|                     | Host `xps`                                                                                                               | Host `amd-9950x` (machine `amd-9950x-fedora`)                                     |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| Hub id              | `hs_01M521395MMCN3ANGMJVQPG1YK`                                                                                          | `hs_01M8A142266H2F6T468TFWFWAH`                                                   |
| Instance dir        | `~/projects/blizzard-blizzard`                                                                                           | `~/blizzard`                                                                      |
| Host directory      | `~/projects/blizzard-blizzard/runner` — `blizzard-host.toml`, store `data/host.db`, host key `data/secret-keys/`, `.env` | `~/blizzard/runner`, laid out the same                                            |
| Port / `public_url` | `127.0.0.1:8431`, plus `https://xps.pufferfish-lungfish.ts.net:8431`                                                     | `127.0.0.1:8431`, plus `https://amd-9950x-fedora.pufferfish-lungfish.ts.net:8431` |
| Systemd unit        | `blizzard-blizzard-runner.service`                                                                                       | `blizzard-builder-runner.service`                                                 |
| Host token          | `BZ_HOST_TOKEN` in the host directory's `.env` (unit drop-in `EnvironmentFile`)                                          | the same                                                                          |
| Trace export        | `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318` in the host directory's `.env`                                       | the same                                                                          |
| Workspace roots     | `~/projects/blizzard-workspace`, `~/projects/winter-dev`                                                                 | `~/blizzard/blizzard-workspace`, `~/blizzard/winter-dev`                          |
| Env pools           | `bz1`–`bz8` in each root                                                                                                 | `bz1`–`bz16` in each root                                                         |

| Runner      | Host        | Hub id                          | Harness           | Subscription | Worker config                                                    |
| ----------- | ----------- | ------------------------------- | ----------------- | ------------ | ---------------------------------------------------------------- |
| `r-claude`  | `xps`       | `rn_01M3MY1NGQR8WE1ZBRKNREFNE4` | Claude Code alone | `anthropic`  | `runner/worker-settings.json`, the file's `worker_settings_path` |
| `r-chatgpt` | `xps`       | `rn_01M3JK4RX4BQJ74W7T3CK4NQ1B` | OpenCode alone    | `openai`     | `runner/runners/r-chatgpt/worker-settings.json`                  |
| `r-claude`  | `amd-9950x` | `rn_01M8A14MN9FZJNPKSY754FRQFR` | Claude Code alone | `anthropic`  | `runner/worker-settings.json`, the file's `worker_settings_path` |
| `r-chatgpt` | `amd-9950x` | `rn_01M8A14WBK9QRVPTMRFH5XASPW` | OpenCode alone    | `openai`     | `runner/runners/r-chatgpt/worker-settings.json`                  |

Every host names its runners alike — `r-claude` and `r-chatgpt` — so a runner's name alone never says which one it is:
name it with its host, as `xps/r-claude` or `amd-9950x/r-claude`, or by its hub id. `blizzard hub runner list` shows
names without hosts; `blizzard hub runner-host get <host id>` lists a host's own. A host's name is its own, not its
machine's tailnet name. Worker-config paths are relative to the host's instance directory. An OpenCode runner's turns
run on that machine's own managed OpenCode service (`opencode service status`), under its login and
`~/.config/opencode/opencode.json`, whose `model` is the fallback for a launch naming none. Each runner's hub token is
sealed in its host's store; replace it with `blizzard runner token set --runner <name>`.

Every runner serves the hub's **`blizzard` project** through its host's `[[workspace]]` `blizzard`, and the **`winter`
project** through its host's `[[workspace]]` `winter`; each host's file is the record of its settings. Within a host,
one env pool per workspace is shared by both runners — keep it in that one declaration rather than adding an overlapping
pool. Pools on different hosts reuse env names (`bz1`, …) under different roots, so no worktree is driven by two hosts.
Each `winter` workspace's `c5xlabs` repo records read their forge token from the hub secret `github-forge-c5xlabs`, not
`github-forge`, since a fine-grained token has one resource owner.

Every `blizzard` workspace uses `base_branch = master`; every `winter` workspace uses `{main}`. Each host file declares
the `blizzard` and `winter` workspaces, the `anthropic` and `openai` subscriptions, and the host's boot keys. Both hosts
draw on the same Anthropic and OpenAI plans: each host's throttle reads the plan's account-wide usage, so each sees the
other's spend.

**The store owns everything live.** Which runners a host serves, each runner's settings, the host's own live settings
(`tick_seconds`, `[transcripts]`, the live `[tracing]` keys, `[tools]`), and each runner's hub token all live in that
host's `data/host.db`. Editing a seeded key in the file changes nothing. Read and change them with
`blizzard runner settings get|set|unset --runner <name>` or `--host`, `blizzard runner token set`, and
`blizzard runner add`/`retire`, each with `--dir <instance dir>/runner`, run on that host's machine. Each runner keeps a
minimal `[[runner]]` (`id`, `name`, `subscription`) because `host --check` checks only the runners the file names, and
its halt check reads `subscription` from the file; a Claude Code runner's entry also keeps the boot key
`worker_settings_path`. The sealed tokens open only under `data/secret-keys/`, so back that directory up with the store.
The settings' current values, agent counts and harness binaries among them, are read live, as
[What it runs now](#what-it-runs-now) shows.

For authorized runner enrollment, renaming, or retirement, use the application's
`blizzard/docs/deployment/runner-auth.md` and `blizzard/docs/remote-runner.md` with the host's paths and identities. Our
policy for a new runner is to pause it at the hub before the host restart that starts it, and resume it only after
inspection confirms it is online. A host restart affects both projects
([post-delivery.md](./post-delivery.md#the-restart-is-fleet-wide)).

#### Recovery artifacts on `xps`

- `~/projects/blizzard-blizzard/runner/backup-host-consolidation-*/` — the store, host file, `.env`, worker config, and
  unit as they stood just before the gathering; `~/projects/blizzard-blizzard/runner/.env.pre-consolidation-*` is
  `~/projects/blizzard-blizzard/runner/.env` before the host token.
- The pre-host `blizzard-runner.toml.graduated` and `backup-host-graduation-*/`, from the graduation before that.

On every host, each move leaves `runner/backup-pre-feed-<version>-<timestamp>/`: the host file, store, and host key as
they stood before it.

### What it runs now

Nothing in this workspace records the instance's build, its harness versions, or its settings' values, and no move edits
a file here. Ask the instance:

```bash
curl -s https://blizzard.grosscode.net/api/health | jq -r .version     # the hub's build
ssh <machine> 'curl -s http://127.0.0.1:<port>/api/health'              # each host's build (in place on its own machine)
blizzard hub runner list --tenant "$FLEET_TENANT" --json | jq -r \
  '.runners[] | "\(.runner_name)  \(.version)  \(.version_standing)  agents=\(.agents)  \([.capabilities[] | "\(.harness_id) \(.version)"] | join(", "))"'
ssh <machine> '<instance dir>/.venv/bin/blizzard runner settings get --runner <name> --dir <instance dir>/runner'
```

The runner line is the hub's view: each runner's build, whether the hub admits it (`in_window`), its agents, and each
harness's version. A dogfood build reports its dev version with its commit, such as `0.5.0.dev1543+acd1c6126`; a release
reports its version in PEP 440 normal form, such as `0.2.0rc2` for `v0.2.0-rc.2`.

### The runners' trace collector

Each host runs its own collector, separate from the hosted hub's private compose network. Each forwards to the same
Honeycomb `blizzard` environment as the hub's. The runner spans land in the `blizzard-runner` dataset beside the hub's
`blizzard-hub`.

The tracing protocol, diagnostic fields, and replay procedure are owned by `blizzard/docs/deployment/tracing.md`; the
collector's local configuration and commands are below.

| Piece       | Value                                                                                                        |
| ----------- | ------------------------------------------------------------------------------------------------------------ |
| Runtime dir | `<instance dir>/otel-collector` — `compose.yaml`, `otel-collector.yaml`, `collector.env`                     |
| Image       | `otel/opentelemetry-collector:0.162.0`, pinned to match `blizzard-infra`'s hosted collector                  |
| Listens     | `127.0.0.1:4318`, OTLP over HTTP — loopback only, nothing off the machine reaches it                         |
| Secret      | `HONEYCOMB_API_KEY` in `collector.env` (mode `0600`), an ingest key for the Honeycomb `blizzard` environment |
| Supervision | docker `restart: unless-stopped`; `docker.service` is enabled, so it comes back after a reboot on its own    |
| Senders     | the host's `.env` sets `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318` for its runners                   |

`otel-collector.yaml` is `blizzard-infra`'s `deploy/otel-collector.yaml` with only its comments changed, and
`compose.yaml` is that repo's `otel-collector` service with `ports: ["127.0.0.1:4318:4318"]` in place of `expose` and no
network. Every host's two files are identical; recreate a lost dir from another host or from `blizzard-infra`, and never
commit `collector.env`.

Operate it from that host's `<instance dir>/otel-collector`:

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
[terminates the fleet workers that host's runners hold](./post-delivery.md).

**Confirm a host's runners are reporting**, on that host's machine:

```bash
for r in <runner> <runner>; do <instance dir>/.venv/bin/blizzard runner traces status --dir <instance dir>/runner --runner "$r"; done
```

Each should show tracing on, exporting to `http://127.0.0.1:4318`, with no lasting export error. An idle runner can have
an old last-export time.

### Traps on `xps`

**`blizzard-blizzard-hub.service` is stopped and `disable`d — do not start it.** `~/projects/blizzard-blizzard/hub/`
still exists on disk, but `~/projects/blizzard-blizzard/hub/data/hub.db` is a copy frozen at the migration, not what the
fleet reads. Starting it serves that frozen state on `127.0.0.1:8421` and looks entirely healthy doing so. Nothing
refreshes it, and it drifts further every day.

**`127.0.0.1:8421` answers nothing.** Any tool or dev surface still aimed there fails to connect rather than silently
reaching the wrong fleet.

Every unit uses `Restart=on-failure`; a SIGTERM can leave it down. For feature-env restarts use
`winter service restart <env>/runner`, not a process-name pattern that could also stop a host
([fleet-machines.md](./fleet-machines.md#every-machine-with-a-runner-host)). General host intervention safety is owned
by [usage runner guidance](../../.winter/ext/usage/context/runner-hosts.md).

### Reaching a host's web surface

Each host binds loopback and declares `http://127.0.0.1:8431` plus its tailnet origin. Federation setup is owned by
`blizzard/docs/deployment/human-auth.md` §Runner-side federation. Our local bindings are:

- **Each host is reachable on the tailnet.** `tailscale serve --bg --https=8431 http://127.0.0.1:8431` on its machine
  fronts 8431 and preserves the browser's `Host`, and its `public_url` carries the tailnet origin plus
  `trusted_proxies = ["127.0.0.1"]` for the address `serve` connects from. Confirm mappings with
  `tailscale serve status`.
- **Changing it costs that host's fleet workers.** A host reads its config only at startup, so a widened set takes
  effect on restart and reaches the hub on the first host report after it — and restarting
  [terminates every fleet worker that host holds](./post-delivery.md).

## Redeploy + restart runbook — moving the instance onto a release

Read [the workspace binding](./blizzard-binding.md) for the approved CLI, shell variables, and sign-in first. Follow
[workflows/release-deploy.md](./workflows/release-deploy.md) for sequencing, upgrade boundaries, and stop conditions.
This section supplies our pins, paths, and commands.

### The hub's pin

The hub runs the build `blizzard-infra`'s `deploy/release.env` pins — a release by its version, or a dogfood build by
its `sha-<full sha>` tag — by index digest. Moving the hub is a change to that file on `blizzard-infra`'s `master`, then
`scripts/deploy.sh` from a `blizzard-infra` worktree: the install syncs the pin to the host and runs the updater, which
converges the hub on it, forward or back. Merging the change moves nothing until the deploy runs. A `BLIZZARD_HUB_IMAGE`
line in the host's `/srv/blizzard/deploy/.env` is a hold that wins over the pin — the updater logs
`held at …; release pin … not applied` — and is normally absent. `blizzard-infra`'s `docs/deployment.md` §The pin and
the hold and `docs/rollback.md` own the commands and the way back.

Moving the hub needs a `blizzard-infra` worktree and the operator's `blizzard-prod-admin` session, which no fleet worker
holds — today both are on `xps`, so an orchestrator elsewhere runs this half as `ssh xps …`. An agent driving the move
asks for `! aws sso login --profile blizzard-prod-admin` to be run on that machine, and stops if it is not. **Proof:**
`curl -s https://blizzard.grosscode.net/api/health | jq -r .version` equals the target in PEP 440 normal form —
`0.2.0rc2` for `v0.2.0-rc.2` — and `GET /api/ready` reports ready.

### The runner hosts

One install, migration, and restart per host moves its runners; every runner host in
[fleet-machines.md](./fleet-machines.md#the-machines) moves in the same sitting, driven from one orchestrating machine
as that page describes. `blizzard/docs/upgrade.md` and `blizzard/docs/rollback.md` own the product's migration
requirements.

The move has four phases, each run across every host before the next begins:

1. **Confirm the hub.** It runs the build being installed, or a newer one inside the window the `blizzard` repo's
   `docs/versioning.md` states: `curl -s https://blizzard.grosscode.net/api/health | jq -r .version`. A runner host
   never runs ahead of its hub.
2. **Prepare every host.** Fetch the build once, on the orchestrating machine — a release's
   `blizzard-<version>-py3-none-any.whl` and `blizzard-<version>-requirements.txt` from
   `gh release download v<version> --repo paul-gross/blizzard`, or a dogfood build's from its `push` run
   ([feeding-the-dog.md § 3](./workflows/feeding-the-dog.md#3-fetch-the-build)) — and copy both files to
   `<instance dir>/builds/<version>/` on every host. Then, on each host in turn, in its own instance directory:

   ```bash
   systemctl --user stop <unit>
   # back up runner/blizzard-host.toml, runner/data/host.db (and its -wal, -shm) and runner/data/secret-keys/
   #   to runner/backup-pre-feed-<version>-<timestamp>/
   uv pip sync --python <instance dir>/.venv --require-hashes <instance dir>/builds/<version>/blizzard-<version>-requirements.txt
   uv pip install --python <instance dir>/.venv --no-deps <instance dir>/builds/<version>/blizzard-<version>-py3-none-any.whl
   <instance dir>/.venv/bin/blizzard --version                                         # must name <version>
   <instance dir>/.venv/bin/blizzard runner migrate --dir <instance dir>/runner
   <instance dir>/.venv/bin/blizzard runner host --check --dir <instance dir>/runner   # must print `host ok`
   ```

   Every host is left **stopped**. The requirements file pins every dependency by hash and, for a dogfood build, names
   no wheel, which is why the wheel goes in after it with `--no-deps`; `uv pip sync` leaves the venv holding exactly
   that set. A host whose install names another version, or whose check does not print `host ok`, stops the phase there:
   it stays stopped, and hosts after it are not touched ([A host that fails](#a-host-that-fails)).
3. **Re-mint every graph the range changed**, once, while no host runs — see
   [Re-minting changed graphs](#re-minting-changed-graphs). Skip it when the range did not touch
   `src/blizzard/hub/graphs/`; check with the diff there rather than minting blindly.
4. **Start every host, and prove each.** `systemctl --user start <unit>`, then wait until its
   `curl -s http://127.0.0.1:<port>/api/health` reports the target version in PEP 440 normal form — not merely that HTTP
   answers — and `curl -s http://127.0.0.1:<port>/api/ready` lists its runners `serving`.

Each `systemctl --user` call needs the user bus a non-login shell lacks
([fleet-machines.md](./fleet-machines.md#operating-one-machine-from-another)).

Then confirm, per host, that each runner is actually reaching the hub — a runner that cannot reach it still reports
healthy: the host's journal shows `"event": "tick end"` lines since the start
(`journalctl --user -u <unit> --since "-2 min"`), and `blizzard hub runner list --tenant "$FLEET_TENANT"` shows every
runner on the build and `in_window`. Last, confirm each host's runners still export traces, per
[Confirm a host's runners are reporting](#the-runners-trace-collector), and that each collector is up. A host restart
never touches its collector, but it is the moment a dropped `.env` line shows.

#### A host that fails

A host that fails phase 2 or 4 is put back on the previous build — its wheel, and a store downgrade if the migration
ran, as [Going back](#going-back) describes — and started on that. Never start a host on an install that did not check.
Then decide with the user: hosts not yet touched can still take the target, which is safe while the previous build sits
inside the hub's skew window, or the whole instance goes back.

Nothing in this workspace records the move: the hub's pin in `blizzard-infra` is the deployment's record, and each
part's `/api/health` reports what it actually runs.

### Going back

Going back is the same move towards an older build, **every runner host first and the hub after**. The build to go back
to is the one the hub reported before the move, which `blizzard-infra`'s `deploy/release.env` history also holds. A
host's store may be ahead of what the older build expects, so before its wheel is installed, with that host's unit
stopped throughout:

1. Read the head revision the older build expects: install its wheel into a throwaway venv and run
   `blizzard runner migrate --head` from it.
2. Downgrade on the host's **current** venv, which holds the downgrade code:
   `<instance dir>/.venv/bin/blizzard runner migrate --dir <instance dir>/runner --down <revision>`.
3. Install the older build, check the directory, and restart, as in a forward move's phases 2 and 4, with the older
   build.

A downgrade that fails leaves the unit stopped on the current venv, which is restarted unchanged. The hub's way back —
its store downgraded on the outgoing image before the swap — is
[workflows/release-deploy.md](./workflows/release-deploy.md)'s and the `blizzard` repo's `docs/rollback.md`'s.

### Re-minting changed graphs

Our deployment move includes minting the packaged graphs the range changed, **once** for the whole instance — graphs are
the hub's, not a host's. Product graph synchronization and inlining behavior belong to
`blizzard/docs/deployment/install.md` and the installed `hub graph mint --help`. Identify changed graph directories from
the tags (or commits, for a dogfood build) rather than the checkout's current `master`:

```bash
git -C projects/blizzard fetch --tags origin
git -C projects/blizzard diff --name-only v<previous>..v<version> -- src/blizzard/hub/graphs/
```

Diff the whole graph directory, including prompt files. Mint from one host's freshly installed venv, never from
`projects/blizzard`, so the definition matches the deployed build:

```bash
ssh <machine> '<instance dir>/.venv/bin/blizzard hub graph mint --hub-url https://blizzard.grosscode.net \
  --tenant ten_01M4N8ZW1AXGMFD9WCAFJ1FJZB <instance dir>/.venv/lib/python3*/site-packages/blizzard/hub/graphs/<graph>/graph.yaml'
```

That machine needs a hub session. Read back each minted definition and confirm the intended graph and prompt changes
landed before declaring the move complete.

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
