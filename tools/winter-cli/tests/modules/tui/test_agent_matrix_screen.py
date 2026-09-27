"""The dashboard Agent matrix screen mirrors `winter agents`, and re-reads on every open.

Opens through the `workspace.open_agent_matrix` action (default key `M`), checks
that all four tables and the shared legend render with cells colored from
`LAYER_COLORS`, that a config edit between two opens is reflected on the second
open (each `AgentMatrixService.build()` re-reads config from scratch), and that
a `ConfigError` raised while building is shown on the screen and captured in the
Log tab — the same capture path `WorkspaceScreen`'s own config-reload failure
uses.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from dependency_injector import providers
from rich.text import Text
from textual.widgets import DataTable, Static
from textual.widgets.data_table import ColumnKey

from winter_cli.container import Container
from winter_cli.core.config_file import ConfigError
from winter_cli.modules.agents.agent_matrix_service import AgentMatrixService
from winter_cli.modules.agents.models import (
    LAYER_COLORS,
    LAYER_LABELS,
    AgentMatrix,
    AgentMatrixEntry,
    AgentOverrideEntry,
    AgentOverrideHarnessValue,
    TierMatrixEntry,
)
from winter_cli.modules.tui.app import WinterDashboardApp
from winter_cli.modules.tui.screens.agent_matrix import AgentMatrixScreen
from winter_cli.modules.tui.screens.agent_matrix import screen as screen_module
from winter_cli.modules.tui.screens.workspace import WorkspaceScreen
from winter_cli.modules.tui.ws_init_runner import WsInitResult
from winter_cli.modules.workspace.agent_transform.agent_copy_inspector import CopyStatus
from winter_cli.modules.workspace.agent_transform.model_tiers import build_effective_tier_table
from winter_cli.modules.workspace.agent_transform.models import (
    AgentModelOverrideValue,
    AgentResolution,
    ConfigSource,
    EffortLayer,
    EffortResolution,
    ModelLayer,
    ModelResolution,
    SourcedValue,
)


def _cell_style(table: DataTable, row_key: str, column_key: str) -> str | None:
    value = table.get_cell(row_key, column_key)
    return getattr(value, "style", None) or None


@pytest.mark.asyncio
async def test_agent_matrix_screen_opens_via_action_and_shows_tables_and_legend(container: Container) -> None:
    app = WinterDashboardApp(container)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause(0.3)
        assert isinstance(app.screen, WorkspaceScreen)

        await pilot.press("M")
        await pilot.pause(0.5)
        assert isinstance(app.screen, AgentMatrixScreen)

        code_defaults = app.screen.query_one("#agent-matrix-code-defaults", DataTable)
        tier_overrides = app.screen.query_one("#agent-matrix-tier-overrides", DataTable)
        effective = app.screen.query_one("#agent-matrix-effective", DataTable)
        legend = app.screen.query_one("#agent-matrix-legend", Static)

        # Code defaults always has one row per built-in tier, regardless of
        # workspace config, so it's a reliable presence + coloring check.
        assert code_defaults.row_count == 4
        assert tier_overrides.row_count == 4
        # Effective may legitimately be empty in this tmp workspace (no
        # installed agents) — its presence as a table is what's asserted.
        assert len(effective.columns) > 0
        assert not app.screen.query("#agent-matrix-agent-overrides")
        # Every override matches (there are none), so no warning shows.
        assert app.screen.query_one("#agent-matrix-unmatched", Static).display is False

        # At least one cell's style comes straight from the shared legend.
        style = _cell_style(code_defaults, "opus", "claude")
        assert style == LAYER_COLORS["code_default"]

        legend_text = legend.content
        assert "Code Default" in str(legend_text)

        await pilot.press("q")
        await pilot.pause(0.3)
        assert isinstance(app.screen, WorkspaceScreen)


@pytest.mark.asyncio
async def test_agent_matrix_screen_reflects_config_change_between_opens(
    container: Container, tmp_workspace_root: Path
) -> None:
    app = WinterDashboardApp(container)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause(0.3)

        await pilot.press("M")
        await pilot.pause(0.5)
        assert isinstance(app.screen, AgentMatrixScreen)
        tier_overrides = app.screen.query_one("#agent-matrix-tier-overrides", DataTable)
        before = tier_overrides.get_cell("opus", "opencode")
        assert "custom-opus-id" not in str(before)
        assert before.style == LAYER_COLORS["code_default"]

        await pilot.press("q")
        await pilot.pause(0.3)
        assert isinstance(app.screen, WorkspaceScreen)

        config_path = tmp_workspace_root / ".winter" / "config.toml"
        config_path.write_text(config_path.read_text() + '\n[model_tiers.opus]\nopencode = "custom-opus-id"\n')

        await pilot.press("M")
        await pilot.pause(0.5)
        assert isinstance(app.screen, AgentMatrixScreen)
        tier_overrides = app.screen.query_one("#agent-matrix-tier-overrides", DataTable)
        after = tier_overrides.get_cell("opus", "opencode")
        assert "custom-opus-id" in str(after)
        assert after.style == LAYER_COLORS["tier_override"]

        await pilot.press("q")
        await pilot.pause(0.3)
        assert isinstance(app.screen, WorkspaceScreen)


@pytest.mark.asyncio
async def test_agent_matrix_screen_config_error_is_shown_and_captured(
    container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(self: AgentMatrixService) -> None:
        raise ConfigError("malformed config.toml")

    monkeypatch.setattr(AgentMatrixService, "build", _boom)

    log = container.error_log_svc()
    log.clear()

    app = WinterDashboardApp(container)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause(0.3)

        await pilot.press("M")
        await pilot.pause(0.5)
        assert isinstance(app.screen, AgentMatrixScreen)

        error = app.screen.query_one("#agent-matrix-error", Static)
        assert error.display is True
        assert "malformed config.toml" in str(error.content)

        assert any(e.message == "malformed config.toml" for e in log.entries())

        await pilot.press("q")
        await pilot.pause(0.3)
        assert isinstance(app.screen, WorkspaceScreen)


@pytest.mark.asyncio
async def test_agent_matrix_screen_survives_an_unexpected_build_failure(
    container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-`ConfigError` raised by `build()` must not crash the dashboard.

    `build()` walks collaborators this screen doesn't control (the copy
    inspector, the resolver chain) — a bug there, or a bare `OSError`, must be
    shown on the screen and captured in the Log tab, the same way a
    `ConfigError` is, and `q` must still return to the workspace screen.
    """

    def _boom(self: AgentMatrixService) -> None:
        raise RuntimeError("unexpected failure deep in the resolver chain")

    monkeypatch.setattr(AgentMatrixService, "build", _boom)

    log = container.error_log_svc()
    log.clear()

    app = WinterDashboardApp(container)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause(0.3)

        await pilot.press("M")
        await pilot.pause(0.5)
        assert isinstance(app.screen, AgentMatrixScreen)

        error = app.screen.query_one("#agent-matrix-error", Static)
        assert error.display is True
        assert "unexpected failure deep in the resolver chain" in str(error.content)

        assert any(e.message == "unexpected failure deep in the resolver chain" for e in log.entries())

        await pilot.press("q")
        await pilot.pause(0.3)
        assert isinstance(app.screen, WorkspaceScreen)


