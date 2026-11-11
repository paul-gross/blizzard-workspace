# The findings sweep

**Rule.** Weekly or fortnightly, an operator-driven session drains the hub's live findings. It re-verifies each one
against `master`, closes what is gone, puts every finding left behind by a passed proposal to the user, and drafts the
rest into lane-sized garden proposals or withdrawals. Nothing is written to the hub until the user approves the draft.

**Why.** A finding leaves the live set only through an explicit exit verb or a delivery. Nothing else moves it:

- Passing a proposal records the pass and leaves every finding it cites live.
- A review-sourced finding has no routine to re-observe it.
- A routine re-checks only the scope it runs.

Without a standing drain, the live set grows past what anyone reads, and the correctness defects in it sink beneath
hundreds of prose nits.

**Scope.** An operator session using [our fleet binding](../blizzard-binding.md). The sweep is read-only on code: it
mints work and never fixes code itself. Each verb's `--help` owns its flags.

Every hub call in this pass takes `--hub-url "$BZ_HUB_URL"` and `--tenant "$FLEET_TENANT"`. The inventory snippets
starting with `hub` abbreviate the approved `blizzard` executable and those flags; they are not complete shell commands.
Keep project-scoped reads and proposal creation in the bound `blizzard` project.

## 1. Inventory

- **Proposals.** List them with `hub garden-proposal list --json`. Use
  [shared proposal review](blizzard-usage:/context/workflows/proposal-review.md) to decide any still-open proposal
  first, or leave it and keep its findings out of the draft. Draft no second proposal over findings an open one already
  cites.
- **Live findings.** No verb lists every finding, so enumerate them. For each routine (`hub routine list`) and each
  scope (`hub scope list`), run `hub finding list --routine <routine> --scope <scope> --source routine --json`. For each
  scope, run `hub finding list --source review --scope <scope> --json`. Give every call `--project`, set to its scope
  row's `project_slug`. Dedupe by `finding_id`.
- **Check every call.** A failed list call leaves no output, which looks exactly like an empty scope. Check each call's
  exit status, keep its stderr, and retry a failure until it succeeds — but a refusal naming a missing flag fails every
  time, so fix the call instead. A pair that never succeeds is a gap in the inventory, not an empty result.
- **Join.** Map each finding to the proposals whose `findings` cite it, and to each proposal's closure.

## 2. Re-verify against `master`

The set rarely fits one context, so fan out read-only subagents over batches of about 75 findings, sorted by scope and
locus so a batch shares files. Each subagent classifies its findings and writes the verdicts to a scratch file. It
replies with counts only.

Use the finding verdicts in the
[shared proposal-review workflow](blizzard-usage:/context/workflows/proposal-review.md#3-sweep-confirm-the-obvious-yeses).

- Every verdict records its current locus and one line of evidence.
- Locate each finding by its quoted text and symbols, not its line number. Loci drift and files move.
- A test-coverage or mutation-survivor verdict reached without re-running mutation testing is medium confidence. The
  draft says so.

## 3. Put passed proposals to the user

For each passed proposal that still cites live findings, show the user three things: the proposal, its pass reason, and
the findings' verdicts. Then ask whether to mark the findings `wont-fix` or carry them into this sweep's proposals. The
user decides. The reason usually points the way: a pass on a proposed gate or lint declines the gate, not the fix
underneath. An accepted proposal whose item outcome is `declined`, with findings still `present`, is put to the user the
same way.

## 4. Draft for review

Write the draft to a scratch file. Every live finding lands in exactly one bucket:

- **Confirm gone.** Every `gone` verdict.
- **Proposals.** An accepted proposal mints one work item, which becomes one chunk and one PR in one repo, so each
  proposal is lane-sized.
  - Group findings by shared file or seam, or by one defect seen from several angles.
  - Aim for roughly 3–15 findings. A singleton is only for a correctness defect that stands alone.
  - A large prose-hygiene set groups by directory, since one pass over a file clears all of its findings.
  - Each proposal carries a title, a class (`remediate`, or `escalate` when a design decision comes first), a priority
    in marshal order, a size, and a body.
  - The body names the defect at its current locus, why it matters, the fix direction, and the test that pins it.
  - Bodies carry no finding or chunk ids (`canon:no-process-refs`); the linked findings travel with the proposal.
- **Withdrawals.** `wont-fix`, `not-a-finding`, or `supersede`, each with a one-line note.

## 5. Approve, then write

Once the user approves the draft, write it in this order:

```bash
blizzard hub finding confirm-gone --hub-url "$BZ_HUB_URL" --tenant "$FLEET_TENANT" --note "…" <finding-id>…
blizzard hub finding wont-fix --hub-url "$BZ_HUB_URL" --tenant "$FLEET_TENANT" --note "…" <finding-id>…
# The same target flags apply to not-a-finding and supersede.
blizzard hub garden-proposal create --hub-url "$BZ_HUB_URL" --tenant "$FLEET_TENANT" --project blizzard \
  --title "…" --class remediate --body-file <body.md> --finding <finding-id>…
blizzard hub garden-proposal accept --hub-url "$BZ_HUB_URL" --tenant "$FLEET_TENANT" <proposal-id>
# Accept only proposals the user approves.
```

Prepare accepted work through [shared marshalling](blizzard-usage:/context/workflows/marshalling.md), under
[our local work policy](../blizzard-binding.md#local-policy).

## 6. Re-count, then report

Re-run the inventory after the writes. Every live finding should now be one the user chose to leave, one an accepted
proposal cites, or one first observed after the inventory. Any other live finding is an inventory gap: sweep it before
reporting.

Report these to the user:

- the live count before and after the sweep, by source
- the proposals created and accepted
- the withdrawals, by exit kind
- every finding left live, and why

Closing the loop after delivery belongs to the hub (`blizzard-product:/delivered/garden/machinery.md`).

## Don't

- Treat a failed inventory call as an empty scope or draft a second proposal over findings in an open one.
- Close the findings cited by a passed proposal without putting their verdicts to the user.
- Write withdrawals or proposals to the hub before the user approves the draft.
