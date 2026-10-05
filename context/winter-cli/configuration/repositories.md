# Repositories

Repositories are declared in `.winter/config.toml`. **Project repositories** are cloned to `projects/` and worktreed
into the Greek-letter env dirs; **standalone repositories** are cloned once at the workspace root with no worktree and
no feature branching.

## Project & standalone repositories

```toml
# Entries appended to every repo's .git/info/exclude on `winter ws init`.
git_excludes = ["*.Local.csproj"]

# Project repositories — cloned to projects/ and worktreed into Greek-letter dirs.
# Entries appear in CLI and TUI output in the order they're listed here, so put
# high-priority repos first.
[[project_repository]]
name = "web"                               # directory name under projects/ — overrides what would be derived from `url`
url = "git@example.com:org/winter-app.git"
cmd = ["pnpm install"]                     # run after clone and in every worktree
git_excludes = [".env.development.local"]  # per-repo excludes, merged with workspace-wide

[[project_repository]]
name = "api"
url = "git@example.com:org/winter-api.git"
main_branch = "development"        # per-repo override of the top-level main_branch
cmd = ["dotnet restore"]

# Pinned repos always track origin/main and are skipped during feature branching.
[[project_repository]]
name = "shared-tools"
url = "git@example.com:org/shared-tools.git"
pinned = true

# Standalone repositories — cloned to the workspace root (or `path`), no worktree,
# no feature branching. Useful for winter extensions (skills/agents) and any repo
# you want available alongside project repos but not multiplied per-feature.
[[standalone_repository]]
name = "winter-backlog"
url = "git@github.com:user/winter-backlog.git"
prefix = "wsb"                                # optional symlink-prefix override; see extensions.md
path = "extensions/winter-backlog"            # optional; relative to the workspace root, defaults to `name`
ref = "v1.4.2"                                # optional; pin this repo to a branch, tag, or commit SHA
config_dir = ".winter/config/winter-backlog"  # optional; override where WINTER_EXT_CONFIG_DIR points

# A repo cloned only as data — a mirror, a corpus, a fixture — says so, and may pick its own context.
[[standalone_repository]]
name = "blizzard-workspace"
url = "git@github.com:paul-gross/blizzard-workspace.git"
path = "mirrors/blizzard-workspace"
extension = false                             # optional; the repo is data, never an extension
# load = "lazy"                               # optional; "eager" | "lazy" | "none" — see below
# entry = ["docs/agents.md", "index.md"]      # optional; prioritized entry-point candidates
```

The workspace-wide `git_excludes` list is appended to every repo's `.git/info/exclude` on `winter ws init`; a per-repo
`git_excludes` merges with it.

## `extension`, `load`, `entry` — the workspace governs a repo's extension role

Whether a repo acts as an extension, and what it injects into the workspace's agent context, is the workspace's call.
Three optional keys on a `[[standalone_repository]]` or `[[project_repository]]` entry take precedence over what the
repo's own `winter-ext.toml` declares; [extensions.md — Context delivery](./extensions.md#context-delivery) owns the
precedence order, how `load` and `entry` resolve, and which repos they opt into context delivery.

| Key         | Value                         | Effect                                                                                                                                                                             |
| ----------- | ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `extension` | `false`                       | The repo is data. Winter still clones, fetches, pulls, and pins it (including `ref` and `config.lock`), but no extension feature sees it, whatever its `winter-ext.toml` declares. |
| `load`      | `"eager"`, `"lazy"`, `"none"` | Replaces the manifest's `load` for the repo's `AGENTS.winter.md` bullet. `none` renders no bullet and leaves every other extension feature in place.                               |
| `entry`     | non-empty list of repo paths  | Prioritized entry-point candidates: the first that exists is the one entry point, and the list replaces the default `index.md`, `AGENTS.md`, `context/index.md` entirely.          |

- **`extension = false`** removes the repo from every extension consumer at once: `AGENTS.winter.md`, skill and agent
  projection, hooks, provision handlers, services, capabilities, `winter doctor` probes, lint scripts, and the
  `winter graph` node. What an earlier `winter ws init` projected for it (skills, agent copies, its `AGENTS.winter.md`
  bullet) is retracted on the next run, except under a prefix a live extension still claims; the clone is kept. On a
  repo declared as both a project and a standalone, `extension = false` on either entry excludes it.
- **`load`** and **`entry`** govern context delivery only. An explicit `load = "eager"` or `"lazy"`, or an explicit
  `entry`, on a repo with no `winter-ext.toml` is an opt-in to context delivery and to nothing else — on a
  `[[project_repository]]` it adds a lazy routing row to `AGENTS.winter.md` and never projects skills, agents, or hooks.
- On a repo declared as both a project and a standalone, the standalone entry's `load` and `entry` win; the project
  entry's fill whatever the standalone leaves unset.

Validation fails config load with a `ConfigError` for an `extension` that is not a boolean, an unknown `load`, an empty
or non-list `entry`, and an `entry` path that is empty, absolute, or holds a `..` segment.

