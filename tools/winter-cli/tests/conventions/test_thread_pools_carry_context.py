"""Convention test — thread pools are built only through `ContextThreadPoolExecutor`.

Convention: `winter-context:/architecture/winter-cli.md` §Tracing, thread pools.

A plain `concurrent.futures.ThreadPoolExecutor` starts its worker threads with an empty
`contextvars` context, so the active trace span is lost at the thread boundary and a child
process started by a pool task carries the wrong `TRACEPARENT`. `ContextThreadPoolExecutor`
(`core/context_thread_pool.py`) runs each task in a copy of the submitter's context, and is the
only place allowed to name `ThreadPoolExecutor`.

Detection walks every module for a reference to the name `ThreadPoolExecutor`: an import of it,
a bare name, or an attribute access such as `concurrent.futures.ThreadPoolExecutor`. The
`ContextThreadPoolExecutor` name is a different identifier and never matches.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.conventions.conftest import SRC_ROOT, location, walk_src

CONVENTION_DOC = "winter-context:/architecture/winter-cli.md"

# The one module that builds on `ThreadPoolExecutor`, relative to `src/winter_cli/`.
HELPER_MODULE = "core/context_thread_pool.py"

_FORBIDDEN_NAME = "ThreadPoolExecutor"


def _relative_module(file_path: Path) -> str | None:
    """Return the path relative to `src/winter_cli/`, or None for out-of-tree files."""
    try:
        return file_path.relative_to(SRC_ROOT).as_posix()
    except ValueError:
        return None


def _references_thread_pool(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == _FORBIDDEN_NAME
    if isinstance(node, ast.Attribute):
        return node.attr == _FORBIDDEN_NAME
    if isinstance(node, ast.alias):
        return node.name.rsplit(".", 1)[-1] == _FORBIDDEN_NAME
    return False


def find_plain_thread_pool_violations(file_path: Path, tree: ast.Module) -> list[str]:
    if _relative_module(file_path) == HELPER_MODULE:
        return []
    return [
        f"{location(file_path, node)}: references {_FORBIDDEN_NAME}; build the pool with "
        f"ContextThreadPoolExecutor so the trace context crosses the thread ({CONVENTION_DOC})"
        for node in ast.walk(tree)
        if _references_thread_pool(node)
    ]


def test_no_plain_thread_pool_outside_the_helper() -> None:
    all_violations: list[str] = []
    for path, tree in walk_src():
        all_violations.extend(find_plain_thread_pool_violations(path, tree))
    if all_violations:
        pytest.fail("\n".join(["Plain ThreadPoolExecutor violations:", *all_violations]))


def test_helper_module_still_builds_on_thread_pool_executor() -> None:
    """The exemption is real: the helper is where `ThreadPoolExecutor` is named."""
    helper = SRC_ROOT / HELPER_MODULE
    tree = ast.parse(helper.read_text(encoding="utf-8"), filename=str(helper))
    assert any(_references_thread_pool(node) for node in ast.walk(tree))


def test_fixture_violation_is_detected() -> None:
    fixture = Path(__file__).parent / "fixtures" / "violating_plain_thread_pool.py"
    tree = ast.parse(fixture.read_text(encoding="utf-8"), filename=str(fixture))
    violations = find_plain_thread_pool_violations(fixture, tree)
    # One per spelling: the `from` import, the bare call, and the attribute access.
    assert len(violations) == 3
    assert all("ContextThreadPoolExecutor" in v for v in violations)
