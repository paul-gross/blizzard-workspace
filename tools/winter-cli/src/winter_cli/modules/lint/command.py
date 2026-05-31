from __future__ import annotations

from pathlib import Path

import click

from winter_cli.cli_context import cli_ctx
from winter_cli.modules.lint.handler import LintParams
from winter_cli.modules.lint.models import LintScopeError, LintScopeRequest


@click.command("lint")
@click.argument("scope", required=False)
@click.option("--all", "all_flag", is_flag=True, default=False, help="Lint the whole workspace tree (default).")
@click.option("--changed", is_flag=True, default=False, help="Lint only the dirty / un-pushed files.")
@click.option("--json", "output_json", is_flag=True, default=False, help="Emit NDJSON lint events instead of a table.")
@click.pass_context
def lint_command(ctx: click.Context, scope: str | None, all_flag: bool, changed: bool, output_json: bool) -> None:
    """Run convention lint checks contributed by installed extensions.

    `winter lint` is a dispatcher — it discovers each installed extension's
    contributed `lint` script (plus an optional workspace-level one), runs the
    applicable checks over the selected scope, and aggregates the findings. The
    checks themselves live in the extensions, not here.

    SCOPE is a repo name or an env name; or pass --all (the default) for the
    whole workspace, or --changed for the dirty / un-pushed file set. Each
    finding reports pass / warn / fail with an optional file:line location.
    Exit code is 0 when nothing failed (warnings allowed), 1 when any check
    failed.
    """
    container = cli_ctx(ctx).container
    handler = container.lint_handler()
    request = LintScopeRequest(name=scope, all=all_flag, changed=changed, cwd=Path.cwd())
    try:
        handler.run(LintParams(scope=request, output_json=output_json))
    except LintScopeError as exc:
        raise click.ClickException(str(exc)) from exc
