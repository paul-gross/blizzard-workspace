"""Agent matrix screen: read-only view of the `winter agents` resolution snapshot.

Follows the `ErrorLogScreen` shape — pushed through `ScreenFactory`, popped by a
fixed `q` (not a rebindable action id) — but unlike that screen it re-reads
`.winter/config.toml` and the on-disk agent copies on every open, through
`AgentMatrixService.build()`. `build()` is the same service `winter agents`
calls, so this screen never resolves anything on its own. It runs off the UI
thread via the `@work(thread=True)` pattern `WorkspaceScreen._refresh_data`
uses, guarded by the same `PluginActionMixin` helpers. A `ConfigError` raised
by `build()` is shown on this screen and captured in the Log tab, mirroring
`WorkspaceScreen`'s own config-reload failure handling — and so is any other
unexpected exception `build()` raises, matching how `render_detail_panels`
isolates a misbehaving collaborator rather than letting it take the whole
dashboard down.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import threading
from typing import ClassVar

from rich.text import Text
from textual import work
from textual.app import App
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Header, Static

from winter_cli.config.models import CodeAgentVendor
from winter_cli.core.config_file import ConfigError
from winter_cli.modules.agents.agent_matrix_service import AgentMatrixService
from winter_cli.modules.agents.cell_text import Segments, code_default_segments, effective_segments, tier_segments
from winter_cli.modules.agents.models import (
    LAYER_COLORS,
    LAYER_LABELS,
    AgentMatrix,
    AgentMatrixEntry,
    TierMatrixEntry,
)
from winter_cli.modules.tui.error_log import ErrorLogService
from winter_cli.modules.tui.keybindings import KeybindingMixin, KeybindingResolver
from winter_cli.modules.tui.keybindings.actions import AGENT_MATRIX_ACTIONS
from winter_cli.modules.tui.screens.agent_matrix.matrix_table import MatrixTable
from winter_cli.modules.tui.screens.plugin_action_mixin import PluginActionMixin
from winter_cli.modules.tui.ws_init_runner import WorkspaceInitRunner
from winter_cli.modules.workspace.agent_transform.model_tiers import ModelTier, TierCell
from winter_cli.modules.workspace.models import RepoError

# Harness column order for every table — matches `CodeAgentVendor` declaration
# order (ClaudeCode, Codex, OpenCode), same as `matrix_reporter._HARNESS_ORDER`.
_HARNESS_ORDER: tuple[str, ...] = tuple(vendor.vendor_label for vendor in CodeAgentVendor)

# How long `i` waits for `winter ws init` before warning that it is still running.
WS_INIT_TIMEOUT_SECONDS = 60

_CODE_DEFAULTS_TABLE_ID = "agent-matrix-code-defaults"
_TIER_OVERRIDES_TABLE_ID = "agent-matrix-tier-overrides"
_EFFECTIVE_TABLE_ID = "agent-matrix-effective"

# Column geometry shared by all three tables, so their columns line up: a
# fixed first column, one fixed middle column (the effective matrix's
# extension, a blank spacer elsewhere), then the fixed harness columns.
_CELL_PADDING = 1
_FIRST_COLUMN_WIDTH = 25
# Fits the longest extension name, "winter-service-docker".
_MIDDLE_COLUMN_WIDTH = 21
_HARNESS_COLUMN_WIDTH = 48
_SPACER_KEY = "spacer"


def _text(segments: Segments) -> Text:
    """The shared cell segments as a `Text` whose base style is the first segment's."""
    (first, first_style), *rest = segments
    text = Text(first, style=first_style or "")
    for part, style in rest:
        text.append(part, style=style)
    return text


def _fit(value: str | Text, width: int) -> str | Text:
    """Truncate `value` with an ellipsis so it fits `width`, keeping a `Text`'s styles."""
    if isinstance(value, Text):
        fitted = value.copy()
        fitted.truncate(width, overflow="ellipsis")
        return fitted
    if len(value) <= width:
        return value
    return value[: width - 1] + "…"


def _add_columns(table: DataTable, first: str, middle: str) -> list[int]:
    """Add the first, middle and harness columns; return every column's width in order."""
    table.cell_padding = _CELL_PADDING
    columns = [
        (first, _FIRST_COLUMN_WIDTH),
        (middle, _MIDDLE_COLUMN_WIDTH),
        *((harness, _HARNESS_COLUMN_WIDTH) for harness in _HARNESS_ORDER),
    ]
    for key, width in columns:
        table.add_column("" if key == _SPACER_KEY else key, key=key, width=width)
    return [width for _, width in columns]


def _fit_row(widths: list[int], row: list[str | Text]) -> list[str | Text]:
    return [_fit(value, width) for value, width in zip(row, widths, strict=True)]


