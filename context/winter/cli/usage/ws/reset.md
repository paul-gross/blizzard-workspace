# `winter ws reset` — move worktree branches to a ref

For the rest of the family, see the [`winter ws` hub](./index.md).

`winter ws reset PATTERNS... REF [--soft|--mixed|--hard] [--force] [--dry-run] [--json]` moves the branch pointer of
every matched, non-pinned project worktree to `REF`, all-or-nothing across the run. **A bare `<env>` matches every
non-pinned worktree in that env** (`<env>/*`) — see
[`winter ws reset`](./patterns.md#winter-ws-reset--same-grammar-as-connect-trailing-ref) in `patterns.md` for the full
`PATTERNS` grammar and scope rules (at least one `PATTERN` required, pinned worktrees always skipped, no
`--standalone`/`--all`). `REF` is required and always trailing: an optional trailing ref would make the variadic form
ambiguous (`winter ws reset alpha beta` — two envs, or one env reset to branch `beta`?).

This is `git reset`, not `git checkout` — **upstream tracking is never touched by any mode.** `reset` moves the pointer;
[`winter ws connect`](./patterns.md#winter-ws-connect--winter-ws-disconnect) changes tracking; the two never mix.
Concretely: resetting a worktree onto a different branch leaves a later `winter ws push` still aimed at the original
upstream, exactly as plain `git reset` would.

`REF` resolves independently per repo — an explicit ref (`origin/main`, `@{u}`, a branch name) applied verbatim, or a
per-repo `{main}` / `{master}` / `{default}` token (see [ref tokens](./ref-tokens.md)) resolved before any git operation
runs. **No network** — like `git reset` it operates on refs you already have; run [`winter ws fetch`](./fetch.md) first
if you want them fresh.

## Modes — git's own three-tree semantics

| Mode                | Branch pointer | Index | Working tree                        |
| ------------------- | -------------- | ----- | ----------------------------------- |
| `--soft`            | moves          | kept  | kept — delta lands **staged**       |
| `--mixed` (default) | moves          | reset | kept — delta lands **unstaged**     |
| `--hard`            | moves          | reset | reset — **tracked** delta discarded |

**Untracked files survive every mode, including `--hard`.** `--hard` resets the three trees git tracks, so an untracked
file — one never `git add`ed — is in none of them and has no path entry to reconcile; git leaves it on disk. Staged
adds, staged and unstaged deletes, and modifications are all discarded or restored as the table says. This is plain
`git reset` behavior, not a winter addition: `reset --hard` and `git clean` are complements, and `winter ws reset` runs
only the former. Resetting an env to `origin/main` therefore does **not** produce a pristine env — stale generated files
and scratch files remain, and remain importable. [`winter ws clean`](./clean.md) is the other half; run it after a
`--hard` reset to get there.

The dirty guard below reflects the same split: it counts staged and unstaged changes but **not** untracked files, so a
worktree whose only local change is untracked files passes the guard, resets, and keeps those files with no refusal and
nothing in the report.

`--soft` and `--mixed` apply **no** dirty or abandonment guard — content is preserved (neither touches the working tree,
matching plain `git reset --soft`/`--mixed`, which never refuse on a dirty tree), but history is not: both still move
the branch pointer, abandoning whatever commits it left behind (reflog-only afterwards). A bare
`winter ws reset '*/*' origin/main --soft` moves every repo's pointer with no refusal at all — the blast radius scales
with `PATTERNS`, not with the mode. `--hard` is the only mode the safety gate applies to: it refuses on any matched repo
that is dirty, or carries commits not reachable from the branch it's moving *away from* (its own current upstream, e.g.
`origin/feature-123`, falling back to `origin/<main-branch>` when disconnected) — **the refusal is all-or-nothing across
the whole run: a per-repo report, and no `git reset` executes in any repo.** `--force` bypasses this gate.

**Confirmation.** A `--hard` reset that matches more than one worktree prints the matched list and asks `Continue?`
before doing anything; `--force` skips the prompt (in addition to its existing meaning of bypassing the
dirty/abandonment gate above). `--soft`/`--mixed`, and any `--hard` matching exactly one worktree, run with **no
confirmation at all** — `--dry-run` (below) is the way to preview those before committing to them.

## Ref-resolution refusals — not bypassed by `--force`

- A bare commit SHA as `REF` refuses unless `PATTERNS` resolves to exactly one worktree — a SHA has no env-wide meaning
  (the same 40 hex chars name a different commit, or nothing, in each repo's independent history). Target one worktree
  explicitly (`alpha/winter abc1234`) to reset to a specific commit. A branch name that happens to be all-hex (e.g.
  `deadbeef`) is indistinguishable from a SHA by this check and refused the same way — qualify it as
  `refs/heads/deadbeef` to reset to it across more than one worktree.
- A `REF` that doesn't resolve locally in a matched repo refuses the whole run before mutating anything, mirroring
  `checkout`'s `refused-missing-ref` — run `winter ws fetch` first if you need fresh remote-tracking refs.

## Examples

```bash
winter ws reset alpha/winter origin/{main}                    # one worktree, that repo's own main
winter ws reset alpha/winter beta/winter origin/main --hard    # two worktrees, explicit — no confirmation
winter ws reset alpha origin/main                              # every non-pinned worktree in alpha, --mixed (default)
winter ws reset alpha origin/main --hard                       # same, --hard — prompts unless --force
winter ws reset alpha abc1234 --hard                            # single worktree only — a bare SHA refuses otherwise
winter ws reset alpha origin/main --hard --dry-run
```

A bare `<env>` (no `/repo` segment) targets **every non-pinned worktree in that env** — the third example above resets
the whole of `alpha`. Scope to `<env>/<repo>` when you mean one worktree.

`--dry-run` runs every safety check and prints the per-repo plan (mode, resolved ref) with no side effects and no
confirmation prompt — a refusal still reports as a refusal. `--json` emits NDJSON (one JSON object per line): a
`reset_started` event, one `repo_reset` event per matched repo (`env`, `repo`, `result`, `ref`), and a trailing
`reset_completed` event — the same `{cmd}_started` / `repo_{cmd}` / `{cmd}_completed` shape `winter ws merge --json`
uses.
