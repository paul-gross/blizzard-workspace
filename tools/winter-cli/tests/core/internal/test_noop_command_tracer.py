from __future__ import annotations

from winter_cli.core.internal.noop_command_tracer import NoopCommandTracer


def test_noop_tracer_changes_nothing() -> None:
    tracer = NoopCommandTracer()
    env = {"PATH": "/bin", "TRACEPARENT": "caller"}

    tracer.start_command("winter ws init")
    tracer.annotate_env("alpha")
    tracer.inject(env)
    tracer.end_command(None)
    tracer.export()

    assert env == {"PATH": "/bin", "TRACEPARENT": "caller"}


def test_noop_tracer_module_imports_only_its_protocol_module() -> None:
    """The no-op must stay import-free so a tracing-off process pays nothing for it."""
    import ast
    import inspect

    from winter_cli.core.internal import noop_command_tracer

    tree = ast.parse(inspect.getsource(noop_command_tracer))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module != "__future__"
    }
    assert imported == {"winter_cli.core.tracing"}
