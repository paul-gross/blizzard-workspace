from __future__ import annotations

import dataclasses
import enum
from typing import Any, Protocol, runtime_checkable

from winter_cli.modules.workspace.models.domain_model import (
    DiffMode,
    FeatureEnvironment,
    FeatureWorktree,
    StandaloneRepository,
)


@runtime_checkable
class IRepoStatus(Protocol):
    @property
    def name(self) -> str: ...
    branch: str | None
    ahead: int
    behind: int
    dirty_count: int
    tracking_ahead: int


@dataclasses.dataclass
class RepoCommit:
    """A single commit on a branch — abbreviated hash and first line of the message."""
    short_hash: str
    message: str


@dataclasses.dataclass
class RepoStatus:
    """Detailed git status of a single repository — branch, ahead/behind, dirty files, and recent commits."""
    name: str
    path: str
    main_branch: str | None
    branch: str | None = None
    ahead: int = 0
    behind: int = 0
    dirty_files: list[str] = dataclasses.field(default_factory=list)
    recent_commits: list[RepoCommit] = dataclasses.field(default_factory=list)
    tracking_branch: str | None = None
    tracking_ahead: int = 0


@dataclasses.dataclass
class StandaloneRepoStatus:
    """Lightweight status for standalone repositories (workspace, product, harness)."""
    repository: StandaloneRepository
    branch: str | None = None
    ahead: int = 0
    behind: int = 0
    dirty_count: int = 0
    tracking_ahead: int = 0
    latest_commit: str | None = None

    @property
    def name(self) -> str:
        return self.repository.name


@dataclasses.dataclass
class FeatureEnvironmentStatus:
    """Runtime status of a feature environment — feature branch plus extension-contributed badges.

    `extensions` is keyed by extension prefix (e.g. `wst` for winter-service-tmux); each value
    is a short badge string an `EnvironmentDecorator` plugin contributed for this env. Renderers
    append the values to the env header so each plugin can advertise whatever it wants.
    """
    environment: FeatureEnvironment
    feature_branch: str | None
    extensions: dict[str, str] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class FeatureEnvironmentOverview:
    """Full picture of a feature environment — its status plus per-repo statuses."""
    status: FeatureEnvironmentStatus
    repo_statuses: list[WorktreeRepoStatus]


class SyncResult(enum.Enum):
    fast_forwarded = "fast_forwarded"
    up_to_date = "up_to_date"
    merged = "merged"
    rebased = "rebased"
    diverged = "diverged"
    no_upstream = "no_upstream"


@dataclasses.dataclass
class RepoSyncOutcome:
    """Result of syncing a single repo — whether it fast-forwarded, merged, or diverged."""
    repo_name: str
    sync_result: SyncResult
    ahead: int = 0
    behind: int = 0


@dataclasses.dataclass
class RepoDiffResult:
    """Diff output for a single repo — the raw diff text and summary statistics."""
    repo_name: str
    diff_text: str
    ahead: int
    files_changed: int
    insertions: int
    deletions: int


@dataclasses.dataclass
class WorktreeRepoStatus:
    """Summary status of one repo within a feature worktree — used in worktree-level views."""
    worktree: FeatureWorktree
    branch: str | None
    ahead: int
    behind: int
    dirty_count: int
    tracking_branch: str | None = None
    tracking_ahead: int = 0
    extensions: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class WorktreeSyncReport:
    """Report from syncing all repos in a worktree — per-repo outcomes and overall success."""
    worktree: str
    repos: list[RepoSyncOutcome]
    success: bool


@dataclasses.dataclass
class WorktreeDiffResult:
    """Combined diff results across all repos in a worktree."""
    worktree: str
    mode: DiffMode
    repos: list[RepoDiffResult]


@dataclasses.dataclass
class RepoFetchOutcome:
    """Result of fetching one repo — name and whether the fetch succeeded."""
    repo_name: str
    success: bool
    error: str | None = None


@dataclasses.dataclass
class WorktreeFetchReport:
    """Per-env fetch outcomes within a multi-env / multi-scope fetch."""
    worktree: str
    repos: list[RepoFetchOutcome]


@dataclasses.dataclass
class FetchReport:
    """Top-level fetch report spanning project worktrees and standalone repos."""
    envs: list[WorktreeFetchReport]
    standalone: list[RepoFetchOutcome]

    @property
    def success(self) -> bool:
        if any(not r.success for env in self.envs for r in env.repos):
            return False
        if any(not r.success for r in self.standalone):
            return False
        return True


@dataclasses.dataclass
class EnvSkipped:
    """An env skipped by a multi-repo op (typically: not connected to a feature branch)."""
    worktree: str
    reason: str


@dataclasses.dataclass
class PullReport:
    """Top-level pull report — per-env sync results plus standalone outcomes."""
    envs: list[WorktreeSyncReport]
    standalone: list[RepoSyncOutcome]
    skipped: list[EnvSkipped] = dataclasses.field(default_factory=list)

    @property
    def success(self) -> bool:
        if any(not e.success for e in self.envs):
            return False
        if any(o.sync_result in (SyncResult.diverged, SyncResult.no_upstream) for o in self.standalone):
            return False
        if self.skipped:
            return False
        return True


@dataclasses.dataclass
class RepoPushOutcome:
    """Result of pushing one repo — name, push status, commits delivered, error if any."""
    repo_name: str
    pushed: bool
    commits: int = 0
    error: str | None = None


@dataclasses.dataclass
class WorktreePushReport:
    """Per-env push outcomes."""
    worktree: str
    repos: list[RepoPushOutcome]


@dataclasses.dataclass
class PushReport:
    """Top-level push report — per-env outcomes plus standalone outcomes."""
    envs: list[WorktreePushReport]
    standalone: list[RepoPushOutcome]
    skipped: list[EnvSkipped] = dataclasses.field(default_factory=list)

    @property
    def success(self) -> bool:
        if any(not r.pushed for env in self.envs for r in env.repos):
            return False
        if any(not r.pushed for r in self.standalone):
            return False
        if self.skipped:
            return False
        return True