class FakeAgentMatrixService:
    """Returns a fixed `AgentMatrix` so every marker and layer color has a cell to land in."""

    def __init__(self, matrix: AgentMatrix) -> None:
        self._matrix = matrix

    def build(self) -> AgentMatrix:
        return self._matrix


def _span_style(text: Text, fragment: str) -> str | None:
    """Style of the span that starts where `fragment` starts in `text`'s plain string."""
    start = text.plain.index(fragment)
    styles = [str(span.style) for span in text.spans if span.start == start]
    return styles[0] if styles else None


def _resolution(
    *,
    model: str,
    model_layer: ModelLayer,
    effort: str | None = None,
    effort_layer: EffortLayer = EffortLayer.inherited,
) -> AgentResolution:
    agent_override = (
        AgentModelOverrideValue(value=model, tier=None, source=ConfigSource.config_local_toml)
        if model_layer is ModelLayer.agent_override
        else None
    )
    return AgentResolution(
        model=ModelResolution(
            declared_tier="sonnet",
            code_default="claude-sonnet",
            tier_override=None,
            harness_block=None,
            agent_override=agent_override,
            effective=model,
            effective_layer=model_layer,
        ),
        effort=EffortResolution(
            harness_block=effort if effort_layer is EffortLayer.harness_block else None,
            agent_override=(
                SourcedValue(value=effort, source=ConfigSource.config_toml)
                if effort is not None and effort_layer is EffortLayer.agent_override
                else None
            ),
            effective=effort,
            effective_layer=effort_layer,
        ),
    )


