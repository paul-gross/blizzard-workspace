"""Issue/151: `WorkspaceScreen`'s periodic refresh re-reads `.winter/config.toml`.

Drives the real dashboard TUI (via `run_test`) against a materialized tmp
workspace to check the two behaviors that can't be observed at the
`DashboardSnapshotService` level alone: a malformed `config.toml` mid-session
must not blank the already-populated grid, and it must land in the error log
tab exactly like any other captured `RepoError` — matching the per-source
error isolation `_refresh_data` already applies to git failures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from winter_cli.container import Container
from winter_cli.modules.tui.app import WinterDashboardApp
from winter_cli.modules.tui.screens.workspace import WorkspaceScreen
from winter_cli.modules.tui.screens.workspace.feature_worktrees import FeatureWorktreesGrid


@pytest.mark.asyncio
async def test_malformed_config_mid_session_does_not_blank_panels(
    container: Container, tmp_workspace_root: Path
) -> None:
    (tmp_workspace_root / "alpha" / "demo-repo").mkdir(parents=True)
    log = container.error_log_svc()
    log.clear()

    app = WinterDashboardApp(container)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        assert isinstance(screen, WorkspaceScreen)
        grid = screen.query_one("#grid", FeatureWorktreesGrid)
        assert any(o.status.environment.name == "alpha" for o in grid.statuses)

        (tmp_workspace_root / ".winter" / "config.toml").write_text("this is [not valid toml")

        screen.action_refresh()
        await pilot.pause(0.5)

        assert isinstance(app.screen, WorkspaceScreen)  # didn't crash
        # Panels remain populated with the last-good snapshot — not blanked.
        assert any(o.status.environment.name == "alpha" for o in grid.statuses)
        assert any(e.location == "WorkspaceScreen.refresh(config)" for e in log.entries())
