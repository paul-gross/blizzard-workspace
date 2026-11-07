# Running the release train

**Rule.** The train has no fixed day. A minor's candidate is cut when the operator chooses, and promoted no sooner than
seven days after that cut — reading every input from the repositories and their runs, and stopping at the first
condition that fails. A tag itself is always cut by [release.md](./release.md).

**Why.** The train is the only road from `master` to a release. The cut is the freeze: everything on `master` at that
commit ships in the minor, so the operator picks the moment the minor's content is done, and the week after it is the
candidate baking on the instance. A step read from memory is a step a routine cannot repeat. The train's published
promise is the `blizzard` repo's `docs/versioning.md` §The train; this procedure does not restate it.

**Scope.** An operator session, or an agent told by name to run it, from a provisioned feature environment holding
`blizzard` and `blizzard-mock` worktrees, never the source checkouts. Creating a `v*` tag — signed, by
[release.md § Tag and watch](./release.md#tag-and-watch) — and a `release/X.Y` branch (step 2.6) each needs the
repository admin role on both repositories; no fleet worker runs any step. Promote (step 1) and a further candidate
(below) run on any day; a cut (step 2) runs when the operator asks for one.

## 0. Read the state

In both worktrees, `git fetch origin --tags`. Then:

- **The line.** `X.Y` is the highest `release/X.Y` on `blizzard`:
  `git ls-remote --heads origin 'release/*' | sed 's|.*refs/heads/release/||' | sort -V | tail -1`.
- **Its candidates.** `vX.Y.0-rc.1`, the cut's tag, and `vX.Y.0-rc.K`, the newest — the highest `K`:
  `git tag -l 'vX.Y.0-rc.*' | sort -V | tail -1`.
- **The window.** `git show origin/master:src/blizzard/wire/window.toml`; an `opening` key means `master` is open for a
  major, and step 2 is [release-major.md](./release-major.md)'s.

## 1. Promote, or set aside

The line is **unpromoted** — its newest candidate **waiting** — when no `vX.Y.0` tag exists and `vX.Y.0-rc.K`'s body
does not open with `**Set aside.**`
(`gh release view vX.Y.0-rc.K --repo paul-gross/blizzard --json body -q .body | head -1`; a candidate with no GitHub
Release is not set aside). Otherwise there is nothing to promote. A cut (step 2) runs this step first: an unpromoted
line is never skipped, but promoted, set aside, or slipped here, or it waits and the cut waits with it.

Check, in order:

1. **It has baked.** The cut's tag, `vX.Y.0-rc.1`, is at least seven days old, and the newest candidate was tagged
   before today. A tag's UTC date is its tagger date, or the commit date for an older lightweight tag:

   ```bash
   tagged() { TZ=UTC git for-each-ref "refs/tags/$1" \
     --format='%(if)%(taggerdate)%(then)%(taggerdate:short-local)%(else)%(committerdate:short-local)%(end)'; }
   test "$(tagged vX.Y.0-rc.1)" '<' "$(date -u -d '-6 days' +%F)" && test "$(tagged vX.Y.0-rc.K)" '<' "$(date -u +%F)"
   ```

   Not yet baked is a wait: promote nothing, and a cut waits too. A further candidate does not restart the seven days;
   tagged today — a repair — it waits until tomorrow.
2. **Its `release` run is green.**
   `gh run list --repo paul-gross/blizzard --workflow release.yml --branch vX.Y.0-rc.K --limit 1 --json databaseId,status,conclusion`
   reports `completed` and `success`. Anything else is a wait: promote nothing and cut nothing until a further candidate
   (below) repairs the line and its own run is green.
3. **No open `release-blocker` names the line, and none names no line.** The first must print `0`:

   ```bash
   gh issue list --repo paul-gross/blizzard --label release-blocker --state open --limit 500 --json body \
     --jq '[.[] | select(.body | test("release_line: *\"X\\.Y\""))] | length'
   gh issue list --repo paul-gross/blizzard --label release-blocker --state open --limit 500 --json number,title,body \
     --jq '.[] | select(.body | test("release_line: *\"[0-9]+\\.[0-9]+\"") | not) | "#\(.number) \(.title)"'
   ```

   The second lists every open blocker that names no line, and must print nothing: each one it lists stops the train
   until it is resolved — given its `release_line` by [§ Filing a release blocker](#filing-a-release-blocker), or its
   label removed — and the check runs again; it is never ignored. When the first does not print `0`, a major's candidate
   (`Y` is `0`, `X ≥ 1`) slips by [release-major.md](./release-major.md#5-promote-on-the-date-or-slip); any other
   candidate is set aside below.

**Promote.** By [release.md § Promoting a candidate](./release.md#promoting-a-candidate): `vX.Y.0` in `blizzard-mock` at
the commit its `vX.Y.0-rc.K` peels to, then in `blizzard` at the commit its `vX.Y.0-rc.K` peels to; then
[§ Confirm the cut](./release.md#confirm-the-cut). The run's `proof` job must report the proof borrowed; a refusal there
is a red run ([§ A red run](./release.md#a-red-run)).

**Set aside.** Push no tag. Once step 2 has run, prepend to the candidate's GitHub Release body a first line reading
`**Set aside.**`, saying its minor has no stable release and naming `X.N`, step 2's version, as the minor that
supersedes it, keeping the rest of the body as published:

```bash
{ printf '%s\n\n' "**Set aside.** ..."; gh release view vX.Y.0-rc.K --repo paul-gross/blizzard --json body -q .body; } > <scratch>/body.md
gh release edit vX.Y.0-rc.K --repo paul-gross/blizzard --notes-file <scratch>/body.md
```

The branch stays and is never patched; the blocker's fix rides the next candidate from `master`.

## 2. Cut the next candidate

Only when the operator asks for a cut, after step 1 has promoted, set aside, or found nothing waiting.

**The major's window.** With `opening` set, the cut is the major's: follow the step
[release-major.md](./release-major.md) names, and stop here.

**Has `master` moved?** The last cut on `master` is its newest commit carrying a `Release-Cut:` trailer:

```bash
last=$(git log origin/master --format='%H%x09%(trailers:key=Release-Cut,valueonly,separator=)' | awk -F'\t' '$2 != "" { print $1; exit }')
git tag --points-at "$last" -l 'v*'   # nothing: the cut landed but was never tagged
git rev-list --count "$last..origin/master"
```

An untagged `$last` is a cut already landed: resume at step 4 with it as `<cut>` and its trailer's version as `X.N.0`.
Otherwise `0` leaves nothing to cut, and a pending set-aside stays pending — the candidate unmarked — until a cut of the
minor that supersedes it.

**The version** is `X.(Y'+1).0`, where `X.Y'.Z` is `master`'s `pyproject.toml` `[project] version`
(`git show origin/master:pyproject.toml`) — a set-aside minor's number stays spent — or `1.0.0` on the cut the user
names as 1.0's ([release-major.md](./release-major.md)). Call it `X.N.0` below.

1. **The fold, when due.** Before 1.0, on the first cut of a calendar month, run [store-fold.md](./store-fold.md) and
   merge its pull request before opening the cut commit; a tree already folded to the floor it reads leaves nothing to
   merge.
2. **The line's promises.** Every deprecation due in `X.N` has its removal on `master`, or is postponed, before the cut
   commit opens. `uv run blizzard-change-notes guide X.N` lists each due one with no retiring removal; each listed one
   either lands its `(BREAK X.N)` issue ([§ Breaking issues](#breaking-issues)) or gains a `kind: changed` note that
   `postpones` it to a later line, its issue retitled to match. An unreleased deprecation whose `removes-in` is `X.N` or
   earlier would ship and retire in one line: edit its `removes-in` instead. Then dry-run the cut —
   `uv run blizzard-change-notes cut X.N.0` in a throwaway worktree of `origin/master` — and land a note for any compat
   finding it says no breaking note names, judged by `blizzard-context:/workflows/judging-a-break.md`.
3. **The cut commit**, `X.N.0`, against `master`, by [release.md § The cut commit](./release.md#the-cut-commit).
4. **Its `push` run is green.** The landed cut commit `<cut>` (`origin/master`'s commit carrying `Release-Cut: X.N.0`):
   `gh run list --repo paul-gross/blizzard --workflow push.yml --commit <cut> --json databaseId,status,conclusion`, then
   `gh run watch <run-id> --repo paul-gross/blizzard --exit-status`. Red is a stop: fix forward on `master`, then open a
   fresh cut commit for the same `X.N.0` — `pyproject.toml` already names it, so the commit carries the trailer and
   whatever its `cut` moves — and read that commit's run.
5. **The mock that proved it.**

   ```bash
   gh run download <run-id> --repo paul-gross/blizzard --name proven-against --dir <scratch>
   jq -r .blizzard <scratch>/proven-against.json            # must equal <cut>
   jq -r '."blizzard-mock"' <scratch>/proven-against.json   # <mock>
   ```

   A missing artifact, or a `blizzard` sha other than `<cut>`, is a stop.
6. **Create `release/X.N`** in both repositories — `blizzard`'s at `<cut>`, `blizzard-mock`'s at `<mock>` — by pushing
   it. The `release/*` ruleset on each repository lets the repository admin role bypass it, so the operator, an admin,
   creates the branch directly; everyone else changes a release branch only through a pull request. The bypass also
   admits a direct push, force-push, or deletion — the train uses it to create the branch and for nothing else. First,
   `git ls-remote --heads origin release/X.N` in each worktree: nothing means create it; a branch already at that
   repository's `<cut>` or `<mock>` is already created; at any other commit it is a stop, never moved.

   ```bash
   git push origin <cut>:refs/heads/release/X.N    # in the blizzard worktree
   git push origin <mock>:refs/heads/release/X.N   # in the blizzard-mock worktree
   ```

7. **Tag `vX.N.0-rc.1`** by [release.md § Tag and watch](./release.md#tag-and-watch): the mock's at its `release/X.N`
   tip first, `blizzard`'s at `<cut>` last; then [§ Confirm the cut](./release.md#confirm-the-cut). Its date starts the
   seven days step 1 counts.
8. **Finish step 1's set-aside**, if one is pending.
9. **The instance.** Offer the user the move onto `vX.N.0-rc.1` by the
   [runbook](../singleton-instance.md#redeploy--restart-runbook--moving-the-instance-onto-a-release); the bake is the
   instance running the candidate. The move runs on `xps` alone.

## A further candidate

A fix the bake finds for the waiting candidate's line — `release/X.Y` with no `vX.Y.0` yet — on any day:

1. Carry each fix onto the line by [release-patch.md § 3. Carry the fix](./release-patch.md#3-carry-the-fix) and
   [§ 4. Prove and merge it](./release-patch.md#4-prove-and-merge-it), in every repository it touched.
2. Open the cut commit against `release/X.Y` by [release.md § The cut commit](./release.md#the-cut-commit), its version
   unchanged: `pyproject.toml` stays `X.Y.0` and the trailer is `Release-Cut: X.Y.0`. When neither cut step moves
   anything, it is an empty commit (`git commit --allow-empty`), and GitHub's rebase-merge drops an empty commit while
   still marking the pull request merged. Land it instead by pushing the proven commit itself through the admin bypass,
   once its pull request run is green and its parent is still the branch tip:
   `test "$(git rev-parse <sha>^)" = "$(git rev-parse origin/release/X.Y)" && git push origin "<full-sha>:refs/heads/release/X.Y"`.
   The landed commit is then the one the run proved, with nothing to compare.
3. Tag `vX.Y.0-rc.(K+1)` by [release.md § Tag and watch](./release.md#tag-and-watch): the mock's at its `release/X.Y`
   tip first, `blizzard`'s at the landed cut commit last; then [§ Confirm the cut](./release.md#confirm-the-cut), and
   offer the instance's move as step 2.9 does.

It does not restart the seven days: step 1 promotes whichever candidate is newest once check 1 admits it.

## Breaking issues

The issue that removes deprecated surface is titled `(BREAK X.Y) <summary>` on `paul-gross/blizzard`, where `X.Y` is the
line the removal lands in — the `removes-in` of every deprecation it retires. One issue may retire several deprecations
due on the same line. A postponement retitles the issue to the new line in the same change; an issue whose surface no
note has deprecated yet has no line, and keeps a bare `(BREAK)` until one does.

## Filing a release blocker

A release blocker is an issue on `paul-gross/blizzard` labelled `release-blocker` whose body's opening YAML metadata
block names the line it blocks:

```yaml
release_line: "X.Y"
```

Quote the value, so `0.10` stays `0.10`. A blocker with no `release_line` blocks nothing step 1 can read, so check 3
surfaces it until it names one.

## Don't

- Promote a candidate fewer than seven days after its line's `rc.1`, on the day its newest candidate was tagged, a red
  or unfinished candidate, a set-aside one, or one a `release-blocker` names; or promote while an open `release-blocker`
  names no line.
- Cut a new minor while the line is unpromoted and not yet promotable — the cut waits; and never skip or set aside a
  line because it has not baked or a repair candidate made it wait.
- Open a cut commit while `guide` lists a promise due on its line with neither a landed removal nor a postponement.
- Cut from a `master` commit whose `push` run is not green, or create the mock's branch at the mock's `master` tip
  rather than the commit `proven-against` names.
- Push `blizzard`'s tag before the mock's — the `release` run fails without it.
- Move, delete, or reuse a tag or a release branch; repair forward instead.
