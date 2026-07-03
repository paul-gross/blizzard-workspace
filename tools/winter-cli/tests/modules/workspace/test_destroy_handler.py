"""Tests for `DestroyHandler` PATTERNS resolution, confirmation, and fan-out.

Covers:
- A single literal env pattern destroys immediately — no confirmation prompt.
- Multiple literal env patterns resolve to each, prompting for confirmation.
- A glob pattern resolves to every discovered env it matches, prompting for confirmation.
- A glob matching no discovered env destroys nothing and reports.
- --force bypasses the confirmation prompt for a multi-env / glob selection.
- Declining the confirmation prompt destroys nothing.
- A failed env's destroy_env() False propagates to sys.exit(1).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import click
import pytest

from winter_cli.modules.workspace.handlers.destroy_handler import DestroyHandler, DestroyParams


class _FakeDestroyService:
    """Records destroy_env() calls; returns a configurable per-env success flag."""

    def __init__(self, failing_envs: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self._failing_envs = failing_envs or set()

    def destroy_env(
        self,
        name: str,
        force: bool,
        strict: bool,
        dry_run: bool,
        reporter: Any,
        provision_teardown: bool = True,
    ) -> bool:
        self.calls.append(name)
        return name not in self._failing_envs


def _make_handler(
    discovered_envs: list[str] | None = None,
    failing_envs: set[str] | None = None,
) -> tuple[DestroyHandler, _FakeDestroyService]:
    workspace_repo = MagicMock()
    workspace_repo.get_environment.side_effect = lambda _ws, name: SimpleNamespace(name=name)
    workspace_repo.get_environments.return_value = [
        SimpleNamespace(name=n) for n in (discovered_envs if discovered_envs is not None else ["alpha", "beta"])
    ]

    repo_factory = MagicMock()
    repo_factory.get_project_repos.return_value = []

    reporter_factory = MagicMock()
    reporter_factory.get_init_reporter.return_value = MagicMock()

    destroy_svc = _FakeDestroyService(failing_envs)
    handler = DestroyHandler(
        destroy_service=destroy_svc,  # type: ignore[arg-type]
        reporter_factory=reporter_factory,
        workspace_repo=workspace_repo,
        repo_factory=repo_factory,
        workspace=MagicMock(),
    )
    return handler, destroy_svc


def _params(patterns: list[str], force: bool = False) -> DestroyParams:
    return DestroyParams(
        patterns=patterns,
        force=force,
        strict=False,
        dry_run=False,
        output_json=False,
    )


def test_single_literal_env_destroys_with_no_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    handler, svc = _make_handler()
    confirm = MagicMock()
    monkeypatch.setattr(click, "confirm", confirm)

    handler.run(_params(["alpha"]))

    assert svc.calls == ["alpha"]
    confirm.assert_not_called()


def test_multiple_envs_prompts_and_destroys_on_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    handler, svc = _make_handler()
    confirm = MagicMock()
    monkeypatch.setattr(click, "confirm", confirm)

    handler.run(_params(["alpha", "beta"]))

    confirm.assert_called_once()
    assert svc.calls == ["alpha", "beta"]


def test_multiple_envs_declined_confirmation_destroys_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    handler, svc = _make_handler()
    monkeypatch.setattr(click, "confirm", MagicMock(side_effect=click.Abort()))

    with pytest.raises(click.Abort):
        handler.run(_params(["alpha", "beta"]))

    assert svc.calls == []


def test_glob_matching_several_prompts_and_destroys_each(monkeypatch: pytest.MonkeyPatch) -> None:
    handler, svc = _make_handler(discovered_envs=["alpha", "beta", "gamma"])
    confirm = MagicMock()
    monkeypatch.setattr(click, "confirm", confirm)

    handler.run(_params(["*"]))

    confirm.assert_called_once()
    assert svc.calls == ["alpha", "beta", "gamma"]


def test_glob_matching_none_destroys_nothing_and_reports(capsys: pytest.CaptureFixture[Any]) -> None:
    handler, svc = _make_handler(discovered_envs=["alpha", "beta"])

    handler.run(_params(["zzz-*"]))

    assert svc.calls == []
    assert "No environments matched" in capsys.readouterr().out


def test_force_bypasses_confirmation_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    handler, svc = _make_handler()
    confirm = MagicMock()
    monkeypatch.setattr(click, "confirm", confirm)

    handler.run(_params(["alpha", "beta"], force=True))

    confirm.assert_not_called()
    assert svc.calls == ["alpha", "beta"]


def test_failed_env_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    handler, _ = _make_handler(failing_envs={"alpha"})
    monkeypatch.setattr(click, "confirm", MagicMock())

    with pytest.raises(SystemExit) as excinfo:
        handler.run(_params(["alpha"]))

    assert excinfo.value.code == 1


def test_all_succeed_no_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    handler, _ = _make_handler()
    monkeypatch.setattr(click, "confirm", MagicMock())

    handler.run(_params(["alpha"]))  # no SystemExit raised
