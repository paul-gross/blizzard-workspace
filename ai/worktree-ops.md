# Worktree Operations

Git commands for the polyrepo workspace topology. All paths are relative to the workspace root.

> **Tip:** For multi-repo setup and bulk operations, prefer `winter ws init` and the other `winter ws` commands over the raw git sequences below — the CLI is idempotent, reads the workspace config, handles pinned repos, and runs in parallel. See [winter-cli/usage.md](./winter-cli/usage.md) for the full command reference. The raw git commands here are still useful for single-repo work and for understanding what the CLI does under the hood.

## Pinned repos

Some repos are **pinned** — they always track the remote main branch and never participate in feature branching. Declare pinning by setting `pinned = true` on a `[[project_repository]]` entry in `workspace:/.winter/config.toml`. The main branch comes from the entry's `main_branch` field, falling back to the workspace-wide `default_main_branch`.

The CLI treats pinned repos specially across commands:

- **init** — sets the worktree branch's upstream to `origin/<main-branch>` and `push.default=upstream`, so `git push` lands on the main branch.
- **connect / disconnect / push** — skipped; pinned repos never get a feature-branch upstream.
- **sync** — pulled from `origin/<main-branch>` via `--ff-only`.

## Cloning (source checkouts)

```bash
winter ws init
```

This reads `.winter/config.toml`, clones every declared repo that's missing into `projects/`, applies git identity, writes git-exclude entries, and runs each repo's `cmd` list. Safe to re-run.

Raw equivalent for a single repo:

```bash
git clone <repo-url> ./projects/<repo-name>
```

## Creating a feature worktree

```bash
winter ws init <name>
```

This command:

- Creates the `./<name>/` directory.
- For each project repo, runs `git worktree add -b <name> <main-branch>`.
- Copies git identity into each worktree.
- Writes git-exclude entries.
- For pinned repos, wires the upstream to `origin/<main-branch>` — see [Pinned repos](#pinned-repos).
- Runs each repo's `cmd` list.
- Seeds `./<name>/.winter.env` with `WINTER_ENV`, `WINTER_ENV_INDEX`, and `WINTER_PORT_BASE`.
- Runs every installed extension's `on_worktree_init` hook.

Greek letters (`alpha`, `beta`, …) are the convention because they carry a port-offset index, but any valid name works.

After this runs, follow `workspace:/ai/project/project-setup.md` for project-specific orchestration (appending project-specific vars to `.winter.env`, provisioning per-environment resources, generating other env files, anything else the project needs).

Raw equivalent, per repo:

```bash
git -C ./projects/<repo-name> worktree add ../../<name>/<repo-name> -b <name> <main-branch>
```

## Connecting a worktree to a remote feature branch

```bash
winter ws connect <name> <feature-branch>
```

Sets `push.default=upstream` and the upstream (`origin/<feature-branch>`) on each non-pinned repo's worktree. The connected feature branch is read back from git's upstream tracking on the first non-pinned repo, so all non-pinned repos in a worktree must use the same remote feature branch name. The remote branch is not created yet — that happens on first push:

```bash
git -C "./<name>/<repo-name>" push -u origin <name>:<feature-branch>
```

**If the recorded feature branch is empty when the user asks to push**, do not guess — ask the user which remote branch they want to push to. Once they provide one, run `winter ws connect` before pushing.

**Before pushing**, ask the user: "Want me to run pre-release checks (lint, format, tests) on the changed repos before pushing?" If they agree, run the checks from the Pre-Release Checklist in [development.md](./project/general/development.md) for each repo with changes. Fix any issues before pushing.

Pinned repos are always skipped during connect/disconnect/push — if they have local commits, push directly to their configured main branch outside the normal feature delivery flow.

## Disconnecting a worktree

```bash
winter ws disconnect <name>
```

Unsets upstream tracking on each non-pinned repo. With no upstream set, the worktree reads as disconnected.

## Syncing a feature worktree

```bash
winter ws sync <name>
```

Fetches every repo in parallel, tries `git merge --ff-only origin/<main-branch>` on each worktree (falls back to a 3-way merge if ff-only fails), then fast-forwards the source checkout in `projects/`. Pinned repos are reset to `origin/<main-branch>` via the same ff-only path. Main branch per repo is read from the config — `main_branch` on the `[[project_repository]]` entry if set, otherwise the top-level `main_branch`.

## Pushing completed work

```bash
winter ws push <name>                # all changed non-pinned repos
winter ws push <name> repo-a repo-b  # specific repos
```

Uses the feature branch recorded during `winter ws connect`. Each repo gets pushed to `origin/<feature-branch>` and its upstream is set on first push.