def _entry(
    agent: str, harness: str, resolution: AgentResolution | None, on_disk: CopyStatus | None, error: str | None = None
) -> AgentMatrixEntry:
    return AgentMatrixEntry(
        agent=agent,
        extension="wf",
        installed_name=f"wf-{agent}",
        harness=harness,
        resolution=resolution,
        error=error,
        on_disk=on_disk,
    )


def _fixture_matrix() -> AgentMatrix:
    tier_table = build_effective_tier_table(
        {"opus": {"opencode": "custom-opus-id"}}, {"opus": ConfigSource.config_local_toml}
    )
    return AgentMatrix(
        tiers=[
            TierMatrixEntry(label="opus", harness=harness, cell=tier_table.cell("opus", harness))
            for harness in ("claude", "codex", "opencode")
        ],
        agent_overrides=[
            AgentOverrideEntry(
                agent="ghost",
                source=ConfigSource.config_local_toml,
                matches_installed=False,
                tier=None,
                harnesses={
                    "claude": AgentOverrideHarnessValue(model="claude-haiku", effort="high"),
                    "codex": None,
                    "opencode": None,
                },
            ),
            AgentOverrideEntry(
                agent="reviewer",
                source=ConfigSource.config_toml,
                matches_installed=True,
                tier="haiku",
                harnesses={
                    "claude": AgentOverrideHarnessValue(model="claude-haiku", effort=None),
                    "codex": AgentOverrideHarnessValue(model=None, effort=None, error="unknown model tier 'haiku'"),
                    "opencode": None,
                },
            ),
        ],
        agents=[
            _entry(
                "reviewer",
                "claude",
                _resolution(
                    model="claude-haiku",
                    model_layer=ModelLayer.agent_override,
                    effort="high",
                    effort_layer=EffortLayer.harness_block,
                ),
                CopyStatus.stale,
            ),
            _entry(
                "reviewer", "codex", _resolution(model="gpt-5", model_layer=ModelLayer.code_default), CopyStatus.in_sync
            ),
            _entry(
                "reviewer",
                "opencode",
                _resolution(model="custom-opus-id", model_layer=ModelLayer.tier_override),
                CopyStatus.missing,
            ),
            _entry("broken", "claude", None, None, error="unknown tier label 'nope'"),
        ],
    )


@pytest.mark.asyncio
async def test_agent_matrix_screen_marks_and_colors_every_table_from_the_shared_legend(container: Container) -> None:
    container.agent_matrix_svc.override(providers.Object(FakeAgentMatrixService(_fixture_matrix())))
    try:
        app = WinterDashboardApp(container)
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("M")
            await pilot.pause(0.5)
            assert isinstance(app.screen, AgentMatrixScreen)
            screen = app.screen

            # Tier overrides: a remapped cell takes the tier_override color and shows its source file.
            tiers = screen.query_one("#agent-matrix-tier-overrides", DataTable)
            remapped = tiers.get_cell("opus", "opencode")
            assert remapped.plain == "custom-opus-id  (config.local.toml)"
            assert remapped.style == LAYER_COLORS["tier_override"]
            assert _span_style(remapped, "  (config") == "dim"
            unmapped = tiers.get_cell("opus", "claude")
            assert "(" not in unmapped.plain
            assert unmapped.style == LAYER_COLORS["code_default"]

            # The agent-overrides table is gone; its one extra fact, an override matching
            # no installed agent, is a yellow warning under the effective matrix.
            assert not screen.query("#agent-matrix-agent-overrides")
            unmatched = screen.query_one("#agent-matrix-unmatched", Static)
            assert unmatched.display is True
            assert isinstance(unmatched.content, Text)
            assert unmatched.content.plain == "Overrides matching no installed agent: ghost"
            assert unmatched.content.style == "yellow"

            # Effective matrix: model half colored by model layer, effort half by effort
            # layer, and a stale / missing on-disk copy is marked; an in-sync one is not.
            effective = screen.query_one("#agent-matrix-effective", DataTable)
            rows = {effective.get_row(key)[0]: key for key in ("0", "1")}
            reviewer = rows["reviewer"]
            stale = effective.get_cell(reviewer, "claude")
            assert stale.plain == "claude-haiku·high  [stale]"
            assert stale.style == LAYER_COLORS["agent_override"]
            assert _span_style(stale, "·high") == LAYER_COLORS["harness_block"]
            in_sync = effective.get_cell(reviewer, "codex")
            assert in_sync.plain == "gpt-5"
            assert in_sync.style == LAYER_COLORS["code_default"]
            missing = effective.get_cell(reviewer, "opencode")
            assert missing.plain == "custom-opus-id  [missing]"
            assert missing.style == LAYER_COLORS["tier_override"]
            assert effective.get_cell(rows["broken"], "claude").plain == "error"

            # Legend: every layer's label, each in its own color.
            legend = screen.query_one("#agent-matrix-legend", Static).content
            assert isinstance(legend, Text)
            for layer_name, color in LAYER_COLORS.items():
                assert _span_style(legend, LAYER_LABELS[layer_name]) == color
    finally:
        container.agent_matrix_svc.reset_override()


