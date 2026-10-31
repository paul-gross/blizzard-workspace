# Running the release train

**Rule.** On the train day, promote the waiting candidate or set it aside, then cut the next minor's candidate if
`master` moved since the last cut — reading every input from the repositories and their runs, and stopping at the first
condition that fails. A tag itself is always cut by [release.md](./release.md).

**Why.** The train is the only road from `master` to a release, and it runs often enough that an agent, and later a
routine, carries it; a step read from memory is a step a routine cannot repeat. The train's published promise is the
`blizzard` repo's `docs/versioning.md` §The train; this procedure does not restate it.

**Scope.** An operator session, or an agent told by name to run it, from a provisioned feature environment holding
`blizzard` and `blizzard-mock` worktrees, never the source checkouts. Creating a `v*` tag — signed, by
[release.md § Tag and watch](./release.md#tag-and-watch) — and a `release/X.Y` branch (step 2.5) each needs the
repository admin role on both repositories; no fleet worker runs any step. A further candidate (below) runs on any day;
the rest runs on the train day alone.

## 0. Read the state

In both worktrees, `git fetch origin --tags`. Then:

- **The day.** `date -u +%A` names the weekday `docs/versioning.md` §The train declares. Any other day is a stop.
- **The line.** `X.Y` is the highest `release/X.Y` on `blizzard`:
  `git ls-remote --heads origin 'release/*' | sed 's|.*refs/heads/release/||' | sort -V | tail -1`.
- **Its newest candidate.** `vX.Y.0-rc.K`, the highest `K`: `git tag -l 'vX.Y.0-rc.*' | sort -V | tail -1`.
- **The window.** `git show origin/master:src/blizzard/wire/window.toml`; an `opening` key means `master` is open for a
  major, and step 2 is [release-major.md](./release-major.md)'s.
- **A held week.** The previous train day was held only when the operator decided to hold it; step 2 notes it.

## 1. Promote, or set aside

The line is **unpromoted** — its newest candidate **waiting** — when no `vX.Y.0` tag exists and `vX.Y.0-rc.K`'s body
does not open with `**Set aside.**`
(`gh release view vX.Y.0-rc.K --repo paul-gross/blizzard --json body -q .body | head -1`; a candidate with no GitHub
Release is not set aside). Otherwise skip to step 2. An unpromoted line is never skipped: its newest candidate is
promoted, set aside, or slipped here, or the whole train halts.

Check, in order:

1. **It was tagged before today.** A candidate is never promoted on the day it was tagged. Its tag's UTC date — the
   tagger date, or the commit date for an older lightweight tag — must be earlier than `date -u +%F`:

   ```bash
   TZ=UTC git for-each-ref refs/tags/vX.Y.0-rc.K \
     --format='%(if)%(taggerdate)%(then)%(taggerdate:short-local)%(else)%(committerdate:short-local)%(end)'
   ```

   Tagged today — a repair — halts the whole train: promote nothing and cut nothing. The line stays current, and the
   next train day promotes it.
2. **Its `release` run is green.**
   `gh run list --repo paul-gross/blizzard --workflow release.yml --branch vX.Y.0-rc.K --limit 1 --json databaseId,status,conclusion`
   reports `completed` and `success`. Anything else halts the whole train: promote nothing and cut nothing until a
   further candidate (below) repairs the line and its own run is green; a later train day then promotes it, by check 1.
3. **No open `release-blocker` names the line, and none names no line.** The first must print `0`:

   ```bash
   gh issue list --repo paul-gross/blizzard --label release-blocker --state open --limit 500 --json body \
     --jq '[.[] | select(.body | test("release_line: *\"X\\.Y\""))] | length'
   gh issue list --repo paul-gross/blizzard --label release-blocker --state open --limit 500 --json number,title,body \
     --jq '.[] | select(.body | test("release_line: *\"[0-9]+\\.[0-9]+\"") | not) | "#\(.number) \(.title)"'
   ```

   The second lists every open blocker that names no line, and must print nothing: each one it lists halts the train
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

**The major's window.** With `opening` set, the cut is the major's: follow the step
[release-major.md](./release-major.md) names for this train day, and stop here.

**Has `master` moved?** The last cut on `master` is its newest commit carrying a `Release-Cut:` trailer:

```bash
last=$(git log origin/master --format='%H%x09%(trailers:key=Release-Cut,valueonly,separator=)' | awk -F'\t' '$2 != "" { print $1; exit }')
git tag --points-at "$last" -l 'v*'   # nothing: the cut landed but was never tagged
git rev-list --count "$last..origin/master"
```

An untagged `$last` is a cut this train already landed: resume at step 3 with it as `<cut>` and its trailer's version as
`X.N.0`. Otherwise `0` ends the train, and a pending set-aside stays pending — the candidate unmarked — until a train
cuts the minor that supersedes it.

**The version** is `X.(Y'+1).0`, where `X.Y'.Z` is `master`'s `pyproject.toml` `[project] version`
(`git show origin/master:pyproject.toml`) — a set-aside minor's number stays spent — or `1.0.0` on the train the user
names as 1.0's ([release-major.md](./release-major.md)). Call it `X.N.0` below.

1. **The fold, when due.** Before 1.0, on the first train day of a calendar month (`date -u +%d` is `07` or less), run
   [store-fold.md](./store-fold.md) and merge its pull request before opening the cut commit; a tree already folded to
   the floor it reads leaves nothing to merge.
2. **The cut commit**, `X.N.0`, against `master`, by [release.md § The cut commit](./release.md#the-cut-commit). For a
   held week, add a `kind: changed` note to `changes/unreleased/` saying the week was held before running its `cut`.
3. **Its `push` run is green.** The landed cut commit `<cut>` (`origin/master`'s commit carrying `Release-Cut: X.N.0`):
   `gh run list --repo paul-gross/blizzard --workflow push.yml --commit <cut> --json databaseId,status,conclusion`, then
   `gh run watch <run-id> --repo paul-gross/blizzard --exit-status`. Red is a stop: fix forward on `master`, then open a
   fresh cut commit for the same `X.N.0` — `pyproject.toml` already names it, so the commit carries the trailer and
   whatever its `cut` moves — and read that commit's run.
4. **The mock that proved it.**

   ```bash
   gh run download <run-id> --repo paul-gross/blizzard --name proven-against --dir <scratch>
   jq -r .blizzard <scratch>/proven-against.json            # must equal <cut>
   jq -r '."blizzard-mock"' <scratch>/proven-against.json   # <mock>
   ```

   A missing artifact, or a `blizzard` sha other than `<cut>`, is a stop.
5. **Create `release/X.N`** in both repositories — `blizzard`'s at `<cut>`, `blizzard-mock`'s at `<mock>` — by pushing
   it. The `release/*` ruleset on each repository lets the repository admin role bypass it, so the operator, an admin,
   creates the branch directly; everyone else changes a release branch only through a pull request. The bypass also
   admits a direct push, force-push, or deletion — the train uses it to create the branch and for nothing else. First,
   `git ls-remote --heads origin release/X.N` in each worktree: nothing means create it; a branch already at that
   repository's `<cut>` or `<mock>` is already created; at any other commit it is a stop, never moved.

   ```bash
   git push origin <cut>:refs/heads/release/X.N    # in the blizzard worktree
   git push origin <mock>:refs/heads/release/X.N   # in the blizzard-mock worktree
   ```

6. **Tag `vX.N.0-rc.1`** by [release.md § Tag and watch](./release.md#tag-and-watch): the mock's at its `release/X.N`
   tip first, `blizzard`'s at `<cut>` last; then [§ Confirm the cut](./release.md#confirm-the-cut).
7. **Finish step 1's set-aside**, if one is pending.
8. **The instance.** Offer the user the move onto `vX.N.0-rc.1` by the
   [runbook](../singleton-instance.md#redeploy--restart-runbook--moving-the-instance-onto-a-release); the candidate's
   week is the instance running it. The move runs on `xps` alone.

## A further candidate

A fix the week finds for the waiting candidate's line — `release/X.Y` with no `vX.Y.0` yet — on any day:

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
   offer the instance's move as step 2.8 does.

It does not restart the week: the next train day promotes whichever candidate is newest, once check 1 admits it.

## Filing a release blocker

A release blocker is an issue on `paul-gross/blizzard` labelled `release-blocker` whose body's opening YAML metadata
block names the line it blocks:

```yaml
release_line: "X.Y"
```

Quote the value, so `0.10` stays `0.10`. A blocker with no `release_line` blocks nothing step 1 can read, so check 3
surfaces it until it names one.

## Don't

- Run the train on any other day, or run it twice in one week; a re-run on the same day resumes it, since each step
  checks what is already done.
- Promote a candidate on the day it was tagged, a red or unfinished candidate, a set-aside one, or one a
  `release-blocker` names; or promote while an open `release-blocker` names no line.
- Cut a new minor while the line is unpromoted and its newest candidate was tagged today, or is red or unfinished — the
  whole train waits; and never skip or set aside a line because a repair candidate made it wait.
- Cut from a `master` commit whose `push` run is not green, or create the mock's branch at the mock's `master` tip
  rather than the commit `proven-against` names.
- Push `blizzard`'s tag before the mock's — the `release` run fails without it.
- Move, delete, or reuse a tag or a release branch; repair forward instead.
