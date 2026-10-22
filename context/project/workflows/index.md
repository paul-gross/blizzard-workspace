# Operator workflows

The passes an operator-driven session carries out by hand against blizzard and its instance — cutting and deploying a
release, draining the hub's findings, marshalling the backlog, folding the store — never a fleet worker's work. Each
workflow owns its choreography; the facts a step acts on stay with their owners, pointed to. How a feature reaches
`master`, judging a break, and the code-restructuring procedures are the fleet's, and stay with
`blizzard-context:/workflows/index.md`. Parent hub: [../index.md](../index.md).

| File                                     | When to read                                                                                                                                                |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [release.md](./release.md)               | Cutting a release — tagging a candidate at its cut commit, or promoting it to the stable tag — from a proven-green commit to a published wheel and image    |
| [release-deploy.md](./release-deploy.md) | Moving a deployment — a hub and its runner hosts — onto a release, or back off one, forward or back, with each half's proof                                 |
| [findings-sweep.md](./findings-sweep.md) | Running the findings sweep — the recurring operator pass that re-verifies the hub's live findings and drafts them into proposals or withdrawals             |
| [marshalling.md](./marshalling.md)       | Told to marshal the backlog, or minting a chunk by any path — turning resting chunks into ordered, claimable work                                           |
| [store-fold.md](./store-fold.md)         | Told to fold the store trees, or when a train is due a baseline — replacing the migration history up to the oldest supported release with one revision each |
