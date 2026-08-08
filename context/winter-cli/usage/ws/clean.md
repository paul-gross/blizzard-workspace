# `winter ws clean` — remove untracked files from worktrees

For the rest of the family, see the [`winter ws` hub](./index.md).

`winter ws clean PATTERNS... [--force] [--dry-run] [--json]` removes untracked files and untracked directories from every matched, non-pinned project worktree. **A bare `<env>` matches every non-pinned worktree in that env** (`<env>/*`) — see [`winter ws clean`](./patterns.md#winter-ws-clean--patterns-only-no-trailing-ref) in `patterns.md` for the `PATTERNS` grammar this shares (pinned worktrees always skipped, no `--standalone`/`--all`). At least one `PATTERN` is required; there is deliberately no implicit "every worktree" default for a command that deletes.

This is `git clean -fd`, the complement to [`winter ws reset`](./reset.md). A `--hard` reset restores everything git tracks; this removes what it doesn't. The two are separate commands rather than one flag because cleaning is independent of moving a branch pointer — dropping cruft before a re-provision, or after a botched codegen run, involves no reset at all. Run both to return a worktree to a pristine ref:

```bash
winter ws clean alpha/winter --dry-run          # look first
winter ws reset alpha/winter origin/{main} --hard
winter ws clean alpha/winter
```

## What is and isn't removed

| Local state | Removed by `ws clean`? |
|-------------|------------------------|
| Untracked file | **yes** |
| Untracked directory, including an empty one | **yes**, reported as one `dir/` entry |
| Untracked nested git repository | no — `git clean -fd` refuses to descend into one |
| Ignored file (`.venv`, `node_modules`, build output) | no — never, in any mode |
| Modified tracked file | no — `ws reset --hard` owns that |
| Staged or unstaged deletion | no — `ws reset --hard` owns that |

The listing granularity is git's own: a whole untracked directory is one `dir/` entry rather than a line per file inside it, because that is the unit being removed.

**Ignored files are never removed.** There is no `-x` equivalent on this command, and that is deliberate: in a winter worktree the ignored set is the provisioned artifacts, so removing it across matched worktrees would silently turn a clean into a full re-provision. When you genuinely want them gone, run `git clean -fdx` in the single worktree you mean, where the blast radius is visible.

## Confirmation and previewing

**`ws clean` prompts before removing anything unless `--force` — at any worktree count**, including a single match. This is stricter than [`ws reset --hard`](./reset.md), whose own prompt threshold that page owns. The asymmetry is intentional: a reset leaves its commits in the reflog, while cleaned files are unrecoverable, so worktree count is the wrong axis to decide on. The prompt lists **every path** it would delete, not a per-repo count.

That list comes from `git clean -nd` — the dry run of the very command the real run executes — so the preview and the removal are the same set by construction rather than by two enumerations that have to be kept in agreement.

`--dry-run` prints that same list and removes nothing, skipping the prompt. It is the only preview available, so prefer it before any run whose `PATTERNS` you haven't used before. A run where every matched worktree is already clean reports `Nothing to clean` and exits without prompting.

Matching no worktrees at all reports the patterns and exits zero — the same no-match shape `ws reset` uses.

## Examples

```bash
winter ws clean alpha/winter --dry-run    # preview one worktree, delete nothing
winter ws clean alpha/winter              # prompt, then clean one worktree
winter ws clean alpha                     # every non-pinned worktree in alpha
winter ws clean alpha --force             # same, no prompt (scripted use)
winter ws clean '*/winter' --json --dry-run   # every env's winter worktree, NDJSON preview
winter ws clean '*/winter' --json --force     # ...and the real run
```

**`--json` requires `--force` or `--dry-run`.** The confirmation prompt writes human text to stdout and blocks, which would corrupt the NDJSON stream and hang a non-interactive consumer; the combination is refused rather than silently auto-forced, so `--json` is never the reason an unrecoverable delete ran unprompted.

`--json` emits NDJSON (one JSON object per line): a `clean_started` event, one `repo_clean` event per matched repo (`env`, `repo`, `count`, `paths`), and a trailing `clean_completed` event carrying `total` — the same `{cmd}_started` / `repo_{cmd}` / `{cmd}_completed` shape [`winter ws reset --json`](./reset.md) and `winter ws merge --json` use. On a real run `paths` is what git reported as actually removed, not what a preview predicted. `repo_clean` carries the full `paths` list rather than only a count, since a scripted consumer has no other way to learn which files went. Worktrees with nothing untracked still emit an event, so the full matched set is visible.
