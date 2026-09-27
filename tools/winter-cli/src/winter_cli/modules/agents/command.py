from __future__ import annotations

import click

from winter_cli.cli_context import cli_ctx
from winter_cli.modules.agents.handler import AgentsParams


@click.command("agents")
@click.option("--json", "output_json", is_flag=True, default=False, help="Emit the agent model matrix as JSON.")
@click.pass_context
def agents_command(ctx: click.Context, output_json: bool) -> None:
    """Show the resolved model and effort for every installed agent x harness,
    plus the tables each one was resolved from — the same resolution path
    `winter ws init` bakes into the rendered agent files.

    Read-only. Exits 0 even when a cell fails to resolve (the failure is
    reported per cell, not raised); only a config load error exits 1.
    `winter doctor` is what flags drift.
    """
    container = cli_ctx(ctx).container
    handler = container.agents_handler()
    handler.run(AgentsParams(output_json=output_json))
