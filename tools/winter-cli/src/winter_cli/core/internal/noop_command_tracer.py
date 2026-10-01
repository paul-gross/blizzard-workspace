from __future__ import annotations

from winter_cli.core.tracing import ICommandTracer


class NoopCommandTracer:
    """Default `ICommandTracer`: tracing is off, so every call does nothing.

    Imports nothing beyond the Protocol it conforms to, so a process that never turns
    tracing on pays no import cost for it.
    """

    def inject(self, env: dict[str, str]) -> None:
        pass

    def annotate_env(self, env_name: str) -> None:
        pass

    def start_command(self, command_path: str) -> None:
        pass

    def end_command(self, error_type: str | None) -> None:
        pass

    def export(self) -> None:
        pass


def _conforms_noop_command_tracer(x: NoopCommandTracer) -> ICommandTracer:
    return x
