from __future__ import annotations

import concurrent.futures
import dataclasses
import fnmatch
import logging

import click

from winter_cli.modules.workspace.models import (
    DiffMode,
    EnvSkipped,
    FeatureEnvironmentStatus,
    FetchReport,
    PinnedScope,
    ProjectRepository,
    PullMode,
    PullReport,
    PushReport,
    RepoDiffResult,
    RepoError,
    RepoFetchOutcome,
    RepoPushOutcome,
    RepoScope,
    RepoStatus,
    RepoSyncOutcome,
    StandaloneRepository,
    SyncResult,
    FeatureEnvironment,
    FeatureEnvironmentWorktrees,
    FeatureWorktree,
    Workspace,
    WorktreeDiffResult,
    WorktreeFetchReport,
    WorktreePushReport,
    WorktreeRepoStatus,
    WorktreeSyncReport,
)
from winter_cli.modules.workspace.repository_factory import RepositoryFactory
from winter_cli.modules.workspace.workspace_repository import ReadWorkspaceRepository
from winter_cli.modules.workspace.repo_repository import WriteRepoRepository
from winter_cli.plugins.types import EnvironmentDecorator, WorktreeRepoDecorator

logger = logging.getLogger(__name__)

# Codeberg.org (and most SSH-based git hosts) throttle simultaneous SSH
# connections per source IP. Empirically the cap is around 5; staying at 4
# keeps a comfortable margin while still parallelizing 4× over serial git ops.
_GIT_PARALLELISM = 4


@dataclasses.dataclass
class _PullTarget:
    """Per-worktree integration target resolved up-front for fan-out."""
    env_name: str
    worktree: FeatureWorktree
    target_ref: str


def _matches_pattern(env_name: str, repo_name: str, pattern: str) -> bool:
    """Match `<env>/<repo>` against a segment-aware glob.

    Bare patterns (no '/') are treated as `<pattern>/*`. Each segment uses
    fnmatch — `*` matches anything within a segment, `?` matches one char.
    `*` does not cross `/`, so `*/winter` matches every env's winter worktree
    but not `alpha/winter-product`.
    """
    if "/" not in pattern:
        pattern = f"{pattern}/*"
    env_pat, repo_pat = pattern.split("/", 1)
    return fnmatch.fnmatchcase(env_name, env_pat) and fnmatch.fnmatchcase(repo_name, repo_pat)


