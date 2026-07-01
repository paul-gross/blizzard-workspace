from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import ClickRecorder, FakeFilesystem
from winter_cli.config.models import (
    AdoptExtensions,
    ProjectRepositoryConfig,
    WorkspaceConfig,
)
from winter_cli.modules.workspace.drift import DriftWarningService
from winter_cli.modules.workspace.models import Workspace
from winter_cli.modules.workspace.repository_factory import RepositoryFactory

WORKSPACE_ROOT = Path("/ws")
PROJECTS_DIR = WORKSPACE_ROOT / "projects"


@pytest.fixture
def workspace() -> Workspace:
    return Workspace(root_path=WORKSPACE_ROOT, service_prefix="t", main_branch="main")


@pytest.fixture
def repo_factory() -> RepositoryFactory:
    config = WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        service_prefix="t",
        main_branch="main",
        adopt_extensions=AdoptExtensions.winter,
        project_repos=[
            ProjectRepositoryConfig(name="frontend", url="git@example.com:org/frontend.git"),
            ProjectRepositoryConfig(name="backend", url="git@example.com:org/backend.git"),
        ],
    )
    return RepositoryFactory(config)


def _service(
    workspace: Workspace,
    repo_factory: RepositoryFactory,
    fs: FakeFilesystem,
    click_recorder: ClickRecorder,
) -> DriftWarningService:
    return DriftWarningService(workspace=workspace, repo_factory=repo_factory, fs=fs, click=click_recorder)


def test_detect_returns_empty_when_projects_dir_missing(
    workspace: Workspace, repo_factory: RepositoryFactory, click_recorder: ClickRecorder
) -> None:
    """No projects/ dir on disk → every declared repo counts as missing."""
    fs = FakeFilesystem()  # nothing materialized
    svc = _service(workspace, repo_factory, fs, click_recorder)
    report = svc.detect()
    missing_names = sorted(r.name for r in report.missing)
    assert missing_names == ["backend", "frontend"]
    assert report.undeclared == []


def test_detect_reports_missing_and_undeclared(
    workspace: Workspace, repo_factory: RepositoryFactory, click_recorder: ClickRecorder
) -> None:
    fs = FakeFilesystem(directories=[PROJECTS_DIR, PROJECTS_DIR / "frontend", PROJECTS_DIR / "stranger"])
    svc = _service(workspace, repo_factory, fs, click_recorder)

    report = svc.detect()

    assert [r.name for r in report.missing] == ["backend"]
    assert report.undeclared == ["stranger"]
    assert report.any is True


def test_raise_warning_echoes_to_stderr_when_drift(
    workspace: Workspace, repo_factory: RepositoryFactory, click_recorder: ClickRecorder
) -> None:
    fs = FakeFilesystem(directories=[PROJECTS_DIR])  # empty projects dir → both missing
    svc = _service(workspace, repo_factory, fs, click_recorder)

    svc.raise_warning()

    assert len(click_recorder.calls) == 1
    message, err = click_recorder.calls[0]
    assert err is True
    assert "warning:" in message
    assert "backend" in message
    assert "frontend" in message


def test_raise_warning_silent_when_no_drift(
    workspace: Workspace, repo_factory: RepositoryFactory, click_recorder: ClickRecorder
) -> None:
    fs = FakeFilesystem(
        directories=[PROJECTS_DIR, PROJECTS_DIR / "frontend", PROJECTS_DIR / "backend"],
    )
    svc = _service(workspace, repo_factory, fs, click_recorder)

    svc.raise_warning()

    assert click_recorder.calls == []


def test_detect_ignores_dotfiles_under_projects(
    workspace: Workspace, repo_factory: RepositoryFactory, click_recorder: ClickRecorder
) -> None:
    """Hidden directories like `.cache` aren't reported as undeclared."""
    fs = FakeFilesystem(
        directories=[
            PROJECTS_DIR,
            PROJECTS_DIR / "frontend",
            PROJECTS_DIR / "backend",
            PROJECTS_DIR / ".cache",
        ],
    )
    svc = _service(workspace, repo_factory, fs, click_recorder)

    report = svc.detect()
    assert report.undeclared == []