@pytest.mark.asyncio
async def test_agent_matrix_action_is_rebindable_through_keybindings_config(tmp_workspace_root: Path) -> None:
    config_path = tmp_workspace_root / ".winter" / "config.toml"
    config_path.write_text(config_path.read_text() + '\n[keybindings.bindings]\n"workspace.open_agent_matrix" = "X"\n')

    app = WinterDashboardApp(Container())
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause(0.3)

        await pilot.press("M")
        await pilot.pause(0.3)
        assert isinstance(app.screen, WorkspaceScreen)

        await pilot.press("X")
        await pilot.pause(0.5)
        assert isinstance(app.screen, AgentMatrixScreen)


@pytest.mark.asyncio
async def test_agent_matrix_tables_share_fixed_width_first_and_harness_columns(container: Container) -> None:
    long_name = "a-very-long-agent-name-that-overflows"
    long_model = "a-very-long-model-id-that-overflows-the-forty-eight-wide-harness-column"
    matrix = _fixture_matrix()
    matrix.agents.append(
        _entry(long_name, "claude", _resolution(model=long_model, model_layer=ModelLayer.agent_override), None)
    )
    container.agent_matrix_svc.override(providers.Object(FakeAgentMatrixService(matrix)))
    try:
        app = WinterDashboardApp(container)
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("M")
            await pilot.pause(0.5)
            assert isinstance(app.screen, AgentMatrixScreen)
            screen = app.screen

            harness_offsets = set()
            for table_id, first_key in (
                ("agent-matrix-code-defaults", "tier"),
                ("agent-matrix-tier-overrides", "tier"),
                ("agent-matrix-effective", "agent"),
            ):
                table = screen.query_one(f"#{table_id}", DataTable)
                assert table.columns[ColumnKey(first_key)].width == 25
                columns = list(table.columns.values())
                harness_columns = columns[-3:]
                assert [str(column.label) for column in harness_columns] == ["claude", "codex", "opencode"]
                assert all(column.width == 48 for column in harness_columns)
                harness_offsets.add(sum(column.get_render_width(table) for column in columns[:-3]))
            assert len(harness_offsets) == 1

            # Rows sort by (extension, agent), so the long-named agent is row 0.
            effective = screen.query_one("#agent-matrix-effective", DataTable)
            assert effective.columns[ColumnKey("extension")].width == 21
            truncated = effective.get_row("0")[0]
            assert truncated == long_name[:24] + "…"
            assert len(truncated) == 25
            assert effective.get_row("1")[0] == "broken"

            # A long harness value is truncated to the column, keeping its style.
            long_cell = effective.get_cell("0", "claude")
            assert long_cell.plain == long_model[:47] + "…"
            assert long_cell.style == LAYER_COLORS["agent_override"]
    finally:
        container.agent_matrix_svc.reset_override()


