from __future__ import annotations

import dataclasses
import enum
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class IWorkspaceRepository(Protocol):
    """Structural interface for any repo `winter ws init` reconciles.

    Both `ProjectRepository` and `StandaloneRepository` satisfy it. Used by helpers
    that don't care which kind of repo they're working with (writing git-excludes,
    running post-clone `cmd` lists, surfacing errors in the reporter).
    """
    name: str
    url: str | None
    git_excludes: list[str]
    cmd: list[str]


@dataclasses.dataclass
class Workspace:
    """The workspace as a whole — high-level attributes that span all environments and repositories."""
    root_path: Path
    session_prefix: str
    main_branch: str


@dataclasses.dataclass
class ProjectRepository:
    """A project repo that participates in feature environments (e.g. winter-app, winter-api).

    `name` doubles as the directory under `projects/` and as the user-facing label.
    It defaults to the trailing path segment of `url` (with `.git` stripped) when not
    explicitly set in the config, and can be overridden to give a clone a friendlier
    handle than its canonical repo name.
    """
    name: str
    main_path: Path
    main_branch: str
    pinned: bool = False
    url: str | None = None
    git_excludes: list[str] = dataclasses.field(default_factory=list)
    cmd: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class StandaloneRepository:
    """A repo that exists independently of feature environments.

    Covers both the implicit singletons (workspace, product, harness) and
    user-declared standalone repos (e.g. winter extensions). Singletons are
    discovered from the filesystem and only carry `name`/`path`; user-declared
    standalones come from `[[standalone_repository]]` in the workspace config
    and additionally carry `url`, `main_branch`, `git_excludes`, `cmd`, and an
    optional `prefix` that overrides the extension symlink prefix.
    """
    name: str
    path: Path
    main_branch: str | None = None
    url: str | None = None
    git_excludes: list[str] = dataclasses.field(default_factory=list)
    cmd: list[str] = dataclasses.field(default_factory=list)
    prefix: str | None = None


class DiffMode(enum.Enum):
    uncommitted = "uncommitted"
    staged = "staged"
    branch = "branch"


@dataclasses.dataclass
class FeatureEnvironment:
    """A named environment (alpha, beta, gamma) for feature development."""
    workspace: Workspace
    name: str
    index: int
    path: Path


@dataclasses.dataclass
class FeatureEnvironmentWorktrees:
    """All feature worktrees within an environment — used for bulk operations across repos."""
    environment: FeatureEnvironment
    worktrees: list[FeatureWorktree]


@dataclasses.dataclass
class FeatureWorktree:
    """A feature worktree — the intersection of an environment and a project repository."""
    workspace: Workspace
    environment: FeatureEnvironment
    repository: ProjectRepository

    @property
    def path(self) -> Path:
        return self.environment.path / self.repository.name
