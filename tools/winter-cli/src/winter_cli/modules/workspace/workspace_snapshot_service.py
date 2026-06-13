from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable

import click

from winter_cli.modules.workspace.drift import DriftWarningService
from winter_cli.modules.workspace.env_status_service import EnvStatusService
from winter_cli.modules.workspace.extension_manifest import PORT_BASE, PORT_STEP
from winter_cli.modules.workspace.models import (
    EnvSnapshot,
    FeatureEnvironmentOverview,
    FeatureWorktree,
    OrphanSnapshot,
    ProjectRepository,
    RepoError,
    SourceCheckoutSnapshot,
    StandaloneRepoStatus,
    Workspace,
    WorkspaceLevelSnapshot,
    WorkspaceSnapshot,
    WorktreeRepoStatus,
    WorktreeSnapshot,
)
from winter_cli.modules.workspace.pattern_match import matches_any_pattern
from winter_cli.modules.workspace.prune_service import PruneService
from winter_cli.modules.workspace.repo_repository import IWriteRepoRepository
from winter_cli.modules.workspace.repository_factory import RepositoryFactory
from winter_cli.modules.workspace.workspace_repository import IReadWorkspaceRepository
from winter_cli.plugins.types import IEnvironmentDecorator, IWorktreeRepoDecorator

logger = logging.getLogger(__name__)


