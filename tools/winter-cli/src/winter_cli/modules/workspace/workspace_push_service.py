from __future__ import annotations

import logging

from winter_cli.modules.workspace.env_status_service import EnvStatusService
from winter_cli.modules.workspace.models import (
    EnvPushReport,
    EnvSkipped,
    FeatureWorktree,
    LocalFastForward,
    PinnedScope,
    PushReport,
    RepoError,
    RepoPushOutcome,
    RepoScope,
    StandaloneRepository,
    Workspace,
)
from winter_cli.modules.workspace.pattern_match import matches_any_pattern
from winter_cli.modules.workspace.repo_repository import IWriteRepoRepository
from winter_cli.modules.workspace.repository_factory import RepositoryFactory
from winter_cli.modules.workspace.workspace_repository import IReadWorkspaceRepository

logger = logging.getLogger(__name__)


class WorkspacePushService:
    """Pushes project worktrees and/or standalone repos with commits ahead of upstream.

    Pulled out of WorkspaceSyncService so each sync-vs-push concern has its
    own bounded surface. Both services share `pattern_match.matches_any_pattern`
    for the segment-aware `<env>/<repo>` glob.
    """

    def __init__(
        self,
        env_status_svc: EnvStatusService,
        worktree_repo: IReadWorkspaceRepository,
        repo_repo: IWriteRepoRepository,
        repo_factory: RepositoryFactory,
        workspace: Workspace,
    ) -> None:
        self._env_status_svc = env_status_svc
        self._worktree_repo = worktree_repo
        self._repo_repo = repo_repo
        self._repo_factory = repo_factory
        self._workspace = workspace

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
        Each non-pinned worktree pushes to the branch *its own* tracking
        config names (resolved per worktree, not from one env-wide feature
        branch); a non-pinned worktree with no upstream is reported per-repo
        as `no upstream` rather than forced onto a sibling's branch. Pinned
        worktrees plain-push to whatever their local branch tracks. Standalone
        repos plain-push to their tracked upstream and ignore `patterns`. Only
        repos with commits ahead of upstream are pushed.
        """
        patterns = patterns or ["*/*"]
        project_repos = self._repo_factory.get_project_repos()
        envs = self._worktree_repo.get_environments(self._workspace, project_repos) if scope.includes_project else []
        standalone_repos = self._repo_factory.get_standalone_repos() if scope.includes_standalone else []

        env_reports: list[EnvPushReport] = []
        skipped: list[EnvSkipped] = []
        for env in envs:
            env_worktrees = self._env_status_svc.get_feature_environment_worktrees(env, project_repos)

            worktrees = [
                wt
                for wt in env_worktrees.worktrees
                if self._matches_pinned_scope(wt, pinned_scope)
                and matches_any_pattern(env.name, wt.repository.name, patterns)
            ]
            if not worktrees:
                if pinned_scope == PinnedScope.exclude:
                    pinned_with_commits = [
                        wt
                        for wt in env_worktrees.worktrees
                        if wt.repository.pinned
                        and matches_any_pattern(env.name, wt.repository.name, patterns)
                        and self._has_commits_to_push(wt)
                    ]
                    if pinned_with_commits:
                        skipped.append(
                            EnvSkipped(
                                env=env.name,
                                reason=(
                                    f"{len(pinned_with_commits)} pinned repo(s) with commits skipped"
                                    " — use --include-pinned or --only-pinned"
                                ),
                            )
                        )
                continue

            outcomes = [self._push_one(wt) for wt in worktrees if self._has_commits_to_push(wt)]
            env_reports.append(EnvPushReport(env=env.name, repos=outcomes))

        standalone_outcomes: list[RepoPushOutcome] = []
        for repo in standalone_repos:
            if self._repo_repo.get_standalone_upstream(repo) is None:
                standalone_outcomes.append(
                    RepoPushOutcome(
                        repo_name=repo.name,
                        pushed=False,
                        error="no upstream — set one with `git branch --set-upstream-to`",
                    )
                )
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

    def _push_one(self, wt: FeatureWorktree) -> RepoPushOutcome:
        """Push one worktree to the branch its own tracking config names.

        Pinned worktrees plain-push (target_branch=None). A non-pinned
        worktree resolves its own push branch from `branch.<head>.merge`; a
        worktree with no upstream is reported `no upstream` per-repo —
        parity with the standalone path — instead of being forced onto a
        sibling repo's feature branch.

        After the push, if the workspace holds a local branch of the pushed
        name that was in sync with the remote beforehand (e.g. an env connected
        to a shared integration branch), that local branch is fast-forwarded to
        the pushed tip. See `_fast_forward_local_branch`.
        """
        if wt.repository.pinned:
            target_branch = None
        else:
            target_branch = self._repo_repo.get_worktree_push_branch(wt)
            if target_branch is None:
                return RepoPushOutcome(
                    repo_name=wt.repository.name,
                    pushed=False,
                    error="no upstream — run `winter ws connect` first",
                )
        # The branch this push actually lands on — resolved even for pinned
        # worktrees (which plain-push to their tracked upstream) so we can
        # sync the workspace's local copy of that branch afterward. Capture the
        # remote tip *before* the push so the ff knows what "in sync" meant.
        landing_branch = self._repo_repo.get_worktree_push_branch(wt)
        pre_remote_tip = (
            self._repo_repo.get_remote_branch_tip(wt, landing_branch) if landing_branch is not None else None
        )
        try:
            commits = self._repo_repo.push(wt, target_branch)
        except RepoError as exc:
            logger.warning("Push failed for %s: %s", wt.repository.name, exc)
            return RepoPushOutcome(repo_name=wt.repository.name, pushed=False, error=str(exc))
        local_ff = self._fast_forward_local_branch(wt, landing_branch, pre_remote_tip)
        return RepoPushOutcome(repo_name=wt.repository.name, pushed=True, commits=commits, local_ff=local_ff)

    def _fast_forward_local_branch(
        self, wt: FeatureWorktree, landing_branch: str | None, pre_remote_tip: str | None
    ) -> LocalFastForward | None:
        """Fast-forward the workspace's local copy of the just-pushed branch.

        `pre_remote_tip` is None unless the remote ref existed before the push
        (a first push has no in-sync local branch to advance), so this returns
        None in that case. A ff failure never fails the push itself (the commits
        are already on the remote); it is surfaced as a skip note.
        """
        if landing_branch is None or pre_remote_tip is None:
            return None
        try:
            return self._repo_repo.fast_forward_local_branch(wt.repository, landing_branch, pre_remote_tip)
        except RepoError as exc:
            logger.warning("Local fast-forward failed for %s: %s", wt.repository.name, exc)
            return LocalFastForward(branch=landing_branch, advanced=False, skipped_reason="ff failed")

    def _push_one_standalone(self, repo: StandaloneRepository) -> RepoPushOutcome:
        try:
            commits = self._repo_repo.push_standalone(repo)
        except RepoError as exc:
            logger.warning("Push failed for standalone %s: %s", repo.name, exc)
            return RepoPushOutcome(repo_name=repo.name, pushed=False, error=str(exc))
        return RepoPushOutcome(repo_name=repo.name, pushed=True, commits=commits)
