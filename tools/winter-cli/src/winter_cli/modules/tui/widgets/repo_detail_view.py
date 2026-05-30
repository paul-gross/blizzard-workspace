"""Shared per-repo detail body: built-in git info plus plugin-contributed panels.

Both the feature-environment (`WorktreeDetailScreen`) and standalone
(`StandaloneDetailScreen`) detail views render the *same* single-repo body — a
`RepoStatus` summary (branch / tracking / dirty files / recent commits) and any
`IDetailPanel`s a plugin contributed. This widget is that body.

With zero contributed panels it renders exactly the built-in info `Static` (no
tab bar). With one or more panels it renders a `TabbedContent` whose first tab
is the built-in info and whose remaining tabs are the panels — so a screen never
shows an empty tab bar.

Panel rendering is pure (no widget access) so it runs in the screens' refresh
worker thread; `render_detail_panels` isolates each panel — a panel that raises
yields an error `PanelOutcome` rather than taking down the screen, matching the
decorator error handling.
"""

from __future__ import annotations

import dataclasses
from typing import cast

from rich.console import RenderableType
from rich.protocol import is_renderable
from textual.containers import Vertical
from textual.widgets import Static, TabbedContent, TabPane

from winter_cli.modules.workspace.models import RepoStatus
from winter_cli.plugins.types import DetailPanelContext, IDetailPanel


@dataclasses.dataclass
class PanelOutcome:
    """Result of rendering one `IDetailPanel` — either content or an error message.

    Aligned by index with the panel list. `error` is set (and `content` is a
    fallback marker) when the panel's `render` raised, so the screen can show an
    isolated error state for that one tab.
    """

    content: RenderableType
    error: str | None = None


def render_detail_panels(panels: list[IDetailPanel], context: DetailPanelContext) -> list[PanelOutcome]:
    """Render every panel against `context`, isolating failures per panel.

    Pure and widget-free so it can run off the UI thread. A panel that raises is
    caught here and reported as an error `PanelOutcome`; a panel that returns a
    non-renderable value is coerced to its `str()` so `Static.update` can't fail
    later in the compositor.
    """
    outcomes: list[PanelOutcome] = []
    for panel in panels:
        try:
            rendered = panel.render(context)
        except Exception as exc:
            # A buggy panel must not crash the screen — isolate it as an error
            # outcome, mirroring the loader's load-and-skip-on-error contract.
            outcomes.append(PanelOutcome(content=f"[red]Panel error:[/red] {exc}", error=str(exc)))
            continue
        content = cast(RenderableType, rendered) if is_renderable(rendered) else str(rendered)
        outcomes.append(PanelOutcome(content=content))
    return outcomes


def build_repo_info_markup(detail: RepoStatus) -> str:
    """Build the built-in info panel's console markup from a repo's status."""
    lines = [
        f"[bold]{detail.name}[/bold]",
        f"Branch:   {detail.branch or '—'}",
        f"Tracking: {detail.tracking_branch or '—'}",
        f"Ahead:    {detail.ahead}  Behind: {detail.behind}",
    ]

    if len(detail.dirty_files) > 0:
        lines.append(f"\n[bold]Modified ({len(detail.dirty_files)}):[/bold]")
        for f in detail.dirty_files[:15]:
            lines.append(f"  {f}")
        remaining = len(detail.dirty_files) - 15
        if remaining > 0:
            lines.append(f"  ... and {remaining} more")

    if len(detail.recent_commits) > 0:
        lines.append("\n[bold]Recent commits:[/bold]")
        for c in detail.recent_commits[:10]:
            lines.append(f"  [dim]{c.short_hash}[/dim] {c.message}")

    return "\n".join(lines)


class RepoDetailView(Vertical):
    """The single-repo detail body — built-in info plus contributed panel tabs.

    Construct with the plugin registry's `detail_panels`. The tab structure is
    fixed at compose time because the registered panels are static for the life
    of a dashboard session.
    """

    def __init__(self, panels: list[IDetailPanel], **kwargs) -> None:
        super().__init__(**kwargs)
        self._panels = panels

    def compose(self):
        if not self._panels:
            yield Static(id="repo-info")
            return
        with TabbedContent(id="detail-tabs"):
            with TabPane("Info", id="detail-tab-info"):
                yield Static(id="repo-info")
            for i, panel in enumerate(self._panels):
                with TabPane(panel.title, id=f"detail-tab-{i}"):
                    yield Static(id=f"detail-panel-{i}")

    def show_repo(self, detail: RepoStatus, outcomes: list[PanelOutcome]) -> None:
        """Update the built-in info and every contributed panel from a fresh refresh.

        `outcomes` is aligned by index with the panels this view composed, so it
        maps one-to-one onto the `#detail-panel-{i}` statics.
        """
        self.query_one("#repo-info", Static).update(build_repo_info_markup(detail))
        for i, outcome in enumerate(outcomes[: len(self._panels)]):
            self.query_one(f"#detail-panel-{i}", Static).update(outcome.content)
