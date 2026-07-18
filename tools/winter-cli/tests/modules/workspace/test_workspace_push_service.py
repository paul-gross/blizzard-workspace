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
    LocalFastForward,
    PinnedScope,
    ProjectRepository,
    RepoError,
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
    return Workspace(root_path=WORKSPACE_ROOT, service_prefix="t", main_branch="main")


@pytest.fixture
def workspace_config() -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        service_prefix="t",
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


class _PushSpyRepoRepo:
    """Spy write-repo for push: per-worktree push branches + recorded pushes.

    `push_branches` maps repo name → the worktree's own push branch (bare,
    e.g. `abc`) or `None` for a worktree with no upstream. `push` records
    `(repo_name, target_branch)` so a test can pin *which* branch each
    worktree pushed to — proving per-worktree resolution rather than one
    env-wide branch. Every matched repo is reported with commits to push.
    """

    def __init__(self, push_branches: dict[str, str | None]) -> None:
        self._push_branches = push_branches
        self.pushes: list[tuple[str, str | None]] = []

    def get_worktree_status(self, worktree: Any) -> RepoStatus:
        return RepoStatus(
            name=worktree.repository.name,
            path=str(worktree.path),
            main_branch="main",
            ahead=1,
            tracking_ahead=1,
        )

    def get_worktree_push_branch(self, worktree: Any) -> str | None:
        return self._push_branches.get(worktree.repository.name)

    def get_remote_branch_tip(self, worktree: Any, branch: str) -> str | None:
        # No remote ref yet — the post-push fast-forward is a no-op, keeping
        # these tests focused on push targeting.
        return None

    def push(self, worktree: Any, feature_branch: str | None = None) -> int:
        self.pushes.append((worktree.repository.name, feature_branch))
        return 1

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"_PushSpyRepoRepo.{name} called unexpectedly")


def _two_repo_factory(workspace: Workspace, repo_names: list[str]) -> Any:
    """Stub factory with several non-pinned project repos, in the given order."""
    repos = [
        ProjectRepository(
            name=name,
            main_path=workspace.root_path / "projects" / name,
            main_branch="main",
        )
        for name in repo_names
    ]

    class _StubFactory:
        def get_project_repos(self) -> list[ProjectRepository]:
            return repos

        def get_standalone_repos(self) -> list[StandaloneRepository]:
            return []

    return _StubFactory()


def _push_service(workspace: Workspace, repo_repo: Any, factory: Any) -> WorkspacePushService:
    worktree_repo = _FakeWorkspaceRepoWithBranch(env_name="alpha", feature_branch="ignored")
    env_status_svc = EnvStatusService(
        worktree_repo=worktree_repo,  # type: ignore[arg-type]
        repo_repo=repo_repo,  # type: ignore[arg-type]
    )
    return WorkspacePushService(
        env_status_svc=env_status_svc,
        worktree_repo=worktree_repo,  # type: ignore[arg-type]
        repo_repo=repo_repo,  # type: ignore[arg-type]
        repo_factory=factory,
        workspace=workspace,
    )


def test_push_resolves_each_worktree_to_its_own_branch(workspace: Workspace) -> None:
    """Mixed env: repos tracking different branches each push to their own ref."""
    repo_repo = _PushSpyRepoRepo({"repo-a": "abc", "repo-b": "xyz"})
    svc = _push_service(workspace, repo_repo, _two_repo_factory(workspace, ["repo-a", "repo-b"]))

    report = svc.push_all(scope=RepoScope.project, patterns=None)

    assert dict(repo_repo.pushes) == {"repo-a": "abc", "repo-b": "xyz"}
    outcomes = {o.repo_name: o for o in report.envs[0].repos}
    assert outcomes["repo-a"].pushed and outcomes["repo-b"].pushed
    assert report.skipped == []


