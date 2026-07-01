from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import click
import pytest

from winter_cli.modules.workspace.handlers.repo_handler import (
    RepoAddParams,
    RepoHandler,
    RepoRemoveParams,
)
from winter_cli.modules.workspace.models import (
    ProjectRepository,
    StandaloneRepository,
    Workspace,
)


def _workspace() -> Workspace:
    return Workspace(root_path=Path("/ws"), service_prefix="ws", main_branch="master")


def _make_handler(
    *,
    project_repos: list[ProjectRepository] | None = None,
    standalone_repos: list[StandaloneRepository] | None = None,
    name_from_url: str = "demo",
) -> tuple[RepoHandler, MagicMock]:
    repo_factory = MagicMock()
    repo_factory.get_project_repos.return_value = project_repos or []
    repo_factory.get_standalone_repos.return_value = standalone_repos or []
    repo_factory.name_from_url.return_value = name_from_url

    write_winter_config_repo = MagicMock()
    write_winter_config_repo.remove_project_repository.return_value = True
    write_winter_config_repo.remove_standalone_repository.return_value = True

    handler = RepoHandler(
        repo_factory=repo_factory,
        drift_warning_svc=MagicMock(),
        cli_output_svc=MagicMock(),
        cli_input_validation_svc=MagicMock(),
        write_winter_config_repo=write_winter_config_repo,
        workspace=_workspace(),
    )
    return handler, write_winter_config_repo


def _add_params(**overrides) -> RepoAddParams:
    defaults: dict = {
        "url": "git@host:org/demo.git",
        "standalone": False,
        "name": None,
        "main_branch": None,
        "ref": None,
        "git_excludes": [],
        "cmd": [],
        "pinned": False,
        "path": None,
        "prefix": None,
        "local": False,
        "output_json": False,
    }
    defaults.update(overrides)
    return RepoAddParams(**defaults)


def _remove_params(**overrides) -> RepoRemoveParams:
    defaults: dict = {
        "kind": "project",
        "name": "demo",
        "local": False,
        "output_json": False,
    }
    defaults.update(overrides)
    return RepoRemoveParams(**defaults)


# ── add: mutex rules ──────────────────────────────────────────────────────


def test_add_pinned_with_standalone_is_rejected() -> None:
    handler, _ = _make_handler()
    with pytest.raises(click.ClickException, match="--pinned only applies to project repos"):
        handler.add(_add_params(standalone=True, pinned=True))


def test_add_path_without_standalone_is_rejected() -> None:
    handler, _ = _make_handler()
    with pytest.raises(click.ClickException, match="--path is only valid with --standalone"):
        handler.add(_add_params(path="sub/dir"))


def test_add_prefix_without_standalone_is_rejected() -> None:
    handler, _ = _make_handler()
    with pytest.raises(click.ClickException, match="--prefix is only valid with --standalone"):
        handler.add(_add_params(prefix="ext"))


def test_add_path_must_be_relative() -> None:
    handler, _ = _make_handler()
    with pytest.raises(click.ClickException, match="must be relative"):
        handler.add(_add_params(standalone=True, path="/abs/path"))


def test_add_path_must_be_free_of_parent_segments() -> None:
    handler, _ = _make_handler()
    with pytest.raises(click.ClickException, match="must be relative"):
        handler.add(_add_params(standalone=True, path="../escape"))


# ── add: duplicate-detection rules ────────────────────────────────────────


def test_add_project_rejects_duplicate_url() -> None:
    existing = ProjectRepository(
        name="demo", main_path=Path("/ws/projects/demo"), main_branch="master", url="git@host:org/demo.git"
    )
    handler, _ = _make_handler(project_repos=[existing])
    with pytest.raises(click.ClickException, match="already declared"):
        handler.add(_add_params(url="git@host:org/demo.git"))


def test_add_project_rejects_duplicate_name() -> None:
    existing = ProjectRepository(
        name="demo", main_path=Path("/ws/projects/demo"), main_branch="master", url="git@host:org/other.git"
    )
    handler, _ = _make_handler(project_repos=[existing])
    with pytest.raises(click.ClickException, match="already declared"):
        handler.add(_add_params(name="demo"))


def test_add_standalone_rejects_duplicate_url() -> None:
    existing = StandaloneRepository(name="demo", path=Path("/ws/demo"), url="git@host:org/demo.git")
    handler, _ = _make_handler(standalone_repos=[existing])
    with pytest.raises(click.ClickException, match="already declared"):
        handler.add(_add_params(standalone=True, url="git@host:org/demo.git"))


def test_add_standalone_rejects_duplicate_path() -> None:
    existing = StandaloneRepository(name="other", path=Path("/ws/demo"))
    handler, _ = _make_handler(
        standalone_repos=[existing],
        name_from_url="demo",
    )
    with pytest.raises(click.ClickException, match=r"path .*already declared"):
        handler.add(_add_params(standalone=True))


# ── add: happy path appends to config ─────────────────────────────────────


def test_add_project_appends_to_config() -> None:
    handler, config_repo = _make_handler()
    handler.add(_add_params())
    config_repo.append_project_repository.assert_called_once()


def test_add_standalone_appends_to_config() -> None:
    handler, config_repo = _make_handler()
    handler.add(_add_params(standalone=True))
    config_repo.append_standalone_repository.assert_called_once()


# ── remove: validation rules ──────────────────────────────────────────────


def test_remove_rejects_unknown_kind() -> None:
    handler, _ = _make_handler()
    with pytest.raises(click.ClickException, match="Type must be"):
        handler.remove(_remove_params(kind="weird"))


def test_remove_reports_not_found_when_config_missing() -> None:
    handler, config_repo = _make_handler()
    config_repo.remove_project_repository.return_value = False
    with pytest.raises(click.ClickException, match="not found"):
        handler.remove(_remove_params())


def test_remove_project_calls_config_repo() -> None:
    handler, config_repo = _make_handler()
    handler.remove(_remove_params(kind="project", name="demo"))
    config_repo.remove_project_repository.assert_called_once_with("demo", local=False)


def test_remove_standalone_calls_config_repo() -> None:
    handler, config_repo = _make_handler()
    handler.remove(_remove_params(kind="standalone", name="ext"))
    config_repo.remove_standalone_repository.assert_called_once_with("ext", local=False)


# ── add --ref: validation and write ──────────────────────────────────────────


def test_add_ref_without_standalone_is_rejected() -> None:
    handler, _ = _make_handler()
    with pytest.raises(click.ClickException, match="--ref is only valid with --standalone"):
        handler.add(_add_params(ref="v1.0.0"))


def test_add_standalone_with_ref_writes_ref_to_config() -> None:
    """repo add --standalone --ref v1.2.0 passes ref through to StandaloneRepositoryConfig."""
    from winter_cli.config.models import StandaloneRepositoryConfig

    handler, config_repo = _make_handler()
    handler.add(_add_params(standalone=True, ref="v1.2.0"))

    config_repo.append_standalone_repository.assert_called_once()
    written_config: StandaloneRepositoryConfig = config_repo.append_standalone_repository.call_args[0][0]
    assert written_config.ref == "v1.2.0"


def test_add_standalone_without_ref_writes_none_ref() -> None:
    """repo add --standalone without --ref leaves ref as None in StandaloneRepositoryConfig."""
    from winter_cli.config.models import StandaloneRepositoryConfig

    handler, config_repo = _make_handler()
    handler.add(_add_params(standalone=True))

    config_repo.append_standalone_repository.assert_called_once()
    written_config: StandaloneRepositoryConfig = config_repo.append_standalone_repository.call_args[0][0]
    assert written_config.ref is None