@pytest.mark.asyncio
async def test_agent_matrix_section_headers_are_centered_and_legend_is_a_footer(container: Container) -> None:
    app = WinterDashboardApp(container)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("M")
        await pilot.pause(0.5)
        assert isinstance(app.screen, AgentMatrixScreen)

        headers = list(app.screen.query(".section-label"))
        assert len(headers) == 3
        for header in headers:
            assert header.styles.text_align == "center"
            assert header.styles.margin.top == 1
            assert header.styles.margin.bottom == 1

        # The legend is a centered footer line below the scrolling tables, not a section of them.
        body = app.screen.query_one("#agent-matrix-body")
        legend = app.screen.query_one("#agent-matrix-legend", Static)
        assert legend not in body.query("*")
        assert legend.styles.text_align == "center"
        assert legend.region.y >= body.region.bottom


async def _open_matrix(pilot, app: WinterDashboardApp) -> AgentMatrixScreen:
    await pilot.pause(0.3)
    await pilot.press("M")
    await pilot.pause(0.5)
    assert isinstance(app.screen, AgentMatrixScreen)
    return app.screen


def _cursor(screen: AgentMatrixScreen) -> tuple[str | None, int]:
    focused = screen.focused
    assert isinstance(focused, DataTable)
    return focused.id, focused.cursor_coordinate.row


@pytest.mark.asyncio
async def test_agent_matrix_jk_walks_rows_and_crosses_between_tables(container: Container) -> None:
    container.agent_matrix_svc.override(providers.Object(FakeAgentMatrixService(_fixture_matrix())))
    try:
        app = WinterDashboardApp(container)
        async with app.run_test(size=(200, 60)) as pilot:
            screen = await _open_matrix(pilot, app)
            screen.query_one("#agent-matrix-code-defaults", DataTable).focus()
            await pilot.pause()

            # Code defaults has 4 rows, tier overrides 1, effective 2.
            await pilot.press("j", "j", "j")
            assert _cursor(screen) == ("agent-matrix-code-defaults", 3)
            await pilot.press("j")
            assert _cursor(screen) == ("agent-matrix-tier-overrides", 0)
            await pilot.press("down")
            assert _cursor(screen) == ("agent-matrix-effective", 0)
            await pilot.press("j")
            assert _cursor(screen) == ("agent-matrix-effective", 1)
            # Last row of the last table: stays put.
            await pilot.press("j")
            assert _cursor(screen) == ("agent-matrix-effective", 1)

            # Crossing back up lands on the previous table's last row.
            await pilot.press("k", "up")
            assert _cursor(screen) == ("agent-matrix-tier-overrides", 0)
            await pilot.press("k")
            assert _cursor(screen) == ("agent-matrix-code-defaults", 3)
            # First row of the first table: stays put.
            await pilot.press("k", "k", "k", "k")
            assert _cursor(screen) == ("agent-matrix-code-defaults", 0)
    finally:
        container.agent_matrix_svc.reset_override()


@pytest.mark.asyncio
async def test_agent_matrix_jk_skips_an_empty_table(container: Container) -> None:
    matrix = _fixture_matrix()
    matrix.tiers.clear()
    container.agent_matrix_svc.override(providers.Object(FakeAgentMatrixService(matrix)))
    try:
        app = WinterDashboardApp(container)
        async with app.run_test(size=(200, 60)) as pilot:
            screen = await _open_matrix(pilot, app)
            screen.query_one("#agent-matrix-code-defaults", DataTable).focus()
            await pilot.pause()

            await pilot.press("j", "j", "j", "j")
            assert _cursor(screen) == ("agent-matrix-effective", 0)
            await pilot.press("k")
            assert _cursor(screen) == ("agent-matrix-code-defaults", 3)
    finally:
        container.agent_matrix_svc.reset_override()


@pytest.mark.asyncio
async def test_agent_matrix_focuses_the_first_table_on_open(container: Container) -> None:
    container.agent_matrix_svc.override(providers.Object(FakeAgentMatrixService(_fixture_matrix())))
    try:
        app = WinterDashboardApp(container)
        async with app.run_test(size=(200, 60)) as pilot:
            screen = await _open_matrix(pilot, app)
            assert _cursor(screen) == ("agent-matrix-code-defaults", 0)

            await pilot.press("j")
            assert _cursor(screen) == ("agent-matrix-code-defaults", 1)
    finally:
        container.agent_matrix_svc.reset_override()


