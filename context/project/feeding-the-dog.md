# Feeding-the-dog

Moving the instance onto a **dogfood build**: one `master` commit whose `push` run went green, so its hub image and its
wheel are both published and proven. The hub and the runner host move onto the same commit, at a moment the user picks.
A release is a promise to other operators; a dogfood build is the user's own choice of when the instance takes a
breaking change or a manual migration, without waiting for a release to carry it.

**Read this when** told to run feeding-the-dog, or to move the instance onto a `master` commit. Moving onto a release is
the [redeploy + restart runbook](./local-instance.md#redeploy--restart-runbook--moving-the-instance-onto-a-release)
instead. Feeding-the-dog keeps that runbook's shape — read what the range asks, hub first, prove each half before the
next — and differs only where a dogfood build is not a release.

**Scope.** An operator session. The hub half needs the `blizzard-prod-admin` AWS session — ask for
`! aws sso login --profile blizzard-prod-admin` and stop without it — and minting graphs needs a hub session
([local-instance.md § The operator CLI needs a session](./local-instance.md#the-operator-cli-needs-a-session)). No fleet
worker holds either, so feeding-the-dog is never a chunk's work.

## 1. Pick the commit

Take the newest `master` commit whose `push.yml` run succeeded, or the one the user names. A commit whose run has not
succeeded is a stop: the run publishes the hub image `ghcr.io/paul-gross/blizzard-hub:sha-<full sha>` only once the
gate, the upper tiers, and the upgrade rehearsal pass. While the newest run is still going, offer the commit before it.

## 2. Read what the range asks

The range runs from the build in the **Release** row of [local-instance.md](./local-instance.md#deployed-surface) to the
target. Read every `## Upgrade` section among the change notes the target carries in `changes/unreleased/`, and those of
any release the range crosses, and carry each step out before installing anything — a step that fails is a stop. Note
which directories under `src/blizzard/hub/graphs/` the range changed; each is re-minted in step 5.

## 3. Fetch the build

- **The wheel** is the `push` run's `blizzard-dev-wheel-<version>` artifact (`gh run download`). Its version, such as
  `0.3.0.dev948+65bc1dc7f`, is what both halves report once moved. Run artifacts expire; a commit too old to have one is
  a stop.
- **The hub image's index digest** comes from an anonymous read of the `sha-<full sha>` manifest on GHCR, which also
  proves the image is pullable.

## 4. Move the hub

`blizzard-infra`'s `deploy/release.env` pins a dogfood build the same way it pins a release:
`BLIZZARD_HUB_IMAGE=ghcr.io/paul-gross/blizzard-hub:sha-<full sha>@sha256:<index digest>`. Move the hub as
[local-instance.md § The hub's pin](./local-instance.md#the-hubs-pin) describes — change that line on `blizzard-infra`'s
`master`, then run `scripts/deploy.sh` — and the updater does the move with its own safeguards: the store snapshot, the
revision check, the fence on a digest that fails readiness.

**Proof:** `/api/ready` reports ready with `store_revision` equal to `expected_revision`, and
`https://blizzard.grosscode.net/api/health` reports the wheel's version. Without both, stop before the runner host.

## 5. Move the runner host

Stop `blizzard-blizzard-runner.service` first, so nothing runs while the venv changes under it. Then follow the
runbook's steps 3–6 with the dogfood wheel: install it into `../.venv`, migrate, `runner host --check` until it prints
`host ok`, and re-mint each graph step 2 found, from the installed venv. Start the unit only once all of that passed.

A failure leaves the host stopped: put the previous build back — its wheel, and a store downgrade if the migration ran,
as [local-instance.md § Going back](./local-instance.md#going-back) describes — and start it on that. Never start the
host on an install that did not check.

**Proof** is the runbook's own: the host's `/api/health` reports the wheel's version, `/api/ready` lists both runners
`serving`, the journal shows `tick end` events, and `runner status` and `runner traces status` show each runner
registered and exporting. Hub-call errors logged while the hub was being swapped are expected; any after the start are
not.

Stopping the host interrupts every in-flight worker, and each resumes in place once it starts
([post-delivery.md § The restart is fleet-wide](./post-delivery.md#the-restart-is-fleet-wide)).

## 6. Record the move

Change the **Release** row in [local-instance.md](./local-instance.md#deployed-surface) to the dogfood build — its
version and its commit — in a workspace commit, so the row and the running instance never disagree for longer than one
move.

## Going back

Runner host first, hub after. The runner host goes back as
[local-instance.md § Going back](./local-instance.md#going-back) describes, onto the previous build's wheel. The hub
goes back by pinning the previous build again and deploying, after `blizzard-infra`'s `docs/rollback.md` schema-delta
check; the updater downgrades the store on the outgoing image and restores its own snapshot if that fails.
