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

## Pass 2 — 2026-10-30T12:00:00Z to 2026-11-25T23:59:59Z (run 2026-11-25)

**Chunks covered:** 553 closed chunks in range (470 with retrospectives; 466 landed). $4,547 total spend; 315 delivery
bounces.

### Findings dispatched

| Cluster                                                                                             | Chunks | Owner                                         | Action                                                                                    | Status   |
| --------------------------------------------------------------------------------------------------- | ------ | --------------------------------------------- | ----------------------------------------------------------------------------------------- | -------- |
| `delivery-findings` / `hub-log.land` is a mid-CI snapshot; a landed chunk has no final check states | ~205   | `blizzard#2439`                               | issue filed                                                                               | approved |
| The full gate cannot finish inside a node; nodes defer it to each other and CI is the only full run | ~170   | `blizzard-context#748`                        | issue filed (matrix split, CI owns the full run)                                          | approved |
| Build and pre-push skip cheap deterministic checks; CI catches them a bounce later                  | ~150   | —                                             | a runner-executed deterministic-check node, later; no project-specific prompt text        | declined |
| Manual methods cannot be staged in feature envs (seeding gaps, harness fence dropped)               | ~140   | `epic:advanced-mocking`; workspace `0783116`  | four open questions on the epic; fence passed to env workers                              | approved |
| Known reds and flakes on master are re-argued every verify, never filed                             | ~130   | `blizzard#2440`, `#2441`, `#2442`             | registry issue + the two live flakes; the other five named flakes already fixed           | approved |
| "Pre-existing" red claimed without proof at the base (rule candidate)                               | —      | —                                             | —                                                                                         | declined |
| `artifact get` returns only the newest epoch; `fail` transitions carry no cause                     | ~120   | `blizzard#2443`, `blizzard#2444`              | issues filed                                                                              | approved |
| `land` answers `failure` while CI is pending or the PR is behind, naming no check                   | ~70    | `blizzard#2219`                               | comment with evidence; split out "pending is not failure, name the red check"             | approved |
| The retrospective prompt claims every node produces a retrospective                                 | ~65    | `blizzard#2447`                               | prompt edit (`advanced-development-workflow/prompts/retrospective.md`)                    | approved |
| `review-findings-delivered` is empty although deferred findings are minted                          | ~48    | `blizzard#2445`                               | issue filed                                                                               | approved |
| Per-env tools (ruff, pyright, ast-grep, node) missing from the worker's PATH                        | ~48    | —                                             | deferred to the deterministic-check node; `uv run` from `<env>/blizzard` is correct today | declined |
| A global `blizzard` binary shadows the env's CLI                                                    | 1      | —                                             | covered by `verification/blizzard/manual.md`; fails safe                                  | declined |
| Resolve routes flakes, CI-infra errors and inherited reds as `broken`                               | ~45    | —                                             | —                                                                                         | declined |
| Headless workers launch background subagents                                                        | ~44    | —                                             | superseded by `1b5894e90` (Nov 24, harness-neutral rule); watch next pass                 | declined |
| `APPLICATION_SOURCE_DIGEST` conflicts on every rebase                                               | ~41    | —                                             | already fixed (`4e389c7da`, Nov 10)                                                       | declined |
| `verification/blizzard.md` overflows a single read                                                  | ~36    | `blizzard-context#748`                        | folded into the matrix split                                                              | approved |
| Plan nodes plan inline and flag it as a deviation                                                   | ~35    | —                                             | harmless                                                                                  | declined |
| Env state stale after reset (`tenant_settings`, old hub process, `--tenant`)                        | ~35    | `blizzard-mock#191`                           | already fixed; the rest declined                                                          | declined |
| Plan manual steps use wrong verbs or flags                                                          | ~31    | —                                             | self-correcting at verify                                                                 | declined |
| Merges into a rebase-only repo are refused                                                          | ~21    | —                                             | already fixed (`blizzard#2350`)                                                           | declined |
| `bas-hwf` review leaves worktrees detached; pre-push inherits them                                  | ~22    | `blizzard#2437`                               | issue filed, queued first                                                                 | approved |
| Workers cannot amend `winter-planning` intent their build departs from                              | ~22    | workspace (`repos.md`, `.winter/config.toml`) | make `winter-planning` a project repo; operator-driven                                    | approved |
| Verify commits code                                                                                 | 14     | —                                             | already fixed (`9b51b2ea6`, Nov 10)                                                       | declined |
| Scratch paths and `$BLIZZARD_TMPDIR` socket length                                                  | ~5     | —                                             | socket length fixed in fixtures; rest are tips                                            | declined |
| `reference-lint` exits 0 without `WINTER_WORKSPACE_DIR`                                             | 17     | `blizzard-context#748`                        | command line sets the variable                                                            | approved |
| `restatement-check` red on master and run by no CI                                                  | 8      | `blizzard#2446`                               | issue filed (blizzard and blizzard-context CI)                                            | approved |