class WorkspaceSnapshotService:
    """Composes existing lower-layer services into a `WorkspaceSnapshot`.

    This service does NO git-probing of its own; it orchestrates what already
    exists (`EnvStatusService`, `DriftWarningService`, `PruneService`, the repo
    repositories, and the factory). Both the dashboard TUI (Phase 5) and the
    `ws status` command (Phase 3) consume this service so the two surfaces cannot
    disagree on what they read.
    """

    def __init__(
        self,
        workspace: Workspace,
        env_status_svc: EnvStatusService,
        workspace_repo: IReadWorkspaceRepository,
        repo_repo: IWriteRepoRepository,
        repo_factory: RepositoryFactory,
        drift_warning_svc: DriftWarningService,
        prune_svc: PruneService,
    ) -> None:
        self._workspace = workspace
        self._env_status_svc = env_status_svc
        self._workspace_repo = workspace_repo
        self._repo_repo = repo_repo
        self._repo_factory = repo_factory
        self._drift_warning_svc = drift_warning_svc
        self._prune_svc = prune_svc

    def collect(
        self,
        *,
        patterns: list[str] | None = None,
        on_repo_error: Callable[[FeatureWorktree, RepoError], None] | None = None,
        env_decorators: list[IEnvironmentDecorator] | None = None,
        worktree_repo_decorators: list[IWorktreeRepoDecorator] | None = None,
    ) -> WorkspaceSnapshot:
        """Collect a complete workspace state snapshot.

        Parameters
        ----------
        patterns:
            When non-empty, filter the environments and worktrees to those
            matching at least one pattern (segment-aware glob over
            ``<env>/<repo>``).  Bare env names expand to ``<env>/*``.
            Envs that end up with zero matching worktrees are dropped from the
            snapshot.  Source-checkout and workspace-level sections are always
            included as context.  When the list is empty or ``None``, all
            environments and worktrees are returned.  Raises
            ``click.ClickException`` when no worktree matches any pattern.
        on_repo_error:
            Mirrors `EnvStatusService`'s tolerate-vs-propagate contract. The
            dashboard (Phase 5) passes a skip+log callback so one broken repo
            doesn't poison the whole refresh. CLI (Phase 3) passes ``None`` so
            the first error propagates and the command exits non-zero.
        env_decorators:
            Optional list of `IEnvironmentDecorator` plugins that may write into
            `FeatureEnvironmentStatus.extensions` (dashboard-only; CLI passes
            ``None``).
        worktree_repo_decorators:
            Optional list of `IWorktreeRepoDecorator` plugins (dashboard-only).
        """
        effective_patterns: list[str] = patterns or []
        project_repos = self._repo_factory.get_project_repos()

        # ── environments ──────────────────────────────────────────────────
        environments = self._workspace_repo.get_environments(self._workspace, project_repos)

        env_snapshots: list[EnvSnapshot] = []
        total_matched_worktrees = 0
        for env in environments:
            env_status = self._env_status_svc.get_environment_status(
                env,
                project_repos,
                env_decorators or None,
            )
            env_worktrees = self._env_status_svc.get_feature_environment_worktrees(env, project_repos)

            def _on_wt_error(wt: FeatureWorktree, exc: RepoError, env_name: str = env.name) -> None:
                if on_repo_error is not None:
                    on_repo_error(wt, exc)

            repo_statuses = self._env_status_svc.get_worktree_repo_statuses(
                env_worktrees,
                worktree_repo_decorators or None,
                on_repo_error=_on_wt_error if on_repo_error is not None else None,
            )

            worktree_snapshots: list[WorktreeSnapshot] = []
            for wt_status in repo_statuses:
                repo_name = wt_status.worktree.repository.name
                # Apply pattern filter: skip worktrees that don't match any pattern.
                if effective_patterns and not matches_any_pattern(env.name, repo_name, effective_patterns):
                    continue

                # get_worktree_repo_statuses maps RepoStatus → WorktreeRepoStatus
                # (losing staged/unstaged/untracked breakdown). Re-probe via
                # get_worktree_status to recover the fine-grained counts needed
                # for WorktreeSnapshot.
                try:
                    rs = self._repo_repo.get_worktree_status(wt_status.worktree)
                except RepoError as exc:
                    if on_repo_error is None:
                        raise
                    on_repo_error(wt_status.worktree, exc)
                    continue

                last_subject: str | None = None
                if rs.recent_commits:
                    last_subject = rs.recent_commits[0].message

                worktree_snapshots.append(
                    WorktreeSnapshot(
                        repo=repo_name,
                        branch=rs.branch,
                        upstream=rs.tracking_branch,
                        ahead=rs.ahead,
                        behind=rs.behind,
                        tracking_ahead=rs.tracking_ahead,
                        tracking_behind=rs.tracking_behind,
                        tracking_ref_present=rs.tracking_ref_present,
                        staged=rs.staged_count,
                        unstaged=rs.unstaged_count,
                        untracked=rs.untracked_count,
                        dirty=len(rs.dirty_files),
                        last_commit_subject=last_subject,
                        pinned=wt_status.worktree.repository.pinned,
                    )
                )

            # When patterns are active, drop envs that ended up with no matching worktrees.
            if effective_patterns and not worktree_snapshots:
                continue

            total_matched_worktrees += len(worktree_snapshots)
            env_snapshots.append(
                EnvSnapshot(
                    name=env.name,
                    index=env.index,
                    port_base=PORT_BASE + env.index * PORT_STEP,
                    feature_branch=env_status.feature_branch,
                    worktrees=worktree_snapshots,
                )
            )

        # Zero-match guard: if patterns were given but nothing matched, raise.
        if effective_patterns and total_matched_worktrees == 0:
            raise click.ClickException(f"No worktrees match: {', '.join(effective_patterns)}")

        # ── source checkouts (project main clones) ────────────────────────
        drift_report = self._drift_warning_svc.detect()
        missing_names = {r.name for r in drift_report.missing}

        source_checkout_snapshots: list[SourceCheckoutSnapshot] = []

        def _on_main_error(repo: ProjectRepository, exc: RepoError) -> None:
            if on_repo_error is not None:
                # Log-and-skip; the CLI passes on_repo_error=None so this path is dashboard-only.
                logger.warning("source-checkout probe failed for %s: %s", repo.name, exc)

        main_statuses = self._env_status_svc.get_main_branch_statuses(
            self._workspace,
            project_repos,
            on_repo_error=_on_main_error if on_repo_error is not None else None,
        )

        for repo in project_repos:
            drift_notes: list[str] = []
            if repo.name in missing_names:
                drift_notes.append("missing from projects/")

            wt_status = main_statuses.get(repo.name)
            if wt_status is not None:
                source_checkout_snapshots.append(
                    SourceCheckoutSnapshot(
                        repo=repo.name,
                        branch=wt_status.branch,
                        behind_origin=wt_status.behind,
                        ahead_origin=wt_status.ahead,
                        dirty=wt_status.dirty_count,
                        drift=drift_notes,
                    )
                )
            elif drift_notes:
                # Repo has drift notes but no git status (missing on disk) —
                # still include it so callers see the drift finding.
                source_checkout_snapshots.append(
                    SourceCheckoutSnapshot(
                        repo=repo.name,
                        branch=None,
                        behind_origin=0,
                        ahead_origin=0,
                        dirty=0,
                        drift=drift_notes,
                    )
                )

        # Add undeclared dirs as drift entries (no corresponding ProjectRepository)
        for undeclared_name in drift_report.undeclared:
            source_checkout_snapshots.append(
                SourceCheckoutSnapshot(
                    repo=undeclared_name,
                    branch=None,
                    behind_origin=0,
                    ahead_origin=0,
                    dirty=0,
                    drift=["undeclared in config"],
                )
            )

        # ── workspace-level ───────────────────────────────────────────────
        orphan_raw = self._prune_svc.find_orphans()
        orphan_snapshots = [
            OrphanSnapshot(
                kind=o.kind,
                path=str(o.path),
                safe_to_remove=o.safe_to_remove,
                notes=o.notes,
            )
            for o in orphan_raw
        ]

        extension_names = [r.name for r in self._repo_factory.get_standalone_repos()]

        workspace_level = WorkspaceLevelSnapshot(
            root_path=str(self._workspace.root_path),
            extensions=extension_names,
            orphans=orphan_snapshots,
            drift_missing=[r.name for r in drift_report.missing],
            drift_undeclared=list(drift_report.undeclared),
        )

        return WorkspaceSnapshot(
            schema_version=1,
            workspace=workspace_level,
            environments=env_snapshots,
            source_checkouts=source_checkout_snapshots,
        )

    def collect_for_dashboard(
        self,
        *,
        on_repo_error: Callable[[FeatureWorktree, RepoError], None] | None = None,
        env_decorators: list[IEnvironmentDecorator] | None = None,
        worktree_repo_decorators: list[IWorktreeRepoDecorator] | None = None,
    ) -> DashboardRefreshData:
        """Collect the dashboard-facing state in one pass.

        Returns the same data the dashboard TUI needs to populate all its
        widgets — `FeatureEnvironmentOverview` items for the grid, standalone
        statuses for the singletons table, and main-branch statuses for the
        repo label column — without duplicating the orchestration that lives
        in `EnvStatusService` and the repo repositories.

        This is deliberately separate from `collect()` so the dashboard does
        not pay for the extra per-worktree `get_worktree_status` re-probe that
        `collect()` performs to build `WorktreeSnapshot` fine-grained fields.
        """
        project_repos = self._repo_factory.get_project_repos()

        # ── environments ──────────────────────────────────────────────────
        environments = self._workspace_repo.get_environments(self._workspace, project_repos)

        overviews: list[FeatureEnvironmentOverview] = []
        for env in environments:

            def _on_wt_error(wt: FeatureWorktree, exc: RepoError, env_name: str = env.name) -> None:
                if on_repo_error is not None:
                    on_repo_error(wt, exc)

            try:
                env_status = self._env_status_svc.get_environment_status(
                    env,
                    project_repos,
                    env_decorators or None,
                )
                env_worktrees = self._env_status_svc.get_feature_environment_worktrees(env, project_repos)
                repo_statuses = self._env_status_svc.get_worktree_repo_statuses(
                    env_worktrees,
                    worktree_repo_decorators or None,
                    on_repo_error=_on_wt_error if on_repo_error is not None else None,
                )
                overviews.append(FeatureEnvironmentOverview(status=env_status, repo_statuses=repo_statuses))
            except RepoError as exc:
                if on_repo_error is not None:
                    # Log-and-skip; the CLI passes on_repo_error=None so this path is dashboard-only.
                    logger.warning("env-level probe failed for %s: %s", env.name, exc)
                else:
                    raise

        # ── singletons + standalones ───────────────────────────────────────
        standalone_statuses: list[StandaloneRepoStatus] = []
        for r in [
            *self._repo_factory.get_singleton_repos(),
            *self._repo_factory.get_standalone_repos(),
        ]:
            try:
                standalone_statuses.append(self._repo_repo.get_standalone_status(r))
            except RepoError as exc:
                if on_repo_error is not None:
                    logger.warning("standalone probe failed for %s: %s", r.name, exc)
                else:
                    raise

        # ── main-branch statuses ──────────────────────────────────────────
        main_statuses: dict[str, WorktreeRepoStatus] = {}
        if project_repos:

            def _on_main_error(repo: ProjectRepository, exc: RepoError) -> None:
                logger.warning("main-branch probe failed for %s: %s", repo.name, exc)

            main_statuses = self._env_status_svc.get_main_branch_statuses(
                self._workspace,
                project_repos,
                on_repo_error=_on_main_error if on_repo_error is not None else None,
            )

        return DashboardRefreshData(
            overviews=overviews,
            standalone_statuses=standalone_statuses,
            main_statuses=main_statuses,
        )


@dataclasses.dataclass
class DashboardRefreshData:
    """Data returned by `WorkspaceSnapshotService.collect_for_dashboard()`.

    Carries exactly the objects the dashboard TUI widgets need:

    - ``overviews`` — one `FeatureEnvironmentOverview` per discovered env,
      fed to `FeatureWorktreesGrid.statuses` and `ServicePanel.statuses`.
    - ``standalone_statuses`` — singletons + standalones, fed to
      `StandaloneReposTable.statuses`.
    - ``main_statuses`` — keyed by repo name, fed to
      `FeatureWorktreesGrid.main_statuses` for the repo-label column.
    """

    overviews: list[FeatureEnvironmentOverview]
    standalone_statuses: list[StandaloneRepoStatus]
    main_statuses: dict[str, WorktreeRepoStatus]