def test_push_target_is_repo_order_independent(workspace: Workspace) -> None:
    """Reversing repo order leaves each worktree's own push target unchanged."""
    repo_repo = _PushSpyRepoRepo({"repo-a": "abc", "repo-b": "xyz"})
    svc = _push_service(workspace, repo_repo, _two_repo_factory(workspace, ["repo-b", "repo-a"]))

    svc.push_all(scope=RepoScope.project, patterns=None)

    assert dict(repo_repo.pushes) == {"repo-a": "abc", "repo-b": "xyz"}


def test_push_reports_no_upstream_per_repo_without_skipping_env(workspace: Workspace) -> None:
    """A no-upstream worktree is reported per-repo; its connected sibling still pushes."""
    repo_repo = _PushSpyRepoRepo({"repo-a": "abc", "repo-b": None})
    svc = _push_service(workspace, repo_repo, _two_repo_factory(workspace, ["repo-a", "repo-b"]))

    report = svc.push_all(scope=RepoScope.project, patterns=None)

    assert repo_repo.pushes == [("repo-a", "abc")]  # repo-b never reaches push
    outcomes = {o.repo_name: o for o in report.envs[0].repos}
    assert outcomes["repo-a"].pushed is True
    assert outcomes["repo-b"].pushed is False
    assert "no upstream" in (outcomes["repo-b"].error or "")
    assert report.skipped == []  # per-repo outcome, not an env-wide group skip


def test_push_pinned_plain_pushes_without_resolving_a_branch(workspace: Workspace) -> None:
    """Pinned worktrees plain-push (target_branch=None) and never resolve a feature branch."""
    repo_repo = _PushSpyRepoRepo({})  # empty: get_worktree_push_branch must not matter for pinned

    pinned_repo = ProjectRepository(
        name="pinned-repo",
        main_path=workspace.root_path / "projects" / "pinned-repo",
        main_branch="main",
        pinned=True,
    )

    class _PinnedFactory:
        def get_project_repos(self) -> list[ProjectRepository]:
            return [pinned_repo]

        def get_standalone_repos(self) -> list[StandaloneRepository]:
            return []

    svc = _push_service(workspace, repo_repo, _PinnedFactory())

    report = svc.push_all(scope=RepoScope.project, patterns=None, pinned_scope=PinnedScope.include)

    assert repo_repo.pushes == [("pinned-repo", None)]
    assert report.envs[0].repos[0].pushed is True


class _FfSpyRepoRepo:
    """Spy write-repo exercising the post-push local-branch fast-forward.

    Every worktree pushes to `push_branch` (default `main`, but any shared branch
    name — the ff is name-independent). `ff_results` maps repo name → the
    `LocalFastForward | None` that `fast_forward_local_branch` returns, letting a
    test drive the in-sync / behind / dirty branches without a real git repo.
    Records `(repo_name, branch, pre_remote_tip)` for each ff call.
    """

    def __init__(self, ff_results: dict[str, LocalFastForward | None], push_branch: str = "main") -> None:
        self._ff_results = ff_results
        self._push_branch = push_branch
        self.pushes: list[tuple[str, str | None]] = []
        self.ff_calls: list[tuple[str, str, str]] = []

    def get_worktree_status(self, worktree: Any) -> RepoStatus:
        return RepoStatus(
            name=worktree.repository.name,
            path=str(worktree.path),
            main_branch="main",
            ahead=1,
            tracking_ahead=1,
        )

    def get_worktree_push_branch(self, worktree: Any) -> str | None:
        return self._push_branch

    def get_remote_branch_tip(self, worktree: Any, branch: str) -> str | None:
        assert branch == self._push_branch
        return f"pre-{worktree.repository.name}"

    def push(self, worktree: Any, feature_branch: str | None = None) -> int:
        self.pushes.append((worktree.repository.name, feature_branch))
        return 1

    def fast_forward_local_branch(self, repo: Any, branch: str, pre_remote_tip: str) -> LocalFastForward | None:
        self.ff_calls.append((repo.name, branch, pre_remote_tip))
        return self._ff_results.get(repo.name)

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"_FfSpyRepoRepo.{name} called unexpectedly")


