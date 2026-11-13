# Live runner proxy

Develop the runner panel against the **live** runner host's API from any feature env, with one sign-in shared by all of
them.

The live runner signs a browser in only on an origin its `public_url` declares, so a feature env's dev server, each on
its own port, can never finish a login against it. This proxy is the one declared origin, `http://localhost:4500`. It
forwards every request to the live runner, saves the session cookie the login callback mints, and adds that cookie to
any later request that arrives without one. Every env's panel (and curl) then points its `/api` at the proxy.

**This is the real fleet.** A panel served through the proxy is a real client of the live runner host: pausing,
cancelling, or releasing in it does so on `xps`, for both runners and both projects. The rule
[hub-data-modes.md](../../context/project/hub-data-modes.md) sets for the hosted hub applies here in spirit. Use the
proxy to read live data and to exercise a change you mean to try against it, and never point a fleet env at it.

## Pieces

| Piece                                   | Where                                                                                    |
| --------------------------------------- | ---------------------------------------------------------------------------------------- |
| The proxy (Node stdlib, no install)     | `proxy.mjs`                                                                              |
| The workspace-scoped service            | `live-runner-proxy` in `.winter/config/winter-service-process-compose/config.toml`       |
| Its port, `BZ_LIVE_RUNNER_PROXY_PORT`   | `[env.workspace.vars]` in `.winter/config.toml`: workspace band +0, `4500`               |
| A feature env's live panel port         | `BZ_RUNNER_LIVE_WEB_PORT`, feature band +7 (alpha `4527`)                                |
| The upstream, machine-local, gitignored | `upstream.local`, one line: the live runner's URL                                        |
| The saved session                       | `~/.local/state/blizzard-live-runner-proxy/session.json` (mode 600)                      |

Without `upstream.local` the proxy idles without binding a port. That is the state on `xps`, which hosts the live
runner, so the workspace scope always comes up there. The upstream lives in a file rather than in
`.winter/config.local.toml` because a local `[env.workspace.vars]` table replaces the committed one instead of merging
with it, which would drop `BZ_LIVE_RUNNER_PROXY_PORT`.

## Setup, once per machine

1. On this machine, write the live runner's URL as reached from here:

   ```text
   echo 'https://xps.<tailnet>.ts.net:8431' > tools/live-runner-proxy/upstream.local
   ```

2. On `xps`, add the proxy's origin as an extra entry to the top-level `public_url` in
   `~/projects/blizzard-blizzard/runner/blizzard-host.toml`. Keep the existing first entry first, because it is the
   canonical origin:

   ```toml
   public_url = ["<existing canonical origin>", "http://localhost:4500"]
   ```

   Then restart the runner host. That restart is fleet-wide, so follow
   [post-delivery.md](../../context/project/post-delivery.md#the-restart-is-fleet-wide). The widened set reaches the hub
   on the host's first tick after the restart. A login attempted before that tick is refused as an unregistered
   redirect URI.

3. Start the proxy: `winter service up workspace`. Any `winter service up <env>` also starts it.

## Use

1. **Sign in once.** Open `http://localhost:4500/api/auth/login`. Once the hub's callback lands, the proxy logs
   `session stored: bz_runner_session_<client>`. A login started from a dev server's page is sent back to that dev
   server when it finishes.
2. **Serve an env's panel against the live runner:**

   ```text
   tools/live-runner-proxy/panel.sh alpha    # http://localhost:4527/
   ```

   This runs `ng serve runner` from the env's `blizzard/web` with `BZ_RUNNER_PORT` aimed at the proxy, on the env's
   `BZ_RUNNER_LIVE_WEB_PORT`. The env's own `web-runner` (local runner, `BZ_RUNNER_WEB_PORT`) keeps running beside it.
   No `service up` starts a live panel. It is started by hand only.
3. **From scripts:** `curl http://localhost:4500/api/...` carries the saved session too.

| Endpoint                     | What it does                                                                 |
| ---------------------------- | ---------------------------------------------------------------------------- |
| `GET /__proxy`               | Upstream, origin, login URL, and the saved session's name and expiry          |
| `POST /__proxy/forget`       | Drop the saved session                                                        |
| anything else                | Forwarded to the live runner, arriving as `Host: localhost:4500`             |

The proxy serves loopback callers only, because it attaches a live session to whatever reaches it. It refuses (403) a
request whose Host is not a loopback name, which stops DNS rebinding, and one whose Origin is not a loopback origin,
which stops cross-site forms and fetches. The login callback is the one exception, since the hub posts it cross-site.

When the session expires, `/__proxy` shows none and the panel's reads come back signed out. Sign in again at step 1.
Logs: `winter service logs workspace/live-runner-proxy`.
