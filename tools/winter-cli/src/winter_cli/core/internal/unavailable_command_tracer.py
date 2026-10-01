from __future__ import annotations

import logging

from winter_cli.core.internal.noop_command_tracer import NoopCommandTracer
from winter_cli.core.tracing import ICommandTracer

logger = logging.getLogger(__name__)


class UnavailableCommandTracer(NoopCommandTracer):
    """The no-op tracer standing in for an OpenTelemetry adapter that could not be built.

    The CLI boundary builds the tracer before it has parsed `--verbose`, so a record logged at
    selection time would be dropped. The failure is kept and logged at `export`, the process's
    last call, once the logging switches have taken effect.
    """

    def __init__(self, failure: Exception) -> None:
        self._failure = failure

    def export(self) -> None:
        logger.debug("tracing unavailable; running without it", exc_info=self._failure)


def _conforms_unavailable_command_tracer(x: UnavailableCommandTracer) -> ICommandTracer:
    return x
