---
name: ws-push
description: Push local commits from a feature environment, a standalone repo, or the workspace branch to its recorded upstream
model: opus
allowed-tools: Bash, Read, AskUserQuestion
---

Push local commits from one of: the workspace branch, a standalone repo, or a feature environment. Parse `$ARGUMENTS` to determine which — a single optional name.

## Big picture

A feature environment contains a worktree for every project repo, so pushing one is a multi-repo operation. Use `winter ws push` — it pushes every matched worktree to its tracked upstream in parallel and honors pinned-repo rules. See [ai/winter-cli/usage.md](./ai/winter-cli/usage.md) and [ai/worktree-ops.md](./ai/worktree-ops.md) for the full reference.

Use raw `git push` for the workspace branch itself — `winter ws push` doesn't operate on it. Standalone repos can be reached via `winter ws push --standalone` or with raw git, whichever is more convenient.

## Dispatch on the argument

- **No argument** → push the `workspace` branch.
- **A standalone repo name** → push that repo.
- **A feature environment name** (greek letter or otherwise, e.g., `alpha`) → push the environment.

If the name could be either a standalone repo or a feature environment, ask the user which they meant.

## Pre-push discovery

After resolving the target (from the dispatch above) and **before** running the per-target push command (in the sections below), scan for documented pre-push processes and surface any matches. This lets the caller honor project-specific gates (review skills, lint runs, manual checks) that the workspace or per-repo docs declare, without `ws-push` knowing about any specific process by name.

Locations to scan, depending on target:

- **Always**: `workspace:/ai/project/contributing.md`
- **Standalone repo target**: also `./<name>/CONTRIBUTING.md`
- **Feature env target**: also `./<env>/<repo>/CONTRIBUTING.md` for each worktree in the env — list `./<env>/*/` to enumerate per-repo worktrees and read each one's `CONTRIBUTING.md` if present

Read each file and look for any pre-push documentation — section, paragraph, checklist, whatever shape the project uses. Skip files that don't exist; skip files that have nothing relevant.

If you find anything, surface it to the caller (annotated by source path) and ask via `AskUserQuestion` with three options:

- **Carry out the documented steps before pushing** — follow what was found, then proceed to the push.
- **Skip and push as-is** — acknowledge the documented steps, proceed straight to the push.
- **Show full text** — relay the raw matched content per source, then re-prompt.

Do **not** execute documented steps unprompted — wait for the caller's choice. If they pick "Carry out", then run the steps before invoking the per-target push command below. The scan itself is awareness; execution only happens when the caller explicitly opts in.

If you find nothing, proceed to the push silently. Absence is fine, not a warning.

## Workspace (no argument)

Push workspace changes to the user's `origin` remote. The `winter` remote is the upstream framework — don't push there.

```bash
git push origin workspace
```

Report the result.

## Standalone repo

Reach standalone repos through the CLI:

```bash
winter ws push --standalone            # push each standalone repo with commits ahead
```

…or use raw git for a single one:

```bash
git -C ./<name> push
```

Report the result.

## Feature environment

```bash
winter ws push <name>                  # all non-pinned worktrees in the env
winter ws push <name>/<repo>           # one specific worktree
winter ws push '<name>/*'              # all worktrees in the env (same as bare <name>)
winter ws push <name> --include-pinned # non-pinned + pinned
winter ws push <name> --only-pinned    # pinned only
```

`PATTERNS` are segment-aware globs over `<env>/<repo>`. A connected environment has each non-pinned worktree's remote tracking branch already set, so `winter ws push <name>` just works — it pushes each non-pinned repo to the feature branch recorded by `winter ws connect`.

Pinned worktrees are excluded by default. If you've landed commits on a pinned repo's main branch and want to ship them, pass `--include-pinned` (alongside non-pinned) or `--only-pinned` (alone). Pushed pinned worktrees go to whatever upstream their local branch tracks.

If an env isn't connected (no recorded feature branch), `winter ws push` reports the non-pinned repos as skipped. Run `winter ws connect <name> <feature-branch>` first, then retry.

## Report

Output a concise summary based on what `winter ws push` printed. For workspace and standalone targets, report the raw push result.

For a feature environment, include a per-repo line — what each repo did (pushed, nothing to push, skipped):

```
## Push: <name>

- repo-a: pushed 2 commits to origin/<feature-branch>
- repo-b: nothing to push
- repo-c: skipped (env not connected)
```

$ARGUMENTS
