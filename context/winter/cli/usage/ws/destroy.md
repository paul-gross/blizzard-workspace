# `winter ws destroy` — tear down one or more feature envs

For the rest of the family, see the [`winter ws` hub](./index.md). `winter ws destroy PATTERNS...` is the symmetric
counterpart to [`winter ws init ENV`](./init.md), fanned out across every env `PATTERNS` matches.

Each `PATTERN` is a **bare env-name glob** — destroy operates on whole envs, not `<env>/<repo>` worktrees, so a
`/`-qualified pattern is rejected. At least one `PATTERN` is required (no implicit "all"). See
[patterns.md](./patterns.md#winter-provision--winter-clean--winter-ws-destroy--env-level-patterns) for the shared
grammar with `winter provision` and `winter clean`.

```bash
winter ws destroy alpha              # one env, no prompt
winter ws destroy alpha beta         # multiple envs — prints the resolved list, asks to confirm
winter ws destroy 'feature-*'        # glob — prints the resolved list, asks to confirm
winter ws destroy alpha beta --force # skip the confirmation prompt (scripted use)
```

Because teardown is irreversible, a glob or more than one `PATTERN` prints the resolved env list and asks for
confirmation before doing anything; `--force` skips the prompt. A single literal `PATTERN` destroys immediately with no
prompt.

Per matched env, in order:

1. **Safety check** — refuses on a missing env path, dirty worktrees, or a nested workspace with a dirty env, unpushed
   work, or unreadable state (override with `--force`).
2. **Nested envs** — for each worktree of a `nested = true` repo, destroys every feature env the nested workspace holds,
   then stops its workspace-scope services when it binds a service provider; see
   [Nested workspaces](#nested-workspaces).
3. **Provision teardown** — runs `data --destroy` then `resource --destroy` (reverse of apply order) using the
   `[[provision.*]]` handlers declared in `.winter/config.toml` and extension manifests. Handlers without a declared
   `destroy` script warn and no-op without aborting structural teardown. Pass `--no-provision-teardown` to skip this
   phase entirely.
4. **Hooks** — fires every extension's `on_env_destroy` hook (mirror of `on_env_init`). With `--strict`, a non-zero hook
   exit aborts the teardown; without it, hook failures are logged and teardown proceeds.
5. **Worktree removal** — `git worktree remove` for every per-repo worktree.
6. **Env cleanup** — removes the env directory, strips the matching `# >>> winter-dir/<env>` block from the workspace's
   [exclude file](./init.md#workspace-exclude-file), and removes the env's index entry from `.winter/state.toml`.

A failure in any one matched env is reported and does not stop teardown of the remaining matched envs; the command exits
non-zero if any env failed.

Use `--dry-run` to preview the plan with no side effects — the nested envs that would be destroyed and the nested
workspace services that would be stopped are listed first, then the provision teardown plan (which `destroy` scripts
would run), then the structural plan, per matched env. `--dry-run` never prompts for confirmation.

**`--strict` behaviour for provision teardown:** when a `destroy` script exits non-zero, `--strict` aborts the entire
teardown *before* removing worktrees or the env directory, preventing resources from being orphaned. Without `--strict`,
the failure is surfaced as an error (and the command exits non-zero) but structural removal proceeds.

**Prefer this over `rm -rf <env>/` + manual `git worktree remove`.** Manual removal bypasses provision teardown and
`on_env_destroy` hooks — extensions that need to clean up per-env state (tmux sessions, watchers, provisioned DBs, RMQ
vhosts, buckets) get skipped, leaving provisioned resources orphaned.

## Nested workspaces

A worktree of a `[[project_repository]]` declaring `nested = true` (see
[configuration/repositories.md — nested](../../configuration/repositories.md#nested--a-project-repo-that-is-itself-a-workspace))
is a workspace root with feature envs of its own. Destroying the outer env tears those down first, through the nested
workspace's own CLI, while the outer env is still whole:

- **Read** — before the safety check, winter reads which envs each nested workspace holds, and what work in it exists
  nowhere else, from one `winter ws status --json` per nested root. That read also verifies the root for every nested
  destroy that follows. If it fails, or the `winter` there resolves a workspace other than the nested root, destroy
  refuses unless `--force` is given; with `--force` it reports the error, skips that nested workspace, and exits
  non-zero after finishing the outer teardown.
- **Dirty refusal** — a nested env with a dirty worktree refuses the destroy like a dirty outer worktree, naming it as
  `<repo> (nested: <env>, ...)`. A nested env worktree that holds a workspace of its own counts as dirty when that
  workspace is dirty or unreadable, at any depth. `--force` bypasses it.
- **Unpushed refusal** — the nested workspace's source checkouts, standalones, and env branches all live inside the
  outer worktree, and a nested `ws destroy` keeps each env's branch in those checkouts. Removing the worktree deletes
  them, so destroy refuses while the nested workspace holds work that exists nowhere else, at any depth — exactly the
  work [ws status — Nested workspaces](./status.md#nested-workspaces) counts as unpushed. A branch pushed to its
  upstream but not yet merged is not refused, and local-only commits cover the branch a nested `ws destroy` kept.

  The refusal names each place and why, as
  `<repo> (nested: <env>/<repo> (unpushed commits), <env>/<repo> (unpushed branch), projects/<repo>
  (local-only commits, stashes), standalone <repo> (uncommitted changes), ...)`.
  Push the commits and branches, commit and push or drop the changes and stashes, or pass `--force` to discard it all.
- **Teardown** — winter runs `winter ws destroy <nested-env>` inside the nested root, one env at a time, passing
  `--force`, `--strict`, and `--no-provision-teardown` through. Each nested destroy runs its own provision teardown and
  `on_env_destroy` hooks, so the nested env's provisioned resources and services go with it. Its output streams as
  `[<repo>] ...` lines.
- **Failure** — every nested env is attempted. If any nested destroy fails, the outer env's destroy stops before its own
  provision teardown and keeps its worktree, unless `--force` is given; with `--force` the outer teardown proceeds and
  the command exits non-zero.
- **Workspace services** — once its envs are destroyed, a nested workspace whose effective config (its `config.toml`
  with its `config.local.toml` over it) binds `[capabilities] service` gets `winter service down workspace` run inside
  its root, so its workspace-scope services do not outlive the worktree. A nested workspace that binds no provider runs
  nothing. A failure stops the outer env's destroy and keeps its worktree, unless `--force` is given; with `--force` the
  outer teardown proceeds and the command exits non-zero.
- **`--dry-run`** lists each nested env with `would_destroy_nested_env` and each nested workspace whose services would
  be stopped with `would_stop_nested_workspace_services`, and runs no nested command except the read-only status read.

## `--json` action vocabulary

`winter ws destroy --json` emits NDJSON. The structural actions appear alongside any provision-teardown actions from the
same stream:

| `action`                               | Phase   | Meaning                                                                                  |
| -------------------------------------- | ------- | ---------------------------------------------------------------------------------------- |
| `nested_env_destroyed`                 | 2       | A nested env was destroyed; `detail` is its name, `location` its path                    |
| `nested_workspace_services_stopped`    | 2       | A nested workspace's workspace-scope services were stopped; `location` is its root       |
| `would_destroy_nested_env`             | dry-run | Nested env that would be destroyed; `detail` is its name                                 |
| `would_stop_nested_workspace_services` | dry-run | Nested workspace whose workspace-scope services would be stopped; `location` is its root |
| `provision_teardown_started`           | 3       | Provision teardown is beginning; `detail` is `data → resource`                           |
| `provision_subtarget_started`          | 3       | A teardown sub-target is starting                                                        |
| `provision_no_handlers`                | 3       | No handlers declared for a sub-target                                                    |
| `provision_handler_done`               | 3       | A teardown handler completed; `detail` is the action (`destroy`)                         |
| `provision_handler_warn`               | 3       | Handler skipped (no `destroy` script); `detail` is the warning message                   |
| `provision_teardown_finished`          | 3       | All teardown subtargets done; `detail` is `"ok"` or `"error"`                            |
| `would_provision_teardown`             | dry-run | Handler that would run; `detail` is `destroy: <script>`                                  |
| `worktree_removed`                     | 5       | A per-repo worktree was removed                                                          |
| `env_removed`                          | 6       | The env directory was removed                                                            |
| `workspace_excludes_updated`           | 6       | The `winter-dir/<env>` block was stripped from the exclude file                          |
| `would_remove_worktree`                | dry-run | Worktree that would be removed                                                           |
| `would_remove_env`                     | dry-run | Env directory that would be removed                                                      |
| `would_remove_workspace_exclude`       | dry-run | Exclude block that would be stripped                                                     |
