# Marshalling the backlog

**Rule.** To **marshal** is to turn the resting backlog into ordered, claimable work. When the user says to marshal,
carry every `not_ready` chunk through the steps below, promotion included. Anything that mints a chunk —
`hub chunk ingest`, `hub garden-proposal accept`, `hub item create` — owes steps 1–3 at once, unasked. Only promotion
waits for the word.

**Why.** Promotion order is the queue order, and a runner claims whatever reaches the head of `ready`, so chunks that
are one change are grouped before then, or two lanes build it twice. An edge is not free: every dependent idles until
its prerequisite lands, and a chain of edges drains the queue to one lane. Two lanes touching the same files is ordinary
— the later one rebases, and a conflict routes its chunk back through its own graph to resolve — so an edge buys
ordering only where a lane could not proceed or could not resolve its rebase without it.

**A chain is ingested whole.** A chunk need not be claimable today to be ingested: an issue whose prerequisites are all
landed or filed is ingested with the rest of its chain, each link resting in `ready` behind the one before it. An epic's
intake note that holds a child back until a sibling lands is met by a dependency edge instead. Only an issue waiting on
an event no chunk stands for — a release cut, a by-hand deployment step — stays uningested until that event happens.

**Breaking work waits on GitHub.** An issue whose title carries `(BREAK)`
([contributing.md § Issue titles](../contributing.md#issue-titles)) is never ingested while marshalling or minting work,
and neither is anything that depends on one: ingesting a chain stops short of its first `(BREAK)`. The issue stays on
GitHub as the record of what a deprecation still owes, until an operator decides a release may carry the break and
ingests it by hand.

**Manual work is never ingested.** A `blizzard:manual` issue
([contributing.md § Fleet-fulfillable issues](../contributing.md#fleet-fulfillable-issues)) is a human operator's, never
ingested while marshalling or minting work, by any path. A fleet issue blocked by an open `blizzard:manual` issue is
waiting on an event no chunk stands for, so it stays uningested until the manual issue closes. The label is this
workspace's alone; the operator's agent applies this rule, and blizzard reads no labels.

**Scope.** An operator session against the instance's hub. Every `blizzard hub` verb needs `$BZ_HUB_URL` and a session
([singleton-instance.md § The operator CLI needs a session](../singleton-instance.md#the-operator-cli-needs-a-session)).

1. **Map the ground.** For each `not_ready` chunk, read its work item and locate the code and docs it will change on
   `origin/master`. Map every unfinished chunk too — `ready`, `running`, `delivering` — since a resting chunk can build
   on, or rewrite the same ground as, work already in flight.
2. **Group** chunks that are one change — the same defect from two angles, or small changes to the same files for the
   same reason: `hub chunk group <survivor> <merged-id>…`. A group is one lane and one PR, so keep it a size one lane
   carries. Every id must be unacquired.
3. **Link** only chunks that must be ordered: `hub chunk depend <dependent> <prerequisite>`. A chunk must follow another
   when it builds on what the other introduces — it calls, extends, or tests code that does not exist yet — or when both
   will massively transform the same files, restructuring or rewriting them so thoroughly that whichever lands second
   faces a conflict no rebase could remediate and must redo its work. Sharing files is not enough: edits to separate
   regions, additive changes, annotation and typing sweeps, renames a rebase can follow, and tests or docs beside a
   rewrite all rebase cleanly, so their lanes run in parallel. The foundation goes first, or else the smaller. A resting
   chunk depends on in-flight work on the same terms. Every GitHub **blocked by** link on a chunk's issue that meets
   this bar becomes an edge, every one that does not is removed from the issue, and every edge added here is written
   back as a blocked-by link ([contributing.md § Issue dependencies](../contributing.md#issue-dependencies)), so the hub
   and the issues agree.
4. **Promote in priority order** with `hub chunk promote <chunk-id>`, most urgent first. Defects that operators or the
   fleet hit come before latent ones, and both come before prose. `blizzard-context:/domain/work/ranking.md` owns how
   promotion places a chunk in `ready`; promote in the intended order. A dependent rests there, blocked, until its
   prerequisites finish. A `(BREAK)` chunk an operator ingested by hand is theirs to promote, never promoted here.
5. **Reorder** only where the new work must jump chunks already `ready`: `hub queue move <chunk-id> <position>`.
6. **Report** each group, edge, and reorder with its reason — for an edge, what the dependent builds on or which files
   both rewrite — each `(BREAK)` issue left uningested, and each `blizzard:manual` issue left uningested, with the fleet
   issues it holds back.

## Don't

- Promote chunks that are one change before grouping them, or chunks that both rewrite the same files before linking
  them.
- Link chunks only because they touch the same files, or hold a mechanical sweep behind a feature that edits files the
  sweep also touches; the later lane rebases.
- Group work that needs separate lanes into a PR too large for one lane to carry.
- Move a `ready` chunk merely to re-express the order already established by promotion.
- Ingest or promote a `(BREAK)` issue, or anything that depends on one, even told to marshal: when a break may land is a
  release's question, and an operator's call.
- Ingest a `blizzard:manual` issue, or a fleet issue blocked by an open one, even told to marshal: the fleet cannot do
  operator work, and the hub would close the issue on merge with it undone.
