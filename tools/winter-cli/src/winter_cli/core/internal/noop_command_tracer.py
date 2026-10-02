from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from types import TracebackType

from winter_cli.core.tracing import AttributeValue, ICommandTracer, IOperationHandle


class _NoopOperation:
    """The operation of a process that does not trace: a handle that records nothing and never suppresses."""

    def __enter__(self) -> IOperationHandle:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        pass

    def mark_failed(self, error_type: str | None = None) -> None:
        pass


_NOOP_OPERATION = _NoopOperation()


class NoopCommandTracer:
    """Default `ICommandTracer`: tracing is off, so every call does nothing.

    Imports nothing from OpenTelemetry and nothing from winter beyond the Protocol it conforms
    to, so a process that never turns tracing on pays no import cost for it.
    """

    def inject(self, env: dict[str, str]) -> None:
        pass

    def annotate_env(self, env_name: str) -> None:
        pass

    def start_command(self, command_path: str) -> None:
        pass

    def end_command(self, error_type: str | None) -> None:
        pass

    def operation(self, name: str, attributes: Mapping[str, AttributeValue] | None = None) -> _NoopOperation:
        return _NOOP_OPERATION

    def start_background_export(self) -> None:
        pass

    def session_root(self, name: str) -> AbstractContextManager[IOperationHandle]:
        return _NOOP_OPERATION

    def export(self) -> None:
        pass


def _conforms_noop_command_tracer(x: NoopCommandTracer) -> ICommandTracer:
    return x
