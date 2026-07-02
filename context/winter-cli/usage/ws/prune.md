# `winter ws prune` — remove orphaned disk state

For the rest of the family, see the [`winter ws` hub](./index.md).

`winter ws prune` finds and removes state for repos no longer in the workspace config:

- Orphan project clones under `projects/`.
- Orphan standalone clones referenced by stale entries in `.git/info/exclude`.
- Broken symlinks under `.claude/skills/` and `.codex/skills/`.
- Orphaned rendered agent copies under `.claude/agents/`, `.codex/agents/`, and `.opencode/agent/` whose source extension was removed from the workspace config (an agent whose source extension is still installed but had one agent deleted is instead flagged by `winter doctor`'s `agent copies: <vendor>` probe and cleaned up on the next `winter ws init`).

Refuses to delete repos with uncommitted changes or attached worktrees. Use `--dry-run` to preview, `--force` to skip the interactive confirmation.
