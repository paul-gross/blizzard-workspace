"""Fixture: classes that declare git operations, only one of which assigns `self._tracer`.

`WithoutATracer` is the violation; the class that assigns the tracer and the subclass that
delegates to its base `__init__` are fine.
"""

from __future__ import annotations

from winter_cli.modules.workspace.internal.git_operation import GitOperationDeclaration


class WithTracer:
    def __init__(self, tracer):
        self._tracer = tracer

    @GitOperationDeclaration("fetch")
    def fetch(self, path):
        return path


class DelegatesToBase(WithTracer):
    def __init__(self, tracer, extra):
        super().__init__(tracer)
        self._extra = extra

    @GitOperationDeclaration("pull")
    def pull(self, path):
        return path


class WithoutATracer:
    def __init__(self, extra):
        self._extra = extra

    @GitOperationDeclaration("status")
    def status(self, path):
        return path
