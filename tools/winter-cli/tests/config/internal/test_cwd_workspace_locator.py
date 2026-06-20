from __future__ import annotations

from pathlib import Path

import pytest

from winter_cli.config.internal.cwd_workspace_locator import CwdWorkspaceLocator
from winter_cli.core.config_file import ConfigError


def _make_root(path: Path) -> Path:
    """Create a workspace root: a directory holding `.winter/config.toml`."""
    config = path / ".winter" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("")
    return path


def test_find_workspace_root_returns_directory_containing_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = _make_root(tmp_path / "workspace")
    nested = workspace_root / "project" / "subdir"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    result = CwdWorkspaceLocator().find_workspace_root()

    assert result == workspace_root


def test_find_workspace_root_ignores_env_local_winter_logs_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The env's per-service logs land in `<env>/.winter/logs/`, creating a bare
    # `.winter/` with no config.toml. The locator must walk past it to the real root.
    workspace_root = _make_root(tmp_path / "workspace")
    env_dir = workspace_root / "alpha"
    (env_dir / ".winter" / "logs").mkdir(parents=True)
    repo_subdir = env_dir / "winter" / "tools"
    repo_subdir.mkdir(parents=True)
    monkeypatch.chdir(repo_subdir)

    result = CwdWorkspaceLocator().find_workspace_root()

    assert result == workspace_root


def test_find_workspace_root_raises_when_no_config_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A bare `.winter/` directory (logs only, no config.toml) is not a root.
    bare = tmp_path / "alpha"
    (bare / ".winter" / "logs").mkdir(parents=True)
    monkeypatch.chdir(bare)

    with pytest.raises(ConfigError, match="Could not find workspace root"):
        CwdWorkspaceLocator().find_workspace_root()
