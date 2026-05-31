from __future__ import annotations

import click

from winter_cli.cli_context import cli_ctx
from winter_cli.modules.graph.handler import GraphParams


@click.command("graph")
@click.option("--json", "output_json", is_flag=True, default=False, help="Emit the graph as a JSON adjacency map.")
@click.pass_context
def graph_command(ctx: click.Context, output_json: bool) -> None:
    """Print the module dependency graph declared in `winter-ext.toml` `requires`.

    Each installed module that ships a `winter-ext.toml` becomes a node; its
    `requires` list becomes its edges. `--json` emits a `{module: [requires...]}`
    adjacency map — the contract lint checks consume via `$WINTER_CLI graph
    --json` so they don't re-parse manifests themselves.
    """
    container = cli_ctx(ctx).container
    handler = container.graph_handler()
    handler.run(GraphParams(output_json=output_json))
