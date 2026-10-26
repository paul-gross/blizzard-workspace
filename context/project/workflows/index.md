# Operator workflows

The passes an operator-driven session carries out by hand against blizzard and its instance — cutting and deploying a
release, draining the hub's findings, tending the garden, marshalling the backlog, folding the store — never a fleet
worker's work. Each workflow owns its choreography; the facts a step acts on stay with their owners, pointed to. How a
feature reaches `master`, judging a break, and the code-restructuring procedures are the fleet's, and stay with
`blizzard-context:/workflows/index.md`. Parent hub: [../index.md](../index.md).

| File                                             | When to read                                                                                                                                                                          |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [release.md](./release.md)                       | Cutting a release — tagging a candidate at its cut commit, or promoting it to the stable tag — from a proven-green commit to a published wheel and image                              |
| [release-deploy.md](./release-deploy.md)         | Moving a deployment — a hub and its runner hosts — onto a release, or back off one, forward or back, with each half's proof                                                           |
| [release-train.md](./release-train.md)           | Told to run the release train, or on its day — promoting the waiting candidate or setting it aside, cutting the next minor's candidate and its branch, or cutting a further candidate |
| [release-patch.md](./release-patch.md)           | Told to patch a release line, or a fix that cannot wait for the train must reach a line already released                                                                              |
| [release-major.md](./release-major.md)           | A major's published date is four trains away or nearer — opening `master` for it, cutting its candidates, closing `master`, and promoting it or slipping the date                     |
| [feeding-the-dog.md](./feeding-the-dog.md)       | Told to run feeding-the-dog, or to move the instance onto a `master` commit rather than a release                                                                                     |
| [findings-sweep.md](./findings-sweep.md)         | Running the findings sweep — the recurring operator pass that re-verifies the hub's live findings and drafts them into proposals or withdrawals                                       |
| [proposal-review.md](./proposal-review.md)       | Told to run a proposal review, or to go through, triage, or decide the hub's open garden proposals                                                                                    |
| [tending-the-garden.md](./tending-the-garden.md) | Told to tend the garden — listing each routine's last sweep, choosing which routines to run, running them, and marshalling the chunks they mint                                       |
| [marshalling.md](./marshalling.md)               | Told to marshal the backlog, or minting a chunk by any path — turning resting chunks into ordered, claimable work                                                                     |
| [store-fold.md](./store-fold.md)                 | Told to fold the store trees, or when a train is due a baseline — replacing the migration history up to the oldest supported release with one revision each                           |
