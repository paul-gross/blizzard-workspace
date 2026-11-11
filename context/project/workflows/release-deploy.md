# Moving our instance onto a release, or back

**Rule.** Move our singleton instance between releases one half at a time, in the order below, proving each half by its
reported version before touching the next — except where a boundary sequences the move otherwise (step 1); a proof that
fails stops the procedure where it stands.

**Why.** A hub accepts runners of its own line and older ones inside the window, never newer, so the order is what keeps
every runner working at every moment of the move; the proof is what tells a moved half from one that only restarted.

**Scope.** A user-requested move of our instance, from an operator session on `xps`. Resolve access and authority
through [our binding](../blizzard-binding.md) first. Product deployment mechanics stay with the application's docs:

| Fact                                                         | Owner                                                   |
| ------------------------------------------------------------ | ------------------------------------------------------- |
| What the release asks of the operator, and each boundary     | the release's **Upgrade notes**, then `docs/upgrade.md` |
| The hub's pull and recreate                                  | `docs/upgrade.md` §Pull and recreate                    |
| The hub's store downgrade, and what a rollback does not undo | `docs/rollback.md`                                      |
| The skew window, what a tag publishes, and the pin's shape   | `docs/versioning.md`                                    |

Our paths, pins, units, and commands live in the
[instance runbook](../singleton-instance.md#redeploy--restart-runbook--moving-the-instance-onto-a-release). This
workflow owns our move's sequencing and stop conditions, not a reusable installation tutorial.

## 1. Read the range

Every release between the one deployed and the target is crossed, in either direction. For each, read its **Upgrade
notes** (`gh release view vX.Y.Z --repo paul-gross/blizzard`) and the section of `docs/upgrade.md` they point to, and
act on what they ask before moving anything. A boundary that sequences the move differently from steps 2–4 — reordering
the halves, or splitting one around the other, as installing every runner host's release and leaving it stopped until
the hub has moved does — overrides them for that crossing, as `docs/upgrade.md`'s sections say when they do; each half
is still proven once it runs.

Also read the issue-level deployment instructions required by [post-delivery.md](../post-delivery.md).

## 2. Order the halves

| Direction | First                 | Then                  |
| --------- | --------------------- | --------------------- |
| Forward   | the hub               | the `xps` runner host |
| Back      | the `xps` runner host | the hub               |

Honor the application's skew window throughout the move. A boundary in the range takes precedence over this table.

## 3. Move the hub

Use [the hub's pin](../singleton-instance.md#the-hubs-pin) and `blizzard-infra`'s deployment/rollback procedure. For a
release move, pin the exact release and index digest, not a moving channel or dogfood tag. Do not operate the hosted
compose stack through a generic local-install recipe.

Prove the target version and readiness as the runbook requires before moving the runner host.

## 4. Move the runner host

Use [the runner-host runbook](../singleton-instance.md#the-runner-host), or its
[going-back section](../singleton-instance.md#going-back) for rollback. Finish installation, migration, configuration
checks, and any changed-graph mints before the restart. A failed download, install, migration, or check stops the move:
never restart onto the failed installation.

Prove the version, both runners' readiness and registration, and trace export using the runbook's local surfaces.

## 5. Confirm the deployment record

The hub's pin in `blizzard-infra` records the target build. Confirm both halves report that build; the workspace keeps
no separate deployed-version record.

## A failed proof

A proof that fails stops the procedure where it stands; nothing after it runs. The half already moved stays where it is
when it sits inside the window with the other half (`docs/versioning.md` §The hub↔runner skew window); otherwise it is
moved back by this same procedure, in the back direction.

**See also.** [release.md](./release.md) — how the release being moved onto was cut.
