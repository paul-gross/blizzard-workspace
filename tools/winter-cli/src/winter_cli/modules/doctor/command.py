from __future__ import annotations

import click

from winter_cli.cli_context import cli_ctx
from winter_cli.modules.doctor.handler import DoctorParams


@click.command("doctor")
@click.option("--json", "output_json", is_flag=True, default=False, help="Emit NDJSON probe events instead of a table.")
@click.pass_context
def doctor_command(ctx: click.Context, output_json: bool) -> None:
    """Run preflight checks for the workspace and every installed extension.

    Reports pass / warn / fail for each probe with a remediation hint under
    failures. Exit code is 0 when nothing failed (warnings allowed), 1 when
    any probe failed.
    """
    container = cli_ctx(ctx).container
    handler = container.doctor_handler()
    handler.run(DoctorParams(output_json=output_json))
