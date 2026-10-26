# Patching a release line

**Rule.** Patch a line only for a fix that cannot wait for the train, and only with that fix: carried from `master` by
cherry-pick, or written on the line alone when `master` no longer has the code, proven by its pull request, and tagged
`vX.Y.P` at a cut commit on `release/X.Y` in both repositories. A tag itself is always cut by
[release.md](./release.md).

**Why.** An operator who pinned a line pinned a promise that nothing but the fix moves. The contract — which lines are
patched, what a patch may carry, how a fix is carried back and proven — is
`blizzard-product:/plans/release-engineering/release-lines/spec/patches.md`, published as the `blizzard` repo's
`docs/versioning.md` §Support and held by `blizzard-context:/standards/patch-scope.md` (`bzh:patch-scope`); this
procedure restates none of it.

**Scope.** An operation a person asks for, never a scheduled one, from a provisioned feature environment holding
`blizzard` and `blizzard-mock` worktrees, never the source checkouts; creating a `v*` tag — signed, by
[release.md § Tag and watch](./release.md#tag-and-watch) — needs the repository admin role on both repositories. A fix
to a candidate not yet promoted is a further candidate ([release-train.md](./release-train.md#a-further-candidate)), not
a patch. In both worktrees, `git fetch origin --tags` first.

## 1. Decide it cannot wait

The fix must be one of the three classes `docs/versioning.md` §Support names as what a patch is for. Any other fix is a
stop: it rides the next train. Then hold the fix's diff to `bzh:patch-scope`, by that rule's **Detect**; a fix that
breaks it is a stop.

## 2. Find the lines

1. **The patched lines**, from `docs/versioning.md` §Support's table. Before 1.0 that is one line, the newest stable
   minor's — the `X.Y` of `git tag -l 'v*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1`. Past 1.0, an old
   major's final line is patched only while §Support's closing date for it is in the future.
2. **Which of them carry the bug.** Name the commit that introduced it, `<bug>`; a line carries it when
   `git merge-base --is-ancestor <bug> origin/release/X.Y` succeeds. A line that does not carry it gets nothing.
3. **The waiting candidate.** When a `release/X.Y` with no `vX.Y.0` yet also carries `<bug>`, the fix goes to it as a
   further candidate too ([release-train.md](./release-train.md#a-further-candidate)).

Steps 3–7 run once per line, `X.Y` below.

## 3. Carry the fix

In each repository the fix touched — `blizzard-mock` first, so `blizzard`'s pull request proves against it:

- **From `master`.** Every fix commit is on `master` (`git merge-base --is-ancestor <sha> origin/master` succeeds for
  each); one that is not is a stop — land it on `master` first. Then:

  ```bash
  git switch -c patch/X.Y/<slug> origin/release/X.Y
  git cherry-pick -x <sha>...        # every commit of the fix, in master's order
  ```

  Resolve a conflict by adapting the fix to the line, never by bringing in what the line lacks. The cherry-picked commit
  carries the fix's change note from `changes/unreleased/`.
- **Line-only.** Where `master` has removed the code the bug is in, write the fix on `patch/X.Y/<slug>` alone, with its
  test and a change note in `changes/unreleased/`, committed with a trailer saying why it has no `master` counterpart:
  `git commit --trailer 'Line-only: <why>'`.

Use the same `<slug>` in both repositories. `blizzard`'s pull request checks its provenance itself; the mock's `pr.yml`
does not, so in the `blizzard-mock` worktree run `blizzard`'s check over the branch, which must exit `0`:

```bash
<blizzard-worktree>/scripts/check-line-provenance.sh origin/release/X.Y HEAD
```

## 4. Prove and merge it

1. Push `patch/X.Y/<slug>` and open its pull request into `release/X.Y`, mock first:
   `gh pr create --repo paul-gross/<repo> --base release/X.Y --head patch/X.Y/<slug>`.
2. The mock's pull request merges once its `pr` run is green.
3. `blizzard`'s pull request merges once its `pr` run is green on every job, `line provenance` included, and a review
   holds it to `bzh:patch-scope`. Its upper tiers prove against the mock's `patch/X.Y/<slug>`, so the mock's must be
   pushed before this run starts.

Rebase-merge both, so each `(cherry picked from commit …)` line and `Line-only:` trailer lands as written. Once every
fix for the patch is merged, the mock's `release/X.Y` holds every mock change it needs.

## 5. Cut the patch

`P` is one more than the highest patch on the line (`git tag -l 'vX.Y.*'`, `vX.Y.0` counting as `0`). Open the cut
commit `X.Y.P` against `release/X.Y` by [release.md § The cut commit](./release.md#the-cut-commit), from a branch whose
name the mock does not carry, so its upper tiers prove against the mock's `release/X.Y`.

## 6. Tag and confirm

Tag `vX.Y.P` by [release.md § Tag and watch](./release.md#tag-and-watch): the mock's at its `release/X.Y` tip first,
`blizzard`'s at the landed cut commit last. Its `release` run is the full suite. Then
[§ Confirm the cut](./release.md#confirm-the-cut); for a line below the highest stable version, also confirm `latest`
did not move — `vX.Y.P` is not the release marked latest
(`gh release list --repo paul-gross/blizzard --json tagName,isLatest`).

## 7. Move the instance

When the instance runs a release on line `X.Y` — the **Release** row of
[singleton-instance.md](../singleton-instance.md#deployed-surface) — offer the user the move onto `vX.Y.P` by the
[runbook](../singleton-instance.md#redeploy--restart-runbook--moving-the-instance-onto-a-release), the instance's
binding of [release-deploy.md](./release-deploy.md). It runs on `xps` alone.

## Don't

- Patch a fix that can wait, a line `docs/versioning.md` §Support does not list, or a set-aside minor.
- Cherry-pick a commit that is not on `master`, drop `-x`, or carry anything beyond the fix, its tests, and its note.
- Push to `release/X.Y` other than through a pull request — the `release/*` ruleset's admin bypass is for creating a
  line's branch ([release-train.md](./release-train.md#2-cut-the-next-candidate)) alone — or open `blizzard`'s before
  the mock's branch exists.
- Tag a patch whose cut pull request's run is not green, or push `blizzard`'s tag before the mock's.
