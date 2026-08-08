from __future__ import annotations

import logging

from winter_cli.modules.workspace.models import (
    CleanReport,
    FeatureWorktree,
    RepoCleanOutcome,
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

    Phase 2 is not atomic across repos: if a git op raises mid-loop, worktrees
    processed earlier are already cleaned. `dry_run` enumerates every worktree
    but removes nothing, and is the only preview available — cleaned files are
    unrecoverable, with no reflog behind them.
    """

    def __init__(self, repo_repo: IWriteRepoRepository) -> None:
        self._repo_repo = repo_repo

    def preview(self, targets: list[FeatureWorktree]) -> CleanReport:
        """Enumerate what a clean would remove, touching nothing.

        Separate from `clean` so the handler can show the caller a per-file
        list *before* prompting — the prompt has to state the real blast
        radius, and a count computed after the deletion would be worthless for
        that. `clean` re-enumerates rather than accepting this report back,
        so a file created between the prompt and the confirmation is still
        removed instead of silently surviving.
        """
        return CleanReport(dry_run=True, repos=[self._outcome(wt) for wt in targets])

    def clean(self, targets: list[FeatureWorktree], dry_run: bool) -> CleanReport:
        logger.info(
            "clean: dry_run=%s targets=%s",
            dry_run,
            [f"{wt.environment.name}/{wt.repository.name}" for wt in targets],
        )

        outcomes: list[RepoCleanOutcome] = []
        for wt in targets:
            # Enumerated before the removal in both modes: afterwards there is
            # nothing left to list, so this is the only point the report's
            # paths can be captured.
            outcome = self._outcome(wt)
            if not dry_run and outcome.paths:
                self._repo_repo.clean_untracked(wt)
            outcomes.append(outcome)

        return CleanReport(dry_run=dry_run, repos=outcomes)

    def _outcome(self, wt: FeatureWorktree) -> RepoCleanOutcome:
        return RepoCleanOutcome(
            env=wt.environment.name,
            repo_name=wt.repository.name,
            paths=self._repo_repo.list_untracked(wt),
        )
