from __future__ import annotations

from unittest.mock import MagicMock

import click
import pytest

from winter_cli.modules.workspace.handlers.init_handler import InitHandler, InitParams


def _make_handler() -> tuple[InitHandler, MagicMock, MagicMock]:
    """Build an InitHandler with mock collaborators.

    Returns (handler, init_service_mock, reporter_factory_mock).
    """
    init_service = MagicMock()
    init_service.reconcile_env.return_value = True
    init_service.reconcile_all.return_value = True
    init_service.reconcile_projects.return_value = True
    init_service.reconcile_standalones.return_value = True
    init_service.run_workspace_reconcile_hooks.return_value = True

    reporter_factory = MagicMock()
    reporter_factory.get_init_reporter.return_value = MagicMock()

    handler = InitHandler(init_service=init_service, reporter_factory=reporter_factory)
    return handler, init_service, reporter_factory


# ---------------------------------------------------------------------------
# existing --all + target guard (reference baseline)
# ---------------------------------------------------------------------------


def test_all_and_target_raises_click_exception() -> None:
    """--all combined with a target name raises ClickException (existing guard)."""
    handler, init_service, _ = _make_handler()

    with pytest.raises(click.ClickException) as excinfo:
        handler.run(InitParams(target="alpha", all=True, output_json=False))

    assert "cannot be combined" in str(excinfo.value.format_message())
    init_service.reconcile_env.assert_not_called()
    init_service.reconcile_all.assert_not_called()


# ---------------------------------------------------------------------------
# reserved-name rejection
# ---------------------------------------------------------------------------


def test_workspace_target_raises_click_exception() -> None:
    """'workspace' as target raises ClickException with a 'reserved' message."""
    handler, _, __ = _make_handler()

    with pytest.raises(click.ClickException) as excinfo:
        handler.run(InitParams(target="workspace", all=False, output_json=False))

    assert "reserved" in excinfo.value.format_message()


def test_workspace_target_does_not_invoke_init_service() -> None:
    """Rejected 'workspace' target must not delegate to the init service."""
    handler, init_service, _ = _make_handler()

    with pytest.raises(click.ClickException):
        handler.run(InitParams(target="workspace", all=False, output_json=False))

    init_service.reconcile_env.assert_not_called()
    init_service.reconcile_all.assert_not_called()
    init_service.reconcile_projects.assert_not_called()


# ---------------------------------------------------------------------------
# negative case: normal names are not rejected
# ---------------------------------------------------------------------------


def test_normal_target_does_not_raise() -> None:
    """A normal env name like 'alpha' does not trigger the reserved-name guard."""
    handler, init_service, _ = _make_handler()

    # Should not raise — delegates to reconcile_env.
    handler.run(InitParams(target="alpha", all=False, output_json=False))

    init_service.reconcile_env.assert_called_once_with("alpha", init_service.reconcile_env.call_args[0][1])
