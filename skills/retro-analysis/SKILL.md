---
description: Run the retrospective-analysis distillation pass over chunks closed in a date range — cluster recurring findings, route each to its owning store for human approval, and reconcile the prior pass's ledger
argument-hint: "[range, e.g. 'past two weeks' or explicit dates]"
allowed-tools: Bash, Read, Grep, Glob, Write, Edit, Agent
---

Run one retrospective-distillation pass: export the retrospective (and other) artifacts of every chunk closed in a range, cluster the recurring complaints, and route each cluster to the store that owns fixing it — a `blizzard-context` rule, a `blizzard`/`blizzard-mock` issue, a `blizzard-product` registry entry, or a graph prompt edit.
This is deliberately human-on-the-loop: every filing action is drafted here for the operator to approve, never filed silently.
Parse `$ARGUMENTS` for the range — a natural-language span ("past two weeks"), explicit dates, or empty (defaults to since the ledger's last recorded pass).

## Big picture

A pass that only ever adds findings and never asks "did the last fix land?" is not a feedback loop, just a list — this skill's reconciliation step (5) is what makes it a loop.

The four owner stores a finding cluster can route to, and when each applies:

| Owner | When a cluster routes there | How to file |
|-------|------------------------------|-------------|
| `blizzard-context` rule | The complaint is a recurring convention violation or a missing convention — something a `bzh:<slug>` rule would prevent | Draft the rule per the slot skeleton in `winter-canon:/rule-shape.md`, with a `bzh:<slug>` id per `blizzard-context:/index.md`'s id scheme; the operator commits it in a `blizzard-context` feature environment |
| `blizzard`/`blizzard-mock` issue | The complaint is a defect or missing capability in the application itself | Draft via the `/wg-issue` skill (`winter-github:/skills/issue/SKILL.md`) — it owns the issue format and repo selection |
| `blizzard-product` registry | The complaint reveals a gap in product intent (a missing epic, a milestone that needs re-scoping) | Draft per `blizzard-product:/context/index.md`'s authoring conventions; the operator adds it to `epics.md`/`milestones.md`/`plans/` |
| Graph prompt edit | The complaint is specific to how a node's prompt asks for work (e.g. a prompt that invites a recurring mistake) | Draft the prompt diff against `src/blizzard/hub/graphs/*/prompts/*.md` inside a `blizzard` feature environment (e.g. `alpha/blizzard/src/blizzard/hub/graphs/...`) |

This skill drafts every action above; it never runs `gh issue create`, never commits a rule, and never edits a prompt file itself.
Present each draft and wait for the operator to say yes, no, or "file it differently" before treating it as dispatched.

## 1. Resolve the range

Read `workspace:/context/project/retro-analysis-ledger.md`.

- **`$ARGUMENTS` is empty:** default the range start to the range end of the most recent `## Pass N` section in the ledger, and the range end to now.
  If the ledger contains no `## Pass N` heading yet, stop and ask the operator for an explicit starting range — do not assume unbounded history.
- **`$ARGUMENTS` names a relative span** ("past two weeks", "since last Monday"): resolve it against today's date to an explicit ISO-8601 UTC start (and end, default now).
- **`$ARGUMENTS` names explicit date(s):** use them directly; a single date means "since that date, through now".

State the resolved `<range-start>` to `<range-end>` back to the operator before continuing.

## 2. Idempotency check

Grep the ledger for an existing `## Pass N — <range-start> to <range-end>` section matching the resolved range exactly.
If one exists, report it (echo its Findings/Reconciliation/Citation tables) and stop — do not append a duplicate pass for a range already recorded.

## 3. Fetch closed chunks and their artifacts

Retrieval is commanded, not hand-rolled — these are the exact calls, run from the workspace root:

```bash
# Auth, once per session if not already logged in:
export BZ_HUB_URL=https://blizzard.grosscode.net
blizzard hub login   # or: blizzard hub login --paste  (headless)

# Every chunk in the fleet — the hub CLI has no server-side status/date filter,
# so narrow client-side:
blizzard hub chunk list --json --hub-url "$BZ_HUB_URL" \
  | jq --arg since "<range-start>" --arg until "<range-end>" \
      '[.[] | select((.status=="done" or .status=="stopped")
                      and .completed_at >= $since and .completed_at <= $until)]'
```

For each matched chunk id, pull its full artifact set (one call per chunk — there is no bulk-export endpoint):

```bash
blizzard hub chunk show <chunk_id> --json --hub-url "$BZ_HUB_URL"
```

From each response, keep:
- The retrospective content: group `.artifacts` by `(node_name, name)`, take the highest `epoch` entry in each group, then filter to `name=="retrospective"`. The artifact store is an append-only series keyed on the node name (`blizzard-context:/domain/artifacts.md` §"The chunk's artifact series"), so a retried node's earlier epochs are superseded text — including them double-counts a retried chunk and feeds stale text to clustering. A chunk built on the `default` graph (no retrospective node) has none; skip it for clustering but still scan its other artifacts for citations below.
- The full `.artifacts[].content` text (every kind, every name, latest epoch per `(node_name, name)`) — the citation-counting input in step 6.

Write the fetched set to a scratch file (e.g. `/tmp/retro-analysis-<range-start>.json`) rather than re-fetching per step; it is not committed.

## 4. Cluster the recurring findings

Fan this out rather than reading every retrospective inline: batch the retrospective texts across `Agent` calls (a few dozen chunks' worth of retrospectives per agent, adjusted down if a batch's combined text is large), each asked to extract recurring complaints — a real, specific, repeated pain point, not a one-off — with which chunk ids exhibit it.
Merge the per-batch results into one cluster list, deduplicating clusters that name the same underlying complaint across batches.

For each surviving cluster, draft:
- A one-line description of the complaint.
- The chunk ids (and count) it appeared in.
- Which owner store (the table in **Big picture**) fits, and a drafted action for that owner.

Present the drafted clusters to the operator, ranked by chunk count, and get an explicit decision per cluster: approve as drafted, approve with changes, or decline.
Only clusters the operator approves get filed (per the owner's own filing mechanism) and recorded as dispatched in the ledger; declined clusters are recorded too, with status `declined`, so a future pass does not re-surface them as new.

## 5. Reconcile against the prior pass

Read the most recent `## Pass N` section's Findings table in the ledger — every cluster it recorded as dispatched (not declined).
For each, check whether this range's clustering (step 4) surfaced the same underlying complaint again, and classify:

- **`recurred`** — the fix landed (the ledger names a commit/issue/rule reference) but the complaint still shows up in this range's chunks. The fix did not work.
- **`silent-after-fix`** — the fix landed and the complaint is absent from this range. Name the fix as the reason (noticeable impact).
- **`fell-off`** — the complaint is absent from this range but the ledger shows no fix landed for it (still `declined`, or approved but never actually filed). Note it as fallen off, not proven fixed — silence is not proof.

A prior finding with no trace either way (too old to still be relevant, e.g. its owner store was decommissioned) is noted as such rather than forced into one of the three verdicts.

## 6. Count `bzh:` citations

Across every artifact fetched in step 3 (not just retrospectives), grep for the citation shape:

```bash
grep -oh 'bzh:[a-z0-9-]*' /tmp/retro-analysis-<range-start>.json | sort | uniq -c | sort -rn
```

Report the per-rule count for the range.
A rule cited often is paying rent; a `blizzard-context` rule that never appears across a wide range is a pruning candidate worth naming in the pass report (not something this skill removes on its own).

## 7. Append the pass and commit

This checkout's `skills/` and `context/` directories are the workspace root itself, never worktreed per feature environment — there is no branch to declare or PR to open here.
This is the by-hand delivery path (`workspace:/context/project/contributing.md`), whose own rule is to rebase onto the latest `origin/master` first so history stays linear.
The ledger is a shared, long-lived file other passes append to, so bring the checkout current *before* editing it, not just before pushing:

```bash
git fetch origin
git rebase origin/master
```

Re-read `workspace:/context/project/retro-analysis-ledger.md` after the rebase — a concurrent pass may have appended since step 1.
If it added a section covering this same range, treat it like step 2's idempotency check and stop.

Append a new section to the ledger in this shape:

```
## Pass <N> — <range-start> to <range-end> (run <today>)

**Chunks covered:** <count> closed chunks in range.

### Findings dispatched

| Cluster | Chunks | Owner | Action | Status |
|---|---|---|---|---|
| <complaint, one line> | <count> | `bzh:<slug>` / `blizzard#N` / `epic:<slug>` / `<prompt path>` | rule candidate / issue filed / epic note / prompt edit | approved / declined |

### Reconciliation (prior pass's findings)

| Prior finding | Fix dispatched | Verdict | Notes |
|---|---|---|---|
| <complaint from the prior pass's Findings table> | <its Owner/Action> | recurred / silent-after-fix / fell-off | <one line> |

### `bzh:` citation counts

| Rule | Citations this range |
|---|---|
| `bzh:<slug>` | <count> |
```

Commit via the `/wf-commit` skill rather than hand-authoring the message — it stages the ledger change (review what else it stages; this checkout is shared, so stop and ask before it sweeps in unrelated uncommitted work) and writes the message from `workspace:/context/project/contributing.md`'s conventions.
Then push:

```bash
git push origin master
```

If the push is rejected (a concurrent pass landed first), `git fetch origin && git rebase origin/master`, re-check the ledger for a same-range section as above, and retry the push.

Re-running this skill on the same range afterward hits the idempotency check in step 2 and stops without appending a second entry.

## Report

Summarize for the operator: the resolved range, chunk count, each dispatched cluster with its owner and status, the reconciliation verdicts, and the citation-count table — the same content just committed to the ledger.

$ARGUMENTS
