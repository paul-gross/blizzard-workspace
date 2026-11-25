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

Use the tenant above explicitly for tenant-scoped calls and the intended project for project-scoped calls.

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

On a machine with a runner host, use that host's venv: `<instance dir>/.venv/bin/blizzard`
([fleet-machines.md](./fleet-machines.md) names each). On a machine without one, use an operator-provided `blizzard` on
`PATH`, or run the call on a runner-host machine over `ssh <machine>`. If no such CLI is available, ask the operator for
setup rather than borrowing a feature-env or source-checkout binary.

The operator CLI and hub must match in **major version**, and also in **minor version when major is `0`**;
[instance.md](./instance.md#deployed-surface) owns the health probes. Minor-version differences are allowed when major
is at least `1`. Patch, prerelease, and development-build differences do not block fleet calls. Check the relevant
command's `--help`; if an operation is unsupported, stop that operation and request a supported invocation or CLI
upgrade. This is the workspace's version policy for the
[usage connection check](../../.winter/ext/usage/context/connection.md#check-access-and-scope).

Use our hub URL with the human login flow in
[usage connection guidance](../../.winter/ext/usage/context/connection.md#check-access-and-scope). Credentials and
sessions stay outside this repo; the fleet's machine tokens are not operator credentials.

## Machine ownership and authority

`AGENTS.local.md` identifies this session's machine and role. An interactive assistant acts for the operator within the
user's request; it is not a fleet worker merely because this workspace develops Blizzard.

The runner hosts sit on the operator's own machines, joined by one tailnet; [fleet-machines.md](./fleet-machines.md)
names them and which holds a host. An **operator session** on any of those machines may operate every runner host — its
runtime, systemd unit, and collector — over `ssh <machine>`, using that machine's own paths. A **fleet worker** operates
none of them, whichever machine it runs on. Authorized hub-API work can run from any checkout.

Moving the deployment onto a build moves the hub and every runner host in one sitting, from one orchestrating session:
moving only some would leave the instance split across builds. Host identities, paths, and the deployment runbook live
in [instance.md](./instance.md).

## Local policy

Use the [shared operator workflows](blizzard-usage:/context/workflows/index.md) with these local bindings:

| Fact                               | Binding                                                                                                          |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Backlog scope                      | The `blizzard` project's chunks, not the tenant-wide backlog                                                     |
| Repository base                    | Current `origin/master` in each affected repository                                                              |
| Garden criteria                    | `blizzard-context:/garden/index.md`                                                                              |
| Garden inventory windows           | 90 days for sweep measurements; initially 14 days for runs, extended to include older unfinished runs            |
| Issue filing                       | `/wg-issue`, following [contributing.md](./contributing.md)                                                      |
| Work eligibility and tracker links | [Contributing](./contributing.md#issue-titles) owns breaking/manual-only exclusions and GitHub dependency policy |
| Findings drain                     | [Our findings sweep](./workflows/findings-sweep.md)                                                              |

Every newly minted chunk immediately owes the shared marshalling workflow's mapping, grouping, and dependency
assessment. Promotion needs the user's authorization. A named pass's draft-only or approval checkpoint takes precedence
over automatic preparation writes.

Our promotion priority is defects operators or the fleet hit, then latent defects, then prose.

| Policy                                | When to read                                                                     |
| ------------------------------------- | -------------------------------------------------------------------------------- |
| [Post-delivery](./post-delivery.md)   | Work has landed; a landing does not authorize a deployment                       |
| [Hub data modes](./hub-data-modes.md) | Developing against hub data; our production fleet is not a UI-development target |