def _matches_any_pattern(env_name: str, repo_name: str, patterns: list[str]) -> bool:
    return any(_matches_pattern(env_name, repo_name, p) for p in patterns)


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
        """Sync a worktree's repos against `origin/<main>` (ff-or-merge).

        Sync intentionally falls back to a merge commit when ff-only fails —
        this keeps source-checkout fast-forwards aligned with the worktree even
        when the worktree has drifted. Use `winter ws pull` for the ff-only
        flow against the feature branch.
        """
        worktrees = env_worktrees.worktrees
        self._fetch_in_parallel(worktrees)

        outcomes = self._integrate_in_parallel([
            (wt, f"origin/{wt.repository.main_branch}") for wt in worktrees
        ], mode=PullMode.merge, autostash=False)
        outcomes = self._sort_outcomes(outcomes, [wt.repository.name for wt in worktrees])

        project_repos = [wt.repository for wt in worktrees]
        with concurrent.futures.ThreadPoolExecutor(max_workers=_GIT_PARALLELISM) as pool:
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

    def fetch_all(self, scope: RepoScope, patterns: list[str] | None = None) -> FetchReport:
        """Fetch project worktrees matched by `patterns`, and/or standalone repos.

        `patterns` filters project worktrees by segment-aware glob over
        `<env>/<repo>` (empty list ⇒ `*/*`). Standalone repos are fetched
        when `scope` includes standalone and ignore `patterns`.
        """
        patterns = patterns or ["*/*"]
        project_repos = self._repo_factory.get_project_repos()
        envs = self._select_envs(scope, project_repos)
        standalone_repos = self._repo_factory.get_standalone_repos() if scope.includes_standalone else []

        env_worktrees_by_env = self._build_env_worktrees_map(envs, project_repos)
        matched_by_env: dict[str, list[FeatureWorktree]] = {
            env.name: [
                wt for wt in env_worktrees_by_env[env.name].worktrees
                if _matches_any_pattern(env.name, wt.repository.name, patterns)
            ]
            for env in envs
        }
        matched_envs = [env for env in envs if matched_by_env[env.name]]
        all_worktrees: list[tuple[str, FeatureWorktree]] = [
            (env.name, wt) for env in matched_envs for wt in matched_by_env[env.name]
        ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=_GIT_PARALLELISM) as pool:
            wt_futures = {
                pool.submit(self._repo_repo.fetch, wt): (env_name_, wt.repository.name)
                for env_name_, wt in all_worktrees
            }
            standalone_futures = {
                pool.submit(self._repo_repo.fetch_standalone, repo): repo.name
                for repo in standalone_repos
            }

            wt_results: dict[str, list[RepoFetchOutcome]] = {env.name: [] for env in matched_envs}
            for fut, (env_name_, repo_name) in wt_futures.items():
                wt_results[env_name_].append(self._collect_fetch(fut, repo_name))

            standalone_results = [
                self._collect_fetch(fut, repo_name)
                for fut, repo_name in standalone_futures.items()
            ]

        env_reports: list[WorktreeFetchReport] = []
        for env in matched_envs:
            repo_order = [wt.repository.name for wt in matched_by_env[env.name]]
            wt_results[env.name].sort(key=lambda o: repo_order.index(o.repo_name))
            env_reports.append(WorktreeFetchReport(worktree=env.name, repos=wt_results[env.name]))

        standalone_results.sort(key=lambda o: o.repo_name)
        return FetchReport(envs=env_reports, standalone=standalone_results)

    def pull_all(
        self,
        scope: RepoScope,
        patterns: list[str] | None = None,
        mode: PullMode = PullMode.ff_only,
        autostash: bool = False,
    ) -> PullReport:
        """Fetch + integrate (ff-only / merge / rebase) project worktrees matched
        by `patterns`, and/or standalone repos.

        `patterns` filters project worktrees by segment-aware glob over
        `<env>/<repo>` (empty list ⇒ `*/*`). Pinned worktrees integrate from
        `origin/<main_branch>`; non-pinned from `origin/<feature_branch>`;
        standalone repos from their tracked upstream. Envs whose matched
        non-pinned worktrees have no feature branch are skipped (pinned
        worktrees still integrate against main).
        """
        patterns = patterns or ["*/*"]
        project_repos = self._repo_factory.get_project_repos()
        envs = self._select_envs(scope, project_repos)
        standalone_repos = self._repo_factory.get_standalone_repos() if scope.includes_standalone else []
        env_worktrees_by_env = self._build_env_worktrees_map(envs, project_repos)

        matched_by_env: dict[str, list[FeatureWorktree]] = {
            env.name: [
                wt for wt in env_worktrees_by_env[env.name].worktrees
                if _matches_any_pattern(env.name, wt.repository.name, patterns)
            ]
            for env in envs
        }
        matched_envs = [env for env in envs if matched_by_env[env.name]]

        targets, skipped = self._build_pull_targets(matched_envs, matched_by_env, project_repos)

        # Stage 1: fetch everything in parallel. Errors are logged (run `winter
        # ws fetch` to see them per-repo) but do not abort the pull — stale local
        # refs simply produce up-to-date / diverged outcomes from the integrate.
        worktrees_to_fetch = [t.worktree for t in targets]
        self._fetch_in_parallel(worktrees_to_fetch, log_errors=True)
        self._fetch_standalone_in_parallel(standalone_repos, log_errors=True)

        # Stage 2: fan all integrates out across envs at once.
        all_outcomes = self._integrate_in_parallel(
            [(t.worktree, t.target_ref) for t in targets],
            mode=mode,
            autostash=autostash,
        )
        outcomes_by_env: dict[str, list[RepoSyncOutcome]] = {env.name: [] for env in matched_envs}
        for outcome, target in zip(all_outcomes, targets):
            outcomes_by_env[target.env_name].append(outcome)

        env_reports: list[WorktreeSyncReport] = []
        for env in matched_envs:
            if not outcomes_by_env[env.name]:
                continue
            repo_order = [t.worktree.repository.name for t in targets if t.env_name == env.name]
            env_outcomes = self._sort_outcomes(outcomes_by_env[env.name], repo_order)
            success = all(o.sync_result != SyncResult.diverged for o in env_outcomes)
            env_reports.append(WorktreeSyncReport(worktree=env.name, repos=env_outcomes, success=success))

        # Stage 3: standalone integrates in parallel.
        standalone_outcomes = self._integrate_standalone_in_parallel(standalone_repos, mode, autostash)

        return PullReport(envs=env_reports, standalone=standalone_outcomes, skipped=skipped)

    def push_all(
        self,
        scope: RepoScope,
        patterns: list[str] | None = None,
        pinned_scope: PinnedScope = PinnedScope.exclude,
    ) -> PushReport:
        """Push project worktrees matched by `patterns`, and/or standalone repos.

        `patterns` filters project worktrees by segment-aware glob over
        `<env>/<repo>` (empty list ⇒ `*/*`). `pinned_scope` controls whether
        pinned worktrees are included, excluded (default), or pushed alone.
        Non-pinned worktrees push HEAD:refs/heads/<feature_branch>; pinned
        worktrees plain-push to whatever their local branch tracks. Standalone
        repos plain-push to their tracked upstream and ignore `patterns`. Only
        repos with commits ahead of upstream are pushed.
        """
        patterns = patterns or ["*/*"]
        project_repos = self._repo_factory.get_project_repos()
        envs = self._select_envs(scope, project_repos)
        standalone_repos = self._repo_factory.get_standalone_repos() if scope.includes_standalone else []

        env_reports: list[WorktreePushReport] = []
        skipped: list[EnvSkipped] = []
        for env in envs:
            env_status = self._worktree_repo.get_environment_status(env, project_repos)
            env_worktrees = self.get_feature_environment_worktrees(env, project_repos)

            worktrees = [
                wt for wt in env_worktrees.worktrees
                if self._matches_pinned_scope(wt, pinned_scope)
                and _matches_any_pattern(env.name, wt.repository.name, patterns)
            ]
            if not worktrees:
                continue

            non_pinned = [wt for wt in worktrees if not wt.repository.pinned]
            if non_pinned and not env_status.feature_branch:
                skipped.append(EnvSkipped(
                    worktree=env.name,
                    reason="not connected — run `winter ws connect` first",
                ))
                worktrees = [wt for wt in worktrees if wt.repository.pinned]

            outcomes = [
                self._push_one(wt, env_status.feature_branch)
                for wt in worktrees
                if self._has_commits_to_push(wt)
            ]
            env_reports.append(WorktreePushReport(worktree=env.name, repos=outcomes))

        standalone_outcomes: list[RepoPushOutcome] = []
        for repo in standalone_repos:
            if self._repo_repo.get_standalone_upstream(repo) is None:
                standalone_outcomes.append(RepoPushOutcome(
                    repo_name=repo.name,
                    pushed=False,
                    error="no upstream — set one with `git branch --set-upstream-to`",
                ))
                continue
            if self._repo_repo.get_standalone_tracking_ahead(repo) == 0:
                continue
            standalone_outcomes.append(self._push_one_standalone(repo))

        return PushReport(envs=env_reports, standalone=standalone_outcomes, skipped=skipped)

    def _has_commits_to_push(self, wt: FeatureWorktree) -> bool:
        status = self._repo_repo.get_worktree_status(wt)
        if wt.repository.pinned:
            return status.tracking_ahead > 0
        return status.tracking_ahead > 0 or status.ahead > 0

    @staticmethod
    def _matches_pinned_scope(wt: FeatureWorktree, pinned_scope: PinnedScope) -> bool:
        if wt.repository.pinned:
            return pinned_scope.matches_pinned
        return pinned_scope.matches_non_pinned

    def _push_one(self, wt: FeatureWorktree, feature_branch: str | None) -> RepoPushOutcome:
        target_branch = None if wt.repository.pinned else feature_branch
        try:
            commits = self._repo_repo.push(wt, target_branch)
        except RepoError as exc:
            logger.warning("Push failed for %s: %s", wt.repository.name, exc)
            return RepoPushOutcome(repo_name=wt.repository.name, pushed=False, error=str(exc))
        return RepoPushOutcome(repo_name=wt.repository.name, pushed=True, commits=commits)

    def _push_one_standalone(self, repo: StandaloneRepository) -> RepoPushOutcome:
        try:
            commits = self._repo_repo.push_standalone(repo)
        except RepoError as exc:
            logger.warning("Push failed for standalone %s: %s", repo.name, exc)
            return RepoPushOutcome(repo_name=repo.name, pushed=False, error=str(exc))
        return RepoPushOutcome(repo_name=repo.name, pushed=True, commits=commits)

    def _build_pull_targets(
        self,
        envs: list[FeatureEnvironment],
        matched_by_env: dict[str, list[FeatureWorktree]],
        project_repos: list[ProjectRepository],
    ) -> tuple[list[_PullTarget], list[EnvSkipped]]:
        targets: list[_PullTarget] = []
        skipped: list[EnvSkipped] = []
        for env in envs:
            env_status = self._worktree_repo.get_environment_status(env, project_repos)
            worktrees = matched_by_env[env.name]
            non_pinned = [wt for wt in worktrees if not wt.repository.pinned]
            pinned = [wt for wt in worktrees if wt.repository.pinned]

            if non_pinned and not env_status.feature_branch:
                skipped.append(EnvSkipped(
                    worktree=env.name,
                    reason="not connected — run `winter ws connect` first",
                ))
                wts_to_pull = pinned
            else:
                wts_to_pull = worktrees

            for wt in wts_to_pull:
                target_ref = (
                    f"origin/{wt.repository.main_branch}"
                    if wt.repository.pinned
                    else f"origin/{env_status.feature_branch}"
                )
                targets.append(_PullTarget(env_name=env.name, worktree=wt, target_ref=target_ref))
        return targets, skipped

    def _build_env_worktrees_map(
        self,
        envs: list[FeatureEnvironment],
        project_repos: list[ProjectRepository],
    ) -> dict[str, FeatureEnvironmentWorktrees]:
        return {
            env.name: self.get_feature_environment_worktrees(env, project_repos)
            for env in envs
        }

    def _fetch_in_parallel(
        self,
        worktrees: list[FeatureWorktree],
        log_errors: bool = False,
    ) -> None:
        if not worktrees:
            return
        with concurrent.futures.ThreadPoolExecutor(max_workers=_GIT_PARALLELISM) as pool:
            futures = {pool.submit(self._repo_repo.fetch, wt): wt for wt in worktrees}
            for fut, wt in futures.items():
                try:
                    fut.result()
                except RepoError as exc:
                    if log_errors:
                        logger.warning("Fetch failed for %s: %s", wt.repository.name, exc)
                    else:
                        raise

    def _fetch_standalone_in_parallel(
        self,
        repos: list[StandaloneRepository],
        log_errors: bool = False,
    ) -> None:
        if not repos:
            return
        with concurrent.futures.ThreadPoolExecutor(max_workers=_GIT_PARALLELISM) as pool:
            futures = {pool.submit(self._repo_repo.fetch_standalone, r): r for r in repos}
            for fut, repo in futures.items():
                try:
                    fut.result()
                except RepoError as exc:
                    if log_errors:
                        logger.warning("Fetch failed for standalone %s: %s", repo.name, exc)
                    else:
                        raise

    def _integrate_in_parallel(
        self,
        targets: list[tuple[FeatureWorktree, str]],
        mode: PullMode,
        autostash: bool,
    ) -> list[RepoSyncOutcome]:
        if not targets:
            return []
        results: list[RepoSyncOutcome | None] = [None] * len(targets)
        with concurrent.futures.ThreadPoolExecutor(max_workers=_GIT_PARALLELISM) as pool:
            futures = {
                pool.submit(self._repo_repo.integrate, wt, target_ref, mode, autostash): idx
                for idx, (wt, target_ref) in enumerate(targets)
            }
            for fut, idx in futures.items():
                results[idx] = fut.result()
        return [r for r in results if r is not None]

    def _integrate_standalone_in_parallel(
        self,
        repos: list[StandaloneRepository],
        mode: PullMode,
        autostash: bool,
    ) -> list[RepoSyncOutcome]:
        if not repos:
            return []
        outcomes: list[RepoSyncOutcome] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=_GIT_PARALLELISM) as pool:
            futures = {
                pool.submit(self._repo_repo.integrate_standalone, r, mode, autostash): r
                for r in repos
            }
            for fut in concurrent.futures.as_completed(futures):
                outcomes.append(fut.result())
        outcomes.sort(key=lambda o: o.repo_name)
        return outcomes

    @staticmethod
    def _sort_outcomes(outcomes: list[RepoSyncOutcome], repo_order: list[str]) -> list[RepoSyncOutcome]:
        return sorted(outcomes, key=lambda o: repo_order.index(o.repo_name))

    def _select_envs(
        self,
        scope: RepoScope,
        project_repos: list[ProjectRepository],
    ) -> list[FeatureEnvironment]:
        """Resolve envs to operate on based on scope.

        Returns no envs when scope excludes project repos (e.g. --standalone).
        Pattern filtering happens at the worktree level in the caller.
        """
        if not scope.includes_project:
            return []
        return self._worktree_repo.get_environments(self._workspace, project_repos)

    @staticmethod
    def _collect_fetch(fut: concurrent.futures.Future, repo_name: str) -> RepoFetchOutcome:
        try:
            fut.result()
            return RepoFetchOutcome(repo_name=repo_name, success=True)
        except RepoError as exc:
            return RepoFetchOutcome(repo_name=repo_name, success=False, error=str(exc))
