# This workspace's Blizzard binding

Read before any fleet call. General connection and operating guidance belongs to
[blizzard-usage](../../.winter/ext/usage/index.md); this page supplies our target, access, and authority.

## Target and scope

| Fact                        | Binding                                                                 |
| --------------------------- | ----------------------------------------------------------------------- |
| Hub / board                 | `https://blizzard.grosscode.net` — production, the real public fleet    |
| Tenant                      | `paul-gross`, `ten_01M4N8ZW1AXGMFD9WCAFJ1FJZB`                          |
| Project for this workspace  | `blizzard`                                                              |
| Other project on this fleet | `winter` — select it only for work on that project                      |
| Human authentication        | GitHub OAuth through `blizzard hub login`; reads also require a session |

The operator also administers the `demo` tenant. Always select our tenant explicitly for tenant-scoped calls and the
intended project for project-scoped calls; neither an ambiguous membership nor a CLI default selects our fleet.

Our operator workflows use these shell bindings:

```bash
export BZ_HUB_URL=https://blizzard.grosscode.net
export FLEET_TENANT=ten_01M4N8ZW1AXGMFD9WCAFJ1FJZB
export HUB_URL="$BZ_HUB_URL" TENANT="$FLEET_TENANT" PROJECT=blizzard
```

`HUB_URL`, `TENANT`, and `PROJECT` supply the examples in `blizzard-usage`. `BZ_HUB_URL` is the CLI's URL setting;
`FLEET_TENANT` is our shell convenience, not a CLI setting. Existing workspace workflows pass it as
`--tenant "$FLEET_TENANT"`.

## Approved CLI and sign-in

On `xps`, use `~/projects/blizzard-blizzard/.venv/bin/blizzard`. On another machine, use an operator-provided `blizzard`
on `PATH` matching the hub's live reported build; [singleton-instance.md](./singleton-instance.md#deployed-surface) owns
the health probes. The runtime venv is not present on other machines. If that CLI is unavailable, ask the operator for
setup rather than borrowing a feature-env or source-checkout binary.

Use our hub URL with the human login flow in
[usage connection guidance](../../.winter/ext/usage/context/connection.md#check-access-and-scope). Credentials and
sessions stay outside this repo; the fleet's machine tokens are not operator credentials.

## Machine ownership and authority

`AGENTS.local.md` identifies this session's machine and role. An interactive assistant acts for the operator within the
user's request; it is not a fleet worker merely because this workspace develops Blizzard.

The runner host is **`xps`**, a Dell XPS. Only its own checkout can operate its runtime, systemd unit, or collector.
Every other checkout holds no part of the deployment: refuse runner-host operations and hand them to the operator on
`xps`. Authorized hub-API work can run from any checkout.

Moving the deployment onto a build also runs only on `xps`, including the hub half: moving the hub alone would leave our
singleton split across builds. Host identities, paths, and the deployment runbook live in
[singleton-instance.md](./singleton-instance.md).

## Local policy

| Policy                                     | When to read                                                                                        |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| [Marshalling](./workflows/marshalling.md)  | Minting chunks or ordering the backlog; apply our selection, tracker, priority, and approval policy |
| [Operator workflows](./workflows/index.md) | Running a named operator pass; its approval checkpoints govern its writes                           |
| [Contributing](./contributing.md)          | Filing fleet work or delivering workspace/project changes                                           |
| [Post-delivery](./post-delivery.md)        | Work has landed; a landing does not authorize a deployment                                          |
| [Hub data modes](./hub-data-modes.md)      | Developing against hub data; our production fleet is not a UI-development target                    |
