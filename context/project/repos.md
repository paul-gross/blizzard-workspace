# The repo inventory

| Repo               | Role                                                                                                                                                        |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `blizzard`         | The main application — hub, runner, CLI, web board.                                                                                                         |
| `blizzard-context` | The blizzard conventions harness — worktreed for editing, and installed as an extension (`.winter/ext/context`) so its rules load into every agent context. |
| `blizzard-usage`   | Product usage guidance, installed at `.winter/ext/usage`. Workspace-specific deployment bindings stay in [blizzard-binding.md](./blizzard-binding.md).      |
| `blizzard-mock`    | The mock fleet: mock coding harnesses, mock forge, mock hub/runner, mock-data CLI.                                                                          |
| `winter-planning`  | The suite's product intent; blizzard's is under `projects/fleet/`. Worktreed for amending blizzard's epics; read at `.winter/ext/planning`. See below.      |
| `winter-strategy`  | The platform's strategic direction — product structure, naming, brand, presentation. One copy at `.winter/ext/strategy`, never worktreed.                   |

**A fleet worker amends `winter-planning` only where its chunk's build departs from it.** When the epic the chunk builds
has a spec, slice, or engineering requirement the work proves wrong, incomplete, or stale, the chunk commits the
correction in its feature env's `winter-planning` worktree, in the same chunk as the build, following
[winter-context-planning's contributing rules](../../.winter/ext/planning-context/context/contributing.md). Read it to
plan at `.winter/ext/planning`; never edit that copy.

- **Only under `projects/fleet/`.** The repo is suite-wide; every other project's intent belongs to that project.
- **Only the epic the chunk builds**, plus a stale citation elsewhere under `projects/fleet/` the work itself moved.
- **Never product intent or its standing**: the charter, milestones, product requirements, decisions, an epic's
  refinement grade, or its status. When the work shows one of those is wrong, name the change in the chunk's
  retrospective for the operator to make.

The winter-* extensions (`winter-canon`, `winter-github`, `winter-workflow`, `winter-service-docker`,
`winter-service-process-compose`) are the same repos that serve the winter workspace, installed under `.winter/ext/` —
see the `# Winter Extensions` block in the workspace `CLAUDE.md`.
