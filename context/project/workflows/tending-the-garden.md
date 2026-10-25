# Tending the garden

**Rule.** When the user asks to tend the garden, an operator-driven session lists every routine with the date each of
its scopes was last swept, recommends which pairs to run, and runs the pairs the user picks. It then marshals the chunks
those runs mint into `ready`. The user approves twice: **which runs to start**, and **whether the proposed grouping and
promotion order may be applied**. Nothing is written to the hub before each approval.

**Why.** The hub has no scheduler, so a routine runs only when someone starts it (`epic:cadence` is the planned
replacement). Without a standing pass, the routines nobody remembers go unswept the longest.

**Scope.** An operator session against the instance's hub. Every `blizzard hub` verb needs `$BZ_HUB_URL`, a session,
`--tenant`, and, for `routine run` and `routine sweeps`, `--project`
([singleton-instance.md § The operator CLI needs a session](../singleton-instance.md#the-operator-cli-needs-a-session)).
This pass mints runs and never fixes code itself. Each verb's `--help` owns its flags.

## 1. Inventory

- **Routines.** `hub routine list --json` lists the live routines. Each row names its `project_slug`, which is the
  `--project` for every later call about that routine.
- **Scopes and last sweep.** For each routine, `hub routine show <routine-id> --json` returns its linked scope set and
  its default scope. `hub routine sweeps <name> --since <90 days ago> --until <now> --json` returns the routine's
  last-swept table. That table ignores the window and covers every linked scope, showing either the produced-at instant
  and recorded revisions or `never`. The window bounds only the measurement series that follows the table.
- **Runs in flight.** `hub run list --since <14 days ago> --json` lists recent runs. A run that has not delivered is
  still in flight, and its routine/scope pair is not run again in this pass.
- **Check every call.** A failed call prints nothing, and that looks exactly like a routine with no sweeps. Check each
  exit status. A pair whose call never succeeds is a gap in the inventory, not a `never`.

## 2. Show everything, then recommend

Show the user one table with every routine/scope pair: routine, scope, last swept (or `never`), the age of that sweep,
and whether a run is in flight. Sort it oldest first, with `never` at the top. Show every pair, including the ones you
don't recommend.

Then recommend which pairs to run, giving each recommendation its reason. Weigh these:

- **Never swept.** The pair has no baseline, so it runs `--mode full`.
- **Ground has moved.** Commits have landed on `origin/master` in the scope's ground since the recorded revision
  (`git log --oneline <rev>..origin/master -- <paths>`). Many commits on a pair that is not yet old still make it worth
  running.
- **Criteria have moved.** The axis's criteria docs in `blizzard-context:/garden/` changed since the last sweep. A
  changed standard reopens judgment on code that didn't change, so the run is `--mode full`.
- **Age.** Use age when nothing else separates two pairs.

The default mode is `delta` for any pair that has a baseline. Leave out pairs whose run is still in flight, and pairs
with no ground and no criteria change since their last sweep.

**Ask: which ones do we run?** Offer the recommended set, and accept any subset or addition from the full table. The
user's pick is final.

## 3. Run

For each chosen pair, run:

```bash
blizzard hub routine run <name> --scope <scope-slug> --mode <full|delta> --project <project-slug> --tenant "$FLEET_TENANT"
```

- Record each minted `chunk_id`, and record whether a requested `delta` was downgraded to `full`.
- A `run refused` conflict usually means a run is already live for that pair. Report it and don't retry it.
- Every minted chunk rests `not_ready` until it is promoted.

## 4. Draft the marshal

Run [marshalling.md](./marshalling.md) steps 1–3 over the new chunks as a draft, without writing to the hub. Map them
against every unfinished chunk, and propose the groups, the dependency edges, and a promotion order. Then show the
draft: each group with the seam it shares, each edge with its reason, and the promotion order with the reason for each
place. A run edits no code, so collisions here come from shared scope ground, not from files two lanes would both edit.

**Ask: are we ready to group these this way and promote?** This approval covers marshalling steps 2–4 for these chunks.
They wait for it instead of being applied unasked. Adjust the draft until the user approves it.

## 5. Apply, then report

Once the user approves, run `hub chunk group`, `hub chunk depend`, and `hub chunk promote` in the approved order, each
according to [marshalling.md](./marshalling.md).

Then report to the user:

- each run started: routine, scope, mode (and any downgrade), and chunk id
- each pair skipped, and why
- each group, edge, and promotion, with its reason

The runs' findings arrive on the hub as each run delivers. Draining them belongs to
[findings-sweep.md](./findings-sweep.md) and [proposal-review.md](./proposal-review.md).

## Don't

- Hide pairs that you don't recommend. The user picks from the whole table.
- Treat a failed inventory call as `never`, or start a second run on a pair whose run is still in flight.
- Start a run, group a chunk, or promote one before the user approves that step.
