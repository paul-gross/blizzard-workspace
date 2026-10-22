# Marshalling the backlog

**Rule.** To **marshal** is to turn the resting backlog into ordered, claimable work. When the user says to marshal,
carry every `not_ready` chunk through the steps below, promotion included, save a `(BREAK)` chunk, which is never
promoted here. Anything that mints a chunk — `hub chunk ingest`, `hub garden-proposal accept`, `hub item create` — owes
steps 1–3 at once, unasked. Only promotion waits for the word.

**Why.** Promotion order is the queue order, and a runner claims whatever reaches the head of `ready`. Unless chunks
that collide are grouped or linked before then, two lanes edit the same files in parallel and one of them lands into a
conflict.

**Scope.** An operator session against the instance's hub. Every `blizzard hub` verb needs `$BZ_HUB_URL` and a session
([local-instance.md § The operator CLI needs a session](../local-instance.md#the-operator-cli-needs-a-session)).

1. **Map the ground.** For each `not_ready` chunk, read its work item and locate the code and docs it will change on
   `origin/master`. Map every unfinished chunk too — `ready`, `running`, `delivering` — since a resting chunk can
   collide with work already in flight.
2. **Group** chunks that are one change — the same defect from two angles, or small changes to the same files for the
   same reason: `hub chunk group <survivor> <merged-id>…`. A group is one lane and one PR, so keep it a size one lane
   carries. Every id must be unacquired.
3. **Link** chunks that are separable but would collide — they edit the same files, or one builds on what the other
   introduces: `hub chunk depend <dependent> <prerequisite>`. The foundation goes first, or else the smaller. A resting
   chunk that would collide with in-flight work depends on that in-flight chunk.
4. **Promote in priority order** with `hub chunk promote <chunk-id>`, most urgent first. Defects that operators or the
   fleet hit come before latent ones, and both come before prose. `blizzard-context:/domain/work/ranking.md` owns how
   promotion places a chunk in `ready`; promote in the intended order. A dependent rests there, blocked, until its
   prerequisites finish. A chunk whose work item's title carries `(BREAK)`
   ([contributing.md § Issue titles](../contributing.md#issue-titles)) stays `not_ready`, grouped and linked like any
   other: an operator promotes it by hand, when a release may carry the break.
5. **Reorder** only where the new work must jump chunks already `ready`: `hub queue move <chunk-id> <position>`.
6. **Report** each group, edge, and reorder with its reason — the file or seam the chunks share — and each `(BREAK)`
   chunk left unpromoted.

## Don't

- Promote colliding chunks before grouping or linking them; two lanes would edit the same files at once.
- Group work that needs separate lanes into a PR too large for one lane to carry.
- Move a `ready` chunk merely to re-express the order already established by promotion.
- Promote a `(BREAK)` chunk, even told to marshal: when a break may land is a release's question, and an operator's
  call.
