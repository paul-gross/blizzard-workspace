# Contributing

## Commit messages

Use Conventional Commits with a scope:

    <type>(<scope>): <description>

    [optional body]

    Co-Authored-By: Claude <noreply@anthropic.com>

Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `perf`, `style`, `ai`.
Scope is the **subsystem the change touches** — `hub`, `runner`, `cli`, `web` in `blizzard`; `architecture`, `standards`, `verification` in `blizzard-harness`; and so on.
Reach for the repo name (`blizzard`, `blizzard-harness`, `blizzard-mock`, `blizzard-workspace`, `blizzard-discovery`) only when a change genuinely spans the whole repo and no subsystem fits — a bare `feat(blizzard)` on a change that lives in one subsystem is the scope done wrong.

The `/wf-commit` skill (from the `winter-workflow` extension) generates commits in this exact format — prefer it over hand-writing messages.

### Issue references

When a commit completes a GitHub issue, include a `Closes #N` footer on its own line just above the `Co-Authored-By` trailer.
GitHub recognizes the keyword and auto-closes the issue once the commit lands on the default branch.

    feat(hub): add chunk ingest endpoint

    [optional body]

    Closes #2

    Co-Authored-By: Claude <noreply@anthropic.com>

Use `Closes #N` (or `Fixes #N` / `Resolves #N`) for issues this commit finishes.
Use `Refs #N` to cross-link an issue this commit relates to but doesn't close.
Always use the short `#N` form, not the full issue URL — only the short form triggers GitHub's auto-close and back-link behavior.

A bare `#N` always resolves against **the repo the commit lands in**, not the repo the issue was filed in.
When a fix lands in a different repo than the one tracking it, **scope the reference** with the `owner/repo#N` form — e.g. `Closes paul-gross/blizzard-harness#21` to close it (cross-repo auto-close works given push access to the target repo) or `Refs paul-gross/blizzard-harness#21` to link without closing.

## Checks before pushing

`blizzard-harness` owns what a change is held to and how it is proven: its [standards](../../.winter/ext/harness/standards/index.md) rules, and its [verification matrix](../../.winter/ext/harness/verification/blizzard.md) for the per-component commands and the tiers each change owes.
Run the checks for the repo you touched before you push.

**Assume nothing blocks a bad push.** CI runs the merge gate on a pull request to `master` and again on push to `master`, but of the three delivery paths only the fleet's `open-pr` mode leaves a PR standing long enough for a check to gate anything — the other two put commits on `master` without waiting on one.
There, CI reports after the fact, so the local run is the only gate there is.

## Delivery

Default branch: `master` on every repo.

Work reaches `master` **three** ways (D-104). Which one applies is a fact about who is driving, not about the change:

| Path | Who drives | Who lands it |
|------|-----------|--------------|
| **By hand** | an agent or human working in a local feature environment, outside a fleet | the agent pushes to `origin/master` itself |
| **Fleet, `merge-to-main`** | a runner in this workspace, driving a chunk through its graph | the hub's `deliver` node lands it — no human step |
| **Fleet, `open-pr`** | a runner in this workspace, driving a chunk through its graph | the hub's `deliver` node parks the chunk on an open PR; a **human** resolves it, and the hub completes the chunk from the outcome |

Only the by-hand path is an agent's to drive.
Both fleet paths are the same hub-executed `deliver` node in its two authored modes, and their mechanics — the modes, the parking, the merge detection — belong to `blizzard-harness:/workflows/feature-delivery.md` (`bzh:feature-delivery`) and the corpus decisions it rests on.
Read that before assuming anything about how a chunk lands; do not infer a path from the shape of a merge commit, because both fleet modes open a PR and their merge commits are indistinguishable.

### The by-hand path

These rules are its own:

- **No PR, no feature branch** — push completed work directly to `origin/master`.
- **Rebase onto the latest `origin/master` first**, so history stays linear and carries no merge commits.
- **One landed unit of work per commit** — one feature or one fix. A feature plus the follow-up fixes to it that never landed is *one* unit: squash it. Keep a genuinely separate concern (a test repair, an unrelated bug) as its own commit.

See [`workspace:/context/worktree-ops.md`](../worktree-ops.md) for the exact git commands per worktree (sync, push, complete).

## Post-delivery

No deploys and no changelog.
A push to `master` publishes a dev-build wheel as a workflow artifact, for fleet dogfooding.
A `v*` tag *is* the release: it runs the full suite and attaches the wheel to a GitHub Release — there is no package-index publish.
See `blizzard-harness:/workflows/release.md` for the sequence.
