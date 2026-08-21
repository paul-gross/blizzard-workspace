# Project context — building blizzard, with winter

This workspace develops **blizzard**: an orchestration platform for autonomous fleets of coding agents — its hub, runner, CLI, and web board. Every change to it is governed by the harness, [blizzard-context](../../.winter/ext/context/index.md) — enter at its hub and route from there. What the workspace itself owns is below.

| Topic | When to read |
|-------|--------------|
| [repos.md](./repos.md) | You need to know which repo owns what, or what a worktree in a feature env is for |
| [discovery-corpus.md](./discovery-corpus.md) | You are reading — or tempted to update — `blizzard-discovery`, the design record blizzard was built from |
| [contributing.md](./contributing.md) | Committing, delivering, or pushing work toward `master` |
| [post-delivery.md](./post-delivery.md) | Work you were driving has reached `master`, by any delivery path |
| [local-instance.md](./local-instance.md) | Touching the live instance this workspace dogfoods — a **hosted** hub that redeploys itself from `master`, plus the local runner you redeploy by hand |
| [hub-data-modes.md](./hub-data-modes.md) | Running a feature env's board or CLI against hub data, and deciding which hub is safe to point it at |
