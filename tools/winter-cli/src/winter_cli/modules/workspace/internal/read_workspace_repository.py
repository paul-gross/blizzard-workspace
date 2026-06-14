from __future__ import annotations

import git

from winter_cli.modules.workspace.env_index import GREEK_LETTERS, resolve_env_index
from winter_cli.modules.workspace.internal.branch_tracking import read_origin_merge_branch
from winter_cli.modules.workspace.internal.repo_error_factory import RepoErrorFactory
from winter_cli.modules.workspace.models import (
    FeatureEnvironment,
    FeatureEnvironmentStatus,
    ProjectRepository,
    Workspace,
)
from winter_cli.modules.workspace.workspace_repository import IReadWorkspaceRepository


class ReadWorkspaceRepository:
    """Read-only filesystem implementation of the workspace repository.

    Internal infrastructure — discovers feature environments by scanning the workspace root
    for Greek-letter directories and derives the connected feature branch from git's upstream
    tracking on the first non-pinned repo. Per-environment status badges are populated later
    by visual plugins (see `IEnvironmentDecorator`); this class leaves `extensions={}` and
    has no awareness of any service-orchestration extension.
    """

    def __init__(self, error_factory: RepoErrorFactory) -> None:
        self._error_factory = error_factory

    def get_environments(
        self, workspace: Workspace, project_repos: list[ProjectRepository]
    ) -> list[FeatureEnvironment]:
        return [self._build_environment(workspace, name) for name in self._discover_env_names(workspace, project_repos)]

    def get_environment(self, workspace: Workspace, name: str) -> FeatureEnvironment:
        return self._build_environment(workspace, name)

    def get_environment_status(
        self,
        env: FeatureEnvironment,
        project_repos: list[ProjectRepository],
    ) -> FeatureEnvironmentStatus:
        feature_branch = self._read_feature_branch(env, project_repos)
        return FeatureEnvironmentStatus(
            environment=env,
            feature_branch=feature_branch,
        )

    def _discover_env_names(self, workspace: Workspace, project_repos: list[ProjectRepository]) -> list[str]:
        known_repos = {r.name for r in project_repos}
        found = []
        for name in GREEK_LETTERS:
            candidate = workspace.root_path / name
            if not candidate.is_dir():
                continue
            subdirs = {d.name for d in candidate.iterdir() if d.is_dir()}
            if subdirs & known_repos:
                found.append(name)
        return found

    def _build_environment(self, workspace: Workspace, name: str) -> FeatureEnvironment:
        path = workspace.root_path / name
        return FeatureEnvironment(
            workspace=workspace,
            name=name,
            index=resolve_env_index(name),
            path=path,
        )

    def _read_feature_branch(
        self,
        env: FeatureEnvironment,
        project_repos: list[ProjectRepository],
    ) -> str | None:
        """Resolve the env-wide feature branch summary for `ws status` / the dashboard.

        This is a *display* summary, not a per-worktree truth: it reads the
        first non-pinned repo only (pinned repos track main and would lie), on
        the contract that `winter ws connect` points every non-pinned repo at
        the same branch. `ws push` / `ws pull` do not depend on this — they
        resolve each worktree's target independently (see
        `WriteRepoRepository.get_worktree_push_branch`), so a worktree
        re-pointed to a different branch won't be reflected here.

        Resolution is delegated to `read_origin_merge_branch`, which reads
        config directly so a freshly-connected, never-fetched env reads back as
        connected immediately.
        """
        for repo in project_repos:
            if repo.pinned:
                continue
            worktree_path = env.path / repo.name
            if not (worktree_path / ".git").exists():
                return None
            with git.Repo(str(worktree_path)) as r:
                return read_origin_merge_branch(r, self._error_factory, cwd=worktree_path, label=repo.name)
        return None


def _conforms_read_workspace_repository(x: ReadWorkspaceRepository) -> IReadWorkspaceRepository:
    return x
