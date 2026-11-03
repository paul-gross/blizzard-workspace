# Shipping a major

**Rule.** Ship a major on its published date: open `master` for it once `X`'s final minor is cut, cut its candidates on
`master` when the operator asks, close `master` and cut its line at least seven days before the date, and promote it on
the date or slip the date later. A tag itself is always cut by [release.md](./release.md), and every cut and promotion
in between runs by [release-train.md](./release-train.md).

**Why.** A major gathers its removals on `master` for a bounded window, and the candidate its line is cut with still
bakes on the instance for seven days like any other. The window file's keys are the `blizzard` repo's
`docs/versioning.md` §The hub↔runner skew window's; this procedure does not restate them.

**Scope.** Past 1.0 only, for a major whose date `D` the `blizzard` repo's `docs/versioning.md` §Schedule publishes.
With no date published there, nothing here runs. 1.0 itself opens no window: it is cut by
[release-train.md](./release-train.md) as a minor whose version is `1.0.0`, once the user says `milestone:hardening` is
reached. Run from a provisioned feature environment holding `blizzard` and `blizzard-mock` worktrees, fetched with tags.
Below, `X` is the outgoing major and `X+1` the one shipping.

| When                                           | What happens                                                                 |
| ---------------------------------------------- | ---------------------------------------------------------------------------- |
| Once `X`'s final minor `X.Y` is cut            | Step 1 opens `master`, step 2 folds                                          |
| Each cut the operator asks for while it's open | Step 3 cuts `v(X+1).0.0-rc.K` on `master`                                    |
| A day the operator picks, by `D` − 7 days      | Step 4 closes `master`, cuts `release/(X+1).0`, and tags its candidate there |
| `D`                                            | Step 5 promotes `v(X+1).0.0`, or the date slips                              |

## 1. Open `master`

Once `X.Y`'s candidate is tagged, on a day the operator picks, check: `date -u +%F` is earlier than `D` minus seven
days, leaving step 4 its room; `release/X.Y` and its `vX.Y.0-rc.1` exist; and `origin/master`'s
`src/blizzard/wire/window.toml` has no `opening`. Any of them failing is a stop.

Open one pull request to `master` whose only change is `window.toml`: `opening = "X+1"`, and `floor = "X.Y"`. Merge it
once its `pr` run is green; its `gate / compatibility against supported releases` job admits the raised floor beside
`opening`.

## 2. Fold the store

Straight after, run [store-fold.md](./store-fold.md), and merge its pull request before step 3's first cut.

## 3. Candidates of the major

Each cut the operator asks for while `opening` is set: [release-train.md](./release-train.md) runs step 1 as usual —
`X.Y.0` promotes there once it has baked — and comes here for its cut, once it has found that `master` moved:

1. **The cut commit**, `(X+1).0.0`, against `master`, by [release.md § The cut commit](./release.md#the-cut-commit).
2. **Its `push` run is green**, and its `proven-against` artifact names it and `<mock>`, as
   [release-train.md](./release-train.md#2-cut-the-next-candidate) steps 2.4 and 2.5 read them.
3. **Tag `v(X+1).0.0-rc.K`**, `K` one more than the highest `git tag -l 'v(X+1).0.0-rc.*'` holds, or `1`: by
   [release.md § Tag and watch](./release.md#tag-and-watch), the mock's at `<mock>` first, `blizzard`'s at the cut
   commit last; no branch is created. Then [§ Confirm the cut](./release.md#confirm-the-cut), and offer the instance's
   move as [release-train.md](./release-train.md#2-cut-the-next-candidate) step 2.9 does.

## 4. Cut the major

On a day the operator picks, no later than `D` minus seven days — `date -u +%F` is earlier than `D` minus six days —
whether or not `master` moved:

1. **The cut commit**, `(X+1).0.0`, against `master`, by [release.md § The cut commit](./release.md#the-cut-commit),
   removing `opening` from `window.toml` in the same commit.
2. **Its `push` run, `proven-against`, and `release/(X+1).0` in both repositories**, as
   [release-train.md](./release-train.md#2-cut-the-next-candidate) steps 2.4–2.6 do for `release/X.N`.
3. **Tag `v(X+1).0.0-rc.K`** at both branches' tips, the mock's first, as step 3.3 numbers it, by
   [release.md § Tag and watch](./release.md#tag-and-watch) and [§ Confirm the cut](./release.md#confirm-the-cut). Its
   date starts the major's seven days, not the first candidate step 3 tagged on `master`.

A fix before the date is a further candidate on `release/(X+1).0`
([release-train.md](./release-train.md#a-further-candidate)).

## 5. Promote on the date, or slip

On `D`, [release-train.md](./release-train.md) step 1 promotes the candidate as it promotes a minor, once step 4's
candidate is at least seven days old as its check 1 reads a tag's date.

When a `release-blocker` names `(X+1).0`, the date slips: push no tag, and open a pull request to `master` republishing
a later date — the user picks it — in `docs/versioning.md` §Schedule with the blocker as its reason. The blocker's fix
reaches `release/(X+1).0` as a further candidate, and step 1 promotes it on the new date.

**The day it ships**, one pull request to `master` writes out in `docs/versioning.md` §Support the date `X.Y`'s critical
fixes end — three months from `D` — and publishes the next major's date in §Schedule.

## Don't

- Open `master` before `X`'s final minor is cut, or in more than the one `window.toml` change.
- Close `master` and cut the major's line fewer than seven days before its date.
- Cut a minor while `opening` is set, or leave `opening` on a release branch.
- Promote the major before its date, or set it aside — a blocked major slips.
- Move a published date earlier.
