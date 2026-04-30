from __future__ import annotations

import click


@click.command()
@click.pass_context
def dashboard(ctx: click.Context):
    """Launch the TUI dashboard."""
    from winter_cli.modules.tui.app import WinterDashboardApp

    container = ctx.obj["container"]
    app = WinterDashboardApp(container)
    app.run()
