# Feeding-the-dog

Moving the instance onto a **dogfood build**: one `master` commit whose `push` run went green, so its hub image and its
wheel are both published and proven. The hub and **every** runner host move onto the same commit, in one sitting, at a
moment the user picks. A release is a promise to other operators; a dogfood build is the user's own choice of when the
instance takes a breaking change or a manual migration, without waiting for a release to carry it.

**Read this when** told to run feeding-the-dog, or to move the instance onto a `master` commit. Moving onto a release is
the [redeploy + restart runbook](../instance.md#redeploy--restart-runbook--moving-the-instance-onto-a-release) instead.
Feeding-the-dog keeps that runbook's shape — read what the range asks, hub first, prove each half before the next — and
differs only where a dogfood build is not a release.

**Scope.** An operator session on any of [our machines](../fleet-machines.md), orchestrating the rest over the tailnet.
The hub half needs a `blizzard-infra` worktree and the `blizzard-prod-admin` AWS session — today only on `xps`, so from
elsewhere it runs as `ssh xps …`; ask for `! aws sso login --profile blizzard-prod-admin` on that machine and stop
without it. Minting graphs needs a hub session ([our fleet binding](../blizzard-binding.md)). No fleet worker holds
either, so feeding-the-dog is never a chunk's work.

**It moves the whole instance.** Every runner host in [our machines' table](../fleet-machines.md#the-machines) moves in
the same sitting as the hub. Before step 1, ask every host its health and readiness
([fleet-machines.md § Asking every host at once](../fleet-machines.md#asking-every-host-at-once)): every host must
answer. A host that does not stops the move before it starts — moving the rest would leave the instance split across
builds.

## 1. Pick the commit

Take the newest `master` commit whose `push.yml` run succeeded, or the one the user names. A commit whose run has not
succeeded is a stop: the run publishes the hub image `ghcr.io/paul-gross/blizzard-hub:sha-<full sha>` only once the
gate, the upper tiers, and the upgrade rehearsal pass. While the newest run is still going, offer the commit before it.

## 2. Read what the range asks

The range runs from the build the instance runs now to the target. Read it from the hub —
`curl -s https://blizzard.grosscode.net/api/health | jq -r .version`, whose `+` segment names a dogfood build's commit —
and keep it: it is the previous build that step 5's failure path and [Going back](#going-back) return to. Confirm every
host reports that same build in its `/api/health`; a host on another build is a stop to raise with the user. Read every
`## Upgrade` section among the change notes the target carries in `changes/unreleased/`, and those of any release the
range crosses, and carry each step out before installing anything — a step that fails is a stop. Note which directories
under `src/blizzard/hub/graphs/` the range changed; each is re-minted in step 5.

## 3. Fetch the build

- **The wheel** is the `push` run's `blizzard-dev-wheel-<version>` artifact, which holds the wheel and its pinned
  requirements file. Fetch it once, on the orchestrating machine: find the commit's successful run with
  `gh run list --repo paul-gross/blizzard --workflow push.yml --commit <full sha>`, then
  `gh run download <run id> --repo paul-gross/blizzard --pattern 'blizzard-dev-wheel-*'`. Its version, such as
  `0.3.0.dev948+65bc1dc7f`, is what every part reports once moved. Run artifacts expire; a commit too old to have one is
  a stop.
- **The hub image's index digest** comes from an anonymous read of the `sha-<full sha>` manifest on GHCR, which also
  proves the image is pullable.

## 4. Move the hub

`blizzard-infra`'s `deploy/release.env` pins a dogfood build the same way it pins a release:
`BLIZZARD_HUB_IMAGE=ghcr.io/paul-gross/blizzard-hub:sha-<full sha>@sha256:<index digest>`. Move the hub as
[instance.md § The hub's pin](../instance.md#the-hubs-pin) describes — change that line on `blizzard-infra`'s `master`,
then run `scripts/deploy.sh` — and the updater does the move with its own safeguards: the store snapshot, the revision
check, the fence on a digest that fails readiness.

**Proof:** `/api/ready` reports ready with `store_revision` equal to `expected_revision`, and
`https://blizzard.grosscode.net/api/health` reports the wheel's version. Without both, stop before any runner host.

## 5. Move every runner host

Follow [instance.md § The runner hosts](../instance.md#the-runner-hosts) from its phase 2, with the build step 3
fetched: copy it to every host, prepare every host — stop, back up, install, migrate, check — leaving each stopped,
re-mint each graph step 2 found, once, from one host's installed venv, then start every host and prove it. Stopping each
host before its venv changes means nothing runs while it does.

**A host that fails** is handled as [instance.md § A host that fails](../instance.md#a-host-that-fails) describes: put
back on the previous build and started on that, never started on an install that did not check, and the rest decided
with the user.

**Proof** is the runbook's own, per host: `/api/health` reports the wheel's version and `/api/ready` lists its runners
`serving`, the journal shows `tick end` events, and `blizzard hub runner list` shows every runner on the build and
`in_window`, with traces exporting. Hub-call errors logged while the hub was being swapped are expected; any after the
start are not.

Stopping a host interrupts every in-flight worker it holds, and each resumes in place once it starts
([post-delivery.md § The restart is fleet-wide](../post-delivery.md#the-restart-is-fleet-wide)).

The move edits no file in this workspace. The hub's pin in `blizzard-infra` is its record, and each part reports what it
runs ([instance.md § What it runs now](../instance.md#what-it-runs-now)).

## Going back

Every runner host first, hub after. Each host goes back as [instance.md § Going back](../instance.md#going-back)
describes, onto the previous build's wheel, fetched by that build's commit as in step 3. The hub goes back by pinning
the previous build again and deploying, after `blizzard-infra`'s `docs/rollback.md` schema-delta check; the updater
downgrades the store on the outgoing image and restores its own snapshot if that fails.
