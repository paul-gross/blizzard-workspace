# Winter CLI — Install

Installing the `winter` CLI.

## Installation

```bash
./tools/winter-cli/install.sh
```

This copies the `winter` wrapper to `~/.local/bin/`. The wrapper auto-discovers the workspace root by searching upward for `.winter/config.toml` + `tools/winter-cli/`, then runs via `mise` and `uv` — no manual virtualenv setup needed.

## Bootstrap order

On a fresh clone, run the skills in this order:

1. **Install the CLI** — `./tools/winter-cli/install.sh`
2. **Run `/ws-setup`** — clones repos, wires worktrees, and performs first-time workspace setup. `/ws-setup` is a winter-core skill committed to the repo and available immediately after cloning.
3. **Run `winter ws init`** — reconciles the workspace against the config on subsequent runs

The core skills (`/ws-setup`, `/ws-init`, and others) are committed to `.claude/skills/` and are available immediately on a fresh clone — no projection step is needed to access them.

### Workspace-authored skills (projection)

In addition to the committed core skills, the CLI supports *workspace-authored skills* — skills you write in a `skills/` directory at the workspace root and project into per-vendor skill directories via `winter ws init`. This is an opt-in feature controlled by the top-level `prefix` key in `.winter/config.toml`. See [configuration/config-files.md](./configuration/config-files.md#workspace-skill-prefix) for the key reference.

**Precondition for skill projection:** The workspace `skills/` directory must exist and contain your authored skill directories. The committed `.claude/skills/<prefix>-*` entries (if any) must be `git rm`-ed before setting `prefix`, or `winter ws init` will error with a symlink collision (`path exists and is not a symlink`). Relocate skills into `workspace_root/skills/` and remove the committed dirs first.

With `prefix = "myprefix"` and a `skills/my-skill/SKILL.md` in the workspace root, `winter ws init` creates:

- `.claude/skills/myprefix-my-skill` (symlink, for ClaudeCode)
- `.codex/skills/myprefix-my-skill` (symlink, for Codex)
- `.opencode/skill/myprefix-my-skill/` (copy, for OpenCode)

These projected entries are generated artifacts that `winter ws init` writes and are not committed to the workspace repo.

When `prefix` is absent, workspace skill projection is skipped entirely.

### Workspace skill prefix

The top-level `prefix` key in `.winter/config.toml` names the namespace used for projected workspace skills. See [configuration/config-files.md](./configuration/config-files.md#workspace-skill-prefix) for full details and disambiguation from the per-`[[standalone_repository]]` `prefix` field.
