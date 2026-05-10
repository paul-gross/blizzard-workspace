from __future__ import annotations

import logging

import git

from winter_cli.modules.workspace.models import (
    FeatureWorktree,
    ProjectRepository,
    PullMode,
    RepoError,
    RepoSyncOutcome,
    StandaloneRepository,
    SyncResult,
)
from winter_cli.modules.workspace.internal.read_repo_repository import ReadRepoRepository

logger = logging.getLogger(__name__)


def _autostash_args(autostash: bool) -> list[str]:
    return ["--autostash"] if autostash else []


class WriteRepoRepository(ReadRepoRepository):
    """Read-write GitPython implementation. Extends ReadRepoRepository with mutating operations."""

    def fetch(self, worktree: FeatureWorktree) -> None:
        try:
            # Shell out via r.git rather than r.remotes.origin.fetch() — gitpython's
            # high-level remotes API reads from the worktree's git-dir, which doesn't
            # have remote config; the shared remotes live in the common-dir.
            git.Repo(str(worktree.path)).git.fetch("origin")
        except git.GitCommandError as exc:
            raise RepoError(f"fetch failed — {exc}") from exc

    def integrate(
        self,
        worktree: FeatureWorktree,
        target_ref: str,
        mode: PullMode,
        autostash: bool,
    ) -> RepoSyncOutcome:
        return self._integrate(
            git.Repo(str(worktree.path)),
            worktree.repository.name,
            target_ref,
            mode,
            autostash,
        )

    def sync_ff_only(self, repo: ProjectRepository) -> None:
        main_branch = repo.main_branch
        r = git.Repo(str(repo.main_path))
        try:
            r.git.fetch("origin")
            r.git.merge("--ff-only", f"origin/{main_branch}")
        except git.GitCommandError:
            logger.warning("Could not fast-forward %s", repo.name)

    def set_upstream(self, worktree: FeatureWorktree, remote_branch: str) -> None:
        r = git.Repo(str(worktree.path))
        try:
            r.git.branch("--set-upstream-to", remote_branch)
        except git.GitCommandError:
            pass

    def unset_upstream(self, worktree: FeatureWorktree) -> None:
        r = git.Repo(str(worktree.path))
        try:
            r.git.branch("--unset-upstream")
        except git.GitCommandError:
            pass

    def set_push_default(self, worktree: FeatureWorktree) -> None:
        r = git.Repo(str(worktree.path))
        with r.config_writer() as cw:
            cw.set_value("push", "default", "upstream")

    def push(self, worktree: FeatureWorktree, feature_branch: str | None = None) -> int:
        r = git.Repo(str(worktree.path))
        status = self.get_worktree_status(worktree)
        commit_count = status.ahead
        try:
            if feature_branch:
                r.git.push("-u", "origin", f"HEAD:refs/heads/{feature_branch}")
            else:
                r.git.push("origin")
        except git.GitCommandError as exc:
            raise RepoError(f"push failed — {exc}") from exc
        return commit_count

    def fetch_standalone(self, repo: StandaloneRepository) -> None:
        try:
            git.Repo(str(repo.path)).git.fetch("origin")
        except git.GitCommandError as exc:
            raise RepoError(f"fetch failed — {exc}") from exc

    def integrate_standalone(
        self,
        repo: StandaloneRepository,
        mode: PullMode,
        autostash: bool,
    ) -> RepoSyncOutcome:
        r = git.Repo(str(repo.path))
        tb = self._tracking_branch_name(r)
        if tb is None:
            return RepoSyncOutcome(repo_name=repo.name, sync_result=SyncResult.no_upstream)
        return self._integrate(r, repo.name, tb, mode, autostash)

    def push_standalone(self, repo: StandaloneRepository) -> int:
        r = git.Repo(str(repo.path))
        if self._tracking_branch_name(r) is None:
            raise RepoError(f"{repo.name} has no upstream — set one with `git branch --set-upstream-to`")
        commit_count = self._tracking_ahead(r)
        try:
            r.git.push("origin")
        except git.GitCommandError as exc:
            raise RepoError(f"push failed — {exc}") from exc
        return commit_count

    def get_standalone_tracking_ahead(self, repo: StandaloneRepository) -> int:
        return self._tracking_ahead(git.Repo(str(repo.path)))

    def get_standalone_upstream(self, repo: StandaloneRepository) -> str | None:
        return self._tracking_branch_name(git.Repo(str(repo.path)))

    def _integrate(
        self,
        r: git.Repo,
        repo_name: str,
        target_ref: str,
        mode: PullMode,
        autostash: bool,
    ) -> RepoSyncOutcome:
        if mode == PullMode.ff_only:
            return self._ff_only(r, repo_name, target_ref, autostash)
        if mode == PullMode.merge:
            return self._ff_or_merge(r, repo_name, target_ref, autostash)
        if mode == PullMode.rebase:
            return self._ff_or_rebase(r, repo_name, target_ref, autostash)
        raise ValueError(f"unknown PullMode: {mode}")

    def _ff_only(self, r: git.Repo, repo_name: str, target_ref: str, autostash: bool) -> RepoSyncOutcome:
        head_before = r.head.commit.hexsha
        try:
            r.git.merge(*_autostash_args(autostash), "--ff-only", target_ref)
        except git.GitCommandError:
            return self._diverged_outcome(r, repo_name, target_ref)
        head_after = r.head.commit.hexsha
        if head_before == head_after:
            return RepoSyncOutcome(repo_name=repo_name, sync_result=SyncResult.up_to_date)
        return RepoSyncOutcome(repo_name=repo_name, sync_result=SyncResult.fast_forwarded)

    def _ff_or_merge(self, r: git.Repo, repo_name: str, target_ref: str, autostash: bool) -> RepoSyncOutcome:
        ff = self._ff_only(r, repo_name, target_ref, autostash)
        if ff.sync_result != SyncResult.diverged:
            return ff
        try:
            r.git.merge(*_autostash_args(autostash), target_ref)
            return RepoSyncOutcome(repo_name=repo_name, sync_result=SyncResult.merged)
        except git.GitCommandError:
            self._abort(r.git.merge)
            return self._diverged_outcome(r, repo_name, target_ref)

    def _ff_or_rebase(self, r: git.Repo, repo_name: str, target_ref: str, autostash: bool) -> RepoSyncOutcome:
        ff = self._ff_only(r, repo_name, target_ref, autostash)
        if ff.sync_result != SyncResult.diverged:
            return ff
        try:
            r.git.rebase(*_autostash_args(autostash), target_ref)
            return RepoSyncOutcome(repo_name=repo_name, sync_result=SyncResult.rebased)
        except git.GitCommandError:
            self._abort(r.git.rebase)
            return self._diverged_outcome(r, repo_name, target_ref)

    def _diverged_outcome(self, r: git.Repo, repo_name: str, target_ref: str) -> RepoSyncOutcome:
        ahead = 0
        behind = 0
        try:
            ahead = int(r.git.rev_list("--count", f"{target_ref}..HEAD"))
            behind = int(r.git.rev_list("--count", f"HEAD..{target_ref}"))
        except git.GitCommandError:
            pass
        return RepoSyncOutcome(
            repo_name=repo_name,
            sync_result=SyncResult.diverged,
            ahead=ahead,
            behind=behind,
        )

    @staticmethod
    def _abort(op) -> None:
        try:
            op("--abort")
        except git.GitCommandError:
            pass

    @staticmethod
    def _tracking_branch_name(r: git.Repo) -> str | None:
        try:
            tb = r.active_branch.tracking_branch()
        except TypeError:
            return None
        return tb.name if tb is not None else None

    def _tracking_ahead(self, r: git.Repo) -> int:
        tb = self._tracking_branch_name(r)
        if tb is None:
            return 0
        try:
            return int(r.git.rev_list("--count", f"{tb}..HEAD"))
        except git.GitCommandError:
            return 0
