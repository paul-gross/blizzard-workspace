# Doctor probes

`winter doctor` (see [../usage/doctor.md](../usage/doctor.md)) aggregates probe results from three sources: built-in core checks in winter-cli, an optional workspace-level probe, and one probe per installed extension. The workspace and extension probes are opt-in shell scripts that follow the same output contract.

## Built-in core probes

These ship with winter-cli and run on every `winter doctor` — no `.winter/config.toml` registration needed. They run first, before the workspace and extension probes, and their results appear under a `[core]` source group. [usage/doctor.md](../usage/doctor.md) lists the full set; the one called out below has non-obvious drift semantics worth documenting.

- **AGENTS.md shim** — verifies that the workspace-root `CLAUDE.md` is a valid one-line shim pointing at `AGENTS.md`. The probe `fail`s (blocking `winter doctor` exit 0) on any drift: `CLAUDE.md` exists but `AGENTS.md` does not, `AGENTS.md` exists but `CLAUDE.md` does not, or `CLAUDE.md`'s stripped content is anything other than `@AGENTS.md`. The probe stays silent (emits nothing) when neither file exists — a workspace that has not yet adopted the AGENTS.md layout is not flagged.

## Workspace skill discoverability probes

The **`workspace skill discoverability: <vendor>`** probe family runs unconditionally on every `winter doctor` — independent of `adopt_extensions` or any config opt-in. One probe runs per code-agent vendor (ClaudeCode, Codex, OpenCode). Each probe checks that the workspace skills under `<skills_dir>/` are projected into the corresponding vendor skill directory; a mismatch (missing or stale projection) emits a `warn` result recommending `winter ws init`. These probes surface under the `[skills]` source group, not `[core]`.

## Probe output contract

Every probe script emits **NDJSON to stdout**, one object per line:

```json
{"name": "tea auth", "status": "pass", "message": "logged in as pgross"}
{"name": "tmux version", "status": "warn", "message": "v2.8 (recommend >= 3.0)", "remediation": "Upgrade tmux: `dnf install tmux`."}
```

Required fields: `name` (string) and `status` (one of `pass` / `warn` / `fail`). Optional: `message` (one-line summary) and `remediation` (one-line fix hint, shown under failures in the table view).

**Exit handling.** A non-zero exit becomes a single synthetic `fail` result with the probe's stderr as the message — surfaced even if no NDJSON was emitted. Lines that don't parse as JSON, or that are missing required fields, become `warn` results so the contract violation is visible without aborting the run.

**Common misconfigurations** (workspace and extension probes alike): a missing `doctor` field is silently skipped; a `doctor` value pointing at a missing script surfaces as a `fail`; a script that exists but isn't executable surfaces as a `fail` so the misconfiguration is actionable; a path that escapes its declaring directory (workspace root for workspace probes, extension directory for extension probes) is refused.

## Workspace doctor probe

The workspace itself can contribute a probe script that runs between the core probes and each extension's probes. Declare it as a top-level field in `.winter/config.toml`:

```toml
doctor = "context/project/doctor.sh"
```

The path is **relative to the workspace root** and must point to an executable file. The probe runs with cwd at the workspace root and `WINTER_WORKSPACE_DIR` set. Use it for project-specific checks that don't belong in any extension — database reachable, `.env` populated, secrets present, build toolchain installed.

Results appear under a `[project]` source group in the table view, between `[core]` and each `[<ext-prefix>]` block.

## Extension doctor probes

Extensions opt in via a top-level field in `winter-ext.toml`:

```toml
doctor = "scripts/doctor.sh"
```

`doctor` is a **top-level scalar** in `winter-ext.toml`, not part of `[hooks]` — there's at most one probe script per extension. The path is **relative to the extension directory** (same rule as hook scripts) and must point to an executable file.

The probe's **cwd is the workspace root**. Probes are workspace-scoped, not per-env, so the env vars are a subset of the hook contract:

| Var | Meaning |
|-----|---------|
| `WINTER_WORKSPACE_DIR` | Absolute path to the workspace root. |
| `WINTER_EXT_DIR` | Absolute path to this extension's clone. |
| `WINTER_EXT_PREFIX` | The resolved symlink prefix for this extension. |
| `WINTER_SERVICE_PREFIX` | The resolved workspace-level service-orchestration namespace prefix. Workspace-invariant — always present. |

Results appear under a `[<ext-prefix>]` source group, one block per installed extension that contributes probes.
