from __future__ import annotations

import logging

import git

from winter_cli.modules.workspace.models import (
    ProjectRepository,
    RepoSyncOutcome,
    SyncResult,
    FeatureWorktree,
)
from winter_cli.modules.workspace.internal.read_repo_repository import ReadRepoRepository

logger = logging.getLogger(__name__)


class WriteRepoRepository(ReadRepoRepository):
    """Read-write GitPython implementation. Extends ReadRepoRepository with mutating operations."""

    def fetch(self, worktree: FeatureWorktree) -> None:
        r = git.Repo(str(worktree.path))
        r.remotes.origin.fetch()

    def sync_ff_or_merge(self, worktree: FeatureWorktree) -> RepoSyncOutcome:
        repo_name = worktree.repository.name
        main_branch = worktree.repository.main_branch
        r = git.Repo(str(worktree.path))
        main_ref = f"origin/{main_branch}"
        try:
            head_before = r.head.commit.hexsha
            r.git.merge("--ff-only", main_ref)
            head_after = r.head.commit.hexsha
            if head_before == head_after:
                return RepoSyncOutcome(repo_name=repo_name, sync_result=SyncResult.up_to_date)
            return RepoSyncOutcome(repo_name=repo_name, sync_result=SyncResult.fast_forwarded)
        except git.GitCommandError:
            try:
                r.git.merge(main_ref)
                return RepoSyncOutcome(repo_name=repo_name, sync_result=SyncResult.merged)
            except git.GitCommandError:
                r.git.merge("--abort")
                ahead = 0
                behind = 0
                try:
                    ahead = int(r.git.rev_list("--count", f"{main_ref}..HEAD"))
                    behind = int(r.git.rev_list("--count", f"HEAD..{main_ref}"))
                except git.GitCommandError:
                    pass
                return RepoSyncOutcome(
                    repo_name=repo_name,
                    sync_result=SyncResult.diverged,
                    ahead=ahead,
                    behind=behind,
                )

    def sync_ff_only(self, repo: ProjectRepository) -> None:
        main_branch = repo.main_branch
        r = git.Repo(str(repo.main_path))
        r.remotes.origin.fetch()
        try:
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
        if feature_branch:
            r.git.push("-u", "origin", f"HEAD:refs/heads/{feature_branch}")
        else:
            r.remotes.origin.push()
        return commit_count
