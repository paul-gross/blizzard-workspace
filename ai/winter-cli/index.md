# Winter CLI

The `winter` command is a workspace-level tool for managing worktrees and repositories. It reads configuration from `.winter/config.toml` and operates across all repos in the workspace.

## What to read

- **Running commands?** → [usage.md](./usage.md). Command reference, common workflows, drift warnings, and when to use the CLI vs raw git.
- **Running preflight checks?** → [usage.md#doctor](./usage.md#doctor). `winter doctor` reports pass / warn / fail across core probes (git, python, config, repos, envs), an optional workspace probe (`.winter/config.toml`'s `doctor` field), and each installed extension's contributed probes.
- **Running convention checks?** → [usage.md#lint](./usage.md#lint). `winter lint` dispatches to lint scripts contributed by the workspace and installed extensions, runs the applicable ones over a scope (a repo, an env, `--all`, or `--changed`), and aggregates `pass` / `warn` / `fail` findings with `file:line`. It owns dispatch only — the checks live in the extensions.
- **Installing or configuring?** → [setup.md](./setup.md). Installation, `.winter/config.toml` schema, local overlay, and extensions.
- **Authoring a TUI plugin?** → `winter-harness:/python/plugin-author.md`. How to extend the `winter` dashboard from a `plugin.py` — contributing dashboard badges, TUI screens, and keybound actions.