## `nested` — a project repo that is itself a workspace

`nested = true` on a `[[project_repository]]` declares that the repo is itself a winter workspace. Winter worktrees it
into each env like any project repo, then drives each env's copy as a workspace in its own right:

```toml
[[project_repository]]
name = "lab"
url = "git@example.com:org/lab-workspace.git"
nested = true
envs = 3                                                # optional; usable nested feature envs
cmd = ["<bootstrap the nested workspace's toolchain>"]  # whatever it needs before its own CLI can run
```

| Key             | Value             | Effect                                                                                                                                                                                                                |
| --------------- | ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `nested`        | `true`            | The repo is itself a workspace, initialized, reported, and destroyed inside each env's worktree of it.                                                                                                                |
| `envs`          | integer `>= 1`    | The number of usable feature envs the nested workspace holds; what winter writes from it is in [ports-and-environments.md — Nested workspaces](./ports-and-environments.md#nested-workspaces). Needs `nested = true`. |
| `inherit_local` | list of key names | The top-level keys or tables of the outer `config.local.toml` copied into the nested workspace's `.winter/config.local.toml`. Defaults to `["git"]`; `[]` copies nothing. Needs `nested = true`.                      |

- **`winter ws init <env>`** runs the entry's `cmd` in `<env>/lab/`, then initializes the nested workspace there — see
  [ws init — Nested workspaces](../usage/ws/init.md#nested-workspaces).
- **`winter ws destroy <env>`** destroys every feature env the nested workspace holds and stops its workspace-scope
  services before it tears down the outer env, and refuses while that would lose work — see
  [ws destroy — Nested workspaces](../usage/ws/destroy.md#nested-workspaces).
- **`winter ws status`** shows each nested workspace's env count, whether it is dirty, and whether it holds unpushed
  work, and a dirty one counts as a dirty worktree — see
  [ws status — Nested workspaces](../usage/ws/status.md#nested-workspaces).
- **Never an extension.** `nested = true` implies `extension = false`, so the repo's root `AGENTS.md`,
  `winter-ext.toml`, skills, and agents reach nothing in the outer workspace.
- **The source checkout stays plain.** `projects/lab/` is cloned, and its `cmd` runs there as for any project repo, but
  it is never initialized as a workspace — only the env copies are.

**`inherit_local`** names top-level keys or tables of the outer `.winter/config.local.toml`; what is copied, and how, is
owned by [config-files.md — Local overlay](./config-files.md#local-overlay-winterconfiglocaltoml) ("What reaches a
nested workspace"). A name must be a non-empty top-level key without a `.`, so `["git"]` names the whole `[git]` table;
a value that is not a list, any other name, or the key on a repo without `nested = true` is a `ConfigError`.

**Which `winter` runs.** Every nested call runs a `winter` from `PATH` with its working directory at the nested root,
and that `winter` must resolve the nested root as its workspace. Winter verifies that it does: before the first call
into a nested root, it checks that the root holds `.winter/config.toml` and that its `winter ws status --json` reports
that same root as `workspace.root_path`; later calls into that root in the same run reuse the check. A `winter` that
resolves any other workspace, such as the outer one, is refused and nothing else runs. The child runs without the outer
CLI's own runtime environment: none of the outer CLI's `WINTER_*` variables except `WINTER_LOG_LEVEL` and
`WINTER_OTEL_*`, and nothing the outer CLI's installation put in the environment to run itself.

**Recursion stops at a mis-resolved winter.** Every nested call sets `WINTER_NESTED_CHAIN` to the caller's own chain
plus the caller's workspace root. A winter whose own root is already on its chain was started by a nested call but
resolved an enclosing workspace instead of the nested one, so it refuses every nested call of its own: it costs one
status read rather than fanning out to every sibling nested worktree. A call into a root already on the chain is refused
the same way. A correctly resolved nested workspace is not on its own chain, so it reads and drives the workspaces
nested inside it in turn.

**The entry's `cmd` runs scrubbed too.** For a `nested = true` entry, each `cmd` runs with the same environment as a
nested call, minus `WINTER_NESTED_CHAIN`, so the bootstrap sees the environment the nested workspace's own CLI will run
in, not the outer CLI's runtime environment.

**Gitignore the nested workspace's local files.** The nested workspace's exclude file keeps `projects/`, its feature-env
directories, and its projected skills and agents out of `git status`. The nested repo must gitignore every other file
the nested workspace's `ws init` and CLI install write at its root, or the outer worktree reads as dirty and
`ws destroy` refuses. Winter's own are:

```gitignore
/.winter/config.local.toml
/.winter/state.toml
/AGENTS.winter.md
```

Add whatever the nested workspace's CLI install writes there besides.

Validation fails config load with a `ConfigError` for a `nested` that is not a boolean, for `nested = true` on a
`[[standalone_repository]]` (a standalone is one checkout shared by the whole workspace, not one per env), and for
`nested = true` together with `load`, `entry`, or `extension = true`.

## `config_dir` — per-extension writable config/asset directory

The optional `config_dir` field overrides where winter stores and exports this extension's writable config/asset
directory. When omitted, winter defaults to `.winter/config/<name>/` relative to the workspace root.

The resolved absolute path is exported as `WINTER_EXT_CONFIG_DIR` on every dispatch to this extension (service, doctor,
lint, and hooks). Extensions use this directory to read their workspace-level config files (e.g. `config.toml`), write
generated assets, or store per-workspace state that should be git-tracked alongside the workspace config.

`config_dir` must be a relative path under the workspace root with no `..` segments (the same guard applied to `path`).
Absolute paths are rejected. An optional `config.local.toml` overlay convention within this directory is typically
git-excluded by the workspace's blanket exclude for `*.local.*` files.

## `ref` — standalone repo pins

The optional `ref` field pins a standalone repo to a branch, tag, or commit SHA. Winter resolves `ref` against the
fetched remote refs in this order: `refs/remotes/origin/<ref>` (branch) → `refs/tags/<ref>` (tag) → `<ref>^{commit}`
(raw SHA). First match wins; no match → unresolvable-ref error (run `winter ws fetch <name>` to refresh refs).

| `ref` value       | Behavior                                                                                                            | Lock behavior                                    |
| ----------------- | ------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| absent            | Today's behavior: clone tracks the default branch; `pull` integrates the tracked upstream                           | No lock entry written                            |
| branch name       | Checkout on that tracking branch (`main_branch` effectively set to `<ref>`); `pull` fast-forwards to `origin/<ref>` | Lock written; rewritten on each `pull` advance   |
| tag or commit SHA | Detached checkout held exactly at the resolved commit; `pull` **never** advances it                                 | Lock written; only updated by `winter ws update` |

To pin every unpinned standalone to its current checkout, see
[`winter ws update --freeze`](../usage/ws/update.md#freezing-the-workspace----freeze).

**`ref` vs `pinned` vs `main_branch`** — three distinct concepts that are easy to conflate:

- **`pinned`** (`[[project_repository]]` only, UNRELATED) — means "exclude this *project* repo from feature branching
  entirely." The term is not reused on standalone repos; standalone repos have no `pinned` field.
- **`main_branch`** — the standalone repo's integration target / tracking branch when `ref` is absent or is a branch
  name.
- **`ref`** (new, `[[standalone_repository]]` only) — the pin intent: which branch, tag, or commit to lock the checkout
  to.

### Lock file (`.winter/config.lock`)

When any standalone repo has a `ref`, winter maintains `.winter/config.lock` at the workspace root. This file records
the resolved commit per pinned repo and is **intentionally committed** to the workspace repo — committing it makes the
pin reproducible across machines and surfaces pin updates as reviewable `git diff`.

```toml
# .winter/config.lock — managed by winter; commit this file.
version = 1

[[standalone]]
name   = "winter-backlog"   # matches [[standalone_repository]].name
ref    = "v1.4.2"           # intent string copied from config (mismatch = stale lock)
kind   = "tag"              # "branch" | "tag" | "commit"
commit = "9f3c1ab2e4d5c6f7089a1b2c3d4e5f60718293a4"  # full 40-char SHA
```

- Repos without a `ref` get **no entry**. Entries are sorted by `name` for stable diffs.
- A mismatch between the lock's `ref` and the config's `ref` marks the lock as stale; `winter ws init` or
  `winter ws update` re-resolves and rewrites it.
- The lock is **not** added to `.gitignore` or `.git/info/exclude` by any winter command — it is committed alongside the
  config.

**What rewrites the lock:**

| Command                     | Condition                  | Action                                                                       |
| --------------------------- | -------------------------- | ---------------------------------------------------------------------------- |
| `winter ws init`            | Lock absent or stale       | Resolves `ref`, checks out, writes lock                                      |
| `winter ws init`            | Lock present and fresh     | Checks out locked commit; no rewrite                                         |
| `winter ws pull`            | Branch `ref` fast-forwards | Checks out new tip, rewrites lock                                            |
| `winter ws pull`            | Tag / commit `ref`         | Held; lock unchanged                                                         |
| `winter ws update`          | Always (explicit re-pin)   | Fetches, re-resolves, checks out, rewrites                                   |
| `winter ws update --freeze` | Repo has no `ref`          | Writes `ref` to config; lock entry (`kind = "commit"`) for HEAD; no checkout |

## Display names and ordering

`name` doubles as the directory under `projects/` and as the user-facing label everywhere a repo is shown (grid columns,
status tables, sync/push/diff headers). When `name` is omitted, it's derived from the trailing path segment of `url`
(with `.git` stripped). Set `name` explicitly when you want a friendlier label than the canonical repo name.

Repos appear in CLI tables and the TUI grid in the order they're declared in `.winter/config.toml`. Put the repos you
work with most often at the top.

## Implicit repositories

The `workspace` repo is discovered implicitly — it doesn't appear in `[[project_repository]]` or
`[[standalone_repository]]`. Winter detects it from the filesystem: the workspace itself is the repo this CLI is invoked
from.