class AgentMatrixScreen(KeybindingMixin, PluginActionMixin, Screen):
    """Read-only agent model/effort resolution matrix, mirroring `winter agents`.

    Shows the same three tables as the CLI (code defaults, global overrides,
    effective matrix), a warning naming any override that matches no
    installed agent, and the shared layer→color legend.
    Nothing is cached across opens — pushing this screen again re-reads
    config and the on-disk agent copies from scratch.

    The first non-empty table takes focus, cursor on row 0, once the matrix
    first loads. `r` (`agent_matrix.refresh`) reloads in place, keeping the
    focused table and cursor row; `i` (`agent_matrix.ws_init`) runs the bare
    `winter ws init` in-process — re-rendering the workspace's agent copies,
    which clears `stale`/`missing` — and then reloads whichever Agent matrix
    screen is open when it finishes, even one reopened mid-run. A run that
    outlasts `WS_INIT_TIMEOUT_SECONDS` gets a still-running warning and keeps
    going; `i` stays blocked until it finishes and its real outcome is
    reported. The outcome and the warning are app-level, so each is reported
    once per run however often the screen is reopened.
    Both keys are installed in on_mount from config-resolved action ids
    (`keybindings.actions.AGENT_MATRIX_ACTIONS`).
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "back", "Back"),
    ]

    def __init__(
        self,
        matrix_svc: AgentMatrixService,
        error_log: ErrorLogService,
        keybinding_resolver: KeybindingResolver,
        ws_init_runner: WorkspaceInitRunner,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._matrix_svc = matrix_svc
        self._error_log = error_log
        self._keybinding_resolver = keybinding_resolver
        self._ws_init_runner = ws_init_runner
        # True from `i` until that run's real outcome is reported — a run that
        # outlasts the timeout still blocks another `i` until it finishes.
        self._ws_init_running = False

    def compose(self):
        yield Header()
        with VerticalScroll(id="agent-matrix-body"):
            yield Static("", id="agent-matrix-error")
            yield Static("[bold]Code defaults[/bold]", classes="section-label")
            yield MatrixTable(id=_CODE_DEFAULTS_TABLE_ID)
            yield Static("[bold]Global overrides[/bold]", classes="section-label")
            yield MatrixTable(id=_TIER_OVERRIDES_TABLE_ID)
            yield Static("[bold]Effective matrix[/bold]", classes="section-label")
            yield MatrixTable(id=_EFFECTIVE_TABLE_ID)
            yield Static("", id="agent-matrix-unmatched")
        with Horizontal(classes="status-bar"):
            yield Static(id="agent-matrix-legend", classes="legend")
        yield Footer()

    def on_mount(self) -> None:
        for message in self._install_keybindings(list(AGENT_MATRIX_ACTIONS)):
            self.app.notify(message, title="keybindings", severity="error", timeout=8)
        self.query_one("#agent-matrix-error", Static).display = False
        self.query_one("#agent-matrix-unmatched", Static).display = False
        self._render_legend()
        self._load()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self._load()

    def action_ws_init(self) -> None:
        if self._ws_init_running or self._ws_init_runner.is_running:
            self.app.notify("winter ws init is still running", title="ws init", severity="warning")
            return
        self._ws_init_running = True
        app = self.app
        app.notify("Running winter ws init…", title="ws init")
        # On the app, not this screen: `q` detaches the screen mid-run, and the
        # warning must still fire exactly once.
        slow_timer = app.set_timer(WS_INIT_TIMEOUT_SECONDS, lambda: self._warn_ws_init_slow(app))
        # A plain daemon thread rather than a Textual worker: init may outlast
        # the timeout by any amount, and the runner cannot stop the child
        # processes it has in flight. Being a daemon, it never holds up
        # quitting the dashboard; a Textual thread worker would.
        threading.Thread(target=self._run_ws_init, args=(app, slow_timer), name="ws-init", daemon=True).start()

    def _run_ws_init(self, app: App, slow_timer: Timer) -> None:
        try:
            result = self._ws_init_runner.run()
            error = None if result.success else "; ".join(result.errors) or "winter ws init failed"
        except Exception as exc:
            # Init walks every repo and extension hook; an unexpected failure
            # there is reported like a failed run rather than escaping the thread.
            error = f"{type(exc).__name__}: {exc}"
        if not app.is_running:
            return
        # The app may begin tearing down between the is_running check and the call.
        with contextlib.suppress(RuntimeError, concurrent.futures.CancelledError):
            app.call_from_thread(self._finish_ws_init, app, slow_timer, error)

    def _warn_ws_init_slow(self, app: App) -> None:
        app.notify(
            f"winter ws init is still running after {WS_INIT_TIMEOUT_SECONDS}s; continuing in the background",
            title="ws init",
            severity="warning",
            timeout=8,
        )

    def _finish_ws_init(self, app: App, slow_timer: Timer, error: str | None) -> None:
        """Report one run's outcome once, then reload every open Agent matrix screen.

        Runs on the screen that started the run, which `q` may have detached
        since — so it reports through `app` and reloads the screens on the
        stack now, not itself.
        """
        self._ws_init_running = False
        slow_timer.stop()
        if error is None:
            app.notify("winter ws init complete", title="ws init")
        else:
            self._error_log.record(location="AgentMatrixScreen.ws_init", exc=RepoError(error))
            app.notify(f"{error}\nSee the Log tab", title="ws init failed", severity="error", timeout=8)
        for screen in app.screen_stack:
            if isinstance(screen, AgentMatrixScreen) and screen.is_attached:
                screen._load()

    # Exclusive: a reload started while `ws init` runs can finish after the
    # post-init reload; cancelling the older one keeps its stale result off screen.
    @work(thread=True, exclusive=True, group="agent-matrix-load")
    def _load(self) -> None:
        if self._worker_cancelled():
            return
        try:
            matrix = self._matrix_svc.build()
        except ConfigError as exc:
            self._capture_error("AgentMatrixScreen.open", RepoError(str(exc)), title="config error")
            self._call_from_thread_safe(self._show_error, str(exc), "config error")
            return
        except Exception as exc:
            # build() delegates to collaborators this screen doesn't control
            # (the copy inspector, the resolver chain) — an unexpected failure
            # there must not take the whole dashboard down. Surface it the
            # same way a ConfigError is surfaced rather than letting it escape
            # this worker thread.
            self._capture_error("AgentMatrixScreen.open", RepoError(str(exc)), title="agent matrix error")
            self._call_from_thread_safe(self._show_error, str(exc), "agent matrix error")
            return
        self._call_from_thread_safe(self._show_matrix, matrix)

    def _show_error(self, message: str, label: str = "config error") -> None:
        error = self.query_one("#agent-matrix-error", Static)
        error.update(f"[red]{label}:[/red] {message}")
        error.display = True

    def _show_matrix(self, matrix: AgentMatrix) -> None:
        # Repopulating clears every table's cursor; restore the focused one's row.
        # With no table focused yet (the first load), focus the first non-empty one.
        focused = self.focused
        row = focused.cursor_coordinate.row if isinstance(focused, MatrixTable) else None
        self.query_one("#agent-matrix-error", Static).display = False
        self._populate_code_defaults()
        self._populate_tier_overrides(matrix.tiers)
        self._populate_effective(matrix.agents)
        self._show_unmatched(matrix.unmatched_override_agents())
        if isinstance(focused, MatrixTable) and row is not None and focused.row_count > 0:
            focused.move_cursor(row=min(row, focused.row_count - 1), animate=False)
        elif not isinstance(focused, MatrixTable):
            first = next((table for table in self.query(MatrixTable) if table.row_count > 0), None)
            if first is not None:
                first.focus()
                first.move_cursor(row=0, animate=False)

    # ── Table 1: code defaults ────────────────────────────────────────────

    def _populate_code_defaults(self) -> None:
        table = self.query_one(f"#{_CODE_DEFAULTS_TABLE_ID}", DataTable)
        table.clear(columns=True)
        widths = _add_columns(table, "tier", _SPACER_KEY)
        for tier in ModelTier:
            cells = (_text(code_default_segments(tier, harness)) for harness in _HARNESS_ORDER)
            table.add_row(*_fit_row(widths, [tier.value, "", *cells]), key=tier.value)

    # ── Table 2: tier overrides (effective tier table) ──────────────────────

    def _populate_tier_overrides(self, tiers: list[TierMatrixEntry]) -> None:
        table = self.query_one(f"#{_TIER_OVERRIDES_TABLE_ID}", DataTable)
        table.clear(columns=True)
        widths = _add_columns(table, "tier", _SPACER_KEY)

        by_label: dict[str, dict[str, TierCell | None]] = {}
        for entry in tiers:
            by_label.setdefault(entry.label, {})[entry.harness] = entry.cell

        for label in sorted(by_label):
            row: list[str | Text] = [
                label,
                "",
                *(_text(tier_segments(by_label[label].get(harness))) for harness in _HARNESS_ORDER),
            ]
            table.add_row(*_fit_row(widths, row), key=label)

    # ── Table 3: effective matrix ────────────────────────────────────────

    def _populate_effective(self, agents: list[AgentMatrixEntry]) -> None:
        table = self.query_one(f"#{_EFFECTIVE_TABLE_ID}", DataTable)
        table.clear(columns=True)
        widths = _add_columns(table, "agent", "extension")

        by_agent: dict[tuple[str, str], dict[str, AgentMatrixEntry]] = {}
        for entry in agents:
            by_agent.setdefault((entry.extension, entry.agent), {})[entry.harness] = entry

        for i, (extension, agent) in enumerate(sorted(by_agent)):
            row: list[str | Text] = [
                agent,
                extension,
                *(_text(effective_segments(by_agent[(extension, agent)].get(harness))) for harness in _HARNESS_ORDER),
            ]
            table.add_row(*_fit_row(widths, row), key=str(i))

    def _show_unmatched(self, agents: list[str]) -> None:
        unmatched = self.query_one("#agent-matrix-unmatched", Static)
        unmatched.update(Text(f"Overrides matching no installed agent: {', '.join(agents)}", style="yellow"))
        unmatched.display = bool(agents)

    # ── Legend ──────────────────────────────────────────────────────────────

    def _render_legend(self) -> None:
        legend = self.query_one("#agent-matrix-legend", Static)
        text = Text()
        for i, (layer_name, color) in enumerate(LAYER_COLORS.items()):
            if i > 0:
                text.append("  ")
            text.append(LAYER_LABELS[layer_name], style=color)
        legend.update(text)
