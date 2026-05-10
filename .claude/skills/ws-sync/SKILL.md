---
name: ws-sync
description: Sync a feature environment, a standalone repo, or the workspace branch with its remote
model: opus
allowed-tools: Bash, Read
---

Sync one of: the workspace branch, a standalone repo, or a feature environment. Parse `$ARGUMENTS` to determine which — a single optional name.

## Big picture

A feature environment contains a worktree for every project repo, so syncing one is a multi-repo operation. Use `winter ws sync` and `winter ws push` — they fetch in parallel, handle pinned repos, and fast-forward the source checkouts under `projects/` as a side effect. See [ai/winter-cli/usage.md](./ai/winter-cli/usage.md) and [ai/worktree-ops.md](./ai/worktree-ops.md) for the full reference.

Use raw git only for the workspace branch itself — `winter ws sync` doesn't operate on it. Standalone repos can be reached via `winter ws fetch/pull/push --standalone` or with raw git, whichever is more convenient.

`winter ws sync <env>` always brings `origin/main` into the environment (ff-only, falling back to a 3-way merge when ff fails). To pull remote commits made on the *feature branch* instead, use `winter ws pull <env>` — ff-only by default; pass `--merge` or `--rebase` to integrate diverged repos explicitly, plus `--autostash` to handle a dirty working tree.

## Project-specific rules

For a feature-environment sync, the choices below (merge vs rebase, what to lint/test, what counts as a clean push) are project-specific. Discover the project's rules by scanning `workspace:/ai/project/` for files like:

- `contributing.md` — delivery conventions (merge vs rebase, push targets, PR flow)
- `development.md` — pre-push checks (lint, format, tests)
- anything else under `ai/project/` that looks like a rule or convention doc

Read whatever exists before doing real work. If nothing relevant is there, ask the user how they want to deliver work — don't assume defaults.

Workspace and standalone-repo syncs don't depend on these rules.

## Dispatch on the argument

- **No argument** → push the `workspace` branch.
- **A standalone repo name** → pull-rebase + push that repo.
- **A feature environment name** (greek letter or otherwise, e.g., `alpha`) → sync the environment.

If the name could be either a standalone repo or a feature environment, ask the user which they meant.

## Workspace (no argument)

Push workspace changes to the user's `origin` remote. The `winter` remote is the upstream framework — don't push there.

```bash
git push origin workspace
```

Report the result.

## Standalone repo

Standalone repos sit at the workspace root and aren't managed by `winter ws sync`. Either reach them through the CLI:

```bash
winter ws pull --standalone            # ff-only against each standalone repo's tracked upstream
winter ws pull --standalone --rebase   # if you have local commits and want a linear history
winter ws push --standalone            # push each standalone repo with commits ahead
```

…or use raw git for a single one:

```bash
git -C ./<name> pull --rebase && git -C ./<name> push
```

Report the result.

## Feature environment

Make sure you've already done the discovery from "Project-specific rules" above — the steps below defer to those rules wherever judgment is required.

### 1. Pull main into the environment

```bash
winter ws sync <name>
```

This fetches every repo in parallel, ff-only-merges `origin/<main-branch>` into each worktree, falls back to a 3-way merge when ff isn't possible, and fast-forwards each source checkout under `projects/`. Pinned repos are reset to `origin/<main-branch>` via the same path.

If a repo reports "diverged" (neither ff nor 3-way merge succeeded), resolve it manually with raw git in that repo's worktree per the project's contributing rules (rebase or merge), then re-run `winter ws sync <name>` to confirm.

### 2. Push the feature branch (only if there are local commits)

Before pushing, ask the user: "Want me to run lint/format/tests on the changed repos first?" If yes, run whatever checks the project's contributing rules define (or ask the user if no rules are established) and fix failures before pushing.

```bash
winter ws push <name>                # all non-pinned worktrees in the env
winter ws push <name>/<repo>         # one specific worktree
winter ws push '<name>/*'            # all worktrees in the env (same as bare <name>)
```

`PATTERNS` are segment-aware globs over `<env>/<repo>`. A connected environment has each non-pinned worktree's remote tracking branch already set, so `winter ws push <name>` just works — it pushes each non-pinned repo to the feature branch recorded by `winter ws connect`.

Pinned worktrees are excluded by default. If you've landed commits on a pinned repo's main branch and want to ship them, pass `--include-pinned` (alongside non-pinned) or `--only-pinned` (alone). Pushed pinned worktrees go to whatever upstream their local branch tracks.

## Report

Output a concise summary based on what `winter ws sync` / `winter ws push` printed. For workspace and standalone targets, report the raw push/pull result.

For a feature environment, include a per-repo line — what each repo did (ff'd, merged, diverged, pushed, no-op):

```
## Sync: <name>

Pull main → environment:
- repo-a: ff'd to origin/main
- repo-b: 3-way merged origin/main
- repo-c: already up to date
- repo-d: DIVERGED — needs manual resolution

Push feature branch (origin/<feature-branch>):
- repo-a: pushed 2 commits
- repo-b: nothing to push
```

$ARGUMENTS