class CountingAgentMatrixService(FakeAgentMatrixService):
    """Counts `build()` calls, so a test can see each (re)load."""

    def __init__(self, matrix: AgentMatrix) -> None:
        super().__init__(matrix)
        self.builds = 0

    def build(self) -> AgentMatrix:
        self.builds += 1
        return super().build()


class GatedAgentMatrixService(FakeAgentMatrixService):
    """Holds the build numbered `gate_on` until `release` is set; others return at once."""

    def __init__(self, matrix: AgentMatrix, gate_on: int) -> None:
        super().__init__(matrix)
        self.builds = 0
        self._gate_on = gate_on
        self.release = threading.Event()

    def build(self) -> AgentMatrix:
        self.builds += 1
        if self.builds == self._gate_on:
            self.release.wait(5)
        return super().build()


class FakeWsInitRunner:
    """Stands in for `WorkspaceInitRunner`; never runs a real `ws init`."""

    def __init__(self, result: WsInitResult | None = None, exc: Exception | None = None) -> None:
        self._result = result or WsInitResult(success=True, errors=[])
        self._exc = exc
        self.release = threading.Event()
        self.release.set()
        self.calls = 0
        self.is_running = False

    def run(self) -> WsInitResult:
        self.calls += 1
        self.is_running = True
        try:
            self.release.wait(5)
            if self._exc is not None:
                raise self._exc
            return self._result
        finally:
            self.is_running = False


async def _open_with(pilot, app: WinterDashboardApp, notes: list[str]) -> tuple[AgentMatrixScreen, DataTable]:
    app.notify = lambda message, **_: notes.append(message)  # type: ignore[method-assign]
    screen = await _open_matrix(pilot, app)
    table = screen.query_one("#agent-matrix-code-defaults", DataTable)
    table.focus()
    await pilot.pause()
    return screen, table


@pytest.mark.asyncio
async def test_agent_matrix_r_reloads_and_keeps_the_cursor_row(container: Container) -> None:
    svc = CountingAgentMatrixService(_fixture_matrix())
    container.agent_matrix_svc.override(providers.Object(svc))
    try:
        app = WinterDashboardApp(container)
        async with app.run_test(size=(200, 60)) as pilot:
            screen, _ = await _open_with(pilot, app, [])
            await pilot.press("j", "j")
            assert svc.builds == 1

            await pilot.press("r")
            await pilot.pause(0.3)
            assert svc.builds == 2
            assert _cursor(screen) == ("agent-matrix-code-defaults", 2)
    finally:
        container.agent_matrix_svc.reset_override()


@pytest.mark.asyncio
async def test_agent_matrix_i_runs_ws_init_once_then_reloads(container: Container) -> None:
    svc = CountingAgentMatrixService(_fixture_matrix())
    runner = FakeWsInitRunner()
    container.agent_matrix_svc.override(providers.Object(svc))
    container.ws_init_runner.override(providers.Object(runner))
    notes: list[str] = []
    try:
        app = WinterDashboardApp(container)
        async with app.run_test(size=(200, 60)) as pilot:
            await _open_with(pilot, app, notes)

            await pilot.press("i")
            await pilot.pause(0.3)
            assert runner.calls == 1
            assert svc.builds == 2
            assert notes == ["Running winter ws init…", "winter ws init complete"]
    finally:
        container.agent_matrix_svc.reset_override()
        container.ws_init_runner.reset_override()


@pytest.mark.parametrize(
    ("runner", "logged"),
    [
        (
            FakeWsInitRunner(result=WsInitResult(success=False, errors=["[winter] clone failed"])),
            "[winter] clone failed",
        ),
        (FakeWsInitRunner(exc=RuntimeError("boom")), "RuntimeError: boom"),
    ],
)
@pytest.mark.asyncio
async def test_agent_matrix_ws_init_failure_is_logged_notified_and_still_reloads(
    container: Container, runner: FakeWsInitRunner, logged: str
) -> None:
    svc = CountingAgentMatrixService(_fixture_matrix())
    container.agent_matrix_svc.override(providers.Object(svc))
    container.ws_init_runner.override(providers.Object(runner))
    log = container.error_log_svc()
    log.clear()
    notes: list[str] = []
    try:
        app = WinterDashboardApp(container)
        async with app.run_test(size=(200, 60)) as pilot:
            await _open_with(pilot, app, notes)

            await pilot.press("i")
            await pilot.pause(0.3)
            assert [e.message for e in log.entries()] == [logged]
            assert notes[-1] == f"{logged}\nSee the Log tab"
            assert svc.builds == 2
            assert isinstance(app.screen, AgentMatrixScreen)
    finally:
        container.agent_matrix_svc.reset_override()
        container.ws_init_runner.reset_override()


