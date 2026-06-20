# `winter ws init` — reconcile the workspace against the config

For the rest of the family, see the [`winter ws` hub](./index.md).

One idempotent command with three modes. Safe to re-run any time.

| Form | What it reconciles |
|------|--------------------|
| `winter ws init` | Source checkouts in `projects/` and standalone repos. |
| `winter ws init <name>` | The `./<name>/` feature environment. |
| `winter ws init --all` | Source checkouts, standalones, and every existing feature environment. |

Each mode applies the same per-repo reconcile steps (git identity, excludes, `cmd` list, extension processing, pinned-repo tracking on worktrees). See [worktree-ops.md](../../../worktree-ops.md) for the full step list and the pinned-repo specifics.

Greek letters (`alpha`, `beta`, …) are the conventional feature environment names. The first 10 (`alpha`…`kappa`) are the default `env_aliases` and receive fixed indices `1..10`. Other names — remaining Greek letters or arbitrary strings — hash into a higher index band; `winter ws init` linear-probes upward on collision, so the assigned index is stable once written but may differ from the raw hash suggestion. `winter ws index <name>` shows what index an existing env was assigned (persisted) or what slot a new name would be suggested (hash, before probe).

**Reserved name:** `workspace` cannot be used as a feature environment name — `winter ws init workspace` is rejected with an error. `workspace` is a reserved service scope used by `winter service`; see [../service.md#workspace-scope](../service.md#workspace-scope).
