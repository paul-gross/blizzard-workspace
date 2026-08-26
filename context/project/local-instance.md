# The instance

This workspace **dogfoods blizzard**: a real blizzard hub and runner drive blizzard's own development (the `r1`–`r4` envs) against the **real** GitHub forge. **The instance** is what this file calls that deployment, and what "redeploy and restart the instance" means — **not** a feature env, and not the per-feature-env verification stacks.

Its two halves run in different places, and that governs everything below:

| Half | Where | Who redeploys it |
|------|-------|------------------|
| **Hub** | hosted, `https://blizzard.grosscode.net` | **itself** — continuous delivery from `master` |
| **Runner** | this machine, beside the workspace | **you**, by hand, after every landing |

## Deployed surface

`../runner` is a sibling of the directory holding `.winter/config.toml`. Paths below are relative to the workspace root.

| Piece | Location / value |
|-------|------------------|
| **Hub** | **`https://blizzard.grosscode.net`** — health `GET /api/health`, readiness `GET /api/ready`. GitHub OAuth; reads need a session |
| Hub deployment | Owned end to end by the **`paul-gross/blizzard-infra`** repo (private; worktreed here only on a machine whose `config.local.toml` declares it). It holds the host, the image channel, the updater, rollback, and the operator entry points. Go there for anything about the host — none of it is restated here |
| Runner runtime dir | `../runner` (`blizzard-runner.toml`, `data/runner.db`, `worker-settings.json`) |
| Runner | `127.0.0.1:8431`, health `GET /api/health`, `hub_url = "https://blizzard.grosscode.net"`, `public_url = "http://127.0.0.1:8431"` — see [Reaching the runner's web surface](#reaching-the-runners-web-surface) |
| Venv | `../.venv` — the `blizzard` binary the **runner** runs, and the operator CLI; installed from a **built wheel**, not editable |
| Wheel source | built from `projects/blizzard` (the source checkout on `master`) → `dist/blizzard-*.whl` |
| Forge | **real GitHub** — owner `paul-gross`; the hub's token lives on the host, never in this repo |
| Runner fleet | `workspace_root` = this workspace, `workspace_envs = [r1,r2,r3,r4]`, `harness_binary = claude` (real), `base_branch = master` |
| Supervision | one systemd **user** unit: `blizzard-blizzard-runner.service` (unit file in `~/.config/systemd/user/`) |

### Two traps on this machine

**`blizzard-blizzard-hub.service` is stopped and `disable`d — do not start it.** `../hub/` still exists on disk, but `../hub/data/hub.db` is a copy frozen at the migration, not what the fleet reads. Starting it serves that frozen state on `127.0.0.1:8421` and looks entirely healthy doing so. Nothing refreshes it, and it drifts further every day.

**`127.0.0.1:8421` answers nothing.** Any tool or dev surface still aimed there fails to connect rather than silently reaching the wrong fleet.

### Reaching the runner's web surface

This instance's runner declares one origin — `public_url = "http://127.0.0.1:8431"` — and binds loopback, so its panel opens **only from a browser on this machine**. The hub returns the SSO token through the browser, so the declared origin is what that browser follows; a phone or laptop following a loopback origin arrives at itself.

Widening it is `blizzard/docs/deployment/human-auth.md` §Runner-side federation, which owns the whole procedure — the origin classes that can complete a bounce, the exact-match rule, and the two proxy settings an off-host origin needs. Two facts are local to this machine rather than that doc's:

- **The reachability half is already built.** `tailscale serve` fronts 8431 on the tailnet and preserves the browser's `Host`, so nothing needs a wider bind — only the tailnet origin added to `public_url`, plus `trusted_proxies = ["127.0.0.1"]` for the address `serve` connects from. Confirm the mapping with `tailscale serve status`.
- **Changing it costs a fleet worker.** The runner reads its config only at startup, so a widened set takes effect on restart and reaches the hub on the first reconciliation tick after it — and restarting [terminates a running fleet worker](./post-delivery.md).

## The operator CLI needs a session

The hub runs OAuth, so an unauthenticated command fails outright:

```
$ blizzard hub status
Error: not authenticated — run `blizzard hub login`
```

**Set this up once per machine, before anything else in this file:**

```bash
export BZ_HUB_URL=https://blizzard.grosscode.net   # every hub command defaults --hub-url to it
blizzard hub login                                  # opens a browser
```

**Export `BZ_HUB_URL` — treat it as a prerequisite, not a convenience.** With it unset and no explicit `--hub-url`, every `blizzard hub …` command falls back to its built-in default of `http://127.0.0.1:8421`, which is the dead port above.

## Redeploy + restart runbook — the runner half

The hub half is automatic (above). This is what a landing on `master` still costs by hand.

The venv runs a built wheel, so code changes need a rebuild and reinstall, not just a restart. **Gotcha:** the runner **fails fast on a stale store** when a new wheel adds a migration — always migrate before restarting.

```bash
# 1. get master current — fast-forwards each projects/<repo> source checkout's
#    local master to origin/master. Without this you rebuild the PREVIOUS deploy.
winter ws fetch --all

# 2. rebuild the wheel (Angular apps + wheel + node-free verify)
cd projects/blizzard && mise run build

# 3. reinstall into the runtime venv (still in projects/blizzard from step 2)
uv pip install --python ../../../.venv --reinstall dist/blizzard-*.whl

# 4. migrate the RUNNER store (idempotent — safe every deploy). There is no local
#    hub store to migrate; the hosted hub migrates itself on boot.
../../../.venv/bin/blizzard runner migrate --dir ../../../runner

# 5. re-mint every graph the deploy changed — see below. Reads the graph from the
#    LOCAL venv and posts it to the HOSTED hub, so it needs $BZ_HUB_URL and a
#    login. Sits BEFORE the runner restart (which terminates a fleet worker).
../../../.venv/bin/blizzard hub graph mint --hub-url "$BZ_HUB_URL" \
  ../../../.venv/lib/python3*/site-packages/blizzard/hub/graphs/<graph>/graph.yaml

# 6. restart the runner. A fleet-worker shell has no user session bus, so
#    systemctl --user fails there ("Failed to connect to bus") until these are set:
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
systemctl --user restart blizzard-blizzard-runner.service   # omit to leave the runner down
```

Paths in steps 3–5 are written from `projects/blizzard`, where step 2 leaves you: `../../../` is the workspace root's parent. Adjust if you run them from elsewhere.

**Skip step 5 when the landed range did not touch `src/blizzard/hub/graphs/`** — check with the diff below rather than minting blindly.

Verify: the **version stamp below is the redeploy proof** — a `200` from `/api/health` alone can come from the *old* process and has, so it confirms liveness, never the deploy. Check `systemctl --user is-active blizzard-blizzard-runner.service` for the unit and `curl -s https://blizzard.grosscode.net/api/ready` for the hub. Then confirm the runner is actually reaching the hub — a runner that cannot reach it still reports healthy:

```bash
journalctl --user -u blizzard-blizzard-runner.service --since "-2 min" | grep -c '"event": "tick end"'
```

**The runner's version names the commit it was built from.** A local build stamps a PEP 440 local segment, so `GET /api/health` reports something like `0.1.0+4efdf334a` — check it against the commit you deployed:

```bash
curl -s http://127.0.0.1:8431/api/health | jq -r .version   # -> 0.1.0+<short-sha>
```

A `.dirty` suffix means the wheel was built from a tree with uncommitted changes, which for a redeploy of `master` means something is wrong with the source checkout. The hosted hub answers the same question with `0.1.0.dev<run>`, stamped by CI from the workflow run.

### Re-minting changed graphs

**A deployed wheel's graph changes are inert until they are minted.** Graphs live in the hub's store, not on disk — the hub reads a *minted* graph per chunk and never consults the packaged YAML again. Nothing mints at boot, so a deploy that ships a changed graph and stops at the restart leaves every new chunk running the previous definition, with no error anywhere to say so.

So after any deploy, check whether the landed range touched `src/blizzard/hub/graphs/` and mint each graph directory it did:

```bash
git -C projects/blizzard diff --name-only <previous-deploy>..HEAD -- src/blizzard/hub/graphs/
```

Two things make this easy to get wrong:

- **A prompt-only change still needs a re-mint.** `graph mint` **inlines** every `prompt` / `prompt_addendum` file reference into the stored definition, so editing a `prompts/*.md` and leaving `graph.yaml` untouched is a real graph change. Diff the whole graph *directory*, never just `graph.yaml`.
- **Mint from the installed venv, not from `projects/blizzard`.** Minting what is deployed is the point; the source checkout can differ, and if it does, minting from it stores a definition no wheel is running.

Minting is additive — the new graph becomes `effective` and the prior one `superseded`, in-flight chunks stay pinned to the definition they started on. Verify with `blizzard hub graph list` (the newest per name should be `effective`) and `... graph show <id>`. Note `show` renders nodes and edges but **not** prompt text — to confirm inlined prose landed, read `GET /api/graphs/<id>` instead.

After a landing on `master`, this runbook is not the whole story — [post-delivery.md](./post-delivery.md) owns *when* it runs, what must be confirmed before building, and the one way it bites a fleet worker (restarting the runner terminates the agent running it).

## The hub's work sources

Which repos the hub can ingest from is set by `[[work_source]]` blocks in its config, which lives on the host. `blizzard`, `blizzard-mock`, `blizzard-infra`, and `blizzard-context` are configured. Changing them is a host operation, owned by `blizzard-infra` along with the rule its own tests enforce about how a source must be named.

**A committed block is not a live source.** The config is bind-mounted onto the host, so a change reaches the hub only when `blizzard-infra`'s `scripts/deploy.sh` ships `deploy/` — the image channel's unattended updater carries code, never this file. The forge PAT is the other half: it selects its repos explicitly, so a source whose repo the token does not cover fails at ingest with a 404 that reads as a missing issue.

## Operating the fleet

Drive the hub with the venv binary (`../.venv/bin/blizzard`). **Every command below needs `$BZ_HUB_URL` exported and a session** — see [The operator CLI needs a session](#the-operator-cli-needs-a-session). Operator verbs are grouped under a noun — `hub chunk …`, `hub runner …`, `hub graph …` — so the bare `hub <verb>` forms do not exist:

| Intent | Command |
|--------|---------|
| See every chunk and its derived status | `hub status` |
| Ingest an issue as a chunk | `hub chunk ingest blizzard:<issue-number>` |
| Make an ingested chunk claimable | `hub chunk promote <chunk-id>` |
| Pin a chunk to a graph (and/or model) | `hub chunk set <chunk-id> --graph <graph-id>` |
| Inspect one chunk in full | `hub chunk show <chunk-id>` |
| Stop a runner claiming new work | `hub runner pause <runner-id>` |
| Let it claim again | `hub runner resume <runner-id>` |
| One runner's liveness + paused state | `hub runner show <runner-id>` |

Every one of these is a pure hub-API client and takes `--hub-url` (default `$BZ_HUB_URL`) — not `--url`.

**Ingest mints a chunk `not_ready`.** It will never be claimed until `chunk promote` moves it to `ready`, so an ingest on its own looks like it worked and then nothing happens. To stage a run deliberately — pinning a non-default graph before any runner can grab it — pause the runner first, then ingest, set the graph, promote, and resume last.

Ingest takes a source-native token — prefer `blizzard:26`, `blizzard#26`, or the issue's own URL pasted in. The `github:<url>` form also works, with a warning.

## Developing a feature env against this instance's data

Running a feature env's web or CLI against hub data — which hub is safe to point it at, and the SQLite single-writer constraint that rules out a second live daemon on the runner's database — is owned by [hub-data-modes.md](./hub-data-modes.md).

The short version, because getting it wrong now reaches a public host: **do not point board or UI development at `https://blizzard.grosscode.net`.** It is the real fleet, and a board served by `ng serve` is a real client.
