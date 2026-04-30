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
    diverged = "diverged"


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
