from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from winter_cli.modules.workspace.env_clean_service import EnvCleanService
from winter_cli.modules.workspace.models import (
    FeatureEnvironment,
    FeatureWorktree,
    ProjectRepository,
    Workspace,
)

WORKSPACE_ROOT = Path("/ws")


@pytest.fixture
def workspace() -> Workspace:
    return Workspace(root_path=WORKSPACE_ROOT, service_prefix="t", main_branch="main")


class FakeWriteRepoRepository:
    """Stub for the `IWriteRepoRepository` Protocol — records every call.

    Pre-seed `untracked` with `(env, repo)` → paths. A pair absent from the
    map has nothing untracked.
    """

    def __init__(self) -> None:
        self.untracked: dict[tuple[str, str], list[str]] = {}
        self.clean_calls: list[tuple[str, str]] = []

    def list_untracked(self, worktree: FeatureWorktree) -> list[str]:
        return list(self.untracked.get((worktree.environment.name, worktree.repository.name), []))

    def clean_untracked(self, worktree: FeatureWorktree) -> None:
        self.clean_calls.append((worktree.environment.name, worktree.repository.name))

    # Methods touched by other IWriteRepoRepository code paths — raise to
    # surface accidental fan-out beyond the call under test.
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"FakeWriteRepoRepository.{name} called unexpectedly")


@pytest.fixture
def fake_repo_repo() -> FakeWriteRepoRepository:
    return FakeWriteRepoRepository()


@pytest.fixture
def service(fake_repo_repo: FakeWriteRepoRepository) -> EnvCleanService:
    return EnvCleanService(repo_repo=fake_repo_repo)  # type: ignore[arg-type]


def _wt(workspace: Workspace, repo_name: str, env_name: str = "alpha") -> FeatureWorktree:
    env = FeatureEnvironment(workspace=workspace, name=env_name, index=1, path=workspace.root_path / env_name)
    project_repo = ProjectRepository(name=repo_name, main_path=workspace.root_path / repo_name, main_branch="main")
    return FeatureWorktree(workspace=workspace, environment=env, repository=project_repo)


# ── clean ─────────────────────────────────────────────────────────────────────


def test_clean_removes_and_reports_paths(
    service: EnvCleanService, fake_repo_repo: FakeWriteRepoRepository, workspace: Workspace
) -> None:
    wt = _wt(workspace, "winter")
    fake_repo_repo.untracked[("alpha", "winter")] = ["scratch.py", "notes/todo.md"]

    report = service.clean([wt], dry_run=False)

    assert fake_repo_repo.clean_calls == [("alpha", "winter")]
    assert report.dry_run is False
    assert report.total == 2
    assert report.repos[0].paths == ["scratch.py", "notes/todo.md"]
    assert report.repos[0].count == 2


def test_clean_dry_run_enumerates_without_removing(
    service: EnvCleanService, fake_repo_repo: FakeWriteRepoRepository, workspace: Workspace
) -> None:
    wt = _wt(workspace, "winter")
    fake_repo_repo.untracked[("alpha", "winter")] = ["scratch.py"]

    report = service.clean([wt], dry_run=True)

    assert fake_repo_repo.clean_calls == []
    assert report.dry_run is True
    assert report.total == 1
    assert report.repos[0].paths == ["scratch.py"]


def test_clean_skips_git_call_for_worktree_with_nothing_untracked(
    service: EnvCleanService, fake_repo_repo: FakeWriteRepoRepository, workspace: Workspace
) -> None:
    """A worktree with nothing untracked is still reported, but `git clean`
    never runs for it — the command is a no-op there, and shelling out anyway
    would make a clean run look like it touched every matched worktree."""
    clean_wt = _wt(workspace, "winter")
    dirty_wt = _wt(workspace, "winter-docs")
    fake_repo_repo.untracked[("alpha", "winter-docs")] = ["build/out.html"]

    report = service.clean([clean_wt, dirty_wt], dry_run=False)

    assert fake_repo_repo.clean_calls == [("alpha", "winter-docs")]
    assert [o.repo_name for o in report.repos] == ["winter", "winter-docs"]
    assert report.repos[0].paths == []
    assert report.total == 1


def test_clean_spans_multiple_envs(
    service: EnvCleanService, fake_repo_repo: FakeWriteRepoRepository, workspace: Workspace
) -> None:
    """PATTERNS may match the same repo name in more than one env, so outcomes
    are keyed by (env, repo) rather than repo name alone."""
    alpha_wt = _wt(workspace, "winter", env_name="alpha")
    beta_wt = _wt(workspace, "winter", env_name="beta")
    fake_repo_repo.untracked[("alpha", "winter")] = ["a.py"]
    fake_repo_repo.untracked[("beta", "winter")] = ["b.py", "c.py"]

    report = service.clean([alpha_wt, beta_wt], dry_run=False)

    assert fake_repo_repo.clean_calls == [("alpha", "winter"), ("beta", "winter")]
    assert [(o.env, o.count) for o in report.repos] == [("alpha", 1), ("beta", 2)]
    assert report.total == 3


def test_clean_with_no_targets_reports_empty(service: EnvCleanService) -> None:
    report = service.clean([], dry_run=False)

    assert report.repos == []
    assert report.total == 0


# ── preview ───────────────────────────────────────────────────────────────────


def test_preview_enumerates_without_removing(
    service: EnvCleanService, fake_repo_repo: FakeWriteRepoRepository, workspace: Workspace
) -> None:
    wt = _wt(workspace, "winter")
    fake_repo_repo.untracked[("alpha", "winter")] = ["scratch.py", "tmp.log"]

    report = service.preview([wt])

    assert fake_repo_repo.clean_calls == []
    assert report.dry_run is True
    assert report.total == 2


def test_preview_does_not_freeze_the_removal_set(
    service: EnvCleanService, fake_repo_repo: FakeWriteRepoRepository, workspace: Workspace
) -> None:
    """`clean` re-enumerates rather than reusing the preview's paths, so a file
    created between the preview and the confirmation is still removed."""
    wt = _wt(workspace, "winter")
    fake_repo_repo.untracked[("alpha", "winter")] = ["first.py"]

    service.preview([wt])
    fake_repo_repo.untracked[("alpha", "winter")] = ["first.py", "appeared-later.py"]
    report = service.clean([wt], dry_run=False)

    assert report.repos[0].paths == ["first.py", "appeared-later.py"]
    assert report.total == 2
