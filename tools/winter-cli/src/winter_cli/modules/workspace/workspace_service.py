from __future__ import annotations

import concurrent.futures
import logging

import click

from winter_cli.modules.workspace.models import (
    DiffMode,
    FeatureEnvironmentStatus,
    ProjectRepository,
    RepoDiffResult,
    RepoStatus,
    RepoSyncOutcome,
    SyncResult,
    FeatureEnvironment,
    FeatureEnvironmentWorktrees,
    FeatureWorktree,
    Workspace,
    WorktreeDiffResult,
    WorktreeRepoStatus,
    WorktreeSyncReport,
)
from winter_cli.modules.workspace.repository_factory import RepositoryFactory
from winter_cli.modules.workspace.workspace_repository import ReadWorkspaceRepository
from winter_cli.modules.workspace.repo_repository import WriteRepoRepository
from winter_cli.plugins.types import EnvironmentDecorator, WorktreeRepoDecorator

logger = logging.getLogger(__name__)


class WorkspaceService:
    def __init__(
        self,
        worktree_repo: ReadWorkspaceRepository,
        repo_repo: WriteRepoRepository,
        repo_factory: RepositoryFactory,
        workspace: Workspace,
    ) -> None:
        self._worktree_repo = worktree_repo
        self._repo_repo = repo_repo
        self._repo_factory = repo_factory
        self._workspace = workspace

    def get_environment_status(
        self,
        env: FeatureEnvironment,
        project_repos: list[ProjectRepository],
        env_decorators: list[EnvironmentDecorator] | None = None,
    ) -> FeatureEnvironmentStatus:
        """Read the env's git-tracked status and let visual plugins decorate it.

        Plugins receive the freshly-built `FeatureEnvironmentStatus` and the env's
        worktree path, and may write into `status.extensions` to surface a badge
        in the dashboard column header. Pass `env_decorators=None` (default) when
        you don't want decoration — e.g. headless `winter ws status` JSON output.
        """
        status = self._worktree_repo.get_environment_status(env, project_repos)
        if env_decorators:
            for decorator in env_decorators:
                try:
                    decorator(status, env.path)
                except Exception:
                    logger.warning("environment decorator failed", exc_info=True)
        return status

    def get_worktree_repo_statuses(
        self,
        env_worktrees: FeatureEnvironmentWorktrees,
        worktree_repo_decorators: list[WorktreeRepoDecorator] | None = None,
    ) -> list[WorktreeRepoStatus]:
        env = env_worktrees.environment

        wt_repo_statuses: list[WorktreeRepoStatus] = []
        for wt in env_worktrees.worktrees:
            rs = self._repo_repo.get_worktree_status(wt)
            wt_repo_statuses.append(WorktreeRepoStatus(
                worktree=wt,
                branch=rs.branch,
                ahead=rs.ahead,
                behind=rs.behind,
                dirty_count=len(rs.dirty_files),
                tracking_branch=rs.tracking_branch,
                tracking_ahead=rs.tracking_ahead,
            ))

        if worktree_repo_decorators:
            for decorator in worktree_repo_decorators:
                for wt_repo_status in wt_repo_statuses:
                    repo_path = env.path / wt_repo_status.worktree.repository.name
                    decorator(wt_repo_status, repo_path)

        return wt_repo_statuses

    def sync_worktree(self, env_worktrees: FeatureEnvironmentWorktrees) -> WorktreeSyncReport:
        worktrees = env_worktrees.worktrees

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(self._repo_repo.fetch, worktrees))

        outcomes: list[RepoSyncOutcome] = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(self._repo_repo.sync_ff_or_merge, wt): wt for wt in worktrees}
            for future in concurrent.futures.as_completed(futures):
                outcomes.append(future.result())

        repo_names = [wt.repository.name for wt in worktrees]
        outcomes.sort(key=lambda o: repo_names.index(o.repo_name))

        project_repos = [wt.repository for wt in worktrees]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(self._repo_repo.sync_ff_only, project_repos))

        success = all(o.sync_result != SyncResult.diverged for o in outcomes)
        return WorktreeSyncReport(worktree=env_worktrees.environment.name, repos=outcomes, success=success)

    def connect_worktree(self, env_worktrees: FeatureEnvironmentWorktrees, feature_branch: str) -> int:
        count = 0
        for wt in env_worktrees.worktrees:
            if wt.repository.pinned:
                continue
            self._repo_repo.set_upstream(wt, f"origin/{feature_branch}")
            self._repo_repo.set_push_default(wt)
            count += 1
        return count

    def disconnect_worktree(self, env_worktrees: FeatureEnvironmentWorktrees) -> int:
        count = 0
        for wt in env_worktrees.worktrees:
            if wt.repository.pinned:
                continue
            self._repo_repo.unset_upstream(wt)
            count += 1
        return count

    def get_feature_environment_worktrees(
        self, env: FeatureEnvironment, project_repos: list[ProjectRepository],
    ) -> FeatureEnvironmentWorktrees:
        worktrees = [
            FeatureWorktree(workspace=env.workspace, environment=env, repository=repo)
            for repo in project_repos
        ]
        return FeatureEnvironmentWorktrees(environment=env, worktrees=worktrees)

    def get_feature_worktree(self, env: FeatureEnvironment, repo: ProjectRepository) -> FeatureWorktree:
        return FeatureWorktree(workspace=env.workspace, environment=env, repository=repo)

    def get_worktree_diff(
        self, env_worktrees: FeatureEnvironmentWorktrees, mode: DiffMode, repo_filter: str | None = None,
    ) -> WorktreeDiffResult:
        worktrees = env_worktrees.worktrees

        if repo_filter:
            matched = [
                wt for wt in worktrees
                if repo_filter == wt.repository.name
            ]
            if not matched:
                raise click.ClickException(f"Repo '{repo_filter}' not found")
            worktrees = matched

        results: list[RepoDiffResult] = []
        for wt in worktrees:
            diff = self._repo_repo.get_diff(wt, mode)
            if not diff.diff_text:
                continue
            if mode == DiffMode.branch and wt.repository.pinned and diff.ahead == 0:
                continue
            results.append(diff)

        return WorktreeDiffResult(worktree=env_worktrees.environment.name, mode=mode, repos=results)

    def push_worktree(
        self,
        env_worktrees: FeatureEnvironmentWorktrees,
        feature_branch: str,
        repo_names: list[str] | None = None,
    ) -> list[dict]:
        worktrees = [wt for wt in env_worktrees.worktrees if not wt.repository.pinned]

        if repo_names:
            wanted = set(repo_names)
            worktrees = [
                wt for wt in worktrees
                if wt.repository.name in wanted
            ]

        targets: list[FeatureWorktree] = []
        for wt in worktrees:
            status = self._repo_repo.get_worktree_status(wt)
            if status.ahead > 0:
                targets.append(wt)

        results: list[dict] = []
        for wt in targets:
            try:
                commits = self._repo_repo.push(wt, feature_branch)
                results.append({
                    "repo_name": wt.repository.name,
                    "pushed": True,
                    "commits": commits,
                })
            except Exception as exc:
                logger.warning("Push failed for %s: %s", wt.repository.name, exc)
                results.append({
                    "repo_name": wt.repository.name,
                    "pushed": False,
                    "error": str(exc),
                })

        return results

