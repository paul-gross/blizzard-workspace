# `winter ws update` — re-pin standalone repos and rewrite the lock

Explicitly re-resolves `ref` pins for standalone repos, checks out the resolved commit, and rewrites
`.winter/config.lock`. With `--freeze`, it instead
[pins every unpinned standalone to its current checkout](#freezing-the-workspace----freeze). For the rest of the family,
see the [`winter ws` hub](./index.md).

This is the **only path** that moves a tag/commit pin or snaps a branch pin to the latest origin tip on demand. It
surfaces the change as a reviewable `git diff` against the committed lock file — making pin bumps deliberate and
auditable.

## Usage

```text
winter ws update [REPOS]... [--autostash] [--json]
winter ws update --freeze [REPOS]... [--force] [--json]
```

| Form                                   | What it does                                                                                                        |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `winter ws update`                     | Re-pins **all** pinned standalone repos (those with a `ref` in the config)                                          |
| `winter ws update <name>`              | Re-pins only the named standalone repo                                                                              |
| `winter ws update <name> <other-name>` | Re-pins exactly those two standalone repos                                                                          |
| `winter ws update '<glob>'`            | Re-pins every pinned standalone repo whose name matches the glob (e.g. `'winter-*'`)                                |
| `winter ws update --autostash`         | Allows re-pin when the working tree is dirty: stash → checkout → pop                                                |
| `winter ws update --freeze [REPOS]...` | Pins every matched **unpinned** standalone to its current checkout — see [below](#freezing-the-workspace----freeze) |

Each `REPO` is a bare glob over standalone-repo names — there is no `<env>/<repo>` segment (standalone repos aren't
scoped to an env), so a name containing `/` is rejected. Without `--freeze`, a literal name that doesn't match a pinned
standalone raises a clear error (see [Errors](#errors)); a glob matching zero pinned standalones is a no-op; and repos
without a `ref` are ignored regardless of scope. Under `--freeze` the selection differs: it is the unpinned standalones
that are acted on, and a literal name may name any standalone (see [`--freeze`](#freezing-the-workspace----freeze)).

## What each step does

For each in-scope repo `update`:

1. **Fetches** `origin` so `resolve_ref` sees current remote refs.
2. **Dirty guard** — if the working tree is not clean and `--autostash` is not set, emits a per-repo failure and
   continues the fan-out. With `--autostash`, stashes → checks out → pops.
3. **Resolves** the `ref` string against the freshly-fetched refs in order: `refs/remotes/origin/<ref>` (branch) →
   `refs/tags/<ref>` (tag) → `<ref>^{commit}` (raw SHA).
4. **Up-to-date check** — if the resolved commit equals the current HEAD and the lock already records the same commit,
   reports `up to date` with no checkout and no lock churn.
5. **Checks out** the resolved commit (detached HEAD for tag/commit, tracking branch for branch) and **rewrites the
   lock** entry for this repo — preserving all other repos' entries.
6. **Unresolvable ref** → emits a per-repo failure; continues fan-out for remaining repos.

## Outcomes

| Outcome               | JSON `result` | Meaning                                                                                                                                                              |
| --------------------- | ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `up to date`          | `up_to_date`  | Resolved commit matches current HEAD and lock; nothing to do                                                                                                         |
| `re-pinned → <sha>`   | `re_pinned`   | HEAD moved; lock rewritten with new 8-char SHA prefix                                                                                                                |
| `pin error: <detail>` | `pin_error`   | The re-pin operation could not run: dirty working tree without `--autostash`, unresolvable ref, stash failure, or checkout error. Fan-out continues for other repos. |
| `diverged: +N/-M`     | `diverged`    | Branch pin refused because origin diverged from local history (branch-pin pull path only; not emitted by `update`).                                                  |

## Freezing the workspace — `--freeze`

A workspace's definition is its own commit plus the commit of every standalone it installs, but only standalones with a
`ref` are pinned; the rest float at whatever their checkout last pulled. `--freeze` closes that gap in one reviewable
diff:

```bash
winter ws update --freeze                        # pin every unpinned standalone
winter ws update --freeze 'winter-*'             # ...only the matching ones
winter ws update --freeze my-lib --force         # ...even though my-lib has uncommitted changes
git -C <workspace-root> diff .winter             # config.toml gains ref lines; config.lock gains entries
```

For each matched standalone (`REPOS` matches exactly as it does without `--freeze`, except a literal name may name any
standalone — pinned or not):

- **No `ref`** → `ref = "<full HEAD sha>"` is written into `.winter/config.toml` and the commit is recorded in
  `.winter/config.lock` (`kind = "commit"`). Comments, layout, and ordering in the file are preserved; a new `ref` line
  lands directly under the repo's last key. A repo declared only in `.winter/config.local.toml` is written there
  instead.
- **Already has a `ref`** → left alone. Neither the config nor the lock entry changes.
- **Dirty working tree** → refused, naming the repo, because its commit does not describe it. `--force` pins the repo's
  `HEAD` anyway; the uncommitted changes are not part of what gets pinned.

Freezing reads the checkout only: it does not fetch, check anything out, or require the commit to exist on origin — a
commit that has not been pushed pins fine (a fresh clone of the workspace cannot resolve it until it is pushed). Project
repositories are never pinned; they follow feature branches. To move an existing pin, run `ws update` without
`--freeze`. `--force` requires `--freeze`, and `--autostash` does not combine with it.

### Outcomes under `--freeze`

| Outcome                  | JSON `result`    | Meaning                                                                              |
| ------------------------ | ---------------- | ------------------------------------------------------------------------------------ |
| `pinned → <sha>`         | `pinned`         | `ref` written to the config and lock; `pin_ref` carries the 8-char SHA prefix        |
| `already pinned @ <ref>` | `already_pinned` | The repo already has a `ref`; nothing written. `pin_ref` carries that `ref`          |
| `refused: <detail>`      | `refused`        | The repo was not pinned. `pin_ref` carries the reason, naming the repo. Causes below |

A `refused` outcome has one of these causes:

- a dirty working tree without `--force`;
- a checkout that is missing on disk (not cloned — run `winter ws init`);
- a directory that is not a git repository, or whose `HEAD` cannot be read — refused with or without `--force`;
- a repo not declared in `config.toml` or `config.local.toml`, so there is nowhere to write its `ref`.

A refusal does not stop the fan-out — the other repos are still pinned — but the command exits non-zero, in `--json`
mode too.

## Reviewable lock diff workflow

After `winter ws update` moves a pin, the change appears in `git -C <workspace-root> diff .winter/config.lock` — a
clean, reviewable record of exactly which commit each pinned repo was bumped to. Commit the lock alongside any other
workspace changes to make the bump deliberate and reproducible across machines.

```bash
winter ws update                                 # re-pin all
git -C <workspace-root> diff .winter/config.lock # review what moved
git -C <workspace-root> add .winter/config.lock
git -C <workspace-root> commit -m "chore: bump standalone pins"
```

See [worktree-ops.md](../../../worktree-ops.md) for why `<workspace-root>` is an absolute path, not a trusted cwd, and
how to resolve it.

## Errors

- **`standalone repo '<name>' has no \`ref\` configured`** — the named repo exists but is not pinned; nothing to update.
- **`no pinned standalone repo named '<name>'`** — the name doesn't match any standalone repo in the config.
- **`refusing to re-pin '<name>': uncommitted changes`** — dirty working tree; commit or stash manually, or pass
  `--autostash`.
- **`no standalone repo named '<name>'`** (`--freeze`) — the name doesn't match any standalone repo in the config.
- **`refused: '<name>' has uncommitted changes, so its commit does not describe it; commit/stash or pass --force`**
  (`--freeze`) — commit or stash, or pass `--force` to pin the repo's committed `HEAD` anyway.
- **Unresolvable ref** — the `ref` string in the config didn't match any branch, tag, or commit in the local ref store
  after the fetch. Run `winter ws fetch <name>` to double-check connectivity, then re-run.

See also: [configuration/repositories.md — `ref`](../../configuration/repositories.md#ref--standalone-repo-pins) for the
full pin semantics and lock schema; [`winter ws pull`](./pull.md) for automatic branch-pin advances during a pull.
