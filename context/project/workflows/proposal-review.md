# Proposal review

How the user works the hub's open garden proposals with an agent: read every one, sort them into three tiers, and settle
the tiers in order, cheapest first. What a proposal is, and what passing or accepting one does, is
`blizzard-context:/domain/findings-and-proposals.md`'s; this file owns only the session.

**Read this when** told to run a proposal review, or to go through, triage, or decide the hub's proposals. The findings
sweep ([workflows/findings-sweep.md](./findings-sweep.md)) opens by deciding every open proposal: this is how that gets
done.

**Scope.** An operator session against the instance's hub. Every verb needs `$BZ_HUB_URL`, a session, and
`--tenant "$FLEET_TENANT"`
([singleton-instance.md § The operator CLI needs a session](../singleton-instance.md#the-operator-cli-needs-a-session)).
The review only reads code, and nothing is written to the hub until the user approves it.

## Roles and handoff

The main agent orchestrates the review and owns the conversation and decision record. Explain the proposals with the
context, costs, tradeoffs, and recommendations the stages below require, and wait for the user's decisions. Delegate
inventory and verification to read-only review agents; delegate approved proposal edits, accepts and passes, immediate
marshalling, and verification of the resulting hub state to a dedicated execution subagent.

Keep the next batch or heavy question prepared, including required verification, before the current answer arrives; hold
only one discussion ahead privately. On the user's answer, present that prepared next discussion immediately first, then
dispatch approved execution, verification, and further reads in the background. Execution handoffs, progress reports,
and grouping or promotion checkpoints must not delay the next discussion. Collect explicit grouping and promotion
approvals at a natural checkpoint after presenting it, batching pending rollouts where appropriate; waiting work remains
unauthorized until approved. Nonblocking completion notices must not repeatedly replace the pending question with
status-only turns.

Each execution handoff includes:

- Exact proposal IDs and session labels, the approved decisions and reshaping, and explicit exclusions.
- The review date and required closure reasons, plus shared manifest and notes paths for decisions and results.
- Current grouping and promotion authorization, including which lanes and additions it covers.

The execution agent applies only that authorization: no decisions on unapproved proposals, new scope, or inferred
promotion. It returns grouping proposals, uncertainties, blockers, and verified hub results to the main agent, not to
the user in a parallel conversation. The main agent relays material issues and approval requests, tracks pending versus
completed work, and keeps the review moving five at a time or one heavy decision at a time rather than ending with a
rollout status. Distinguish handed-off work from execution actually started and results verified.

## 1. Inventory

`blizzard hub garden-proposal list --tenant "$FLEET_TENANT" --json` lists every proposal, newest first. The open ones
are those with no `closure`. Read each open proposal's body in full, along with the findings it cites. Proposals usually
outnumber what fits in one context, so fan the reading out to read-only subagents in batches. Each subagent writes its
notes to a scratch file and replies with tiers only.

## 2. Sort into three tiers

| Tier                      | Holds                                                                                                  |
| ------------------------- | ------------------------------------------------------------------------------------------------------ |
| **Obvious yes**           | A clear defect with a clear fix in one lane, needing no design decision; accepting it is plainly right |
| **For consideration**     | Worth doing or not on the user's judgment, such as a tweak, a new gate, or a scope or priority call    |
| **Requires much thought** | Turns on a design decision, crosses seams or repos, or conflicts with another proposal or the charter  |

Put each proposal in one tier with a one-line reason, and show the user the three lists before deciding anything. A
proposal that duplicates another open one, or work already accepted or in flight, is flagged wherever it lands: check
recently closed proposals, unfinished chunks, and open issues, since in-flight work can make a proposal moot.

## 3. Sweep-confirm the obvious yeses

Re-verify every obvious yes against `master` before it is accepted, unless the user waives it. Each cited finding gets
one of the findings sweep's verdicts (`present`, `partial`, `gone`, `unclear`), and the proposal's body must still
describe the code as it stands. A proposal survives the sweep when its findings are still present and its fix direction
still holds. Anything that fails drops to **for consideration**, with the reason it failed.

Put the surviving list to the user. Accept only what they approve:
`blizzard hub garden-proposal accept --tenant "$FLEET_TENANT" <proposal-id>`. Every accept and pass reason names the
review and its date, so the closure reads as one session's decision. Every accept mints a chunk, and minting owes the
marshal steps at once ([workflows/marshalling.md](./marshalling.md)).

**Group liberally when marshalling a review's chunks.** One accepted proposal is often a small fix, and a lane given
only that underuses its agent: mid-sized chunks do well. Group small chunks that share an area (one package, one feature
folder, one seam) into one lane, even when they are separable. Stop short of a sprawling PR. Keep a chunk standalone
only when it is already large, such as a broad mechanical sweep, and `depend` across groups only where marshalling's bar
for an edge is met. Promote the lanes in priority order only with the user's grouping and promotion approval. A later
tier's accepts join a lane that is still unacquired, or depend on it, within the authorized grouping. Bring additions
outside that scope back for approval; joining an already-ready lane also needs promotion authorization for the added
work, since grouping must not bypass that gate.

## 4. Walk the consideration tier

While the current five await the user's decisions, delegate read-only preparation of the next five to a review agent.
Keep only one batch ahead, hold its prepared material privately, and present it after the user answers the current
batch, not alongside it. This preparation is separate from approved background execution and confers no approval or
authority to mutate proposals or the hub.

Present the tier five proposals at a time. Give each a short paragraph covering what is wrong, what the fix is, its
costs and tradeoffs, and a recommendation. Write for a senior software engineer, not an architect: say what the code
does and why it hurts, and leave out the harness's rule ids and layering vocabulary unless they carry the point. The
user calls each one *do* or *don't*, often with a reshaping:

- **Do.** Accept the proposal. When the user reshapes it, edit its title, body, and findings first, so the minted item
  carries the decision. Fold a proposal the user calls answered by another into that one, and pass it with a note naming
  where it went.
- **Don't.** Pass it with the user's reason, because a pass is what stops a later run from raising the same proposal
  again.

## 5. Take the heavy thinkers one at a time

Each proposal here gets its own discussion. Verify its claims against `master` first, since a heavy thinker's premise is
sometimes dormant or overstated. Then explain the background in plain terms, name the decision it waits on, the options,
and a recommendation, and settle it before moving to the next. Order the tier so decisions that feed later ones come
first.

The outcome is usually one of these:

- **Accept to the fleet** when the work is a rule plus well-understood fixes that fit one lane. Edit the body so it
  carries the user's decisions.
- **Pass, and file a GitHub issue** when the work needs design first: a new capability, a new seam or contract, or a
  change to the domain model. File through `/wg-issue`, write the user's design into it, and name the issue in the pass
  reason. A change that has to stay compatible across the skew window, such as emitting a new shape, then moving
  consumers onto it, then retiring the old one, becomes a small epic with one child per stage.
- **Pass** when the premise is dormant or the user declines. Record why, so a later run does not raise it again.

## 6. Report

Reconcile execution results with the shared manifest. Report the open count before and after, what was accepted with the
lanes it was marshalled into, what was passed and why, the issues filed, and what was left open and why. Name any
pending execution or blockers separately from completed changes.

## Don't

- Accept or pass any proposal before the user has approved that tier's list.
- Accept an obvious yes without re-verifying it against `master`, unless the user waived the re-verification.
- Pass a proposal without a reason.
- Treat passing a proposal as withdrawing its findings; their lifecycle stays with
  `blizzard-context:/domain/findings-and-proposals.md`.
