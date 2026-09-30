# The instance

This workspace **dogfoods blizzard**: a real blizzard hub and two runners drive blizzard's own development (the
`r1`–`r4` and `oce1`–`oce4` envs) against the **real** GitHub forge. **The instance** is what this file calls that
deployment, and what "redeploy and restart the instance" means — **not** a feature env, and not the per-feature-env
verification stacks.

Its two halves run in different places, and that governs everything below:

| Half              | Where                                    | Who redeploys it                               |
| ----------------- | ---------------------------------------- | ---------------------------------------------- |
| **Hub**           | hosted, `https://blizzard.grosscode.net` | **itself** — continuous delivery from `master` |
| **Runners** (two) | this machine, beside the workspace       | **you**, by hand, after every landing          |

## Deployed surface

`../runner` and `../runner-opencode` are siblings of the directory holding `.winter/config.toml`. Paths below are
relative to the workspace root.

| Piece          | Location / value                                                                                                                                                                                                                                                                                               |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hub**        | **`https://blizzard.grosscode.net`** — health `GET /api/health`, readiness `GET /api/ready`. GitHub OAuth; reads need a session                                                                                                                                                                                |
| Hub deployment | Owned end to end by the **`paul-gross/blizzard-infra`** repo (private; worktreed here only on a machine whose `config.local.toml` declares it). It holds the host, the image channel, the updater, rollback, and the operator entry points. Go there for anything about the host — none of it is restated here |
| Runners        | Two, split by harness — see [The two runners](#the-two-runners) for each one's id, runtime dir, port, envs, and unit                                                                                                                                                                                           |
| Venv           | `../.venv` — the `blizzard` binary **both runners** run, and the operator CLI; installed from a **built wheel**, not editable                                                                                                                                                                                  |
| Wheel source   | built from `projects/blizzard` (the source checkout on `master`) → `dist/blizzard-*.whl`                                                                                                                                                                                                                       |
| Forge          | **real GitHub** — owner `paul-gross`; the hub's token lives on the host, never in this repo                                                                                                                                                                                                                    |
| Supervision    | two systemd **user** units, one per runner (unit files in `~/.config/systemd/user/`)                                                                                                                                                                                                                           |

### The two runners

The fleet is split by subscription: each runner binds exactly one coding harness, so each can be paused on its own when
its plan's quota runs out while the other keeps working. Both share `workspace_root` = this workspace,
`hub_url = "https://blizzard.grosscode.net"`, `base_branch = master`, and the venv.

|                         | Claude Code runner                                | OpenCode runner                                    |
| ----------------------- | ------------------------------------------------- | -------------------------------------------------- |
| `runner_id`             | `r-claude`                                        | `r-chatgpt`                                        |
| Runtime dir             | `../runner`                                       | `../runner-opencode`                               |
| Harness                 | `[opencode] enabled = false`                      | `[claude_code] enabled = false`                    |
| Envs (`workspace_envs`) | `r1`–`r4`                                         | `oce1`–`oce4`                                      |
| `max_agents`            | 2                                                 | 1                                                  |
| Port / `public_url`     | `127.0.0.1:8431`, plus the tailnet origin         | `127.0.0.1:8432`, loopback only                    |
| Subscription sampled    | `anthropic`                                       | `openai`                                           |
| Systemd unit            | `blizzard-blizzard-runner.service`                | `blizzard-blizzard-runner-opencode.service`        |
| Hub token               | `../runner/.env` (unit drop-in `EnvironmentFile`) | `../runner-opencode/.env` (unit `EnvironmentFile`) |

Each runtime dir holds its own `blizzard-runner.toml`, `data/runner.db`, `worker-settings.json`, and OpenCode worker
config. Health is `GET /api/health` on each port.

**Keep the env pools disjoint.** Nothing at the hub stops two runners holding the same env, so a pool that overlaps the
other runner's lets both drive one worktree.

**Which runner a chunk lands on is the harness set's call.** A runner is eligible for a chunk only when it can serve
every runner node the chunk can still reach, so a graph whose sessions accept only `claude_code` goes to `r-claude`, one
accepting only `opencode` to `r-chatgpt`, and one accepting both to whichever claims first. A session with no harness
set runs under the claiming runner's default — its one enabled harness — so it too goes to whichever claims first.

**Adding or renaming a runner needs a hub-side window.** The hosted hub runs `runner_auth_mode = "enforce"`, and
enrollment requires a prior registration, which `enforce` refuses to an unenrolled id — so a new `runner_id` (a rename
included) cannot join unaided. The sequence is `blizzard/docs/remote-runner.md` §Enroll: set the host's hub config to
`warn` and restart the hub (a `blizzard-infra` host operation), start the runner so it registers, `hub runner pause` it,
`hub runner enroll <runner-id>`, write the token to its `.env` as `BZ_HUB_TOKEN`, restart the runner, restore `enforce`,
then `hub runner resume` it. Keep the window short — `warn` relaxes enforcement fleet-wide. A **rename** also strands
every chunk the hub routes to the old id, so drain the runner first (`hub runner pause` the old id and let its running
chunks finish).

Two traps in that sequence. **A new id registers unpaused** — the hub's pause is keyed by id, so the runner can claim on
its very first tick, before `hub runner pause` can reach it. Boot it with `max_agents = 0` until
`blizzard runner status --dir <runtime-dir>` shows `paused [hub]` (the mirrored brake survives a restart), then restore
capacity. **The old id stays live until you retire it** — its registration stays in `hub runner list` with its token
still resolving. Once the rename is done and the new id is claiming on its own token, `hub runner retire <old-id>`
revokes the old token and refuses its claims and registrations. Expect it to refuse with a 409 listing held chunks even
when every one is `done`: a chunk that finishes normally never releases its route. Confirm each listed chunk is `done`
or `stopped` in `hub status`, then re-run with `--force` — on a terminal chunk the release only records itself, while a
`running` one would go back to the queue. A long list can time out the client mid-release; re-running finishes it. Then
delete the pre-rename `.env` rather than leaving the revoked token lying beside the new one.

### Traps on this machine

**`blizzard-blizzard-hub.service` is stopped and `disable`d — do not start it.** `../hub/` still exists on disk, but
`../hub/data/hub.db` is a copy frozen at the migration, not what the fleet reads. Starting it serves that frozen state
on `127.0.0.1:8421` and looks entirely healthy doing so. Nothing refreshes it, and it drifts further every day.

**`127.0.0.1:8421` answers nothing.** Any tool or dev surface still aimed there fails to connect rather than silently
reaching the wrong fleet.

**Never kill a runner by a pattern.** `pkill -f "blizzard runner host"` matches both instance runners as well as a
feature env's, and a runner exits `0` on SIGTERM, so its unit's `Restart=on-failure` leaves it down — and the fleet
worker that ran it dies with its runner, then does it again when the runner restarts and resumes it. Restart a feature
env's runner with `winter service restart <env>/runner`; stop a hand-launched one by its pid.

### Reaching a runner's web surface

Both runners bind loopback. `r-chatgpt` declares one origin, `http://127.0.0.1:8432`, so its panel opens **only from a
browser on this machine**; `r-claude` also declares the tailnet origin below. The hub returns the SSO token through the
browser, so the declared origin is what that browser follows; a phone or laptop following a loopback origin arrives at
itself.

Widening it is `blizzard/docs/deployment/human-auth.md` §Runner-side federation, which owns the whole procedure — the
origin classes that can complete a bounce, the exact-match rule, and the two proxy settings an off-host origin needs.
Two facts are local to this machine rather than that doc's:

- **`r-claude` is already reachable on the tailnet.** `tailscale serve` fronts 8431 and preserves the browser's `Host`,
  and its `public_url` carries the tailnet origin plus `trusted_proxies = ["127.0.0.1"]` for the address `serve`
  connects from. Doing the same for `r-chatgpt` means a `serve` mapping for 8432 and the same two keys in its toml.
  Confirm mappings with `tailscale serve status`.
- **Changing it costs a fleet worker.** A runner reads its config only at startup, so a widened set takes effect on
  restart and reaches the hub on the first reconciliation tick after it — and restarting
  [terminates a running fleet worker](./post-delivery.md).

## The operator CLI needs a session

The hub runs OAuth, so an unauthenticated command fails outright:

```text
$ blizzard hub status
Error: not authenticated — run `blizzard hub login`
```

**Set this up once per machine, before anything else in this file:**

```bash
export BZ_HUB_URL=https://blizzard.grosscode.net   # every hub command defaults --hub-url to it
blizzard hub login                                  # opens a browser
```

**Export `BZ_HUB_URL` — treat it as a prerequisite, not a convenience.** With it unset and no explicit `--hub-url`,
every `blizzard hub …` command falls back to its built-in default of `http://127.0.0.1:8421`, which is the dead port
above.

## Redeploy + restart runbook — the runner half

The hub half is automatic (above). This is what a landing on `master` still costs by hand.

Both runners run the one venv, so a single rebuild and reinstall redeploys both — but each has its own store and its own
unit, so steps 4 and 6 run once per runner. Code changes need a rebuild and reinstall, not just a restart. **Gotcha:** a
runner **fails fast on a stale store** when a new wheel adds a migration — always migrate both stores before restarting
either.

```bash
# 1. get master current — fast-forwards each projects/<repo> source checkout's
#    local master to origin/master. Without this you rebuild the PREVIOUS deploy.
winter ws fetch --all

# 2. rebuild the wheel (Angular apps + wheel + node-free verify)
cd projects/blizzard && mise run build

# 3. reinstall into the runtime venv (still in projects/blizzard from step 2)
uv pip install --python ../../../.venv --reinstall dist/blizzard-*.whl

# 4. migrate BOTH runner stores (idempotent — safe every deploy). There is no local
#    hub store to migrate; the hosted hub migrates itself on boot.
../../../.venv/bin/blizzard runner migrate --dir ../../../runner
../../../.venv/bin/blizzard runner migrate --dir ../../../runner-opencode

# 5. re-mint every graph the deploy changed — see below. Reads the graph from the
#    LOCAL venv and posts it to the HOSTED hub, so it needs $BZ_HUB_URL and a
#    login. Sits BEFORE the runner restarts (which terminate fleet workers).
../../../.venv/bin/blizzard hub graph mint --hub-url "$BZ_HUB_URL" \
  ../../../.venv/lib/python3*/site-packages/blizzard/hub/graphs/<graph>/graph.yaml

# 6. restart both runners. A fleet-worker shell has no user session bus, so
#    systemctl --user fails there ("Failed to connect to bus") until these are set:
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
systemctl --user restart blizzard-blizzard-runner-opencode.service blizzard-blizzard-runner.service
```

Paths in steps 3–5 are written from `projects/blizzard`, where step 2 leaves you: `../../../` is the workspace root's
parent. Adjust if you run them from elsewhere.

**Skip step 5 when the landed range did not touch `src/blizzard/hub/graphs/`** — check with the diff below rather than
minting blindly.

Verify each runner: the **version stamp below is the redeploy proof** — a `200` from `/api/health` alone can come from
the *old* process and has, so it confirms liveness, never the deploy. Check
`systemctl --user is-active blizzard-blizzard-runner.service blizzard-blizzard-runner-opencode.service` for the units
and `curl -s https://blizzard.grosscode.net/api/ready` for the hub. Then confirm each runner is actually reaching the
hub — a runner that cannot reach it still reports healthy:

```bash
for u in blizzard-blizzard-runner blizzard-blizzard-runner-opencode; do
  journalctl --user -u "$u.service" --since "-2 min" | grep -c '"event": "tick end"'
done
```

**A runner's version names the commit it was built from.** A local build stamps a PEP 440 local segment, so
`GET /api/health` reports something like `0.1.0+4efdf334a` — check it against the commit you deployed:

```bash
for port in 8431 8432; do curl -s "http://127.0.0.1:$port/api/health" | jq -r .version; done   # -> 0.1.0+<short-sha>
```

A `.dirty` suffix means the wheel was built from a tree with uncommitted changes, which for a redeploy of `master` means
something is wrong with the source checkout. The hosted hub answers the same question with `0.1.0.dev<run>`, stamped by
CI from the workflow run.

### Re-minting changed graphs

**A deployed wheel's graph changes are inert until they are minted.** Graphs live in the hub's store, not on disk — the
hub reads a *minted* graph per chunk and never consults the packaged YAML again. Nothing mints at boot, so a deploy that
ships a changed graph and stops at the restart leaves every new chunk running the previous definition, with no error
anywhere to say so.

So after any deploy, check whether the landed range touched `src/blizzard/hub/graphs/` and mint each graph directory it
did:

```bash
git -C projects/blizzard diff --name-only <previous-deploy>..HEAD -- src/blizzard/hub/graphs/
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

After a landing on `master`, this runbook is not the whole story — [post-delivery.md](./post-delivery.md) owns *when* it
runs, what must be confirmed before building, and the one way it bites a fleet worker (restarting a runner terminates
the agents it is running).

## The hub's work sources

Which repos the hub can ingest from is set by `[[work_source]]` blocks in its config, which lives on the host.
`blizzard`, `blizzard-mock`, `blizzard-infra`, and `blizzard-context` are configured. Changing them is a host operation,
owned by `blizzard-infra` along with the rule its own tests enforce about how a source must be named.

**A committed block is not a live source.** The config is bind-mounted onto the host, so a change reaches the hub only
when `blizzard-infra`'s `scripts/deploy.sh` ships `deploy/` — the image channel's unattended updater carries code, never
this file. The forge PAT is the other half: it selects its repos explicitly, so a source whose repo the token does not
cover fails at ingest with a 404 that reads as a missing issue.

## Operating the fleet

Drive the hub with the venv binary (`../.venv/bin/blizzard`). **Every command below needs `$BZ_HUB_URL` exported and a
session** — see [The operator CLI needs a session](#the-operator-cli-needs-a-session). Operator verbs are grouped under
a noun — `hub chunk …`, `hub runner …`, `hub graph …` — so the bare `hub <verb>` forms do not exist:

| Intent                                                                            | Command                                                                                         |
| --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| See every chunk and its derived status                                            | `hub status`                                                                                    |
| Ingest an issue as a chunk                                                        | `hub chunk ingest blizzard:<issue-number>`                                                      |
| Make an ingested chunk claimable                                                  | `hub chunk promote <chunk-id>`                                                                  |
| Pin a chunk to a graph (and/or model)                                             | `hub chunk set <chunk-id> --graph <graph-id>`                                                   |
| Inspect one chunk in full                                                         | `hub chunk show <chunk-id>`                                                                     |
| Stop a runner claiming new work — e.g. `r-chatgpt` when the OpenAI quota runs out | `hub runner pause <runner-id>`                                                                  |
| Let it claim again                                                                | `hub runner resume <runner-id>`                                                                 |
| One runner's liveness + paused state                                              | `hub runner show <runner-id>`                                                                   |
| Retire a runner for good — revoke its token, refuse its claims                    | `hub runner retire <runner-id>` — see [The two runners](#the-two-runners) for a renamed-away id |

Every one of these is a pure hub-API client and takes `--hub-url` (default `$BZ_HUB_URL`) — not `--url`.

**Ingest mints a chunk `not_ready`.** It will never be claimed until `chunk promote` moves it to `ready`, so an ingest
on its own looks like it worked and then nothing happens. To stage a run deliberately — pinning a non-default graph
before any runner can grab it — pause both runners (`r-claude` and `r-chatgpt`) first, then ingest, set the graph,
promote, and resume last.

Ingest takes a source-native token — prefer `blizzard:26`, `blizzard#26`, or the issue's own URL pasted in. The
`github:<url>` form also works, with a warning.

### Marshalling the backlog

**Marshal** names turning the resting backlog into ordered, claimable work: when the user says to marshal, carry every
`not_ready` chunk through the steps below, promotion included. Anything that mints a chunk — `hub chunk ingest`,
`hub garden-proposal accept`, `hub item create` — owes steps 1–3 at once, unasked; only promotion waits for the word.

1. **Map the ground.** For each `not_ready` chunk, read its work item and locate the code and docs it will change on
   `origin/master`. Map every unfinished chunk too — `ready`, `running`, `delivering` — since a resting chunk can
   collide with work already in flight.
2. **Group** chunks that are one change — the same defect from two angles, or small changes to the same files for the
   same reason: `hub chunk group <survivor> <merged-id>…`. A group is one lane and one PR, so keep it a size one lane
   carries. Every id must be unacquired.
3. **Link** chunks that are separable but would collide — they edit the same files, or one builds on what the other
   introduces: `hub chunk depend <dependent> <prerequisite>`. The foundation, or else the smaller, goes first. A resting
   chunk that would collide with in-flight work depends on that in-flight chunk.
4. **Promote in priority order** — `hub chunk promote <chunk-id>`, most urgent first: defects operators or the fleet hit
   ahead of latent ones, both ahead of prose. Promotion lands each at the tail of the `ready` queue, so promoting in
   order is the ordering; a dependent rests there blocked until its prerequisites finish.
5. **Reorder** only where the new work must jump chunks already `ready`: `hub queue move <chunk-id> <position>`.
6. **Report** each group, edge, and reorder with its reason — the file or seam the chunks share.

## Developing a feature env against this instance's data

Running a feature env's web or CLI against hub data — which hub is safe to point it at, and the SQLite single-writer
constraint that rules out a second live daemon on either runner's database — is owned by
[hub-data-modes.md](./hub-data-modes.md).

The short version, because getting it wrong now reaches a public host: **do not point board or UI development at
`https://blizzard.grosscode.net`.** It is the real fleet, and a board served by `ng serve` is a real client.
