# Post-delivery

What this workspace owes a change once it has landed on `master`.

**Read this whenever work you were driving reaches `master`** — however it got there, and whoever you are. Both of
[contributing.md](./contributing.md)'s delivery paths land here:

| Delivery path       | Reached `master` when…                                                                                                             | You read this because…                                                |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Fleet, `land-pr-ci` | the hub's `deliver` node merged its PR                                                                                             | you are working the `retrospective` node, or any node after `deliver` |
| By hand             | your landing returned successfully — by the PR or direct push [contributing.md](./contributing.md#the-by-hand-path) gives the repo | the merge or push returned successfully                               |

## The rule: a landing owes the instance nothing

**Delivery ends at landing.** Our [instance](./singleton-instance.md) runs a pinned build. A landed change reaches it
only through a separate, user-requested move onto a build that carries the change. Do not rebuild, reinstall, restart,
or move the deployment's pin as part of landing work.

| Move                                                | When to read                                                       |
| --------------------------------------------------- | ------------------------------------------------------------------ |
| [Release deployment](./workflows/release-deploy.md) | The user requests a move onto a published release, or back off one |
| [Feeding-the-dog](./workflows/feeding-the-dog.md)   | The user requests a move onto a proven `master` build              |

Until every deployment instruction is carried by release Upgrade notes, a release move also reads the
`## Landing and deployment` section of every issue the range closed. Carry out extra steps where they place themselves
in the runbook. **Deploy: by hand** excludes fleet-worker execution: report that the release awaits an attended move.

Deployment failures are reported, not repaired by editing released code during the move. A code fix belongs to new work
and a subsequent proven build. Installation, migration, rollback, and health checks belong to the
[instance runbook](./singleton-instance.md#redeploy--restart-runbook--moving-the-instance-onto-a-release).

## The restart is fleet-wide

Our host serves both `blizzard` and `winter`; restarting its unit interrupts workers in both projects. Finish durable
outputs before the authorized restart and account for the whole host, not just this workspace's chunks. General
intervention and worker-recovery guidance belongs to
[blizzard-usage](../../.winter/ext/usage/context/runner-hosts.md#change-a-host-only-with-its-runbook).
