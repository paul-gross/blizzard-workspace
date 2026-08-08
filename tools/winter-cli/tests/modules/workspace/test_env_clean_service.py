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
        self.removed: dict[tuple[str, str], list[str]] = {}
        self.clean_calls: list[tuple[str, str]] = []

    def list_untracked(self, worktree: FeatureWorktree) -> list[str]:
        return list(self.untracked.get((worktree.environment.name, worktree.repository.name), []))

    def clean_untracked(self, worktree: FeatureWorktree) -> list[str]:
        key = (worktree.environment.name, worktree.repository.name)
        self.clean_calls.append(key)
        # Defaults to the enumerated set; seed `removed` separately to model a
        # real run whose removed set differs from what a preview enumerated.
        return list(self.removed.get(key, self.untracked.get(key, [])))

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


def test_clean_runs_git_clean_on_every_target_unconditionally(
    service: EnvCleanService, fake_repo_repo: FakeWriteRepoRepository, workspace: Workspace
) -> None:
    """`git clean` runs for every matched worktree, including ones a preview
    would call empty.

    Regression: gating the call on a prior enumeration meant a worktree whose
    only untracked content was an **empty directory** — invisible to
    `git ls-files --others` but removed by `git clean -fd` — reported
    "nothing to clean" and was never cleaned.
    """
    quiet_wt = _wt(workspace, "winter")
    noisy_wt = _wt(workspace, "winter-docs")
    fake_repo_repo.untracked[("alpha", "winter-docs")] = ["build/out.html"]

    report = service.clean([quiet_wt, noisy_wt], dry_run=False)

    assert fake_repo_repo.clean_calls == [("alpha", "winter"), ("alpha", "winter-docs")]
    assert [o.repo_name for o in report.repos] == ["winter", "winter-docs"]
    assert report.repos[0].paths == []
    assert report.total == 1


def test_clean_reports_what_git_removed_not_what_was_enumerated(
    service: EnvCleanService, fake_repo_repo: FakeWriteRepoRepository, workspace: Workspace
) -> None:
    """The real run's report comes from `clean_untracked`'s return value.

    Regression: an untracked nested git repository is enumerated by
    `git ls-files --others` but is **not** removed by `git clean -fd`, so a
    report derived from the enumeration claimed a deletion that never
    happened.
    """
    wt = _wt(workspace, "winter")
    fake_repo_repo.untracked[("alpha", "winter")] = ["loose.txt", "nested/"]
    fake_repo_repo.removed[("alpha", "winter")] = ["loose.txt"]

    report = service.clean([wt], dry_run=False)

    assert report.repos[0].paths == ["loose.txt"]
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
    """`clean` re-derives rather than reusing the preview's paths, so a file
    created between the preview and the confirmation is still removed."""
    wt = _wt(workspace, "winter")
    fake_repo_repo.untracked[("alpha", "winter")] = ["first.py"]

    service.preview([wt])
    fake_repo_repo.untracked[("alpha", "winter")] = ["first.py", "appeared-later.py"]
    report = service.clean([wt], dry_run=False)

    assert report.repos[0].paths == ["first.py", "appeared-later.py"]
    assert report.total == 2


def test_dry_run_uses_the_enumeration_and_never_the_removal_call(
    service: EnvCleanService, fake_repo_repo: FakeWriteRepoRepository, workspace: Workspace
) -> None:
    wt = _wt(workspace, "winter")
    fake_repo_repo.untracked[("alpha", "winter")] = ["scratch.py"]

    report = service.clean([wt], dry_run=True)

    assert fake_repo_repo.clean_calls == []
    assert report.repos[0].paths == ["scratch.py"]
