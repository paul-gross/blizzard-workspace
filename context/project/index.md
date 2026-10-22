# Project context — building blizzard, with winter

This workspace develops **blizzard**: an orchestration platform for autonomous fleets of coding agents — its hub,
runner, CLI, and web board. Every change to it is governed by the harness,
[blizzard-context](../../.winter/ext/context/index.md) — enter at its hub and route from there. What the workspace
itself owns is below.

| Topic                                        | When to read                                                                                                                                                                                     |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [repos.md](./repos.md)                       | You need to know which repo owns what, or what a worktree in a feature env is for                                                                                                                |
| [discovery-corpus.md](./discovery-corpus.md) | You are reading — or tempted to update — `blizzard-discovery`, the design record blizzard was built from                                                                                         |
| [contributing.md](./contributing.md)         | Committing, delivering, or pushing work toward `master`                                                                                                                                          |
| [post-delivery.md](./post-delivery.md)       | Work you were driving has reached `master`, by any delivery path                                                                                                                                 |
| [local-instance.md](./local-instance.md)     | Touching the live instance this workspace dogfoods — a **hosted** hub and the local runner host, both pinned to one build — a release or a dogfood build — and moved by hand — or minting chunks |
| [hub-data-modes.md](./hub-data-modes.md)     | Running a feature env's board or CLI against hub data, and deciding which hub is safe to point it at                                                                                             |
| [proposal-review.md](./proposal-review.md)   | Told to run a proposal review, or to go through, triage, or decide the hub's open garden proposals                                                                                               |
| [feeding-the-dog.md](./feeding-the-dog.md)   | Told to run feeding-the-dog, or to move the instance onto a `master` commit rather than a release                                                                                                |
| [workflows/](./workflows/index.md)           | Cutting a release or moving a deployment onto one, running the findings sweep, marshalling the backlog or minting a chunk, or folding the store trees — the passes an operator drives by hand    |
