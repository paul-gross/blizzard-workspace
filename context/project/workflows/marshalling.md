# Our marshalling policy

Use the [shared marshalling workflow](blizzard-usage:/context/workflows/marshalling.md) with
[our fleet binding](../blizzard-binding.md). This page owns only Blizzard-development policy; the shared workflow owns
the procedure and the grouping and prerequisite criteria.

## Scope and approval

When the user says to marshal, carry every `not_ready` chunk in our project through the shared workflow, promotion
included, subject to the exclusions below. Read the affected repositories at `origin/master` when mapping the work.

Anything that mints a chunk — `hub chunk ingest`, `hub garden-proposal accept`, or `hub item create` — immediately owes
the shared workflow's mapping, grouping, and dependency assessment, unasked. Only promotion waits for the word. An
enclosing operator workflow's explicit draft-only or approval checkpoint takes precedence for its chunks; see
[operator workflows](./index.md).

## Exclusions

**Breaking work waits on GitHub.** An issue whose title carries `(BREAK)`
([contributing.md § Issue titles](../contributing.md#issue-titles)) is never ingested while marshalling or minting work,
and neither is anything that depends on one: ingesting a chain stops short of its first `(BREAK)`. It stays on GitHub
until the operator decides a release may carry the break and ingests it by hand. Such a chunk remains the operator's to
promote, not this pass's.

**Manual work is never ingested.** A `blizzard:manual` issue
([contributing.md § Fleet-fulfillable issues](../contributing.md#fleet-fulfillable-issues)) is the human operator's,
never ingested while marshalling or minting work. A fleet issue blocked by an open manual issue stays uningested until
the manual issue closes. The label is our convention; Blizzard does not interpret it. Include excluded issues and the
work they hold back in the marshalling report.

## Tracker dependencies

Apply the shared workflow's prerequisite criteria to GitHub's **blocked by** links. Every qualifying link becomes a hub
edge; remove links that do not meet that bar, recording the reason. Write every added hub edge back as a GitHub
blocked-by link so the two agree. [Contributing](../contributing.md#issue-dependencies) owns the GitHub link mechanics.

## Priority

Promote most urgent first: defects operators or the fleet hit come before latent defects, and both come before prose.
Keep any more specific grouping or promotion approval required by the operator pass that minted the work.
