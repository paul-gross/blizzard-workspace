from __future__ import annotations

import dataclasses

import click

from winter_cli.modules.workspace.handlers.json_render import echo_json, to_dict
from winter_cli.modules.workspace.workspace_fingerprint_service import WorkspaceFingerprintService


@dataclasses.dataclass
class FingerprintParams:
    output_json: bool = False


class FingerprintHandler:
    """Handles `winter ws fingerprint`: prints the digest alone, or with `--json`
    the digest beside every component (commit, content tree, dirty flag) of the
    workspace and each standalone."""

    def __init__(self, fingerprint_svc: WorkspaceFingerprintService) -> None:
        self._fingerprint_svc = fingerprint_svc

    def run(self, params: FingerprintParams) -> None:
        fingerprint = self._fingerprint_svc.compute()
        if params.output_json:
            echo_json(to_dict(fingerprint))
        else:
            click.echo(fingerprint.digest)
