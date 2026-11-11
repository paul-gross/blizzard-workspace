# Our proposal-review policy

Run the [shared proposal-review workflow](blizzard-usage:/context/workflows/proposal-review.md) with
[our fleet binding](../blizzard-binding.md). The shared workflow owns the session, tiers, verification, handoffs, and
approval boundaries; this page supplies Blizzard-development policy.

| Local fact        | Binding                                                                                                   |
| ----------------- | --------------------------------------------------------------------------------------------------------- |
| Verification base | Current `origin/master` in each affected repository.                                                      |
| Accepted work     | Apply [our marshalling policy](./marshalling.md), within the review's grouping and promotion approvals.   |
| Design-first work | File GitHub issues through `/wg-issue`, using [our contributing conventions](../contributing.md).         |
| Lifecycle model   | `blizzard-context:/domain/findings-and-proposals.md` owns the domain invariants for our development work. |

The [findings sweep](./findings-sweep.md) opens by deciding every open proposal through this review, or leaving it open
and keeping its findings out of that sweep's new draft. Closing a proposal does not settle its findings by itself.
