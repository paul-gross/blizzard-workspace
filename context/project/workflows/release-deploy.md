# Moving a deployment onto a release, or back

**Rule.** Move a blizzard deployment — a compose hub and the runner hosts working against it — between releases one half
at a time, in the order below, proving each half by its reported version before touching the next — except where a
boundary sequences the move otherwise (step 1); a proof that fails stops the procedure where it stands.

**Why.** A hub accepts runners of its own line and older ones inside the window, never newer, so the order is what keeps
every runner working at every moment of the move; the proof is what tells a moved half from one that only restarted.

**Scope.** Any deployment of blizzard, forward onto a newer release or back onto an older one. The facts a step acts on
stay with their owners in the `blizzard` repo, pointed to and never restated here:

| Fact                                                         | Owner                                                   |
| ------------------------------------------------------------ | ------------------------------------------------------- |
| What the release asks of the operator, and each boundary     | the release's **Upgrade notes**, then `docs/upgrade.md` |
| The hub's pull and recreate                                  | `docs/upgrade.md` §Pull and recreate                    |
| The hub's store downgrade, and what a rollback does not undo | `docs/rollback.md`                                      |
| The skew window, what a tag publishes, and the pin's shape   | `docs/versioning.md`                                    |

Where the deployment keeps its pins, and each command with its own paths, hosts, and units, is the deployment's own
runbook — its binding of this procedure. The sequence lives here alone.

## 1. Read the range

Every release between the one deployed and the target is crossed, in either direction. For each, read its **Upgrade
notes** (`gh release view vX.Y.Z --repo paul-gross/blizzard`) and the section of `docs/upgrade.md` they point to, and
act on what they ask before moving anything. A boundary that sequences the move differently from steps 2–4 — reordering
the halves, or splitting one around the other, as installing every runner host's release and leaving it stopped until
the hub has moved does — overrides them for that crossing, as `docs/upgrade.md`'s sections say when they do; each half
is still proven once it runs.

## 2. Order the halves

| Direction | First            | Then             |
| --------- | ---------------- | ---------------- |
| Forward   | the hub          | each runner host |
| Back      | each runner host | the hub          |

Either way, no runner host runs a release newer than its hub at any point of the move.

## 3. Move the hub

Pin the target's exact version and its digest — never `latest`, an `X.Y` line, `edge`, or a `sha-` tag — wherever the
deployment keeps the hub's pin, and let the deployment apply it as `docs/upgrade.md` §Pull and recreate describes. The
digest is the one the tag resolves to when the pin is written — pull the tag once, and read the repository digest the
pull recorded, which is the multi-arch index's:

```bash
docker pull ghcr.io/paul-gross/blizzard-hub:<target>
docker image inspect --format '{{index .RepoDigests 0}}' ghcr.io/paul-gross/blizzard-hub:<target>
```

A forward move migrates the store on boot. Going back, the store is downgraded before the swap, on the outgoing image,
which holds the downgrade code (`docs/rollback.md`): read the head the target names from the target's own image,
touching no store —

```bash
docker run --rm --entrypoint blizzard-hub ghcr.io/paul-gross/blizzard-hub:<target> migrate --head
```

— then, with the hub stopped, run `blizzard-hub migrate --down <that revision>` on the outgoing image, and swap.

**Proof.** `GET /api/health` reports `version` equal to the target in PEP 440 normal form — `0.2.0rc1` for
`v0.2.0-rc.1`, `0.2.0` for `v0.2.0` — and `GET /api/ready` reports ready, with `store_revision` at the head the target
names.

## 4. Move each runner host

Install the target's wheel from its GitHub Release, never a build from a source checkout:

```bash
gh release download vX.Y.Z --repo paul-gross/blizzard --pattern 'blizzard-*.whl' --dir <scratch>
uv pip install --python <venv> --reinstall <scratch>/blizzard-*.whl
```

Going back, downgrade the host's store before the older wheel is installed, on the current install, which holds the
downgrade code: read the head the older release names by installing its wheel into a throwaway virtualenv and running
`blizzard runner migrate --head` from there; then, with the host stopped, run
`blizzard runner migrate --dir <host-dir> --down <that revision>` from the current install; then install the older
wheel. The host stays stopped through all three; a downgrade that fails leaves it on the current install, restarted
unchanged.

Then, in this order:

1. **Migrate.** `blizzard runner migrate --dir <host-dir>` brings the store to the installed release's head.
2. **Check.** `blizzard runner host --check --dir <host-dir>` must pass; a refusal is a stop, and nothing restarts.
3. **Mint** every graph the range changed — `blizzard hub graph mint <graph.yaml>`, read from the installed virtualenv's
   `blizzard/hub/graphs/<graph>/`, never from a source checkout. The graphs are the directories
   `git diff --name-only v<from>..v<to> -- src/blizzard/hub/graphs/` names between the two tags, so the answer is the
   same however far `master` has moved; a prompt-only change is a graph change. A deployed graph change is inert until
   it is minted.
4. **Restart** the host.

**Proof.** The host's `GET /api/health` reports `version` equal to the target in PEP 440 normal form, exactly — a `+`
local segment means the virtualenv runs a build, not the release — and its `GET /api/ready` reports ready, listing every
runner the host holds as `serving` with its hub id (`runner_id`); a `halted` runner is a failed proof.

## 5. Record the pins

Record the hub's pin and each host's release wherever the deployment keeps them, in a reviewed change, so the recorded
pins and the running deployment disagree for no longer than one move.

## A failed proof

A proof that fails stops the procedure where it stands; nothing after it runs. The half already moved stays where it is
when it sits inside the window with the other half (`docs/versioning.md` §The hub↔runner skew window); otherwise it is
moved back by this same procedure, in the back direction.

## Don't

- Pin `latest`, an `X.Y` line, `edge`, or a `sha-` tag — none names a release, and a moving tag can move the host.
- Downgrade a store on the incoming older build — its tree lacks the revisions being reversed.
- Move a runner host onto a release newer than its hub, or move the hub back before every runner host is back.
- Build a runner host's wheel from a source checkout for a release move — the release's wheel is the release.
- Carry on past a failed proof, or take a `200` from `/api/health` as the proof: only the version string is, and a `200`
  alone can come from the process that was already running.

**See also.** [release.md](./release.md) — how the release being moved onto was cut.
