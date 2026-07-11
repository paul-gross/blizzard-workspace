"""Issue/151: the dashboard's periodic refresh re-reads config.toml so
config-derived state (project repos, standalone repos, and env discovery's
`known_repos` gate) converges without a restart.

Drives `DashboardSnapshotService` directly through the real DI container
against a materialized tmp workspace — the same "small script" shape the
issue's runtime probe calls for, just as pytest cases: edit `config.toml`
between two `collect_for_dashboard()` calls and assert the second call
reflects the change, with no dashboard/textual machinery involved.
"""

from __future__ import annotations

from pathlib import Path

from winter_cli.container import Container
from winter_cli.core.config_file import ConfigError


def _append_config(tmp_workspace_root: Path, snippet: str) -> None:
    path = tmp_workspace_root / ".winter" / "config.toml"
    path.write_text(path.read_text() + "\n" + snippet + "\n")


def test_project_repo_added_mid_session_appears_on_next_refresh(container: Container, tmp_workspace_root: Path) -> None:
    """A project repo declared after launch appears in an *existing* env's worktree row.

    `alpha/demo-repo` is materialized before the first refresh, so `alpha` is
    already a known env. `second-repo` is declared and materialized into that
    same env only afterward — the second refresh must pick it up as a new
    worktree column within `alpha`, with its git status probed like any other
    (Acceptance Criterion 1's Given/When/Then).
    """
    (tmp_workspace_root / "alpha" / "demo-repo").mkdir(parents=True)
    svc = container.dashboard_snapshot_svc()

    first = svc.collect_for_dashboard()
    (alpha_first,) = [o for o in first.overviews if o.status.environment.name == "alpha"]
    assert {rs.worktree.repository.name for rs in alpha_first.repo_statuses} == {"demo-repo"}

    _append_config(
        tmp_workspace_root,
        '[[project_repository]]\nname = "second-repo"\nurl = "git@example.com:demo/second-repo.git"\n',
    )
    (tmp_workspace_root / "alpha" / "second-repo").mkdir(parents=True)

    second = svc.collect_for_dashboard()
    (alpha_second,) = [o for o in second.overviews if o.status.environment.name == "alpha"]
    assert {rs.worktree.repository.name for rs in alpha_second.repo_statuses} == {"demo-repo", "second-repo"}


def test_standalone_repo_added_mid_session_appears_in_standalone_panel(
    container: Container, tmp_workspace_root: Path
) -> None:
    svc = container.dashboard_snapshot_svc()

    first = svc.collect_for_dashboard()
    assert not any(s.repository.name == "my-ext" for s in first.standalone_statuses)

    _append_config(
        tmp_workspace_root,
        '[[standalone_repository]]\nname = "my-ext"\nurl = "git@example.com:demo/my-ext.git"\n',
    )

    second = svc.collect_for_dashboard()
    assert any(s.repository.name == "my-ext" for s in second.standalone_statuses)


def test_env_of_repo_added_post_launch_is_discovered_on_next_refresh(
    container: Container, tmp_workspace_root: Path
) -> None:
    """`known_repos` in env discovery reflects re-read config, not the launch snapshot.

    An `alpha/` env whose only worktree is `second-repo` — a repo declared
    *after* the first refresh — must be discovered on the next poll, not
    stay invisible because `known_repos` was computed from the launch-time
    project repo list.
    """
    svc = container.dashboard_snapshot_svc()

    first = svc.collect_for_dashboard()
    assert first.overviews == []

    _append_config(
        tmp_workspace_root,
        '[[project_repository]]\nname = "second-repo"\nurl = "git@example.com:demo/second-repo.git"\n',
    )
    (tmp_workspace_root / "alpha" / "second-repo").mkdir(parents=True)

    second = svc.collect_for_dashboard()
    assert any(o.status.environment.name == "alpha" for o in second.overviews)


def test_malformed_config_at_refresh_is_tolerated(container: Container, tmp_workspace_root: Path) -> None:
    """A malformed `config.toml` mid-session logs via `on_config_error` and keeps last-good state."""
    svc = container.dashboard_snapshot_svc()

    first = svc.collect_for_dashboard()

    (tmp_workspace_root / ".winter" / "config.toml").write_text("this is [not valid toml")

    errors: list[ConfigError] = []
    second = svc.collect_for_dashboard(on_config_error=errors.append)

    assert len(errors) == 1
    assert isinstance(errors[0], ConfigError)
    # Last-good config was retained — same repo set as before the malformed edit.
    assert second.main_statuses.keys() == first.main_statuses.keys()
