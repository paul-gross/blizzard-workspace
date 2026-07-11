from __future__ import annotations

import logging
from collections.abc import Callable

from winter_cli.config.models import WorkspaceConfig
from winter_cli.config.workspace import WorkspaceConfigService
from winter_cli.core.config_file import ConfigError
from winter_cli.modules.workspace.config_lock_repository import IConfigLockRepository
from winter_cli.modules.workspace.drift import DriftWarningService
from winter_cli.modules.workspace.env_index_registry import IEnvIndexRegistry
from winter_cli.modules.workspace.env_status_service import EnvStatusService
from winter_cli.modules.workspace.git_repository import IGitRepository
from winter_cli.modules.workspace.internal.read_workspace_repository import ReadWorkspaceRepository
from winter_cli.modules.workspace.internal.repo_error_factory import RepoErrorFactory
from winter_cli.modules.workspace.models import FeatureWorktree, RepoError
from winter_cli.modules.workspace.prune_service import PruneService
from winter_cli.modules.workspace.repo_repository import IWriteRepoRepository
from winter_cli.modules.workspace.repository_factory import RepositoryFactory
from winter_cli.modules.workspace.workspace_snapshot_service import (
    DashboardRefreshData,
    WorkspaceSnapshotService,
)
from winter_cli.plugins.types import IEnvironmentDecorator, IWorktreeRepoDecorator

logger = logging.getLogger(__name__)


class DashboardSnapshotService:
    """Re-reads `.winter/config.toml` on every dashboard poll and rebuilds the
    config-derived collaborators `WorkspaceSnapshotService.collect_for_dashboard`
    needs, so a repo/standalone/env added mid-session surfaces without a restart.

    The DI container loads `WorkspaceConfig` and the collaborators that capture
    it (`RepositoryFactory`, `ReadWorkspaceRepository`, `Workspace`) exactly once,
    at launch (see `container.py`'s `workspace_config` / `repo_factory`
    singletons). Those launch-time instances are correct for every command that
    resolves the container fresh per-invocation, but the dashboard resolves the
    container once and then polls forever — so it holds this service instead,
    which re-invokes `WorkspaceConfigService.load()` and rebuilds its own
    `RepositoryFactory` / `ReadWorkspaceRepository` / `Workspace` /
    `EnvStatusService` on every call, wiring them into a fresh
    `WorkspaceSnapshotService`. The remaining collaborators
    (`repo_repo`, `drift_warning_svc`, `prune_svc`, `config_lock_repo`,
    `git_repo`) are not config-derived (or are unused by
    `collect_for_dashboard`) and are reused as-is.

    A malformed `config.toml` at refresh time is tolerated: the parse error is
    reported via `on_config_error` and the last known-good config (and its
    built collaborators) is reused, so the dashboard never crashes or blanks
    its panels just because a mid-edit `config.toml` was momentarily invalid.

    Concurrency invariant: this is a DI Singleton, and `self._snapshot_svc` is
    reassigned by `_reload` on every poll while the dashboard's refresh worker
    runs off the main thread (`@work(thread=True)`, non-exclusive — a poll can
    still be in flight when the 30s timer or a manual `r` starts another). This
    is safe without a lock only because `_build` fully constructs a new,
    independently-valid `WorkspaceSnapshotService` before `self._snapshot_svc`
    is reassigned, and the reassignment itself is a single attribute set (atomic
    under the GIL) — a concurrent reader of `self._snapshot_svc` always observes
    either the previous or the new fully-built instance, never a partial one.
    Do not replace this with in-place mutation of the existing instance.
    """

    def __init__(
        self,
        workspace_config_svc: WorkspaceConfigService,
        repo_error_factory: RepoErrorFactory,
        repo_repo: IWriteRepoRepository,
        env_index_registry: IEnvIndexRegistry,
        drift_warning_svc: DriftWarningService,
        prune_svc: PruneService,
        config_lock_repo: IConfigLockRepository,
        git_repo: IGitRepository,
    ) -> None:
        self._workspace_config_svc = workspace_config_svc
        self._repo_error_factory = repo_error_factory
        self._repo_repo = repo_repo
        self._env_index_registry = env_index_registry
        self._drift_warning_svc = drift_warning_svc
        self._prune_svc = prune_svc
        self._config_lock_repo = config_lock_repo
        self._git_repo = git_repo
        self._snapshot_svc: WorkspaceSnapshotService | None = None

    def collect_for_dashboard(
        self,
        *,
        on_repo_error: Callable[[FeatureWorktree, RepoError], None] | None = None,
        on_config_error: Callable[[ConfigError], None] | None = None,
        env_decorators: list[IEnvironmentDecorator] | None = None,
        worktree_repo_decorators: list[IWorktreeRepoDecorator] | None = None,
    ) -> DashboardRefreshData:
        """Reload config, rebuild collaborators, then collect the dashboard snapshot.

        `on_config_error` is invoked when the reload finds a malformed
        `config.toml` and a last-good snapshot service exists to fall back to;
        the fallback is used silently otherwise (the caller decides how to
        surface it — the dashboard logs it to the error tab). If no last-good
        config was ever loaded (the very first call fails), the `ConfigError`
        propagates — there is nothing valid to fall back to.
        """
        snapshot_svc = self._reload(on_config_error)
        return snapshot_svc.collect_for_dashboard(
            on_repo_error=on_repo_error,
            env_decorators=env_decorators,
            worktree_repo_decorators=worktree_repo_decorators,
        )

    def _reload(self, on_config_error: Callable[[ConfigError], None] | None) -> WorkspaceSnapshotService:
        try:
            config = self._workspace_config_svc.load()
        except ConfigError as exc:
            if self._snapshot_svc is None:
                raise
            logger.warning("config reload failed, retaining last-good config: %s", exc)
            if on_config_error is not None:
                on_config_error(exc)
            return self._snapshot_svc
        self._snapshot_svc = self._build(config)
        return self._snapshot_svc

    def _build(self, config: WorkspaceConfig) -> WorkspaceSnapshotService:
        repo_factory = RepositoryFactory(config=config)
        worktree_repo = ReadWorkspaceRepository(
            error_factory=self._repo_error_factory,
            env_aliases=config.env_aliases,
            envs_per_workspace=config.envs_per_workspace,
            registry=self._env_index_registry,
        )
        workspace = self._repo_repo.get_workspace(
            config.workspace_root,
            config.service_prefix,
            config.main_branch,
            config.base_port,
            config.ports_per_env,
        )
        env_status_svc = EnvStatusService(worktree_repo=worktree_repo, repo_repo=self._repo_repo)
        return WorkspaceSnapshotService(
            workspace=workspace,
            env_status_svc=env_status_svc,
            workspace_repo=worktree_repo,
            repo_repo=self._repo_repo,
            repo_factory=repo_factory,
            drift_warning_svc=self._drift_warning_svc,
            prune_svc=self._prune_svc,
            config_lock_repo=self._config_lock_repo,
            git_repo=self._git_repo,
            dashboard_layout=config.dashboard.layout,
        )