def test_push_fast_forwards_local_branch_when_in_sync(workspace: Workspace) -> None:
    """A push reports the local-branch fast-forward, keyed on the pushed branch name."""
    repo_repo = _FfSpyRepoRepo({"repo-a": LocalFastForward(branch="main", advanced=True, commits=1)})
    svc = _push_service(workspace, repo_repo, _two_repo_factory(workspace, ["repo-a"]))

    report = svc.push_all(scope=RepoScope.project, patterns=None)

    assert repo_repo.ff_calls == [("repo-a", "main", "pre-repo-a")]
    outcome = report.envs[0].repos[0]
    assert outcome.pushed is True
    assert outcome.local_ff == LocalFastForward(branch="main", advanced=True, commits=1)


def test_push_ff_is_name_independent(workspace: Workspace) -> None:
    """The fast-forward applies to any shared branch name, not just the repo's main."""
    repo_repo = _FfSpyRepoRepo(
        {"repo-a": LocalFastForward(branch="develop", advanced=True, commits=2)},
        push_branch="develop",
    )
    svc = _push_service(workspace, repo_repo, _two_repo_factory(workspace, ["repo-a"]))

    report = svc.push_all(scope=RepoScope.project, patterns=None)

    assert repo_repo.ff_calls == [("repo-a", "develop", "pre-repo-a")]
    assert report.envs[0].repos[0].local_ff == LocalFastForward(branch="develop", advanced=True, commits=2)


def test_push_per_repo_ff_evaluation(workspace: Workspace) -> None:
    """Two repos pushing to the same branch: only the in-sync one advances."""
    repo_repo = _FfSpyRepoRepo(
        {
            "repo-a": LocalFastForward(branch="main", advanced=True, commits=1),
            "repo-b": LocalFastForward(branch="main", advanced=False, skipped_reason="not in sync"),
        }
    )
    svc = _push_service(workspace, repo_repo, _two_repo_factory(workspace, ["repo-a", "repo-b"]))

    report = svc.push_all(scope=RepoScope.project, patterns=None)

    outcomes = {o.repo_name: o for o in report.envs[0].repos}
    assert outcomes["repo-a"].local_ff is not None and outcomes["repo-a"].local_ff.advanced is True
    assert outcomes["repo-b"].local_ff is not None and outcomes["repo-b"].local_ff.advanced is False
    assert outcomes["repo-b"].local_ff.skipped_reason == "not in sync"


def test_push_ff_failure_does_not_fail_the_push(workspace: Workspace) -> None:
    """A fast-forward that raises is surfaced as a skip note, not a push failure."""

    class _RaisingFfRepoRepo(_FfSpyRepoRepo):
        def fast_forward_local_branch(self, repo: Any, branch: str, pre_remote_tip: str) -> LocalFastForward | None:
            raise RepoError("ff blew up")

    repo_repo = _RaisingFfRepoRepo({})
    svc = _push_service(workspace, repo_repo, _two_repo_factory(workspace, ["repo-a"]))

    report = svc.push_all(scope=RepoScope.project, patterns=None)

    outcome = report.envs[0].repos[0]
    assert outcome.pushed is True  # push itself succeeded
    assert report.success is True
    assert outcome.local_ff is not None and outcome.local_ff.skipped_reason == "ff failed"


def test_push_first_push_skips_ff(workspace: Workspace) -> None:
    """A first push (no remote ref beforehand) has no in-sync local branch to advance."""

    class _NoRemoteTipRepoRepo(_FfSpyRepoRepo):
        def get_remote_branch_tip(self, worktree: Any, branch: str) -> str | None:
            return None  # remote branch does not exist yet

    repo_repo = _NoRemoteTipRepoRepo({})
    svc = _push_service(workspace, repo_repo, _two_repo_factory(workspace, ["repo-a"]))

    report = svc.push_all(scope=RepoScope.project, patterns=None)

    # fast_forward_local_branch is never consulted (would append to ff_calls).
    assert repo_repo.ff_calls == []
    assert report.envs[0].repos[0].local_ff is None


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
