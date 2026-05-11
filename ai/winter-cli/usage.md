# Winter CLI — Usage

Command reference for agents executing `winter` commands. For installation and configuration, see [setup.md](./setup.md).

## When to use the CLI vs raw git

**Use the CLI** for operations that span multiple repos — init, status, sync, connect, push, diff. The CLI handles pinned repos, parallel fetching, source checkout fast-forwarding, and idempotent setup automatically.

**Use raw git** for single-repo operations — staging files, committing, resolving conflicts, interactive rebase, branch inspection. The CLI doesn't replace git for per-repo work.

## `winter ws init` — reconcile the workspace against the config

One idempotent command with three modes. Safe to re-run any time.

| Form | What it reconciles |
|------|--------------------|
| `winter ws init` | Source checkouts in `projects/` and standalone repos. |
| `winter ws init <name>` | The `./<name>/` feature worktree. |
| `winter ws init --all` | Source checkouts, standalones, and every existing worktree. |

Each mode applies the same per-repo reconcile steps (git identity, excludes, `cmd` list, extension processing, pinned-repo tracking on worktrees). See [worktree-ops.md](../worktree-ops.md) for the full step list and the pinned-repo specifics.

Greek letters (`alpha`, `beta`, …) are the suggested convention for worktree names because they carry a fixed port-offset index 1..24. Any other valid directory name is accepted and gets a deterministic SHA-1-derived index in the range 26..281 (index 25 is reserved as a buffer). Hash collisions among non-Greek names are possible but unlikely.

## Workspace commands (`winter ws`)

| Command | Usage | Purpose |
|---------|-------|---------|
| `winter ws init` | `winter ws init [TARGET] [--all] [--json]` | Reconcile source checkouts or a feature worktree |
| `winter ws list` | `winter ws list [--json]` | List all feature worktrees |
| `winter ws status` | `winter ws status [WORKTREE] [--json]` | Git status across all repos in a worktree |
| `winter ws sync` | `winter ws sync WORKTREE [--json]` | Fetch all repos, ff-only merge (falls back to merge), then fast-forward source checkouts |
| `winter ws connect` | `winter ws connect WORKTREE FEATURE_BRANCH [--json]` | Connect a worktree to a remote feature branch |
| `winter ws disconnect` | `winter ws disconnect WORKTREE [--json]` | Disconnect from the feature branch |
| `winter ws push` | `winter ws push WORKTREE [REPOS...] [--json]` | Push changed repos to their upstream branch |
| `winter ws diff` | `winter ws diff WORKTREE [--staged\|--branch] [--repo REPO] [--json]` | Unified diff across all repos in a worktree |
| `winter ws index` | `winter ws index NAME [--json]` | Print the port-offset index for a worktree name (Greek = 1..24, other = hashed 26..281) |

## Repository commands (`winter repo`)

| Command | Usage | Purpose |
|---------|-------|---------|
| `winter repo list` | `winter repo list [--json]` | List all project and standalone repositories and their types |
| `winter repo status` | `winter repo status WORKTREE REPO [--json]` | Detailed git status for one repo in a worktree |

## Dashboard

```bash
winter dashboard
```

Interactive TUI showing workspace status, worktrees, and repo details. Navigate with keyboard.

## Drift warnings

Operations that iterate repos (`ws list`, `ws status`, `ws sync`, `ws connect`, `ws disconnect`, `ws push`, `ws diff`, `repo list`) warn to stderr when the config and filesystem disagree:

- **Missing:** a declared project repo has no directory under `projects/` — run `winter ws init`
- **Undeclared:** a directory under `projects/` is not in the config — add it to `.winter/config.toml` or remove it

`winter ws init` treats both cases as actionable rather than a warning: missing repos are cloned; undeclared directories are left alone.

Drift detection currently covers project repos only. Missing or undeclared standalone repos are not warned about; if a `[[standalone_repository]]` entry's directory is missing, `winter ws init` clones it on the next run.

## Common workflows

### Bootstrap a new workspace
```bash
winter ws init              # clone every declared repo into projects/
winter ws init alpha        # create the alpha/ worktree
```

### Check workspace state
```bash
winter ws status alpha
```

### Sync before starting work
```bash
winter ws sync alpha    # tries ff-only, falls back to merge, reports diverged if both fail
```

### Start a new feature
```bash
winter ws init alpha                       # ensures alpha/ exists
winter ws connect alpha feature/my-feature
```

### Push completed work
```bash
winter ws push alpha
```

### Review changes before committing
```bash
winter ws diff alpha --branch          # full branch diff vs main
winter ws diff alpha --staged          # staged changes only
winter ws diff alpha --repo my-app     # single repo
```

### Reuse a worktree for a different feature
```bash
winter ws disconnect alpha
winter ws connect alpha feature/other-feature
```

### Propagate a config change
After adding a repo to the config or changing `cmd`/`git_excludes`, reconcile everything:
```bash
winter ws init --all
```
