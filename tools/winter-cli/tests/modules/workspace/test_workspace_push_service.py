from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from winter_cli.config.models import (
    AdoptExtensions,
    ProjectRepositoryConfig,
    WorkspaceConfig,
)
from winter_cli.modules.workspace.env_status_service import EnvStatusService
from winter_cli.modules.workspace.models import (
    FeatureEnvironment,
    FeatureEnvironmentStatus,
    ProjectRepository,
    RepoScope,
    RepoStatus,
    StandaloneRepository,
    Workspace,
)
from winter_cli.modules.workspace.repository_factory import RepositoryFactory
from winter_cli.modules.workspace.workspace_push_service import WorkspacePushService

WORKSPACE_ROOT = Path("/ws")


@pytest.fixture
def workspace() -> Workspace:
    return Workspace(root_path=WORKSPACE_ROOT, session_prefix="t", main_branch="main")


@pytest.fixture
def workspace_config() -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        session_prefix="t",
        main_branch="main",
        adopt_extensions=AdoptExtensions.winter,
        project_repos=[
            ProjectRepositoryConfig(name="demo", url="git@example.com:org/demo.git"),
        ],
    )


class FakeReadWorkspaceRepository:
    def get_environments(
        self, workspace: Workspace, project_repos: list[ProjectRepository]
    ) -> list[FeatureEnvironment]:
        return []

    def get_environment_status(
        self, env: FeatureEnvironment, project_repos: list[ProjectRepository]
    ) -> FeatureEnvironmentStatus:
        return FeatureEnvironmentStatus(environment=env, feature_branch=None)


class FakeWriteRepoRepository:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"FakeWriteRepoRepository.{name} called unexpectedly")


class _FakeWorkspaceRepoWithBranch:
    """Returns one env with a configured feature branch."""

    def __init__(self, env_name: str, feature_branch: str) -> None:
        self._env_name = env_name
        self._feature_branch = feature_branch

    def get_environments(
        self, workspace: Workspace, project_repos: list[ProjectRepository]
    ) -> list[FeatureEnvironment]:
        return [
            FeatureEnvironment(
                workspace=workspace,
                name=self._env_name,
                index=1,
                path=workspace.root_path / self._env_name,
            )
        ]

    def get_environment_status(
        self, env: FeatureEnvironment, project_repos: list[ProjectRepository]
    ) -> FeatureEnvironmentStatus:
        return FeatureEnvironmentStatus(environment=env, feature_branch=self._feature_branch)


class _FakeRepoRepoWithStatus:
    """Returns canned worktree statuses; raises on any push call."""

    def __init__(self, statuses: dict[tuple[str, str], RepoStatus]) -> None:
        self._statuses = statuses

    def get_worktree_status(self, worktree: Any) -> RepoStatus:
        key = (worktree.environment.name, worktree.repository.name)
        return self._statuses[key]

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"_FakeRepoRepoWithStatus.{name} called unexpectedly")


def _pinned_only_factory(workspace: Workspace) -> Any:
    """Stub factory with a single pinned project repo and no standalones."""
    pinned_repo = ProjectRepository(
        name="pinned-repo",
        main_path=workspace.root_path / "projects" / "pinned-repo",
        main_branch="main",
        pinned=True,
    )

    class _StubFactory:
        def get_project_repos(self) -> list[ProjectRepository]:
            return [pinned_repo]

        def get_standalone_repos(self) -> list[StandaloneRepository]:
            return []

    return _StubFactory()


def test_push_all_reports_skipped_when_only_pinned_repos_have_commits(workspace: Workspace) -> None:
    """Default PinnedScope.exclude: env with only pinned repos ahead of upstream emits EnvSkipped."""
    fake_worktree_repo = _FakeWorkspaceRepoWithBranch(env_name="alpha", feature_branch="feature/my-feature")
    fake_repo_repo = _FakeRepoRepoWithStatus(
        statuses={
            ("alpha", "pinned-repo"): RepoStatus(
                name="pinned-repo",
                path=str(workspace.root_path / "alpha" / "pinned-repo"),
                main_branch="main",
                tracking_ahead=1,
            )
        }
    )
    env_status_svc = EnvStatusService(
        worktree_repo=fake_worktree_repo,  # type: ignore[arg-type]
        repo_repo=fake_repo_repo,  # type: ignore[arg-type]
    )
    svc = WorkspacePushService(
        env_status_svc=env_status_svc,
        worktree_repo=fake_worktree_repo,  # type: ignore[arg-type]
        repo_repo=fake_repo_repo,  # type: ignore[arg-type]
        repo_factory=_pinned_only_factory(workspace),
        workspace=workspace,
    )

    report = svc.push_all(scope=RepoScope.project, patterns=None)

    assert len(report.skipped) == 1
    assert report.skipped[0].env == "alpha"
    assert "--include-pinned" in report.skipped[0].reason
    assert report.envs == []


def test_push_all_with_no_envs_returns_empty_report(workspace: Workspace, workspace_config: WorkspaceConfig) -> None:
    """Smoke: push_all returns an empty report when the workspace has no envs or standalones."""
    fake_worktree_repo = FakeReadWorkspaceRepository()
    fake_repo_repo = FakeWriteRepoRepository()
    env_status_svc = EnvStatusService(
        worktree_repo=fake_worktree_repo,  # type: ignore[arg-type]
        repo_repo=fake_repo_repo,  # type: ignore[arg-type]
    )
    svc = WorkspacePushService(
        env_status_svc=env_status_svc,
        worktree_repo=fake_worktree_repo,  # type: ignore[arg-type]
        repo_repo=fake_repo_repo,  # type: ignore[arg-type]
        repo_factory=RepositoryFactory(workspace_config),
        workspace=workspace,
    )

    report = svc.push_all(scope=RepoScope.project, patterns=None)
    assert report.envs == []
    assert report.standalone == []
    assert report.skipped == []
