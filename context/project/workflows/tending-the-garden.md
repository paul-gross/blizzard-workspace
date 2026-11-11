# Our garden-tending policy

Run the [shared garden-tending workflow](blizzard-usage:/context/workflows/tending-the-garden.md) with
[our fleet binding](../blizzard-binding.md). The shared workflow owns the inventory, recommendations, execution, and
approval checkpoints; this page supplies Blizzard-development context.

| Local fact        | Binding                                                                                                                          |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Work ground       | Read the selected scopes against their repositories' `origin/master`.                                                            |
| Criteria          | Resolve each routine's axis through `blizzard-context:/garden/index.md`.                                                         |
| Inventory windows | Use 90 days for sweep measurements and 14 days as the initial run-list window; extend the latter to cover older unfinished runs. |
| Minted chunks     | Apply [our marshalling policy](./marshalling.md), within the shared garden-tending workflow's approvals.                         |

After runs deliver, [the findings sweep](./findings-sweep.md) drains their findings and
[proposal review](./proposal-review.md) applies our local policy to decisions about proposals. Starting a run does not
authorize those later writes.
