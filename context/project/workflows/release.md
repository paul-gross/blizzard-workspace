# Cutting a release

**Rule.** A blizzard release is cut by pushing a `v*` tag at a proven-green cut commit — the tag is itself the release,
and no other path publishes a release build. A candidate is tagged at its cut commit, and the stable tag is pushed only
at the commit its newest candidate was tagged at.

**Why.** Development is trunk-based with releases as tags: a `master` push publishes only dogfood artifacts — the dev
wheel and the `edge` / `sha-<full-git-sha>` image channel — while version tags move only on a release tag and `latest`
only on a stable one.

## Before cutting

- Pick the version from the `blizzard` repo's `docs/versioning.md` — it solely owns the version scheme, what counts as
  breaking, and the supported hub↔runner skew; pick there, never restate it.
- Let the cut pull request prove the full suite, including e2e, the full crash sweep, the journey, both container
  smokes, and the upgrade rehearsal. These checks run on the pull request; no separate local tier run is required.

## The cut commit

A candidate is tagged at a **cut commit**, as `blizzard-product:/plans/release-engineering/release-lines/spec/train.md`
§The cut commit defines it: the commit that sets `pyproject.toml`'s `[project] version` to the version being cut,
carries the `Release-Cut: <X.Y.Z>` trailer, and runs the steps that section names, each as far as it has landed. The
release workflow's `build` job asserts that the tag and that value agree (`scripts/check-version-tag.sh`, which reads
the tag with any `-rc.K` suffix removed), failing before anything is built.

- Open the cut commit as a pull request against the branch the cut lands on — that section's table names it, `master`
  for a minor's first candidate — and rebase-merge it only once all its checks, including
  `upgrade rehearsal (release cut)`, pass. Opening one is an attended step, never a fleet chunk's work.
- Tag only a state the remote gate already proved, and the worktree sits at exactly that commit — nothing local or
  unpushed. A landing after the cut commit belongs to the next release and is not carried. The gate depends on where the
  cut landed:
  - **On `master`**: the landed cut commit's `push`-workflow run is green (`blizzard:ci`,
    `blizzard-context:/verification/blizzard.md`). Rebase-merge puts the commit carrying the trailer itself on the
    branch so it gets its own `push` run; a merge commit would leave it with none.
  - **On `release/X.Y`**: a push to a release branch runs nothing, so the gate is the cut pull request's `pr` run, green
    on every job including `line provenance` and `upgrade rehearsal (release cut)`. Rebase-merge rewrites the commit, so
    tie that run to what landed: right before merging, the pull request head must sit on the current `release/X.Y` tip
    (if the branch moved, rebase and let the run go again), and after landing, the cut commit's tree
    (`git rev-parse <sha>^{tree}`) must equal the tree of the pull request head that run proved. The `release/*` ruleset
    requires neither status checks nor an up-to-date branch, so this check is the operator's.

Before tagging, run `uv run blizzard-compat --tag <version>` over the cut commit's tree, the check the release workflow
runs again at the tag. A failure routes to `blizzard-context:/workflows/judging-a-break.md`.

## Tag and watch

The candidate tag names the cut commit's version with its candidate suffix, and sits on that commit:

```bash
git tag v0.2.0-rc.1
git push origin v0.2.0-rc.1
```

The `release` workflow checks out the `blizzard-mock` tag of the same name, so push that tag first, at the commit
`blizzard-product:` `mock.md` §Branches and tags names. For a candidate cut on `master` that is the mock sha the cut
commit's `push` run recorded:

```bash
gh run download <run-id> --repo paul-gross/blizzard --name proven-against
```

The `v*` tag triggers the `release` workflow: the full verification suite, the wheel built with the embedded frontend, a
multi-arch hub image pushed to GHCR and signed, a GitHub Release with the inventory attached, and last the `verify` job,
which proves what was published; `docs/ci.md` owns the release workflow's jobs.

## Promoting a candidate

When a candidate is promoted, and what must hold before it is, is release-lines'
(`blizzard-product:/plans/release-engineering/release-lines/spec/train.md` §Promotion). What this procedure holds is the
commit: the stable tag is pushed only at the commit its newest candidate was tagged at, confirmed before the push, never
by assumption:

```bash
test "$(git rev-list -n1 v0.2.0-rc.1)" = "$(git rev-parse HEAD)" && git tag v0.2.0 && git push origin v0.2.0
```

Push the mock's stable tag first, on the commit the candidate's mock tag sits on. A promotion writes no cut commit:
`pyproject.toml` already names the stable version, and the notes were final at the candidate.

## Confirm the cut

- Watch the `release.yml` run to green through `verify`, not only through `publish` (`blizzard:ci`) — `verify` is the
  confirmation of the signatures, attestations, bills of materials, checksums, and the install from the release page —
  then confirm the Release and its attached wheel: `gh release view v0.2.0-rc.1 --repo paul-gross/blizzard`.
- Run `scripts/verify-release.sh v0.2.0-rc.1` once from outside GitHub's runners (`blizzard:manual-release-verify`), so
  a verifier that shares nothing with the run that built the release proves it too. The script and `docs/ci.md` own what
  it checks.
- Confirm the image with an anonymous `docker pull ghcr.io/paul-gross/blizzard-hub:0.2.0-rc.1` — the GHCR push has no
  local proof, and package visibility is a one-time repo setting no workflow can assert — see `docs/ci.md` §Trigger
  workflows: a documented gap proven by each real cut, never invented around.
- The generated notes (`scripts/release-notes.sh`) always hold a placeholder **Upgrade notes** heading after any
  **Breaking changes**; when the release asks something of the operator (`docs/versioning.md` names what counts),
  replace it with real prose — `gh release edit v0.2.0-rc.1 --repo paul-gross/blizzard --notes-file <edited-notes.md>`,
  or the web UI. For a release that crosses a boundary, that prose points to the release's section of `docs/upgrade.md`
  — `## Upgrading to 0.2.0` for a `0.2.0` candidate — rather than restating the boundary.

## A red run

Repair forward: a further candidate (`-rc.K+1`), or the next patch, each at a cut commit of its own on the branch the
train document names for it. A pushed tag is immutable — never re-pointed, deleted, or reused.

A release that published but does not verify — a red `verify` job, or a red `blizzard:manual-release-verify` — is a red
run too. The release stays: an install that pinned it fetches its files by their address on its page, so deleting the
page breaks exactly the operators who did what the docs asked, and a tag is never re-pointed. The fix is the next tag.

When the fault could hand an operator a wrong or unverifiable artifact, a maintainer also prepends a warning to the
release's body — immutable releases lock the tag and the attached files, not the body — naming the fault and the release
that replaces it once one exists. A signature that fails to verify always meets that bar; a missing bill of materials
never does. Then the instance is not moved onto that release, or is moved off it, through
[release-deploy.md](./release-deploy.md), whose first step already reads the release body.

**Don't.** Publish a wheel or image by hand — a build no tag produced is a build no gate proved.

**See also.** The `blizzard` repo's `docs/ci.md` — the operator reference for the triggered workflows and the `gh run`
watch commands. [release-deploy.md](./release-deploy.md) — moving a deployment onto the release once it is cut.
