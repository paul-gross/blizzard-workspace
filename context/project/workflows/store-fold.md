# Folding a store's migration history

**Rule.** A fold replaces both store trees' revisions up to a floor release with one baseline revision each, and lands
as a pull request merged before the train cuts its candidate. It is an attended step, never a fleet chunk's work: the
floor is a fact `blizzard-compat` reports, and a fold deletes released history.

**Why.** Every supported release carries a revision tree the candidate need not keep rebuilding. The contract — when a
fold happens, what the baseline holds, how an older store is turned away — is
`blizzard-product:/plans/release-engineering/release-lines/spec/fold.md`; this procedure restates none of it.

## Steps

1. **Read the floor.** In the `blizzard` worktree, the `oldest` entry of `uv run blizzard-compat releases --json` is the
   floor's tag. Never pick it by hand.
2. **Run the tool.** `uv run python scripts/fold_store.py <floor-tag>` with `gh` access (`GH_TOKEN`; `GH_REPO` outside a
   GitHub checkout). It writes each tree's baseline over the floor revision's file, deletes the absorbed revisions and
   the colocated tests that pin them, and leaves every revision above the floor alone. A tree already folded to that
   floor is left as it is; a tag with no `contracts/store/<tree>.json` is refused, naming both.
3. **Retire every test that pins an absorbed revision.** The default suite fails on the tests that name a deleted
   revision as input — a test that seeds a store at one, downgrades to one, or replays a chain through one. Retire or
   re-point each; a test that proves a kept revision stays.
4. **Regenerate nothing.** The head is unchanged, so `blizzard:store-contract` stays green with `contracts/store/`
   untouched. A diff there means the fold touched a revision above the floor; stop and find it.
5. **Prove it.** Run `blizzard:store-baseline` (`mise run store-baseline`) with `BLIZZARD_STORE_BASELINE_PG_URL` set,
   then `blizzard:gate`. The proof is the only thing that says the baseline builds the history's store; read a failure
   as the baseline's, never loosen the comparison.
6. **Open the pull request** with `blizzard:store-baseline` green — `pr.yml` runs it when `versions/` changes — and the
   rehearsal walking down only as far as the baseline.
7. **Merge before the train cuts its candidate**, so the candidate ships the baseline and the fleet runs it first.
8. **Name the floor** beside the release that ships it in `docs/upgrade.md`.

## Verifying the tool itself

`blizzard:manual-store-fold` (`blizzard-context:/verification/blizzard.md`) drives `scripts/fold_store.py` over a
throwaway clone against a published release. Run it when the tool changes, not on each fold.