@pytest.mark.asyncio
async def test_agent_matrix_i_is_ignored_while_ws_init_is_running(container: Container) -> None:
    runner = FakeWsInitRunner()
    runner.release.clear()
    container.agent_matrix_svc.override(providers.Object(FakeAgentMatrixService(_fixture_matrix())))
    container.ws_init_runner.override(providers.Object(runner))
    notes: list[str] = []
    try:
        app = WinterDashboardApp(container)
        async with app.run_test(size=(200, 60)) as pilot:
            await _open_with(pilot, app, notes)

            await pilot.press("i")
            await pilot.pause(0.2)
            await pilot.press("i")
            await pilot.pause(0.2)
            assert runner.calls == 1
            assert notes == ["Running winter ws init…", "winter ws init is still running"]

            runner.release.set()
            await pilot.pause(0.3)
            assert notes[-1] == "winter ws init complete"
            assert runner.calls == 1
    finally:
        runner.release.set()
        container.agent_matrix_svc.reset_override()
        container.ws_init_runner.reset_override()


@pytest.mark.asyncio
async def test_agent_matrix_an_older_reload_finishing_last_is_not_shown(container: Container) -> None:
    svc = GatedAgentMatrixService(_fixture_matrix(), gate_on=2)
    container.agent_matrix_svc.override(providers.Object(svc))
    try:
        app = WinterDashboardApp(container)
        async with app.run_test(size=(200, 60)) as pilot:
            screen = await _open_matrix(pilot, app)
            shown: list[int] = []
            original_show = screen._show_matrix
            screen._show_matrix = lambda matrix: (shown.append(svc.builds), original_show(matrix))  # type: ignore[method-assign]

            await pilot.press("r")  # build 2: held
            await pilot.pause(0.2)
            await pilot.press("r")  # build 3: returns at once
            await pilot.pause(0.3)
            svc.release.set()  # the older build now finishes last
            await pilot.pause(0.3)

            assert svc.builds == 3
            assert shown == [3]
    finally:
        svc.release.set()
        container.agent_matrix_svc.reset_override()


@pytest.mark.asyncio
async def test_agent_matrix_reopened_mid_run_does_not_start_a_second_ws_init(container: Container) -> None:
    runner = FakeWsInitRunner()
    runner.release.clear()
    container.agent_matrix_svc.override(providers.Object(FakeAgentMatrixService(_fixture_matrix())))
    container.ws_init_runner.override(providers.Object(runner))
    notes: list[str] = []
    try:
        app = WinterDashboardApp(container)
        async with app.run_test(size=(200, 60)) as pilot:
            await _open_with(pilot, app, notes)
            await pilot.press("i")
            await pilot.pause(0.2)

            await pilot.press("q")
            await pilot.pause(0.2)
            await _open_with(pilot, app, notes)
            await pilot.press("i")
            await pilot.pause(0.2)

            assert runner.calls == 1
            assert notes[-1] == "winter ws init is still running"
    finally:
        runner.release.set()
        container.agent_matrix_svc.reset_override()
        container.ws_init_runner.reset_override()


_STILL_RUNNING = "winter ws init is still running after 0.2s; continuing in the background"


