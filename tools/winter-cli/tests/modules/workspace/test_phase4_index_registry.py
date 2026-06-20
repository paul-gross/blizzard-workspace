"""Phase 4 tests: winter ws index registry-aware output.

Tests cover:
  (a) Persisted index returned (and flagged as "registry") when env is registered.
  (b) Suggested index returned (and flagged as "suggested") when env is not registered.
  (c) Human-readable output: registered env prints the bare index; unregistered
      alias prints the bare index; unregistered ad-hoc name prints with caveat.
  (d) JSON output shape — "source" field distinguishes registry vs suggested.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from winter_cli.modules.workspace.handlers.workspace_handler import (
    EnvIndexParams,
    WorkspaceHandler,
)

# ---------------------------------------------------------------------------
# In-memory registry for handler tests
# ---------------------------------------------------------------------------


class _InMemoryRegistry:
    def __init__(self) -> None:
        self._data: dict[str, int] = {}

    def get_index(self, name: str) -> int | None:
        return self._data.get(name)

    def all_assignments(self) -> dict[str, int]:
        return dict(self._data)

    def assign(self, name: str, index: int) -> None:
        self._data[name] = index

    def remove(self, name: str) -> None:
        self._data.pop(name, None)


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def _make_index_handler(
    env_aliases: list[str] | None = None,
    envs_per_workspace: int | None = None,
    registry: _InMemoryRegistry | None = None,
) -> WorkspaceHandler:
    """Minimal WorkspaceHandler wired for index() tests."""
    cli_output_svc = MagicMock()
    cli_output_svc.style.side_effect = lambda text, _style: text

    return WorkspaceHandler(
        env_status_svc=MagicMock(),
        workspace_sync_svc=MagicMock(),
        workspace_push_svc=MagicMock(),
        workspace_merge_svc=MagicMock(),
        env_checkout_svc=MagicMock(),
        workspace_repo=MagicMock(),
        repo_repo=MagicMock(),
        repo_factory=MagicMock(),
        drift_warning_svc=MagicMock(),
        prune_svc=MagicMock(),
        reporter_factory=MagicMock(),
        cli_output_svc=cli_output_svc,
        workspace=MagicMock(),
        env_aliases=env_aliases,
        envs_per_workspace=envs_per_workspace,
        env_index_registry=registry,
    )


# ---------------------------------------------------------------------------
# (a) Persisted / registry path
# ---------------------------------------------------------------------------


class TestIndexRegisteredEnv:
    def test_json_returns_registry_source_for_registered_env(self, capsys: pytest.CaptureFixture[Any]) -> None:
        """JSON output has source="registry" when the env is in the registry."""
        registry = _InMemoryRegistry()
        registry.assign("alpha", 1)

        handler = _make_index_handler(
            env_aliases=["alpha", "beta"],
            envs_per_workspace=48,
            registry=registry,
        )
        handler.index(EnvIndexParams(name="alpha", output_json=True))

        data = json.loads(capsys.readouterr().out)
        assert data["name"] == "alpha"
        assert data["index"] == 1
        assert data["source"] == "registry"

    def test_json_registry_index_is_authoritative_even_if_different_from_suggestion(
        self, capsys: pytest.CaptureFixture[Any]
    ) -> None:
        """Registry index may differ from hash suggestion (after probing); registry wins."""
        registry = _InMemoryRegistry()
        # Assign a non-default index to "feature-x" (simulating probed slot).
        registry.assign("feature-x", 15)

        handler = _make_index_handler(
            env_aliases=["alpha", "beta"],
            envs_per_workspace=48,
            registry=registry,
        )
        handler.index(EnvIndexParams(name="feature-x", output_json=True))

        data = json.loads(capsys.readouterr().out)
        assert data["index"] == 15
        assert data["source"] == "registry"

    def test_human_output_prints_bare_index_for_registered_env(self, capsys: pytest.CaptureFixture[Any]) -> None:
        """Human output for a registered env is just the index (no caveat)."""
        registry = _InMemoryRegistry()
        registry.assign("alpha", 1)

        handler = _make_index_handler(registry=registry)
        handler.index(EnvIndexParams(name="alpha", output_json=False))

        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_human_output_registered_adhoc_env_prints_bare_index(self, capsys: pytest.CaptureFixture[Any]) -> None:
        """A registered ad-hoc env also prints just the index (no caveat)."""
        registry = _InMemoryRegistry()
        registry.assign("my-feature", 22)

        handler = _make_index_handler(
            env_aliases=["alpha", "beta"],
            envs_per_workspace=48,
            registry=registry,
        )
        handler.index(EnvIndexParams(name="my-feature", output_json=False))

        out = capsys.readouterr().out.strip()
        assert out == "22"


# ---------------------------------------------------------------------------
# (b) Unregistered / suggested path
# ---------------------------------------------------------------------------


class TestIndexUnregisteredEnv:
    def test_json_returns_suggested_source_for_unregistered_env(self, capsys: pytest.CaptureFixture[Any]) -> None:
        """JSON output has source="suggested" when the env is not in the registry."""
        registry = _InMemoryRegistry()  # empty registry

        handler = _make_index_handler(
            env_aliases=["alpha", "beta"],
            envs_per_workspace=48,
            registry=registry,
        )
        handler.index(EnvIndexParams(name="gamma", output_json=True))

        data = json.loads(capsys.readouterr().out)
        assert data["name"] == "gamma"
        assert data["source"] == "suggested"
        # gamma is not in the alias list, so it hashes into the hash band
        assert isinstance(data["index"], int)

    def test_json_unregistered_alias_still_returns_suggested(self, capsys: pytest.CaptureFixture[Any]) -> None:
        """An alias that has not been created yet is still source="suggested"."""
        registry = _InMemoryRegistry()

        handler = _make_index_handler(
            env_aliases=["alpha", "beta"],
            envs_per_workspace=48,
            registry=registry,
        )
        handler.index(EnvIndexParams(name="alpha", output_json=True))

        data = json.loads(capsys.readouterr().out)
        assert data["source"] == "suggested"
        assert data["index"] == 1  # alpha is alias[0] → index 1

    def test_human_unregistered_alias_prints_bare_index(self, capsys: pytest.CaptureFixture[Any]) -> None:
        """An unregistered alias prints just the index (fixed slot, no caveat needed)."""
        registry = _InMemoryRegistry()

        handler = _make_index_handler(
            env_aliases=["alpha", "beta"],
            envs_per_workspace=48,
            registry=registry,
        )
        handler.index(EnvIndexParams(name="alpha", output_json=False))

        out = capsys.readouterr().out.strip()
        assert out == "1"

    def test_human_unregistered_adhoc_name_prints_caveat(self, capsys: pytest.CaptureFixture[Any]) -> None:
        """An unregistered ad-hoc name includes the 'may shift on create' caveat."""
        registry = _InMemoryRegistry()

        handler = _make_index_handler(
            env_aliases=["alpha", "beta"],
            envs_per_workspace=48,
            registry=registry,
        )
        handler.index(EnvIndexParams(name="my-feature", output_json=False))

        out = capsys.readouterr().out.strip()
        # Must contain the index number and the caveat phrase.
        assert "suggested" in out or "shift" in out
        # Must still contain an integer (the suggested slot).
        parts = out.split()
        assert any(p.isdigit() for p in parts)

    def test_no_registry_returns_suggested_index(self, capsys: pytest.CaptureFixture[Any]) -> None:
        """When no registry is wired (None), the suggested index is always returned."""
        handler = _make_index_handler(
            env_aliases=["alpha", "beta"],
            envs_per_workspace=48,
            registry=None,
        )
        handler.index(EnvIndexParams(name="alpha", output_json=True))

        data = json.loads(capsys.readouterr().out)
        assert data["source"] == "suggested"
        assert data["index"] == 1
