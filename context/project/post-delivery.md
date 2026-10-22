# Post-delivery

What this workspace owes a change once it has landed on `master`, and what moving the instance onto a release owes the
fleet.

**Read this whenever work you were driving reaches `master`** — however it got there, and whoever you are. Both of
[contributing.md](./contributing.md)'s delivery paths land here:

| Delivery path       | Reached `master` when…                                                                         | You read this because…                                                |
| ------------------- | ---------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Fleet, `land-pr-ci` | the hub's `deliver` node merged its PR                                                         | you are working the `retrospective` node, or any node after `deliver` |
| By hand             | your PR merged — or your push to `origin/master` landed, when the user asked for a direct push | the merge or push returned successfully                               |

**Read it too before you move the instance onto a release** — the rest of this file is about that move.

This is workspace policy, not project policy.
[contributing.md § Release publishing](./contributing.md#release-publishing) covers how blizzard is *released* to the
world. This file is about **this workspace's own dogfooding deployment**, which is a different thing entirely.

## The rule: a landing owes the instance nothing

A live blizzard hub and one local host of two runners drive blizzard's own development — the **instance**, whose
deployed surface and commands are in [local-instance.md](./local-instance.md). Both halves run one pinned **build**: a
release, or a dogfood build — one `master` commit whose `push` run went green, moved onto by
[feeding-the-dog](./feeding-the-dog.md). The hub runs that build's image by digest, and the runners — `r-claude` (Claude
Code) and `r-chatgpt` (OpenCode), one host daemon and one venv between them — run its wheel.

**Delivery ends at landing.** A landed change reaches the instance only once the user moves the instance onto a build
that carries it — a release, or a dogfood build through feeding-the-dog. **Do not rebuild or reinstall the runner host
from `master` after a landing** — a build from a source checkout is one no `push` run proved, and the instance no longer
runs what its **Release** row says.

Moving the instance onto a release is the **redeploy + restart runbook** in [local-instance.md](./local-instance.md) —
it owns the sequence, and this file does not restate it. What follows is what that runbook cannot tell you: what to read
before you install, and how the move behaves differently depending on who you are.

## Before you install: read what the release asks of its move

A release can ask more of its move than the runbook does. Before installing anything, read the **Upgrade notes** of
every release the move crosses (`gh release view v<version> --repo paul-gross/blizzard`) and the `docs/upgrade.md`
sections they point to, and act on them. Until every such step reaches a release's Upgrade notes, also read the
`## Landing and deployment` section of each issue the release range closed:

- **Extra deployment steps** are yours to carry out where they place them, before the runbook installs or migrates
  anything. A step that fails is a stop under
  [Never restart onto a broken install](#never-restart-onto-a-broken-install), exactly as a failed migration is.
- **Deploy: by hand** means no fleet worker installs this release: its move stops the runners, which no worker can do
  from inside one. Run none of the runbook, report that the release awaits its attended move, and stop.

A release that asks for nothing beyond the standard move leaves the runbook as it is.

## Confirm you are installing the release you mean

Install the wheel the release itself published — `gh release download` — or, for feeding-the-dog, the dogfood build's
`push`-run wheel; never a build from `projects/blizzard`. A build from a source checkout still reports success at every
step — the install succeeds, the runners come up healthy — while running code nothing proved. The host reports what it
runs once it restarts: a release wheel reports the release exactly (`GET /api/health` → `0.2.0rc2` for `v0.2.0-rc.2`),
and a dogfood wheel its dev version with its commit (`0.3.0.dev948+65bc1dc7f`). Check it against the **Release** row
before you trust the move.

## Why the runner restarts go last

**Restarting the runners is the step that terminates fleet workers — see below.** Anything you still owe must be
finished before it: any retrospective asset, and any graph re-mint (which targets the hosted hub and therefore does not
depend on either runner at all).

The hub moves first going forward and last going back, so a runner host never runs a release newer than the hub it works
against; the runbook's first step confirms the hub's version before anything is installed.

## Restarting the runners, when you are a fleet worker

**If you are a fleet worker, you are running inside one of the runners you are about to restart** — `r-claude` if your
env is `r-claude-<n>`, `r-chatgpt` if it is `r-chatgpt-<n>`. Restarting it kills your worker process mid-command. You
will not see the output of the restart command, and you get no chance to tidy up first.

**This is normal and designed for. Do not treat it as a crash, a mistake, or something to route around.** A graceful
runner shutdown marks every in-flight lease for restart-resume, and the runner's first tick after startup re-attaches
your session **in place** — the same lease, the same epoch, the same conversation, no retry consumed. What you will
observe is simply your next turn arriving after a message like
`# The supervisor restarted; continue your task where you left off.`

Plan for the interruption rather than trying to avoid it:

- **Restart the host last** (the runbook's step 7), and finish everything else first — the retrospective asset above all
  — so nothing valuable is riding on the wake-up. Nothing can follow the restart in the same turn: the command stops
  your own runner with it.
- **When you wake up, do not start over.** Your own prior turns are still in the transcript; read them to see how far
  the move got. The restart you issued succeeded — that is why you are running again.
- **Then finish**: confirm the host's health endpoint answers and `blizzard-blizzard-runner.service` is active (the
  disabled hub and OpenCode units are not checks), confirm the version it reports is the release you installed, and
  declare done.
- **Do not restart the runners twice.** A second restart buys nothing and costs another round trip through resume.

**If you are a standalone agent, none of this touches you** — the restart does not kill your session, so just run it and
check health directly. It does still interrupt any fleet workers in flight; see the last section.

## Never restart onto a broken install

The runners you are moving drive the whole fleet, including chunks other than yours.

**If the download, install, the migration, or `host --check` fails, stop and do not restart anything.** Leaving the
runners on their current release is always the correct outcome of a failed move: the fleet keeps working, and the
failure stays recoverable. Restarting onto a wheel that does not install, a store that did not migrate, or a host
directory that did not check takes the fleet down — and, for a fleet worker, takes down the very runner that would
otherwise bring you back to report it.

**The hub half is held the same way.** It runs the release its pin names, so a bad landing never reaches it on its own;
only moving the pin does, and a hub move whose proof fails stops the runbook before the runner host is touched.

Report the failure plainly and stop there. Do not attempt to fix the released code from a deployment step; that is the
next chunk's work, and the next release's.

## The restart is fleet-wide

Restarting the host interrupts **every** in-flight worker either runner holds, not only you. They all come back the same
way, through the same restart-resume path, so the cost is a hiccup rather than lost work — but it is a real cost, and it
is why the move restarts the host once, at the end.