@pytest.mark.asyncio
async def test_agent_matrix_reopened_mid_run_reloads_and_reports_the_outcome_once(
    container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(screen_module, "WS_INIT_TIMEOUT_SECONDS", 0.2)
    runner = FakeWsInitRunner()
    runner.release.clear()
    svc = CountingAgentMatrixService(_fixture_matrix())
    container.agent_matrix_svc.override(providers.Object(svc))
    container.ws_init_runner.override(providers.Object(runner))
    notes: list[str] = []
    try:
        app = WinterDashboardApp(container)
        async with app.run_test(size=(200, 60)) as pilot:
            await _open_with(pilot, app, notes)
            await pilot.press("i")
            await pilot.pause(0.1)
            await pilot.press("q")
            await pilot.pause(0.2)
            reopened, _ = await _open_with(pilot, app, notes)
            assert svc.builds == 2
            assert notes.count(_STILL_RUNNING) == 1

            runner.release.set()
            await pilot.pause(0.3)
            assert notes.count("winter ws init complete") == 1
            assert app.screen is reopened
            assert svc.builds == 3

            await pilot.press("i")
            await pilot.pause(0.3)
            assert runner.calls == 2
            assert svc.builds == 4
            assert notes.count("winter ws init complete") == 2
            assert notes.count(_STILL_RUNNING) == 1
    finally:
        runner.release.set()
        container.agent_matrix_svc.reset_override()
        container.ws_init_runner.reset_override()


@pytest.mark.parametrize(
    ("runner", "outcome", "logged"),
    [
        (FakeWsInitRunner(), "winter ws init complete", []),
        (
            FakeWsInitRunner(result=WsInitResult(success=False, errors=["[winter] clone failed"])),
            "[winter] clone failed\nSee the Log tab",
            ["[winter] clone failed"],
        ),
    ],
)
@pytest.mark.asyncio
async def test_agent_matrix_ws_init_past_the_timeout_warns_blocks_i_and_reports_its_real_outcome(
    container: Container,
    monkeypatch: pytest.MonkeyPatch,
    runner: FakeWsInitRunner,
    outcome: str,
    logged: list[str],
) -> None:
    monkeypatch.setattr(screen_module, "WS_INIT_TIMEOUT_SECONDS", 0.2)
    runner.release.clear()
    svc = CountingAgentMatrixService(_fixture_matrix())
    container.agent_matrix_svc.override(providers.Object(svc))
    container.ws_init_runner.override(providers.Object(runner))
    log = container.error_log_svc()
    log.clear()
    notes: list[str] = []
    try:
        app = WinterDashboardApp(container)
        async with app.run_test(size=(200, 60)) as pilot:
            await _open_with(pilot, app, notes)

            await pilot.press("i")
            await pilot.pause(0.5)
            assert notes == ["Running winter ws init…", _STILL_RUNNING]
            assert log.entries() == []
            assert svc.builds == 1

            await pilot.press("i")
            await pilot.pause(0.1)
            assert runner.calls == 1
            assert notes[-1] == "winter ws init is still running"

            runner.release.set()
            await pilot.pause(0.3)
            assert notes[-1] == outcome
            assert [e.message for e in log.entries()] == logged
            assert svc.builds == 2

            await pilot.press("i")
            await pilot.pause(0.3)
            assert runner.calls == 2
            assert svc.builds == 3
    finally:
        runner.release.set()
        container.agent_matrix_svc.reset_override()
        container.ws_init_runner.reset_override()


@pytest.mark.asyncio
async def test_agent_matrix_cells_come_from_the_shared_cell_builders(
    container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(screen_module, "effective_segments", lambda entry: (("EFFECTIVE-CELL", "red"),))
    monkeypatch.setattr(screen_module, "tier_segments", lambda cell: (("TIER-CELL", None),))
    monkeypatch.setattr(screen_module, "code_default_segments", lambda tier, harness: (("CODE-CELL", None),))
    container.agent_matrix_svc.override(providers.Object(FakeAgentMatrixService(_fixture_matrix())))
    try:
        app = WinterDashboardApp(container)
        async with app.run_test(size=(200, 60)) as pilot:
            screen = await _open_matrix(pilot, app)
            code = screen.query_one("#agent-matrix-code-defaults", DataTable).get_cell("opus", "claude")
            tier = screen.query_one("#agent-matrix-tier-overrides", DataTable).get_cell("opus", "claude")
            effective = screen.query_one("#agent-matrix-effective", DataTable).get_cell("0", "claude")
            assert (code.plain, tier.plain) == ("CODE-CELL", "TIER-CELL")
            assert (effective.plain, effective.style) == ("EFFECTIVE-CELL", "red")
    finally:
        container.agent_matrix_svc.reset_override()
