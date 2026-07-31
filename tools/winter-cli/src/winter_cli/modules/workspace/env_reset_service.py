from __future__ import annotations

import logging
import re

from winter_cli.modules.workspace.models import (
    FeatureWorktree,
    RepoResetOutcome,
    ResetMode,
    ResetReport,
    ResetResult,
)
from winter_cli.modules.workspace.ref_resolver import resolve_ref
from winter_cli.modules.workspace.repo_repository import IWriteRepoRepository
from winter_cli.modules.workspace.worktree_safety import WorktreeSafetyService

logger = logging.getLogger(__name__)

# Git's own abbreviated-SHA acceptance window is 4 hex chars minimum, up to the
# full 40-char SHA-1. Purely syntactic — no network, no per-repo resolution —
# because the ambiguous-SHA refusal is about REF's *shape*, not whether it
# happens to also resolve as a branch name in some repo. A hex-only branch
# name (e.g. `deadbeef`) is misclassified as a SHA by this heuristic; that
# ambiguity is inherent to git's own ref grammar, not specific to winter.
_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")


def looks_like_sha(ref: str) -> bool:
    """Whether REF is a bare commit SHA (full or abbreviated) rather than a
    branch / tag / remote-ref spelling.

    A SHA has no env-wide meaning — the same 40 hex chars name a wholly
    different commit (or nothing at all) in each repo's independent history —
    so `EnvResetService.reset` refuses it outright against more than one
    matched worktree.
    """
    return bool(_SHA_RE.fullmatch(ref))


class EnvResetService:
    """Moves matched worktrees' branch pointers to REF — the `git reset` half
    of the checkout/reset split `winter ws checkout` deliberately couples
    together. Never touches upstream tracking, in any mode — that stays
    `winter ws connect`'s job.

    Operates on an already-resolved, already-pinned-filtered flat list of
    worktrees, so this service stays env-agnostic; PATTERNS may span more
    than one environment (`winter ws diff`'s grammar, not `ws checkout`'s
    single-ENV one) and the caller (the handler) does that pattern-matching
    up front.

    Phase 1 is a non-destructive safety check, all-or-nothing: any refusal
    means Phase 2 never runs, in any repo. Two ref-resolution guards run
    regardless of mode, `--force`, or `--dry-run`: a bare commit SHA is
    refused unless exactly one worktree matched (a SHA has no env-wide
    meaning), and a REF that doesn't resolve locally in a matched repo
    refuses (nothing to reset that repo to). The dirty / abandonment safety
    gate — reusing `worktree_safety.abandonment_safety_ref`, the same helper
    `EnvCheckoutService` uses — applies only to `--hard` (the only mode that
    touches the working tree) and is skipped under `--force`; `--soft` /
    `--mixed` never run it, matching git's own semantics (nothing is
    discarded by either).

    Phase 2 is not atomic across repos: if a git op raises mid-loop, repos
    processed earlier are already mutated. `--dry-run` skips Phase 2's git
    op entirely — the returned report still carries a `reset` outcome per
    matched repo (the plan), just with nothing executed.
    """

    def __init__(self, repo_repo: IWriteRepoRepository, worktree_safety_svc: WorktreeSafetyService) -> None:
        self._repo_repo = repo_repo
        self._worktree_safety_svc = worktree_safety_svc

    def reset(
        self,
        targets: list[FeatureWorktree],
        ref: str,
        mode: ResetMode,
        force: bool,
        dry_run: bool,
    ) -> ResetReport:
        logger.info(
            "reset: ref=%s mode=%s force=%s dry_run=%s targets=%s",
            ref,
            mode.value,
            force,
            dry_run,
            [f"{wt.environment.name}/{wt.repository.name}" for wt in targets],
        )

        if looks_like_sha(ref) and len(targets) > 1:
            # Not bypassable by --force: a bare SHA genuinely has no
            # cross-repo meaning, there is nothing to force through.
            refused = [_outcome(wt, ResetResult.refused_ambiguous_sha) for wt in targets]
            return ResetReport(ref=ref, mode=mode, dry_run=dry_run, aborted=True, repos=refused)

        # Resolved per repo up front — before any git op — so an unknown
        # `{...}` token in `ref` refuses immediately rather than mid-loop.
        # Keyed by (env, repo) rather than repo name alone: PATTERNS may span
        # multiple envs, and the same project repo name can appear in more
        # than one of them.
        resolved_refs = {_key(wt): resolve_ref(ref, wt.repository) for wt in targets}
        has_ref = {_key(wt): self._repo_repo.has_local_ref(wt, resolved_refs[_key(wt)]) for wt in targets}

        missing_ref_targets = [wt for wt in targets if not has_ref[_key(wt)]]
        refused: list[RepoResetOutcome] = [_outcome(wt, ResetResult.refused_missing_ref) for wt in missing_ref_targets]
        refused_keys = {_key(wt) for wt in missing_ref_targets}

        # --hard's dirty / abandonment safety gate — skipped for --soft/--mixed
        # (neither touches the working tree) and under --force.
        if mode == ResetMode.hard and not force:
            for wt in targets:
                if _key(wt) in refused_keys:
                    continue
                if self._repo_repo.is_worktree_dirty(wt):
                    refused.append(_outcome(wt, ResetResult.refused_dirty))
                    continue
                safety_ref = self._worktree_safety_svc.abandonment_safety_ref(wt)
                if self._repo_repo.count_commits_not_in(wt, safety_ref) > 0:
                    refused.append(_outcome(wt, ResetResult.refused_abandonment))

        if refused:
            logger.warning("reset: aborting — refused repos: %s", ", ".join(o.repo_name for o in refused))
            return ResetReport(ref=ref, mode=mode, dry_run=dry_run, aborted=True, repos=refused)

        outcomes: list[RepoResetOutcome] = []
        for wt in targets:
            target_ref = resolved_refs[_key(wt)]
            if not dry_run:
                self._repo_repo.reset_to(wt, mode, target_ref)
            outcomes.append(_outcome(wt, ResetResult.reset, ref=target_ref))

        return ResetReport(ref=ref, mode=mode, dry_run=dry_run, aborted=False, repos=outcomes)


def _key(wt: FeatureWorktree) -> tuple[str, str]:
    return (wt.environment.name, wt.repository.name)


def _outcome(wt: FeatureWorktree, result: ResetResult, ref: str = "") -> RepoResetOutcome:
    return RepoResetOutcome(env=wt.environment.name, repo_name=wt.repository.name, result=result, ref=ref)
