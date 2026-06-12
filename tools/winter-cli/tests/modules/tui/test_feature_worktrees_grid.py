"""FeatureWorktreesGrid renders main-branch status indicator in the repo label column.

Issue/38 acceptance: the "repo" column cell for each project repo shows a git
status indicator (dirty count, ahead, behind) sourced from the main-branch
checkout when main_statuses is set. A clean / absent entry renders with no
indicator suffix.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from winter_cli.modules.tui.screens.workspace.feature_worktrees import FeatureWorktreesGrid
from winter_cli.modules.tui.screens.workspace.repo_status import render_repo_cell
from winter_cli.modules.workspace.models.domain_model import (
    FeatureEnvironment,
    FeatureWorktree,
    ProjectRepository,
    Workspace,
)
from winter_cli.modules.workspace.models.service_model import (
    FeatureEnvironmentOverview,
    FeatureEnvironmentStatus,
    WorktreeRepoStatus,
)

_WORKSPACE = Workspace(root_path=Path("/tmp/ws"), session_prefix="t", main_branch="main")

_PIN_PAD = "  "


def _env(name: str, index: int) -> FeatureEnvironment:
    return FeatureEnvironment(workspace=_WORKSPACE, name=name, index=index, path=Path(f"/tmp/ws/{name}"))


def _repo(repo_name: str) -> ProjectRepository:
    return ProjectRepository(name=repo_name, main_path=Path(f"/tmp/ws/projects/{repo_name}"), main_branch="main")


def _worktree(env: FeatureEnvironment, repo_name: str) -> FeatureWorktree:
    return FeatureWorktree(workspace=_WORKSPACE, environment=env, repository=_repo(repo_name))


def _overview(name: str, index: int, repo_names: list[str]) -> FeatureEnvironmentOverview:
    env = _env(name, index)
    repo_statuses = [
        WorktreeRepoStatus(worktree=_worktree(env, rn), branch=name, ahead=0, behind=0, dirty_count=0)
        for rn in repo_names
    ]
    status = FeatureEnvironmentStatus(environment=env, feature_branch=f"feature/{name}")
    return FeatureEnvironmentOverview(status=status, repo_statuses=repo_statuses)


def _main_status(repo_name: str, dirty_count: int = 0, ahead: int = 0, behind: int = 0) -> WorktreeRepoStatus:
    dummy_env = FeatureEnvironment(workspace=_WORKSPACE, name="", index=0, path=Path(f"/tmp/ws/projects/{repo_name}"))
    dummy_wt = FeatureWorktree(workspace=_WORKSPACE, environment=dummy_env, repository=_repo(repo_name))
    return WorktreeRepoStatus(
        worktree=dummy_wt,
        branch="main",
        ahead=ahead,
        behind=behind,
        dirty_count=dirty_count,
    )


class _GridApp(App):
    def __init__(self, statuses: list[FeatureEnvironmentOverview]) -> None:
        super().__init__()
        self._statuses = statuses

    def compose(self) -> ComposeResult:
        yield FeatureWorktreesGrid(id="grid")

    def on_mount(self) -> None:
        self.query_one("#grid", FeatureWorktreesGrid).statuses = self._statuses


@pytest.mark.asyncio
async def test_main_branch_status_renders_in_label_column():
    """A repo with dirty_count=3 in main_statuses shows the rendered indicator in its label cell."""
    statuses = [_overview("alpha", 1, ["myrepo"])]
    app = _GridApp(statuses)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        grid = app.query_one("#grid", FeatureWorktreesGrid)

        # Before setting main_statuses — label should not contain indicator
        cell_before = grid.get_cell("myrepo", "repo")
        assert "files" not in cell_before.plain

        # Set main_statuses with a dirty repo
        ms = _main_status("myrepo", dirty_count=3)
        grid.main_statuses = {"myrepo": ms}
        await pilot.pause()

        expected_suffix = render_repo_cell(ms, include_extensions=False).plain
        cell = grid.get_cell("myrepo", "repo")
        assert expected_suffix in cell.plain


@pytest.mark.asyncio
async def test_clean_main_branch_renders_no_indicator():
    """A repo with no main_statuses entry renders its label as the bare prefix+name with no suffix."""
    statuses = [_overview("alpha", 1, ["myrepo", "otherrepo"])]
    app = _GridApp(statuses)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        grid = app.query_one("#grid", FeatureWorktreesGrid)

        # Set main_statuses only for otherrepo — myrepo has no entry (clean)
        ms_other = _main_status("otherrepo", dirty_count=2)
        grid.main_statuses = {"otherrepo": ms_other}
        await pilot.pause()

        clean_cell = grid.get_cell("myrepo", "repo")
        # Clean repo label is exactly the pin-pad prefix + repo name with no suffix
        assert clean_cell.plain == f"{_PIN_PAD} myrepo"

        dirty_cell = grid.get_cell("otherrepo", "repo")
        expected_suffix = render_repo_cell(ms_other, include_extensions=False).plain
        assert expected_suffix in dirty_cell.plain