### Reconciliation (prior pass's findings)

| Prior finding                                             | Fix dispatched                           | Verdict          | Notes                                                                                        |
| --------------------------------------------------------- | ---------------------------------------- | ---------------- | -------------------------------------------------------------------------------------------- |
| Retrospective trusts a stale `delivery-findings` snapshot | `blizzard#1682`                          | recurred         | The re-verify half held; the mid-CI snapshot itself recurred (~205), now `blizzard#2439`     |
| Per-chunk mutation testing measures nothing               | `blizzard#1683`                          | silent-after-fix |                                                                                              |
| Prose ratchet red on `master` and outside CI              | `blizzard#1688`                          | silent-after-fix | The same shape recurred for `restatement-check`, now `blizzard#2446`                         |
| Agents guess `artifact read`/`show`                       | `blizzard#1689`                          | silent-after-fix |                                                                                              |
| Full gate cannot finish in a node                         | `blizzard#1690`                          | recurred         | ~170 gate + ~150 cheap-check complaints; now `blizzard-context#748`                          |
| Headless workers cannot await background subagents        | `blizzard#1684`                          | recurred         | ~44, steady; the prompt rule was rewritten as a harness-neutral goal in `1b5894e90` (Nov 24) |
| `finding get` refuses folded work items' findings         | `blizzard#1685`                          | silent-after-fix |                                                                                              |
| zsh traps in worker Bash                                  | `blizzard#1686`                          | silent-after-fix |                                                                                              |
| `review-finding-delta` first submitted as prose           | `blizzard#1687`                          | silent-after-fix |                                                                                              |
| Issues closed with unsatisfiable acceptance criteria      | `blizzard#1691`, `blizzard-workspace#11` | recurred         | Lower rate                                                                                   |
| "Nothing to land" recorded as `landed`                    | `blizzard#1692`                          | silent-after-fix |                                                                                              |
| Build pushes from a worktree tracking the base            | `blizzard#1693`, `blizzard#1696`         | silent-after-fix | A related detached-HEAD shape is new, `blizzard#2437`                                        |
| No branch protection; no `hotfix/` route                  | `blizzard#1694`                          | silent-after-fix |                                                                                              |
| Review cites project rules almost never                   | `blizzard#1695`                          | silent-after-fix | Review citations rose from 9 to 224                                                          |
| Verifier tracing leaks spans into the fleet collector     | `winter-context#22`                      | silent-after-fix |                                                                                              |
| Deferred should-fix findings ship with no owner           | — (declined)                             | recurred         | Findings are minted; the empty asset is now `blizzard#2445`                                  |
| Resolve cannot re-run a flaky CI job                      | — (declined)                             | recurred         | `blizzard#1503` sends flakes to `broken`; declined again this pass                           |

### `bzh:` citation counts

| Rule                              | Citations this range |
| --------------------------------- | -------------------- |
| `bzh:domain-orchestration-split`  | 466                  |
| `bzh:dependency-injection`        | 455                  |
| `bzh:branch-name`                 | 358                  |
| `bzh:frontend-kit-floor`          | 340                  |
| `bzh:shared-kernel`               | 284                  |
| `bzh:bulk-reconstitution`         | 279                  |
| `bzh:change-note`                 | 268                  |
| `bzh:frontend-wire-conformist`    | 231                  |
| `bzh:narrow-seams`                | 223                  |
| `bzh:mirrored-test-layout`        | 219                  |
| `bzh:domain-package-layers`       | 219                  |
| `bzh:domain-core`                 | 195                  |
| `bzh:frontend-open-vocabulary`    | 170                  |
| `bzh:frontend-domain-boundary`    | 157                  |
| `bzh:transaction-participation`   | 149                  |
| `bzh:released-surface-additive`   | 138                  |
| `bzh:frontend-containers-compose` | 136                  |
| `bzh:data-roles`                  | 129                  |
| `bzh:injected-clock`              | 128                  |
| `bzh:pluggable-seams`             | 127                  |
| `bzh:strict-types`                | 125                  |
| `bzh:page-bounded-read`           | 118                  |
| `bzh:frontend-formatters`         | 117                  |
| `bzh:open-response-vocabulary`    | 113                  |
| `bzh:store-exclusive-write`       | 111                  |

213 distinct ids cited (latest epoch per node and asset, all artifact kinds); top 25 shown. 43 of the 224 rules declared
by heading id went uncited — mostly verification-matrix and worker-node procedure ids, cited by method name instead.
Pruning candidates to review: `bzh:claim-vocabulary`, `bzh:human-entries`, `bzh:readme-voice`, `bzh:patch-scope`,
`bzh:mise`, `bzh:operational-event-log`. Reviewed, not pruned.
