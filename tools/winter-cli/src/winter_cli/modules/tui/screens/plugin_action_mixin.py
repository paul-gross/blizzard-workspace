"""Shared plugin-action plumbing for dashboard screens.

Every screen that surfaces plugin `TuiAction`s repeats the same three pieces:
bind each action's key to a `plugin_<name>` binding, resolve the dynamic
`action_plugin_<name>` attribute the Textual binding dispatch looks up, and log a
`RepoError` to the session log + toast without crashing. This mixin owns those
three; the per-scope context resolution (which differs by screen — a worktree
screen has a focused repo, the workspace screen reads its grid) stays on each
screen as `_run_plugin_action`.

Host requirements (a Textual `Screen` subclass provides all of these):
  - `self._plugin_registry` — the `PluginRegistry`
  - `self._error_log` — the `ErrorLogService`
  - `self.app`, `self._bindings` — from Textual
  - `_run_plugin_action(self, action_name: str) -> None` — the screen's dispatcher
"""

from __future__ import annotations

from collections.abc import Iterable

from winter_cli.modules.tui.error_log import ErrorLogService
from winter_cli.modules.workspace.models import RepoError
from winter_cli.plugins.loader import PluginRegistry
from winter_cli.plugins.types import ActionScope


class PluginActionMixin:
    """Mixin supplying the screen-agnostic half of plugin-action handling."""

    _plugin_registry: PluginRegistry
    _error_log: ErrorLogService

    def _bind_plugin_actions(self, scopes: Iterable[ActionScope] = tuple(ActionScope)) -> None:
        """Bind every contributed action in `scopes` to a `plugin_<name>` keybinding."""
        for scope in scopes:
            for action in self._plugin_registry.actions_for_scope(scope):
                self._bindings.bind(action.key, f"plugin_{action.name}", action.description)  # type: ignore[attr-defined]

    def _capture_error(self, location: str, exc: RepoError) -> None:
        """Log a RepoError to the session log and toast (deduped) without crashing.

        Called from refresh/detail worker threads, so the toast is marshaled onto
        the UI thread via `call_from_thread`.
        """
        entry, should_notify = self._error_log.record(location=location, exc=exc)
        if should_notify:
            self.app.call_from_thread(  # type: ignore[attr-defined]
                self.app.notify,  # type: ignore[attr-defined]
                f"{entry.message}\nPress L for log",
                title="git error",
                severity="error",
                timeout=6,
            )

    def __getattr__(self, name: str):
        # Textual resolves a binding named `plugin_<x>` to `action_plugin_<x>`;
        # synthesize that handler so screens declare actions without a method each.
        if name.startswith("action_plugin_"):
            action_name = name[len("action_plugin_") :]

            def handler() -> None:
                self._run_plugin_action(action_name)  # type: ignore[attr-defined]

            return handler
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")
