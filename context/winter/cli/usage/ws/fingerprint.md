# `winter ws fingerprint` — identify a workspace definition

For the rest of the family, see the [`winter ws` hub](./index.md).

## Synopsis

```text
winter ws fingerprint [--json]
```

Prints one digest that identifies this workspace's *definition*, so two copies of a workspace can be compared with one
value instead of walking every repo by hand. Two copies print the same digest exactly when their definitions match —
useful to prove two experiment arms identical, to detect an arm that changed underneath a trial, or to record which
workspace a runner primed a session from.

The command makes no network calls. It never touches refs, the index or the working tree; on a repo with tracked changes
it may write unreferenced objects to the object store.

## What the digest covers

| Input                                                   | In the digest?                                  |
| ------------------------------------------------------- | ----------------------------------------------- |
| The workspace repo's tracked content                    | **yes**                                         |
| Each standalone repo's **name** and tracked content     | **yes** (standalones sorted by name)            |
| Untracked files — generated projections, local scratch  | no                                              |
| Feature environments (`<env>/` directories)             | no                                              |
| Project repositories                                    | no — their commits vary per feature environment |
| The workspace directory's own name, harness/CLI version | no                                              |

Standalones are the repos declared in `[[standalone_repository]]`, the same set `winter ws pull --standalone` acts on.

**Tracked content** of a repo is a git tree:

- A **clean** repo contributes `HEAD^{tree}`. The tree, not the commit, so two copies whose histories differ but whose
  files are identical match.
- A repo with **staged or modified tracked files** (added, renamed, removed, edited, or deleted) contributes the tree of
  its tracked content: the index brought up to the working tree, built in a throwaway copy of the index so the real one
  is never written. Two copies with different uncommitted changes therefore never share a digest, and a second edit to
  an already-dirty file changes it again. Reverting every change returns the repo to its `HEAD^{tree}`.

Because the digest is over trees, moving a standalone to another commit changes the digest exactly when that commit's
content differs; a commit that leaves the tree identical (a message-only amend, an empty commit) does not.

## Output

Without `--json`, the digest alone — a 64-character lowercase SHA-256 hex string — on one line:

```text
9f2c0e…
```

With `--json`, one JSON object on stdout:

```json
{
  "digest": "9f2c0e…",
  "workspace": { "name": "my-workspace", "commit": "…", "tree": "…", "dirty": false },
  "standalones": [{ "name": "winter-context", "commit": "…", "tree": "…", "dirty": true }]
}
```

| Field    | Meaning                                                                                                                                                                                                                        |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `commit` | The repo's `HEAD` commit. Informational — it is not an input to the digest.                                                                                                                                                    |
| `tree`   | The tree of the repo's tracked content (see above). This is what the digest is computed from.                                                                                                                                  |
| `dirty`  | `true` when a tracked file has a staged or unstaged change, the judgement `ws status` makes. Untracked files do not count. A change staged and then reverted in the working tree is `dirty` while `tree` equals `HEAD^{tree}`. |

`standalones` is sorted by `name`. The workspace entry's `name` is shown for reading only; it is not in the digest.

## Serialization

The digest is the SHA-256 of a canonical JSON document — keys sorted, no insignificant whitespace — holding a format
`version`, the workspace tree, and the sorted list of `{name, tree}` pairs. A future change to that document bumps
`version`, so digests from different formats never collide.

## Exit codes

| Code | Meaning                                                                                                                                                                    |
| ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `0`  | Digest printed.                                                                                                                                                            |
| `1`  | A git probe failed, the workspace repo is not configured or not cloned, or a declared standalone is not cloned (run `winter ws init` first). Nothing is printed to stdout. |
