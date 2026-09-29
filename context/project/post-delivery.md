# Post-delivery

What this workspace owes a change once it has landed on `master`.

**Read this whenever work you were driving reaches `master`** — however it got there, and whoever you are. Both of
[contributing.md](./contributing.md)'s delivery paths land here:

| Delivery path       | Reached `master` when…                                                                         | You read this because…                                                |
| ------------------- | ---------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Fleet, `land-pr-ci` | the hub's `deliver` node merged its PR                                                         | you are working the `retrospective` node, or any node after `deliver` |
| By hand             | your PR merged — or your push to `origin/master` landed, when the user asked for a direct push | the merge or push returned successfully                               |

This is workspace policy, not project policy.
[contributing.md § Release publishing](./contributing.md#release-publishing) covers how blizzard is *released* to the
world. This file is about **this workspace's own dogfooding deployment**, which is a different thing entirely.

## The rule: landing on `master` means redeploying the runners

A live blizzard hub and two runners drive blizzard's own development — the **instance**, whose deployed surface and
commands are in [local-instance.md](./local-instance.md). Its two halves redeploy differently, and only one of them is
your job: the **hub** redeploys itself from `master`, and the **runners** — `r-claude` (Claude Code) and `r-chatgpt`
(OpenCode), one venv between them — are yours to rebuild by hand.

**Every landing on `master` is followed by a rebuild and a redeploy of both runners, onto exactly the code that just
landed.** The fleet dogfoods its own output, so a delivered change that never reaches the running runners is a change
the fleet cannot feel. Both run the same venv, so one reinstall changes the code under both; restart only one and the
other runs the old code in memory while its installed files are the new wheel. Do not wait to be asked and do not defer
it to a later session: the redeploy is the second half of delivering, in the same way that pushing is the second half of
committing.

Rebuild even when a landing looks like it cannot touch the runner. Judging that correctly means knowing exactly what the
wheel carries, and the cost of being wrong is a fleet quietly running code you did not ship. (This is about the
*rebuild*; the runbook's graph re-mint step really is conditional, and says so.)

**Run the redeploy + restart runbook in [local-instance.md](./local-instance.md) — it owns the sequence, and this file
does not restate it.** What follows is what that runbook cannot tell you: when to run it, what to check before you trust
it, and how it behaves differently depending on who you are.

## Before you build: confirm you are deploying what you landed

The runbook opens with `winter ws fetch --all`, which is what moves the source checkout the wheel gets built from
([what fetch does to source checkouts](../winter-cli/usage/ws/fetch.md)). Run it and then **confirm the commit you just
landed is actually in `projects/blizzard`** before you build.

That check is the point of this section. Build from a source checkout that never advanced and everything still reports
success — the wheel builds, the install succeeds, the runners come up healthy — and nothing you delivered is in any of
it. A fetch that reports a **failed** repo (a diverged source checkout) is a stop, not a warning, for the same reason.

Catch it here rather than downstream. Each runner does report the commit it was built from once it restarts
(`GET /api/health` → `0.1.0+<short-sha>`), so a wrong-commit redeploy is detectable after the fact — but only after you
have already restarted the fleet's runners onto it. Confirming the checkout first is cheaper than noticing afterward.

## Why the runner restarts go last

**Restarting the runners is the step that terminates fleet workers — see below.** Anything you still owe the node must
be finished before it: the retrospective asset, and any graph re-mint (which targets the hosted hub and therefore does
not depend on either runner at all).

There is no local hub to sequence against. The hosted hub updates on its own schedule, independent of anything you run
here, so runners briefly newer or older than the hub are the normal steady state rather than something the runbook
orders around.

## Restarting the runners, when you are a fleet worker

**If you are a fleet worker, you are running inside one of the runners you are about to restart** — `r-claude` if your
env is `r1`–`r4`, `r-chatgpt` if it is `oce1`–`oce4`. Restarting it kills your worker process mid-command. You will not
see the output of the restart command, and you get no chance to tidy up first.

**This is normal and designed for. Do not treat it as a crash, a mistake, or something to route around.** A graceful
runner shutdown marks every in-flight lease for restart-resume, and the runner's first tick after startup re-attaches
your session **in place** — the same lease, the same epoch, the same conversation, no retry consumed. What you will
observe is simply your next turn arriving after a message like
`# The supervisor restarted; continue your task where you left off.`

Plan for the interruption rather than trying to avoid it:

- **Restart the runners last, in one `systemctl --user restart` naming both units** (the runbook's step 6), and finish
  everything else in the node first — the retrospective asset above all — so nothing valuable is riding on the wake-up.
  One command gets both restarts to systemd before your own runner stops; restart them one command at a time and the
  command after your own runner's restart never runs, leaving the other runner on the old code.
- **When you wake up, do not start over.** Your own prior turns are still in the transcript; read them to see how far
  the deploy got. The restart you issued succeeded — that is why you are running again.
- **Then finish**: confirm both runners' health endpoints answer and both units — `blizzard-blizzard-runner.service` and
  `blizzard-blizzard-runner-opencode.service` — are active (the disabled hub unit is not a check), confirm the version
  each reports carries the commit you landed, and declare done.
- **Do not restart the runners twice.** A second restart buys nothing and costs another round trip through resume.

**If you are a standalone agent, none of this touches you** — the restart does not kill your session, so just run it and
check health directly. It does still interrupt any fleet workers in flight; see the last section.

## Never restart onto a broken build

The runners you are redeploying drive the whole fleet, including chunks other than yours.

**If the fetch, build, install, or either runner migration fails, stop and do not restart anything.** Leaving the
runners on their current wheel is always the correct outcome of a failed deploy: the fleet keeps working, and the
failure stays recoverable. Restarting onto a wheel that does not install or a store that did not migrate takes the fleet
down — and, for a fleet worker, takes down the very runner that would otherwise bring you back to report it.

**You cannot stop the hub half the same way.** It follows `edge` on a timer, so a bad landing is already on its way to
the hosted hub the moment CI publishes it — there is no gate here for you to hold. Keeping a landing off that hub means
pinning the image, which is a `blizzard-infra` operation and has to happen before the next tick rather than after you
notice.

Report the failure plainly and stop there. Do not attempt to fix the landed code from a post-delivery step; that is the
next chunk's work, not this one's.

## The restart is fleet-wide

Restarting the runners interrupts **every** in-flight worker either one holds, not only you. They all come back the same
way, through the same restart-resume path, so the cost is a hiccup rather than lost work — but it is a real cost, and it
is why the redeploy happens once, at the end.
