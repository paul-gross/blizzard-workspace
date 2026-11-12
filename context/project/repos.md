# The repo inventory

| Repo               | Role                                                                                                                                                        |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `blizzard`         | The main application — hub, runner, CLI, web board.                                                                                                         |
| `blizzard-context` | The blizzard conventions harness — worktreed for editing, and installed as an extension (`.winter/ext/context`) so its rules load into every agent context. |
| `blizzard-usage`   | Product usage guidance, installed at `.winter/ext/usage`. Workspace-specific deployment bindings stay in [blizzard-binding.md](./blizzard-binding.md).      |
| `blizzard-mock`    | The mock fleet: mock coding harnesses, mock forge, mock hub/runner, mock-data CLI.                                                                          |
| `blizzard-product` | Live product intent — charter, epics, plans. One copy at `.winter/ext/product`, never worktreed. The operator's alone: see below.                           |

**A fleet worker never edits `blizzard-product`**, and never plans an edit to it. The hub holds no repository record for
it, so a chunk cannot declare a commit there, and a worktree of it a worker makes by hand strands its changes outside
delivery. Read it to plan; when the work shows product intent is stale or wrong, name the change in the chunk's
retrospective for the operator to make.

The winter-* extensions (`winter-canon`, `winter-github`, `winter-workflow`, `winter-service-docker`,
`winter-service-process-compose`) are the same repos that serve the winter workspace, installed under `.winter/ext/` —
see the `# Winter Extensions` block in the workspace `CLAUDE.md`.
