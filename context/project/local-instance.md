# The local instance

This workspace **dogfoods blizzard**: a real blizzard hub + runner run beside it and drive blizzard's own development (the `r1`–`r4` envs) against the **real** GitHub forge.

When someone says **"the local blizzard"** or **"redeploy and restart the local instance"**, this deployment is what they mean — **not** a feature env, and not the per-feature-env verification stacks. It is the target of the redeploy [post-delivery.md](./post-delivery.md) requires after every landing on `master`.

## Deployed surface

The instance lives one level **up** from the workspace root, beside the workspace — so `../hub` and `../runner` are siblings of the directory holding `.winter/config.toml`. Paths below are relative to the workspace root.

| Piece | Location / value |
|-------|------------------|
| Hub runtime dir | `../hub` (`blizzard-hub.toml`, `data/hub.db`, `.env`) |
| Runner runtime dir | `../runner` (`blizzard-runner.toml`, `data/runner.db`, `worker-settings.json`) |
| Shared venv | `../.venv` — the `blizzard` binary both daemons run; installed from a **built wheel**, not editable |
| Wheel source | built from `projects/blizzard` (the source checkout on `master`) → `dist/blizzard-*.whl` |
| Hub | `127.0.0.1:8421`, health `GET /api/health` |
| Runner | `127.0.0.1:8431`, health `GET /api/health` |
| Forge | **real GitHub** — `BZ_FORGE_URL=https://api.github.com`, owner `paul-gross`, token in `../hub/.env` (never in this repo) |
| Runner fleet | `workspace_root` = this workspace, `workspace_envs = [r1,r2,r3,r4]`, `harness_binary = claude` (real), `base_branch = master` |
| Supervision | systemd **user** units: `blizzard-blizzard-hub.service`, `blizzard-blizzard-runner.service` (unit files in `~/.config/systemd/user/`) |

## Redeploy + restart runbook

The venv runs a built wheel, so code changes need a rebuild and reinstall, not just a restart. **Gotcha:** each daemon **fails fast on a stale store** when a new wheel adds a migration — always migrate before (re)starting.

```bash
# 1. get master current — fast-forwards each projects/<repo> source checkout's
#    local master to origin/master. Without this you rebuild the PREVIOUS deploy.
winter ws fetch --all

# 2. rebuild the wheel (Angular apps + wheel + node-free verify)
cd projects/blizzard && mise run build

# 3. reinstall into the shared runtime venv (still in projects/blizzard from step 2)
uv pip install --python ../../../.venv --reinstall dist/blizzard-*.whl

# 4. migrate the stores (idempotent — safe every deploy)
../../../.venv/bin/blizzard hub    migrate --dir ../../../hub
../../../.venv/bin/blizzard runner migrate --dir ../../../runner

# 5. restart the hub (runner is After= hub, so the hub goes first)
systemctl --user restart blizzard-blizzard-hub.service

# 6. re-mint every graph the deploy changed — see below. Needs the hub UP, so it
#    sits here, and BEFORE the runner restart (which terminates a fleet worker).
../../../.venv/bin/blizzard hub graph mint \
  ../../../.venv/lib/python3*/site-packages/blizzard/hub/graphs/<graph>/graph.yaml

# 7. restart the runner
systemctl --user restart blizzard-blizzard-runner.service   # omit to leave the runner down
```

Paths in steps 3–6 are written from `projects/blizzard`, where step 2 leaves you: `../../../` is the workspace root's parent. Adjust if you run them from elsewhere.

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

Verify: `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8421/api/health` (and `:8431`), plus `systemctl --user is-active blizzard-blizzard-{hub,runner}.service`.

After a landing on `master`, this runbook is not the whole story — [post-delivery.md](./post-delivery.md) owns *when* it runs, what must be confirmed before building, and the one way it bites a fleet worker (restarting the runner terminates the agent running it).

## One-time: the hub's PM source

The hub needs a `[[pm_source]]` declared in `../hub/blizzard-hub.toml` (the **runtime dir's** config, not this workspace's checkout). Without one, `pm-items` 503s and every board pointer's label reads null. Skip this once it is present.

```toml
[[pm_source]]
name = "blizzard"
provider = "github"
repo = "paul-gross/blizzard"
token_env = "BZ_FORGE_TOKEN"
```

`token_env` names `BZ_FORGE_TOKEN` because that credential is already in `../hub/.env` — no `.env` edit needed, just this block.

**The name must be `blizzard`, not anything else.** The migration that introduced pointer source refs backfills every existing pointer to `source="blizzard"`, derived from the `paul-gross/blizzard` repo tail, before this config block exists to consult. Name the source anything else (or point it at a different `repo`) and those pointers name a source the config does not define: their board labels go null and their PM reads fail, even though the toml "looks" configured.

> **Vocabulary in flux.** blizzard#55 renames this whole surface to work-source terminology (`[[work_source]]`, `work-items`). It is **not landed** — `master` reads `pm_source` and nothing rejects it. Use the spelling above until the rename lands on `master`, then update this section with it.

## Operating the fleet

Drive the running hub with the venv binary (`../.venv/bin/blizzard`). Operator verbs are grouped under a noun — `hub chunk …`, `hub runner …`, `hub graph …` — so the bare `hub <verb>` forms do not exist:

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

Every one of these is a pure hub-API client and takes `--hub-url` (default `$BZ_HUB_URL`, else `http://127.0.0.1:8421`) — not `--url`.

**Ingest mints a chunk `not_ready`.** It will never be claimed until `chunk promote` moves it to `ready`, so an ingest on its own looks like it worked and then nothing happens. To stage a run deliberately — pinning a non-default graph before any runner can grab it — pause the runner first, then ingest, set the graph, promote, and resume last.

Ingest takes a source-native token — prefer `blizzard:26`, `blizzard#26`, or the issue's own URL pasted in. The `github:<url>` form also works, with a warning.

## Developing a feature env against this instance's data

Running a feature env's web or CLI against the live local hub — and the SQLite single-writer constraint that rules out sharing its databases with a second live daemon — is covered in the `# Local Settings` block of the workspace `AGENTS.md` (`AGENTS.local.md`). That file is **machine-local and gitignored**, so it is present only on a machine that has been set up for it; there is nothing to read there in a fresh clone.
