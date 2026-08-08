from __future__ import annotations

import logging

from winter_cli.modules.workspace.models import (
    CleanFailure,
    CleanReport,
    FeatureWorktree,
    PartialCleanError,
    RepoCleanOutcome,
    RepoError,
)
from winter_cli.modules.workspace.repo_repository import IWriteRepoRepository

logger = logging.getLogger(__name__)


class EnvCleanService:
    """Removes untracked files from matched worktrees — the `git clean` half of
    the pair `winter ws reset` completes.

    `ws reset --hard` reconciles the three trees git tracks, so a file that was
    never added has no path entry and survives it. This service removes exactly
    that remainder, which is why the two are separate commands rather than one
    flag: cleaning is also what you want before a re-provision or after a
    botched codegen run, neither of which involves moving a branch pointer.

    Operates on an already-resolved, already-pinned-filtered flat list of
    worktrees, so this service stays env-agnostic; PATTERNS may span more than
    one environment and the caller (the handler) does that matching up front —
    the same split `EnvResetService` uses.

    There is no Phase 1 safety gate, and deliberately no refusal vocabulary.
    Reset has preconditions to check (a ref must resolve, history must not be
    abandoned); cleaning has none — every untracked file is equally removable,
    and the only question is whether the caller meant it. That question is
    answered by the handler's confirmation prompt, which is why this service
    never prompts and never refuses.

    A run is **not** all-or-nothing and cannot be: a deleted file has nothing
    to roll back to. When a git op fails the loop stops, but the report still
    carries every worktree already cleaned plus whatever the failing one had
    already lost — that partial record is the only trace those files existed,
    so it is returned rather than thrown away with the stack frame.

    `dry_run` enumerates every worktree but removes nothing, and is the only
    preview available — cleaned files are unrecoverable, with no reflog behind
    them.
    """

    def __init__(self, repo_repo: IWriteRepoRepository) -> None:
        self._repo_repo = repo_repo

    def preview(self, targets: list[FeatureWorktree]) -> CleanReport:
        """Enumerate what a clean would remove, touching nothing.

        Separate from `clean` so the handler can show the caller the list
        *before* prompting — the prompt has to state the real blast radius,
        and a set computed after the deletion would be worthless for that.
        `clean` re-enumerates rather than accepting this report back, so a
        file created between the preview and the confirmation is still
        removed instead of silently surviving.
        """
        return CleanReport(
            dry_run=True,
            repos=[self._outcome(wt, self._repo_repo.list_untracked(wt)) for wt in targets],
        )

    def clean(self, targets: list[FeatureWorktree], dry_run: bool) -> CleanReport:
        logger.info(
            "clean: dry_run=%s targets=%s",
            dry_run,
            [f"{wt.environment.name}/{wt.repository.name}" for wt in targets],
        )

        outcomes: list[RepoCleanOutcome] = []
        for wt in targets:
            # The real run reports what `git clean` says it removed rather
            # than reusing the preview's list. The two are derived from the
            # same command (`-nd` vs `-fd`) so they share selection rules, but
            # they are enumerated at different moments: a file created in
            # between is removed and must still be reported. `clean_untracked`
            # runs unconditionally — gating it on a prior enumeration is what
            # previously let a worktree whose only untracked content was an
            # empty directory report "nothing to clean" and never run.
            try:
                paths = self._repo_repo.list_untracked(wt) if dry_run else self._repo_repo.clean_untracked(wt)
            except RepoError as exc:
                # Stop, but return rather than propagate. Anything already
                # deleted — in this worktree and every earlier one — is
                # unrecoverable, so discarding `outcomes` with the stack frame
                # would destroy the only record of what was lost.
                removed = exc.removed if isinstance(exc, PartialCleanError) else []
                outcomes.append(self._outcome(wt, removed))
                logger.error("clean: stopped at %s/%s — %s", wt.environment.name, wt.repository.name, exc)
                return CleanReport(
                    dry_run=dry_run,
                    repos=outcomes,
                    failure=CleanFailure(
                        env=wt.environment.name,
                        repo_name=wt.repository.name,
                        message=str(exc),
                    ),
                )
            outcomes.append(self._outcome(wt, paths))

        return CleanReport(dry_run=dry_run, repos=outcomes)

    def _outcome(self, wt: FeatureWorktree, paths: list[str]) -> RepoCleanOutcome:
        return RepoCleanOutcome(env=wt.environment.name, repo_name=wt.repository.name, paths=paths)
