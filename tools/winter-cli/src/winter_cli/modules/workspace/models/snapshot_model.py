from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True, slots=True)
class OrphanSnapshot:
    """An orphaned filesystem entry — a directory or file with no declared owner.

    `kind` is a short label such as ``"worktree_dir"``, ``"env_dir"``, or
    ``"git_worktree"`` so renderers can group or filter orphans by type.
    `safe_to_remove` is True when the collector has determined the entry can
    be deleted without data loss (e.g. no uncommitted changes, no live git
    worktree registration). `notes` is a free-form human-readable explanation.
    """

    kind: str
    path: str
    safe_to_remove: bool
    notes: str


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceLevelSnapshot:
    """Workspace-wide metadata — extensions, orphans, and drift findings.

    `extensions` lists the names of installed standalone repos (extensions),
    e.g. ``["winter-github", "winter-harness"]``. `drift_missing` names repo
    directories declared in config but absent on disk; `drift_undeclared` names
    directories present under the projects root but not declared in config.
    """

    root_path: str
    extensions: list[str]
    orphans: list[OrphanSnapshot]
    drift_missing: list[str]
    drift_undeclared: list[str]


@dataclasses.dataclass(frozen=True, slots=True)
class WorktreeSnapshot:
    """Per-repo snapshot inside a feature environment worktree.

    `upstream` is the configured remote-tracking ref (e.g.
    ``"origin/feature/my-branch"``), or None when no upstream is configured.
    `ahead`/`behind` are relative to ``origin/<main-branch>``; the
    `tracking_*` fields are relative to the configured upstream ref.
    `staged`, `unstaged`, and `untracked` are file counts; `dirty` is the
    deduplicated union of staged, unstaged, and untracked. `last_commit_subject`
    is the first line of the most recent commit message, or None when the
    branch has no commits beyond origin/<main>.
    """

    repo: str
    branch: str | None
    upstream: str | None
    ahead: int
    behind: int
    tracking_ahead: int
    tracking_behind: int
    tracking_ref_present: bool
    staged: int
    unstaged: int
    untracked: int
    dirty: int
    last_commit_subject: str | None
    pinned: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class EnvSnapshot:
    """Full snapshot of one feature environment.

    `feature_branch` is the remote feature branch the env is tracking (e.g.
    ``"feature/my-branch"``), or None when the env is not yet connected.
    `port_base` is the env's assigned port base derived from its index.
    """

    name: str
    index: int
    port_base: int
    feature_branch: str | None
    worktrees: list[WorktreeSnapshot]


@dataclasses.dataclass(frozen=True, slots=True)
class SourceCheckoutSnapshot:
    """Snapshot of a source checkout (project main clone or standalone repo).

    `behind_origin` and `ahead_origin` are relative to ``origin/<main-branch>``.
    `dirty` is the count of changed files (staged + unstaged + untracked).
    `drift` lists any drift findings specific to this checkout (e.g. missing
    declared sub-paths).
    """

    repo: str
    branch: str | None
    behind_origin: int
    ahead_origin: int
    dirty: int
    drift: list[str]


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    """Top-level machine-readable workspace state snapshot.

    `schema_version` is 1 for this release. Consumers should reject or warn
    on unexpected versions. All sub-snapshots are pure data with no behavior —
    renderers select the slice they need.
    """

    schema_version: int
    workspace: WorkspaceLevelSnapshot
    environments: list[EnvSnapshot]
    source_checkouts: list[SourceCheckoutSnapshot]
