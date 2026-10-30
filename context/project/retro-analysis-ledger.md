# Retro-analysis ledger

The pass-to-pass memory for `/ws-retro-analysis`. Passes are append-only and read newest-last.

See `workspace:/skills/retro-analysis/SKILL.md` for the pass procedure and the exact section format each pass appends.

## Passes

## Pass 1 — 2026-10-16T00:00:00Z to 2026-10-30T12:00:00Z (run 2026-10-30)

**Chunks covered:** 320 closed chunks in range (290 with retrospectives; 288 landed). $2,517 total spend; 226 delivery
bounces, 62 in the first week and 164 in the second.

### Findings dispatched

| Cluster                                                                                                                                   | Chunks           | Owner                                    | Action                                      | Status   |
| ----------------------------------------------------------------------------------------------------------------------------------------- | ---------------- | ---------------------------------------- | ------------------------------------------- | -------- |
| Retrospective re-verifies the landing against GitHub, trusting a stale `delivery-findings` snapshot; base gate still pending when it runs | ~175             | `blizzard#1682`                          | issue filed                                 | approved |
| Per-chunk mutation testing measures nothing: overruns, baseline failures, not applicable; one survivor-driven test in 182 chunks          | ~180             | `blizzard#1683`                          | issue filed (completed experiment)          | approved |
| Prose ratchet red on `master` and outside CI; baseline conflicts on rebase                                                                | ~80              | `blizzard#1688`                          | issue filed                                 | approved |
| Agents guess `artifact read`/`show` for `get`                                                                                             | ~30              | `blizzard#1689`                          | issue filed (+ `bzh:` rule)                 | approved |
| Full gate cannot finish in a node; build hands off without cheap lint-class checks                                                        | ~75              | `blizzard#1690`                          | issue filed                                 | approved |
| Headless workers cannot await background subagents                                                                                        | ~45              | `blizzard#1684`                          | issue filed                                 | approved |
| `finding get` refuses findings of folded work items                                                                                       | ~8               | `blizzard#1685`                          | issue filed                                 | approved |
| zsh traps in worker Bash (SHELL stripped; Claude Code prefers zsh)                                                                        | ~27              | `blizzard#1686`                          | issue filed                                 | approved |
| `review-finding-delta` first submitted as prose; no submit-time validation                                                                | 26               | `blizzard#1687`                          | issue filed                                 | approved |
| Issues closed with acceptance criteria the fleet cannot satisfy                                                                           | 5 (+~17 PR-body) | `blizzard#1691`, `blizzard-workspace#11` | issues filed                                | approved |
| "Nothing to land" recorded as `landed` with the base tip as `merged/`                                                                     | 2                | `blizzard#1692`                          | issue filed                                 | approved |
| Build pushes from a worktree whose upstream is the base branch                                                                            | 1                | `blizzard#1693`, `blizzard#1696`         | issues filed                                | approved |
| No branch protection; no fast `hotfix/` route                                                                                             | —                | `blizzard#1694` (`blizzard:manual`)      | issue filed                                 | approved |
| Review cites project rules almost never (9 citations vs ~1,150 in plans)                                                                  | —                | `blizzard#1695`                          | issue filed                                 | approved |
| Verifier tracing runs leak spans into the fleet collector                                                                                 | 1                | `winter-context#22`                      | issue filed                                 | approved |
| Deliver poll timeout fires at 30 min                                                                                                      | 54 bounces       | —                                        | already fixed (10800s, `0e0337a19`, Oct 24) | declined |
| Land reads only the first page of check runs                                                                                              | 1                | —                                        | already fixed (`86ac7c5ad`, Oct 24–25)      | declined |
| Mutation tree leaks the host's hub token into tests                                                                                       | 2                | —                                        | already fixed (`ae17f652b`, Oct 17)         | declined |
| Resolve cannot re-run a flaky CI job                                                                                                      | many             | —                                        | in progress elsewhere                       | declined |
| Deferred should-fix findings ship with no owner (relay across review rounds)                                                              | ~31              | —                                        | cross-round rollup deferred                 | declined |

### Reconciliation (prior pass's findings)

None — first pass.

### `bzh:` citation counts

| Rule                              | Citations this range |
| --------------------------------- | -------------------- |
| `bzh:comment-locality`            | 634                  |
| `bzh:comment-encapsulation`       | 572                  |
| `bzh:domain-orchestration-split`  | 191                  |
| `bzh:one-prose-home`              | 140                  |
| `bzh:shared-kernel`               | 137                  |
| `bzh:dependency-injection`        | 125                  |
| `bzh:help-states-effect`          | 110                  |
| `bzh:domain-package-layers`       | 78                   |
| `bzh:data-roles`                  | 78                   |
| `bzh:store-exclusive-write`       | 66                   |
| `bzh:service-waits-on-the-system` | 63                   |
| `bzh:page-bounded-read`           | 62                   |
| `bzh:frontend-wire-conformist`    | 62                   |
| `bzh:domain-core`                 | 60                   |
| `bzh:released-surface-additive`   | 58                   |
| `bzh:service-clients-only`        | 56                   |
| `bzh:frontend-kit-floor`          | 55                   |
| `bzh:bulk-reconstitution`         | 54                   |
| `bzh:narrow-seams`                | 53                   |
| `bzh:domain-takes-objects`        | 50                   |
| `bzh:service-public-surfaces`     | 50                   |
| `bzh:sql-portable`                | 47                   |
| `bzh:wire-change-extends-mock`    | 43                   |
| `bzh:service-fixtures-only`       | 43                   |
| `bzh:frontend-formatters`         | 42                   |

153 distinct ids cited (latest epoch per node and asset, all artifact kinds); top 25 shown. 62 of the 198 rules declared
by heading id went uncited — mostly verification-matrix, e2e and worker-node procedure ids, cited by method name
instead. Reviewed, not pruned.
