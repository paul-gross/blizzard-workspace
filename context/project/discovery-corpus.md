# The discovery corpus — history, not a live artifact

The **discovery corpus** in the `blizzard-discovery` repo (product, design, decisions, implementation) is the design record blizzard was built *from*. It is declared as a project repo, so every feature environment carries a `blizzard-discovery/` worktree, and it is worth reading for background: why a thing is shaped the way it is, and what was considered and rejected.

**Do not maintain it.** It is a historical artifact, not a source of truth to keep in sync. Concretely, when you change blizzard:

- Do **not** record a new decision in `decisions/log.md`, and do not renumber or reshape the ones there.
- Do **not** update the corpus's owner docs to match new code.
- Do **not** treat a corpus statement as binding on a change. Where the corpus and the code disagree, the **code** is current and the corpus is a snapshot of what was once intended.

The `D-NNN` citations already in blizzard's code are history, and stay put — they explain why something was built a certain way. Don't add new ones.

What governs a change instead is the harness, [blizzard-context](../../.winter/ext/context/index.md).
