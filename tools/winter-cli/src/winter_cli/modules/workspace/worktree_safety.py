from __future__ import annotations

from winter_cli.modules.workspace.models import FeatureWorktree
from winter_cli.modules.workspace.ref_resolver import resolve_ref
from winter_cli.modules.workspace.repo_repository import IWriteRepoRepository


class WorktreeSafetyService:
    """Safety-ref computation shared by every service that moves a worktree's
    HEAD destructively — `EnvCheckoutService` (`ws checkout`) and
    `EnvResetService` (`ws reset --hard`).
    """

    def __init__(self, repo_repo: IWriteRepoRepository) -> None:
        self._repo_repo = repo_repo

    def abandonment_safety_ref(self, wt: FeatureWorktree) -> str:
        """The ref a destructive move away from HEAD would abandon work relative to.

        Both callers move a worktree's HEAD away from wherever it currently
        sits, and both need the same answer to "what would this abandon?": the
        worktree's own current upstream when it resolves locally, else the
        repo's `origin/<main_branch>`. Comparing against the branch the
        worktree is moving *away from* (never the destructive op's target) is
        what makes the guard protect unpushed local commits regardless of
        what the worktree is being moved to. The fallback covers a
        disconnected worktree or a never-pushed upstream whose ref isn't in
        the local object store.
        """
        upstream = self._repo_repo.get_worktree_upstream(wt)
        if upstream is not None and self._repo_repo.has_local_ref(wt, upstream):
            return upstream
        return resolve_ref("origin/{main}", wt.repository)

    def branch_abandonment_safety_ref(self, wt: FeatureWorktree, branch_name: str) -> str:
        """Same computation as `abandonment_safety_ref`, but keyed off an explicit
        branch rather than HEAD's current branch.

        `EnvCheckoutService.checkout_env` needs this in addition to the
        HEAD-relative check: `force_checkout_env_branch` force-moves
        `refs/heads/<env>` regardless of what HEAD currently points at (a
        detached worktree, or one parked on a different branch), so a worktree
        whose env branch carries unpushed commits would otherwise abandon them
        with no refusal (winter#159 B1) — the HEAD-relative guard alone never
        looks at that branch when it isn't checked out.
        """
        upstream = self._repo_repo.get_branch_upstream(wt, branch_name)
        if upstream is not None and self._repo_repo.has_local_ref(wt, upstream):
            return upstream
        return resolve_ref("origin/{main}", wt.repository)
