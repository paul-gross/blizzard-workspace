from __future__ import annotations

import click

from winter_cli.cli_context import cli_ctx
from winter_cli.core.tracing import ISessionTracer


@click.command()
@click.pass_context
def dashboard(ctx: click.Context):
    """Launch the TUI dashboard."""
    from winter_cli.modules.tui.app import WinterDashboardApp

    context = cli_ctx(ctx)
    # The dashboard runs for as long as the user keeps it open, so its spans are exported while
    # it runs; the command span, the session span, is exported when the app returns.
    session_tracer: ISessionTracer = context.container.command_tracer()
    session_tracer.start_background_export()
    app = WinterDashboardApp(context.container, source_override=context.source_override)
    app.run()
