# Project context — building blizzard, with winter

This workspace develops **blizzard**: an orchestration platform for autonomous fleets of coding agents — its hub,
runner, CLI, and web board. Every change to it is governed by the harness,
[blizzard-context](../../.winter/ext/context/index.md) — enter at its hub and route from there. What the workspace
itself owns is below.

**Do not develop against the hosted hub, `https://blizzard.grosscode.net`** — it is the real, public fleet, and
`ng serve` is a real client against it. [hub-data-modes.md](./hub-data-modes.md) owns the safe alternatives.

**A fleet worker never edits `blizzard-product`, and never plans an edit to it** — it reads it to plan, and names a
change it needs for the operator. [repos.md](./repos.md) owns why.

| Topic                                            | When to read                                                                                                                                                                                                                                                                          |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [repos.md](./repos.md)                           | You need to know which repo owns what, or what a worktree in a feature env is for                                                                                                                                                                                                     |
| [discovery-corpus.md](./discovery-corpus.md)     | You are reading — or tempted to update — `blizzard-discovery`, the design record blizzard was built from                                                                                                                                                                              |
| [contributing.md](./contributing.md)             | Committing, delivering, or pushing work toward `master`                                                                                                                                                                                                                               |
| [post-delivery.md](./post-delivery.md)           | Work you were driving has reached `master`, by any delivery path                                                                                                                                                                                                                      |
| [singleton-instance.md](./singleton-instance.md) | Touching the singleton instance this workspace dogfoods — a **hosted** hub and one runner host on the machine `xps`, both pinned to one build — a release or a dogfood build — and moved by hand — deciding whether this checkout can reach its runner host, or minting chunks        |
| [hub-data-modes.md](./hub-data-modes.md)         | Running a feature env's board or CLI against hub data, and deciding which hub is safe to point it at                                                                                                                                                                                  |
| [workflows/](./workflows/index.md)               | Cutting a release or moving a deployment onto one, feeding the dog onto a `master` commit, running the findings sweep, reviewing garden proposals, tending the garden, marshalling the backlog or minting a chunk, or folding the store trees — the passes an operator drives by hand |
