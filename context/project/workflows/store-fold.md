# Folding a store's migration history

**Rule.** A fold replaces both store trees' revisions up to a floor release with one baseline revision each, and lands
as a pull request merged before the train cuts its candidate. It is an attended step, never a fleet chunk's work: the
floor is a fact `blizzard-compat` reports, and a fold deletes released history.

**Why.** Every supported release carries a revision tree the candidate need not keep rebuilding.

## Steps

1. **Read the floor.** In the `blizzard` worktree, the `oldest` entry of `uv run blizzard-compat releases --json` is the
   floor's tag. Never pick it by hand.
2. **Run the tool.** `uv run python scripts/fold_store.py <floor-tag>` with `gh` access (`GH_TOKEN`; `GH_REPO` outside a
   GitHub checkout). It writes each tree's baseline over the floor revision's file, deletes the absorbed revisions and
   the colocated tests that pin them, and leaves every revision above the floor alone. A tree already folded to that
   floor is left as it is; a tag with no `contracts/store/<tree>.json` is refused, naming both.
3. **Clear every name of an absorbed revision.** Grep `src/`, `tests/` (the service tier and its Playwright specs
   included), `scripts/`, and `docs/` for each deleted revision's id; the default suite alone misses the service tier.
   - In `src/`, a constant naming an absorbed revision as a threshold for `reaches()` or a downgrade target silently
     changes behavior once the id is gone. Re-point it to the tree's baseline, which every store this build accepts has
     reached.
   - A test that seeds a store at one, downgrades to one, or replays a chain through one: re-point it to the baseline or
     a kept revision, seeding the baseline's required columns; retire it when the state it proves can no longer exist
     above the floor. A test that proves a kept revision stays.
   - Delete the support modules and helpers left with no caller once those tests go.
   - Stage the deletions (`git add -A`) before the gate: its pointer check walks `git ls-files`.
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
