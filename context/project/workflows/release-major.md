# Shipping a major

**Rule.** Ship a major on its published date: open `master` for it four trains before, cut its candidates on `master`,
close `master` and cut its line a train before the date, and promote it on the date or slip the date a week. A tag
itself is always cut by [release.md](./release.md), and every train in between runs by
[release-train.md](./release-train.md).

**Why.** A major gathers its removals on `master` for a bounded window, and its last candidate still runs on the fleet
for a week like any other. The window file's keys are the `blizzard` repo's `docs/versioning.md` §The hub↔runner skew
window's; this procedure does not restate them.

**Scope.** Past 1.0 only, for a major whose date `D` the `blizzard` repo's `docs/versioning.md` §Schedule publishes.
With no date published there, nothing here runs. 1.0 itself opens no window: it is cut by
[release-train.md](./release-train.md) as a minor whose version is `1.0.0`, once the user says `milestone:hardening` is
reached. Run from a provisioned feature environment holding `blizzard` and `blizzard-mock` worktrees, fetched with tags.
Below, `X` is the outgoing major and `X+1` the one shipping.

| Train day | What happens                                                                                  |
| --------- | --------------------------------------------------------------------------------------------- |
| `D` − 4w  | The train as usual, cutting `X`'s final minor `X.Y`; then step 1 opens `master`, step 2 folds |
| `D` − 3w  | The train promotes `X.Y.0`, then cuts `v(X+1).0.0-rc.K` on `master` (step 3)                  |
| `D` − 2w  | The train cuts `v(X+1).0.0-rc.K` on `master` (step 3)                                         |
| `D` − 1w  | Step 4 closes `master`, cuts `release/(X+1).0`, and tags its candidate there                  |
| `D`       | The train promotes `v(X+1).0.0`, or the date slips (step 5)                                   |

## 1. Open `master`

On `D` − 4w, once that train's candidate is tagged, check: `date -u +%F` is `D` minus 28 days; `release/X.Y` and its
`vX.Y.0-rc.1` exist; and `origin/master`'s `src/blizzard/wire/window.toml` has no `opening`. Any of them failing is a
stop.

Open one pull request to `master` whose only change is `window.toml`: `opening = "X+1"`, and `floor = "X.Y"`. Merge it
once its `pr` run is green; its `gate / compatibility against supported releases` job admits the raised floor beside
`opening`.

## 2. Fold the store

Straight after, run [store-fold.md](./store-fold.md), and merge its pull request before `D` − 3w's train cuts.

## 3. Candidates of the major

On `D` − 3w and `D` − 2w, [release-train.md](./release-train.md) runs step 1 as usual and comes here for its cut, once
it has found that `master` moved:

1. **The cut commit**, `(X+1).0.0`, against `master`, by [release.md § The cut commit](./release.md#the-cut-commit).
2. **Its `push` run is green**, and its `proven-against` artifact names it and `<mock>`, as
   [release-train.md](./release-train.md#2-cut-the-next-candidate) steps 2.3 and 2.4 read them.
3. **Tag `v(X+1).0.0-rc.K`**, `K` one more than the highest `git tag -l 'v(X+1).0.0-rc.*'` holds, or `1`: by
   [release.md § Tag and watch](./release.md#tag-and-watch), the mock's at `<mock>` first, `blizzard`'s at the cut
   commit last; no branch is created. Then [§ Confirm the cut](./release.md#confirm-the-cut), and offer the instance's
   move as [release-train.md](./release-train.md#2-cut-the-next-candidate) step 2.8 does.

## 4. Cut the major

On `D` − 1w, whether or not `master` moved:

1. **The cut commit**, `(X+1).0.0`, against `master`, by [release.md § The cut commit](./release.md#the-cut-commit),
   removing `opening` from `window.toml` in the same commit.
2. **Its `push` run, `proven-against`, and `release/(X+1).0` in both repositories**, as
   [release-train.md](./release-train.md#2-cut-the-next-candidate) steps 2.3–2.5 do for `release/X.N`.
3. **Tag `v(X+1).0.0-rc.K`** at both branches' tips, the mock's first, as step 3.3 numbers it, by
   [release.md § Tag and watch](./release.md#tag-and-watch) and [§ Confirm the cut](./release.md#confirm-the-cut).

A fix that week is a further candidate on `release/(X+1).0`
([release-train.md](./release-train.md#a-further-candidate)).

## 5. Promote on the date, or slip

On `D`, [release-train.md](./release-train.md) step 1 promotes the candidate as it promotes a minor.

When a `release-blocker` names `(X+1).0`, the date slips a week: push no tag, and open a pull request to `master`
republishing `D` + 1w in `docs/versioning.md` §Schedule with the blocker as its reason. The blocker's fix reaches
`release/(X+1).0` as a further candidate, and the next train day promotes it.

**The day it ships**, one pull request to `master` writes out in `docs/versioning.md` §Support the date `X.Y`'s critical
fixes end — three months from `D` — and publishes the next major's date in §Schedule.

## Don't

- Open `master` before that week's train has cut `X`'s final minor, or in more than the one `window.toml` change.
- Cut a minor while `opening` is set, or leave `opening` on a release branch.
- Promote the major before its date, or set it aside — a blocked major slips.
- Move a published date earlier.
