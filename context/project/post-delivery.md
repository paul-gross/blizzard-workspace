# Post-delivery

What this workspace owes a change once it has landed on `master`.

**Read this whenever work you were driving reaches `master`** — however it got there, and whoever you are. All three of [contributing.md](./contributing.md)'s delivery paths land here:

| Delivery path | Reached `master` when… | You read this because… |
|---------------|------------------------|------------------------|
| Fleet, `merge-to-main` | the hub's `deliver` node landed it | you are working the `retrospective` node, or any node after `deliver` |
| Fleet, `open-pr` | a human merged the PR `deliver` parked the chunk on | the hub completed the chunk from that outcome |
| By hand | you pushed to `origin/master` yourself | the push returned successfully |

This is workspace policy, not project policy. [contributing.md § Release publishing](./contributing.md#release-publishing) covers how blizzard is *released* to the world (no deploys, no changelog — a `v*` tag is the release). This file is about **this workspace's own dogfooding deployment**, which is a different thing entirely and does have a deploy.

## The rule: landing on `master` means redeploying locally

This workspace runs a live blizzard hub and runner that drive blizzard's own development — the **local instance**, whose deployed surface and commands are in [local-instance.md](./local-instance.md).

**Every landing on `master` is followed by a local rebuild and redeploy, onto exactly the code that just landed.** The fleet dogfoods its own output, so a delivered change that never reaches the running daemons is a change the fleet cannot feel. Do not wait to be asked and do not defer it to a later session: the redeploy is the second half of delivering, in the same way that pushing is the second half of committing.

**Run the redeploy + restart runbook in [local-instance.md](./local-instance.md) — it owns the sequence, and this file does not restate it.** What follows is what that runbook cannot tell you: when to run it, what to check before you trust it, and how it behaves differently depending on who you are.

## Before you build: confirm you are deploying what you landed

The runbook opens with `winter ws fetch --all`, which is what moves the source checkout the wheel gets built from ([what fetch does to source checkouts](../winter-cli/usage/ws/fetch.md)). Run it and then **confirm the commit you just landed is actually in `projects/blizzard`** before you build.

That check is the point of this section. Build from a source checkout that never advanced and everything still reports success — the wheel builds, the install succeeds, the daemons come up healthy — and nothing you delivered is in any of it. A fetch that reports a **failed** repo (a diverged source checkout) is a stop, not a warning, for the same reason.

## Why the runbook is ordered hub-then-runner

Two constraints fix that order, and both are worth understanding before you deviate from it:

- **The hub goes first** because the runner is ordered after it (`After=` in the unit files) and talks to it for everything; bringing the runner up against a hub still on the old wheel is the pairing to avoid.
- **The runner goes last** because restarting it is the step that terminates a fleet worker — see below. Anything you still owe the node must be finished before it.

## Restarting the runner, when you are a fleet worker

**If you are a fleet worker, you are running inside the very runner you are about to restart.** Restarting it kills your worker process mid-command. You will not see the output of the restart command, and you get no chance to tidy up first.

**This is normal and designed for. Do not treat it as a crash, a mistake, or something to route around.** A graceful runner shutdown marks every in-flight lease for restart-resume, and the runner's first tick after startup re-attaches your session **in place** — the same lease, the same epoch, the same conversation, no retry consumed. What you will observe is simply your next turn arriving after a message like `# The supervisor restarted; continue your task where you left off.`

Plan for the interruption rather than trying to avoid it:

- **Restart the runner last**, and finish everything else in the node first — the retrospective asset above all — so nothing valuable is riding on the wake-up.
- **When you wake up, do not start over.** Your own prior turns are still in the transcript; read them to see how far the deploy got. The restart you issued succeeded — that is why you are running again.
- **Then finish**: confirm both health endpoints answer and both units are active, confirm the deployed wheel is the one you built, and declare done.
- **Do not restart the runner twice.** A second restart buys nothing and costs another round trip through resume.

**If you are a standalone agent, none of this touches you** — the restart does not kill your session, so just run it and check health directly. It does still interrupt any fleet workers in flight; see the last section.

## Never restart onto a broken build

The daemons running this deployment drive the whole fleet, including chunks other than yours.

**If the fetch, build, install, or either migration fails, stop and do not restart anything.** Leaving the running instance on its current wheel is always the correct outcome of a failed deploy: the fleet keeps working, and the failure stays recoverable. Restarting onto a wheel that does not install or a store that did not migrate takes the fleet down — and, for a fleet worker, takes down the very runner that would otherwise bring you back to report it.

Report the failure plainly and stop there. Do not attempt to fix the landed code from a post-delivery step; that is the next chunk's work, not this one's.

## The restart is fleet-wide

Restarting the runner interrupts **every** in-flight worker it holds, not only you. They all come back the same way, through the same restart-resume path, so the cost is a hiccup rather than lost work — but it is a real cost, and it is why the redeploy happens once, at the end.
