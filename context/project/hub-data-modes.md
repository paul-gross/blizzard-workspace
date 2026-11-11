# Developing a feature env against hub data

Goal: work in a feature env (`alpha`/`beta`/…) but see realistic — or real — fleet data. What is deployed where, and the
redeploy runbook, is [singleton-instance.md](./singleton-instance.md).

**Do not point board or UI development at the hosted hub, `https://blizzard.grosscode.net`.** It is a **public,
authenticated, real** fleet driving real work, and a board served by `ng serve` is a *real* client: clicking Pause in it
pauses a real chunk, over the internet, against a hub other people can see. UI work runs against a **local, non-live
hub**; the hosted one is for operator-driven inspection only, using [our fleet binding](./blizzard-binding.md) and
[usage connection guidance](../../.winter/ext/usage/context/connection.md).

## What data is actually reachable

**There is no local hub DB.** The live hub store is on the EC2 host and is not reachable as a file from any checkout —
but the host ships a full copy of it to S3 every day, which any machine with the `blizzard-prod-admin` profile can pull
(mode 2). On `xps` ([singleton-instance.md § Where it runs](./singleton-instance.md#where-it-runs)),
`~/projects/blizzard-blizzard/hub/data/hub.db` exists but is a **frozen snapshot** — the fleet as it stood at the
migration, not as it is — and `~/projects/blizzard-blizzard/runner/data/host.db`, the runner host's store holding both
runners' rows, is the only live database beside the checkout. Anywhere else there is neither.

**Hard constraint (runner only, on `xps`):** SQLite is single-writer. The systemd host holds its store —
`~/projects/blizzard-blizzard/runner/data/host.db` (`blizzard-blizzard-runner.service`) — and runs migrations on boot,
so a second daemon writing it concurrently risks lock contention / corruption. **Do not point a live feature-env runner
at the host's store while its unit is up.** This is a data-safety constraint, not a tooling gap — no config mechanism
relaxes it.

Port 8421 answers **nothing** — on `xps`, the old local hub unit is stopped and disabled. A dev surface still aimed
there fails to connect rather than quietly reaching the wrong fleet.

## Pointing an env at another hub

A per-single-env var override — `[env.<name>.vars]` — points one env at a different hub while its siblings keep their
derived per-env values. Precedence rules, reserved band names, and `source <(winter env <name>)` are owned by
[ports-and-environments.md](../winter/cli/configuration/ports-and-environments.md).

What the band is good for here is reaching **another hub on this machine** — a snapshot hub (mode 2 below), or a sibling
env's. **No band reaches the hosted hub**, which is a hostname rather than a port — and pointing a dev surface at it is
the thing the rule above forbids anyway.

Pin `BZ_HUB_PORT`, not `BZ_HUB_URL`: the port is the one knob both consumers read, since `BZ_HUB_URL` is derived *from*
it for the CLI while the web dev-server proxy reads `BZ_HUB_PORT` directly (`blizzard:web/proxy.conf.hub.js`). Pin only
`BZ_HUB_URL` and you move the CLI while `ng serve hub` keeps proxying to the env's own hub — two surfaces silently aimed
at different data.

## Workable modes, preferred first

1. **Env-local hub — the default for UI work.** `winter service up <env>` starts the env's own hub and injects its
   `BZ_HUB_PORT` band, so `npm start` proxies to it with no extra configuration (`npm start` errors out if the var is
   unset rather than serving a board whose every read 404s). Data is whatever that env's store holds — seed it (mode 2
   or 3) when the empty board is not enough.
2. **Snapshot hub — real fleet data, zero live risk.** The production host ships a full, consistent copy of its store to
   S3 daily (and on demand); pull one and host it on a free port, on localhost only:

   ```text
   blizzard-infra/scripts/hub-snapshot.sh pull           # the newest; --fresh takes one first
   blizzard hub init <dest>                              # scaffolds config, migrates the store; never replaces it
   blizzard hub host --dir <dest> --host 127.0.0.1 --port 4599
   ```

   `pull` prints `<dest>` (under `~/.cache/blizzard/hub-snapshots/`) and the hub build that wrote the snapshot — the
   local `blizzard` must be at or above it, since a hub refuses a store ahead of its code. Then
   `BZ_HUB_PORT=4599 npm start`. The snapshot is the **whole production store, transcripts included**: keep it on the
   machine and never bind it beyond localhost. Its sealed hub secrets stay sealed — their key is the host's, not in the
   store. Retention, layout, and the on-host side are owned by `blizzard-infra:README.md` § Store snapshots.
3. **Seeded mock stack — synthetic worlds.** `blizzard-mock-data reset --store hub --url <sqlite>` returns a scratch hub
   store to clean; richer state seeds through the hub's own HTTP API (ingest → promote, with `blizzard-mock-forge` as
   the work source). The mock-data `fixture` subgroup (named one-command scenarios) is still stubbed — until it lands,
   rich synthetic worlds are assembled by hand.
4. **Avoid:** a second live **runner** against the host's store — it spawns real `claude` or `opencode` workers, mutates
   real worktrees, and claims real chunks from the hosted hub; two runners double-drive the fleet. And avoid client mode
   against `https://blizzard.grosscode.net` for development — see the rule above; operators inspecting live state by
   hand, under the [fleet binding](./blizzard-binding.md), are the exception.

**Resetting an env's own hub store strands its runner.** A `reset` of the store under `BZ_HUB_RUNTIME`, or deleting that
dir, leaves the runner's runtime dir holding a token the hub no longer knows, and every call it makes is refused.
Restart it with `winter service restart <env>/runner`: the service runs `runner init --allow-readd`, which adds the
runner afresh under a new id (`blizzard/docs/deployment/runner-auth.md` §Adding a runner owns the flag).
