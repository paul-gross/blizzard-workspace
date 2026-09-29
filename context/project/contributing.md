# Contributing

## Commit messages

Use Conventional Commits with a scope:

```text
<type>(<scope>): <description>
```

```text
[optional body]
```

```text
Co-Authored-By: Claude <noreply@anthropic.com>
```

Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `perf`, `style`, `ai`. Scope is the **subsystem the change
touches** — `hub`, `runner`, `cli`, `web` in `blizzard`; `architecture`, `standards`, `verification` in
`blizzard-context`; and so on. Reach for the repo name (`blizzard`, `blizzard-context`, `blizzard-mock`,
`blizzard-workspace`, `blizzard-discovery`) only when a change genuinely spans the whole repo and no subsystem fits — a
bare `feat(blizzard)` on a change that lives in one subsystem is the scope done wrong.

The `/wf-commit` skill (from the `winter-workflow` extension) generates commits in this exact format — prefer it over
hand-writing messages.

### Issue references

When a commit completes a GitHub issue, include a `Closes #N` footer on its own line just above the `Co-Authored-By`
trailer. GitHub recognizes the keyword and auto-closes the issue once the commit lands on the default branch.

```text
feat(hub): add chunk ingest endpoint
```

```text
[optional body]
```

```text
Closes #2
```

```text
Co-Authored-By: Claude <noreply@anthropic.com>
```

Use `Closes #N` (or `Fixes #N` / `Resolves #N`) for issues this commit finishes. Use `Refs #N` to cross-link an issue
this commit relates to but doesn't close. Always use the short `#N` form, not the full issue URL — only the short form
triggers GitHub's auto-close and back-link behavior.

A bare `#N` always resolves against **the repo the commit lands in**, not the repo the issue was filed in. When a fix
lands in a different repo than the one tracking it, **scope the reference** with the `owner/repo#N` form — e.g.
`Closes paul-gross/blizzard-context#21` to close it (cross-repo auto-close works given push access to the target repo)
or `Refs paul-gross/blizzard-context#21` to link without closing.

## Checks before pushing

`blizzard-context` owns what a change is held to and how it is proven: its
[standards](../../.winter/ext/context/standards/index.md) rules, and its
[verification matrix](../../.winter/ext/context/verification/blizzard.md) for the per-component commands and the tiers
each change owes. Run the checks for the repo you touched before you push.

CI runs the merge gate on a pull request to `master` and again on push to `master`. A by-hand PR waits on it before
landing; a direct push to `master` does not — there CI reports after the fact, so the local run is the only gate there
is.

## Delivery

Default branch: `master` on every repo.

Work reaches `master` **three** ways (D-104). Which one applies is a fact about who is driving, not about the change:

| Path                       | Who drives                                                                | Who lands it                                                                                                                      |
| -------------------------- | ------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| **By hand**                | an agent or human working in a local feature environment, outside a fleet | the agent, through a PR it opens, watches, and rebase-merges on green                                                             |
| **Fleet, `merge-to-main`** | a runner in this workspace, driving a chunk through its graph             | the hub's `deliver` node lands it — no human step                                                                                 |
| **Fleet, `open-pr`**       | a runner in this workspace, driving a chunk through its graph             | the hub's `deliver` node parks the chunk on an open PR; a **human** resolves it, and the hub completes the chunk from the outcome |

Only the by-hand path is an agent's to drive. Both fleet paths are the same hub-executed `deliver` node in its two
authored modes, and their mechanics — the modes, the parking, the merge detection — belong to
`blizzard-context:/workflows/feature-delivery.md` (`bzh:feature-delivery`) and the corpus decisions it rests on. Read
that before assuming anything about how a chunk lands; do not infer a path from the shape of a merge commit, because
both fleet modes open a PR and their merge commits are indistinguishable.

### The by-hand path

**Land** names this whole sequence: when the user says to land work, carry it from branch to `master` and through
post-delivery. **Push** means only the git push it names — never a PR, a merge, or a landing.

By default, work lands through a pull request the agent drives to completion:

1. **Curate the branch** — rebase onto the latest `origin/master` and squash to one landed unit of work per commit: one
   feature or one fix. A feature plus the follow-up fixes to it that never landed is *one* unit; a genuinely separate
   concern (a test repair, an unrelated bug) stays its own commit.
2. **Open the PR** — push the curated branch to a feature branch and `gh pr create --base master`.
3. **Watch it to completion** — `gh pr checks <pr> --watch`. On a red check, fix it, re-curate, force-push, and watch
   again. A repo that reports no PR checks has nothing to wait on.
4. **Rebase-merge on green** — `gh pr merge <pr> --rebase`, which puts the branch's curated commits on top of `master`
   with no merge commit, so history stays linear. First confirm the branch still sits on the latest `origin/master`; if
   `master` moved, rebase again, force-push, and watch again, so what lands is what CI proved. Not `--squash`: it
   rewrites the commit message from the PR title and collapses deliberately separate commits. Afterward the landed
   commits carry new SHAs, so resync the worktree from `origin/master` rather than pushing the old branch again.
5. **Landing is not the end** — it starts a deploy to the hosted hub on its own, and still owes a local runner redeploy
   by hand. Go to [post-delivery.md](./post-delivery.md) once it lands.

A merge commit (`--merge`) is the exception, not the default: take one only when grouping a multi-commit branch under it
tells a clearer story than landing the commits flat.

**Push directly to `origin/master` only when the user explicitly asks.** Curate the same way first; with no PR, nothing
gates the push but your local checks, and post-delivery is owed just the same.

See [`workspace:/context/worktree-ops.md`](../worktree-ops.md) for the exact git commands per worktree (sync, push,
complete).

## Release publishing

No deploys and no changelog. A push to `master` publishes two dogfood artifacts: a dev-build wheel as a workflow
artifact, and a multi-arch hub image to GHCR on the `edge` / `sha-<full-git-sha>` channel. A `v*` tag *is* the release:
it runs the full suite, publishes the release image tags, and attaches the wheel to a GitHub Release — there is no
package-index publish. **A push to `master` does deploy something.** The `edge` image it publishes is the channel this
workspace's own hosted hub follows, so that hub picks the new build up unattended — no human step between landing and
running ([local-instance.md](./local-instance.md)). Neither a `v*` tag nor the wheel deploys anywhere. The **runner**
half is still redeployed by hand, which is what [post-delivery.md](./post-delivery.md) requires of you after every
landing. The tag fan-out and what `latest` tracks are the `blizzard` repo's `docs/versioning.md`. See
`blizzard-context:/workflows/release.md` for the sequence.
