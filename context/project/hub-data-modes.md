# Developing a feature env against hub data

Goal: work in a feature env (`alpha`/`beta`/…) but see realistic — or real — fleet data. What is deployed where, and the redeploy runbook, is [local-instance.md](./local-instance.md).

**Do not point board or UI development at the hosted hub, `https://blizzard.grosscode.net`.** It is a **public, authenticated, real** fleet driving real work, and a board served by `ng serve` is a *real* client: clicking Pause in it pauses a real chunk, over the internet, against a hub other people can see. UI work runs against a **local, non-live hub**; the hosted one is for operator-driven inspection only, and needs `blizzard hub login` even for that.

## What data is actually reachable

**There is no local hub DB.** `../hub/data/hub.db` exists but is a **frozen snapshot** — the fleet as it stood at the migration, not as it is. The live hub store is on the EC2 host and is not reachable as a file from here at all. `../runner/data/runner.db` is the only live local database.

**Hard constraint (runner only):** SQLite is single-writer. The systemd runner holds `../runner/data/runner.db` and runs migrations on boot, so a second daemon writing it concurrently risks lock contention / corruption. **Do not point a live feature-env runner at it while `blizzard-blizzard-runner.service` is up.** This is a data-safety constraint, not a tooling gap — no config mechanism relaxes it.

Port 8421 answers **nothing** — the local hub unit is stopped and disabled. A dev surface still aimed there fails to connect rather than quietly reaching the wrong fleet.

## Pointing an env at another hub

A per-single-env var override — `[env.<name>.vars]` — points one env at a different hub while its siblings keep their derived per-env values. Precedence rules, reserved band names, and `source <(winter env <name>)` are owned by [ports-and-environments.md](../winter-cli/configuration/ports-and-environments.md).

What the band is good for here is reaching **another hub on this machine** — a snapshot hub (mode 2 below), or a sibling env's. **No band reaches the hosted hub**, which is a hostname rather than a port — and pointing a dev surface at it is the thing the rule above forbids anyway.

Pin `BZ_HUB_PORT`, not `BZ_HUB_URL`: the port is the one knob both consumers read, since `BZ_HUB_URL` is derived *from* it for the CLI while the web dev-server proxy reads `BZ_HUB_PORT` directly (`blizzard:web/proxy.conf.hub.js`). Pin only `BZ_HUB_URL` and you move the CLI while `ng serve hub` keeps proxying to the env's own hub — two surfaces silently aimed at different data.

## Workable modes, preferred first

1. **Env-local hub — the default for UI work.** `winter service up <env>` starts the env's own hub and injects its `BZ_HUB_PORT` band, so `npm start` proxies to it with no extra configuration (`npm start` errors out if the var is unset rather than serving a board whose every read 404s). Data is whatever that env's store holds — seed it (mode 2 or 3) when the empty board is not enough.
2. **Snapshot hub — realistic shape, zero live risk.** `../hub/` is a point-in-time copy of the fleet, frozen at the migration. Host a hub on a **copy of the copy** at a free port, migrating it first — the venv's wheel advances with every landing while that store does not, and a hub fails fast on a store behind its code:
   ```
   mkdir -p /tmp/hub-snap && cp -r ../hub/* /tmp/hub-snap/
   blizzard hub migrate --dir /tmp/hub-snap
   blizzard hub host --dir /tmp/hub-snap --port 4599
   ```
   then `BZ_HUB_PORT=4599 npm start`. The best source of realistically *shaped* data available locally — but it is a fixed snapshot, not current fleet state, and drifts further every day. Nothing refreshes it; a fresh one means dumping the store off the host.
3. **Seeded mock stack — synthetic worlds.** `blizzard-mock-data reset --store hub --url <sqlite>` returns a scratch hub store to clean; richer state seeds through the hub's own HTTP API (ingest → promote, with `blizzard-mock-forge` as the work source). The mock-data `fixture` subgroup (named one-command scenarios) is still stubbed — until it lands, rich synthetic worlds are assembled by hand.
4. **Avoid:** a second live **runner** against `../runner/data/runner.db` — it spawns real `claude` workers, mutates real worktrees, and claims real chunks from the hosted hub; two runners double-drive the fleet. And avoid client mode against `https://blizzard.grosscode.net` for development — see the rule above; operators inspecting live state by hand (`blizzard hub status`, after `blizzard hub login`) are the exception.
