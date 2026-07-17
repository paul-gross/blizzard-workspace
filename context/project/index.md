# Project context — building blizzard, with winter

This workspace develops **blizzard**: an orchestration platform for autonomous fleets of coding agents — its hub, runner, CLI, and web board. Blizzard is built **with winter**, the same multi-worktree, multi-repository, agent-first development model that builds winter itself. This workspace repo is a **winter-lineage fork** carrying the blizzard-specific customization commit on top of `winter/master`.

## The discovery corpus — history, not a live artifact

The **discovery corpus** in the `blizzard-discovery` repo (product, design, decisions, implementation) is the design record blizzard was built *from*. It is declared as a project repo, so every feature environment carries a `blizzard-discovery/` worktree, and it is worth reading for background: why a thing is shaped the way it is, and what was considered and rejected.

**Do not maintain it.** It is a historical artifact, not a source of truth to keep in sync. Concretely, when you change blizzard:

- Do **not** record a new decision in `decisions/log.md`, and do not renumber or reshape the ones there.
- Do **not** update the corpus's owner docs to match new code.
- Do **not** treat a corpus statement as binding on a change. Where the corpus and the code disagree, the **code** is current and the corpus is a snapshot of what was once intended.

The `D-NNN` citations already in blizzard's code are history, and stay put — they explain why something was built a certain way. Don't add new ones.

What governs a change instead is [blizzard-harness](../../.winter/ext/harness/index.md) — see [The harness](#the-harness) below.

## The repo inventory

| Repo | Role |
|------|------|
| `blizzard` | The main application — hub, runner, CLI, web board. |
| `blizzard-harness` | The blizzard conventions harness — worktreed for editing, and installed as an extension (`.winter/ext/harness`) so its rules load into every agent context. |
| `blizzard-mock` | The mock fleet: mock coding harnesses, mock forge, mock hub/runner, mock-data CLI. |
| `blizzard-discovery` | The design corpus blizzard was built from — history, read-only, not maintained. |

The winter-* extensions (`winter-canon`, `winter-github`, `winter-workflow`, `winter-service-tmux`, `winter-service-docker`) are the same repos that serve the winter workspace, installed under `.winter/ext/` — see the `# Winter Extensions` block in the workspace `CLAUDE.md`.

## The harness

Blizzard adopts **winter-canon** as its substrate and instantiates its own harness in `blizzard-harness`, following `winter-harness` in style (domain-organized convention directories, routing hubs, a verifiability matrix, architectural guidance) but carrying **blizzard's own rules**. It governs every change: read [its hub](../../.winter/ext/harness/index.md) and route from there. Project-level delivery and commit conventions stay in [contributing.md](./contributing.md).

## Project-level conventions

| Topic | Where to read |
|-------|---------------|
| Commit format, delivery, push rules | [contributing.md](./contributing.md) |
