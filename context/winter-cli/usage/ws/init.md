# `winter ws init` — reconcile the workspace against the config

For the rest of the family, see the [`winter ws` hub](./index.md).

One idempotent command with three modes. Safe to re-run any time.

| Form                    | What it reconciles                                                     |
| ----------------------- | ---------------------------------------------------------------------- |
| `winter ws init`        | Source checkouts in `projects/` and standalone repos.                  |
| `winter ws init <name>` | The `./<name>/` feature environment.                                   |
| `winter ws init --all`  | Source checkouts, standalones, and every existing feature environment. |

Each mode applies the same per-repo reconcile steps (git identity, excludes, `cmd` list, extension processing,
pinned-repo tracking on worktrees). At the workspace level, `winter ws init` (no target) and `--all` also run a
**workspace-skill projection pass** — it reads every skill directory under `workspace_root/<skills_dir>/` (default
`skills/`) and projects it into all three per-vendor skill directories using the configured `prefix` (default `ws`),
then prunes stale `<prefix>-*` and bare `<prefix>` entries from removed skills. Projection is always-on; no explicit
`prefix` key in the config is needed. For the naming rule and full configuration surface, see
[configuration/config-files.md — workspace skill prefix](../../configuration/config-files.md#workspace-skill-prefix).
For the env-init path, init also infers and wires an upstream for non-pinned newly-added worktrees when their connected
siblings agree on one; ambiguous or divergent siblings are left for explicit `winter ws connect`. See
[worktree-ops.md](../../../worktree-ops.md) for the full step list, the pinned-repo specifics, and the
upstream-inference contract.

**Standalone repo pin behavior.** When a standalone repo has a `ref` configured (see
[configuration/repositories.md — ref](../../configuration/repositories.md#ref--standalone-repo-pins)), `init` applies
the pin during reconcile:

- **Lock present and fresh** (`entry.ref` matches config `ref`): checks out the locked commit without network access or
  re-resolution. This is the reproducible-install path — the locked commit wins even if the remote branch or tag has
  since moved.
- **Lock absent or stale** (`entry.ref` differs, or no entry): resolves `ref` against the on-disk remote refs, checks
  out the result, and writes/rewrites the lock entry for this repo. If the working tree has uncommitted changes, refuses
  with a clear error — commit or stash first. On a fresh clone the tree is always clean.

The lock file (`.winter/config.lock`) is committed alongside the workspace config; run `winter ws init` after cloning a
workspace and the correct commit will be checked out automatically with no manual ref resolution.

Greek letters (`alpha`, `beta`, …) are the conventional feature environment names. The first 10 (`alpha`…`kappa`) are
the default `env_aliases` and receive fixed indices `1..10`. Other names — remaining Greek letters or arbitrary strings
— hash into a higher index band; `winter ws init` linear-probes upward on collision, so the assigned index is stable
once written but may differ from the raw hash suggestion. `winter ws index <name>` shows what index an existing env was
assigned (persisted) or what slot a new name would be suggested (hash, before probe).

**Reserved name:** `workspace` cannot be used as a feature environment name — `winter ws init workspace` is rejected
with an error. `workspace` is a reserved service scope used by `winter service`; see
[../service.md#workspace-scope](../service.md#workspace-scope).

## Nested workspaces

For a `[[project_repository]]` declaring `nested = true` (see
[configuration/repositories.md — nested](../../configuration/repositories.md#nested--a-project-repo-that-is-itself-a-workspace)),
`winter ws init <name>` reconciles that repo's worktree in these steps:

1. Create or reuse `<name>/<repo>/`, apply identity and excludes, and run the entry's `cmd` there, without the outer
   CLI's own runtime environment (see
   [repositories.md — nested](../../configuration/repositories.md#nested--a-project-repo-that-is-itself-a-workspace)).
2. Write the delegated port and prefix keys into `<name>/<repo>/.winter/config.local.toml` — see
   [ports-and-environments.md — Nested workspaces](../../configuration/ports-and-environments.md#nested-workspaces). A
   delegation that does not fit the outer env's port band is refused here.
3. Run `winter ws init` inside `<name>/<repo>/`, streaming its output under the repo's name as `[<repo>] ...` lines.

A failing `cmd` skips steps 2 and 3, and a refused delegation skips step 3. A failing nested init (non-zero exit, or a
`winter` that resolves some workspace other than `<name>/<repo>/`) is reported as that repo's error and fails the env;
which workspace `winter` resolves is checked before step 2, so a mis-resolved one writes nothing. The nested init is
bare, so it clones the nested workspace's projects but creates none of its feature envs; create those from inside the
nested root with its own `winter ws init <nested-env>`. `winter ws init` with no target never initializes
`projects/<repo>/` as a workspace, and `--all` initializes each existing env's copy again.

## Workspace exclude file

`winter ws init` keeps its generated workspace paths (`/projects/`, feature-env directories, projected skills and
agents, extension checkouts, `.winter/config/**/*.local.*`) out of `git status` with managed blocks in the workspace
repo's exclude file. `winter ws destroy` and `winter ws prune` read and rewrite the same blocks. Where the file lives
depends on the workspace root:

- **Normal clone** — `<root>/.git/info/exclude`.
- **Linked git worktree** (the root was made with `git worktree add`) — the worktree's own `<git-dir>/info/exclude`,
  where `<git-dir>` is `git rev-parse --absolute-git-dir`. `winter ws init` also enables `extensions.worktreeConfig` and
  sets `core.excludesFile` to that file with `git config --worktree`, so the blocks apply to that worktree alone.
  Sibling worktrees of one repository, and the main checkout, never see or rewrite each other's blocks, and the shared
  `info/exclude` of the common git directory is never written. The per-worktree `core.excludesFile` replaces the
  user-level `$XDG_CONFIG_HOME/git/ignore` for that worktree.

Both settings are read first and written only when they differ, so re-running `ws init`, or running it in several
sibling worktrees at once, leaves the shared `.git/config` alone once it is set up.

When the common git directory is a **bare repository**, enabling `extensions.worktreeConfig` first moves `core.bare`
(and `core.worktree`, if set) from the shared `config` to the common directory's `config.worktree`, as
[git-worktree](https://git-scm.com/docs/git-worktree#_configuration_file) requires. Otherwise `core.bare = true` would
apply to every linked worktree and each sibling would fail with `this operation must be run in a work tree`.

`winter ws destroy` and `winter ws prune` only read the exclude file's location and never change git configuration. If
git refuses to resolve it (for example a dubious-ownership error), destroy reports the error, still finishes the env's
teardown, and exits non-zero; prune logs a warning and skips the scans that read the exclude file.

A workspace root whose git directory is relocated with `GIT_DIR` is not supported.

## Errors

- **`set-upstream-to <ref> failed at <path>: HEAD is detached`** — a worktree's tracking wiring (pinned or inferred)
  could not be applied because that worktree's HEAD is detached. This fails the repo — and therefore the env — with a
  non-zero exit; the repo's `cmd` list still runs regardless (upstream wiring is isolated from bootstrap). Re-attach the
  branch with `winter ws checkout <name> <feature-branch>` (whole env) or `winter ws reset <name>/<repo> <ref>` (single
  worktree), then re-run `winter ws init <name>`.
