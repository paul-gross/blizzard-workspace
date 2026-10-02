"""Convention test — `opentelemetry` is imported only by the OTel tracer adapter.

Convention: `winter-context:/architecture/winter-cli.md` §Tracing, OpenTelemetry confined to its adapter.

A process with tracing off must not load the SDK, and every span site must depend on the
`IOperationTracer` Protocol rather than on OpenTelemetry. `core/internal/otel_command_tracer.py`
is the one module that imports `opentelemetry`; the container reaches it lazily.

Detection walks every module for an `import opentelemetry...` or `from opentelemetry... import`
statement, wherever it sits, including under `TYPE_CHECKING` and inside functions.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.conventions.conftest import SRC_ROOT, location, walk_src

CONVENTION_DOC = "winter-context:/architecture/winter-cli.md"

# The one module that imports `opentelemetry`, relative to `src/winter_cli/`.
ADAPTER_MODULE = "core/internal/otel_command_tracer.py"

_PACKAGE = "opentelemetry"


def _relative_module(file_path: Path) -> str | None:
    """Return the path relative to `src/winter_cli/`, or None for out-of-tree files."""
    try:
        return file_path.relative_to(SRC_ROOT).as_posix()
    except ValueError:
        return None


def _is_opentelemetry(module: str | None) -> bool:
    return module is not None and module.split(".")[0] == _PACKAGE


def _imports_opentelemetry(node: ast.AST) -> bool:
    if isinstance(node, ast.ImportFrom):
        return node.level == 0 and _is_opentelemetry(node.module)
    if isinstance(node, ast.Import):
        return any(_is_opentelemetry(alias.name) for alias in node.names)
    return False


def find_opentelemetry_import_violations(file_path: Path, tree: ast.Module) -> list[str]:
    if _relative_module(file_path) == ADAPTER_MODULE:
        return []
    return [
        f"{location(file_path, node)}: imports {_PACKAGE}; open spans through IOperationTracer "
        f"and keep the SDK in {ADAPTER_MODULE} ({CONVENTION_DOC})"
        for node in ast.walk(tree)
        if _imports_opentelemetry(node)
    ]


def test_opentelemetry_is_imported_only_by_its_adapter() -> None:
    all_violations: list[str] = []
    for path, tree in walk_src():
        all_violations.extend(find_opentelemetry_import_violations(path, tree))
    if all_violations:
        pytest.fail("\n".join(["opentelemetry import violations:", *all_violations]))


def test_adapter_module_still_imports_opentelemetry() -> None:
    """The exemption is real: the adapter is where `opentelemetry` is imported."""
    adapter = SRC_ROOT / ADAPTER_MODULE
    tree = ast.parse(adapter.read_text(encoding="utf-8"), filename=str(adapter))
    assert any(_imports_opentelemetry(node) for node in ast.walk(tree))


def test_fixture_violation_is_detected() -> None:
    fixture = Path(__file__).parent / "fixtures" / "violating_opentelemetry_import.py"
    tree = ast.parse(fixture.read_text(encoding="utf-8"), filename=str(fixture))
    violations = find_opentelemetry_import_violations(fixture, tree)
    # One per spelling: the plain import, the `from` import, and the submodule `from` import.
    assert len(violations) == 3
