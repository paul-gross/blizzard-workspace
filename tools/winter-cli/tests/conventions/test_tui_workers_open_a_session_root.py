"""Convention test — every TUI thread worker opens a session root as its whole body.

Convention: `winter-context:/architecture/winter-cli.md` §Tracing, TUI workers.

A Textual `@work(thread=True)` worker runs on an executor thread that starts with an empty
`contextvars` context, so the active trace span is lost and everything the worker does would
trace as unparented fragments. The worker opens `session_root(...)` on its injected
`ISessionTracer` first, and everything it does runs inside that root, which makes each refresh or
user action one trace linked to the dashboard's session span. The agent matrix's raw
`threading.Thread` is the same kind of boundary, so a function started as a thread's `target`
follows the rule too.

Detection walks every module under `modules/tui/` for a function decorated with `work(...)` or
`textual.work(...)` whose keyword `thread` is `True`, and for each function named as the
`target=` of a `Thread(...)` call in the same module. Each such function's body, after an
optional docstring, must be exactly one `with` statement whose context manager is a
`<expr>.session_root(...)` call.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.conventions.conftest import SRC_ROOT, location, walk_src

CONVENTION_DOC = "winter-context:/architecture/winter-cli.md"

TUI_ROOT = SRC_ROOT / "modules" / "tui"

_ROOT_METHOD = "session_root"

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def _is_work_decorator(decorator: ast.expr) -> bool:
    """A call of `work` or `<module>.work` with `thread=True`."""
    if not isinstance(decorator, ast.Call):
        return False
    func = decorator.func
    named_work = (isinstance(func, ast.Name) and func.id == "work") or (
        isinstance(func, ast.Attribute) and func.attr == "work"
    )
    return named_work and any(
        keyword.arg == "thread" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True
        for keyword in decorator.keywords
    )


def _thread_target_names(tree: ast.Module) -> set[str]:
    """Names of the functions a `Thread(target=...)` call in the module starts: `self.name` or a bare name."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            (isinstance(func, ast.Name) and func.id == "Thread")
            or (isinstance(func, ast.Attribute) and func.attr == "Thread")
        ):
            continue
        for keyword in node.keywords:
            if keyword.arg != "target":
                continue
            if isinstance(keyword.value, ast.Attribute):
                names.add(keyword.value.attr)
            elif isinstance(keyword.value, ast.Name):
                names.add(keyword.value.id)
    return names


def _opens_a_session_root(function: FunctionNode) -> bool:
    """Whether the body, after an optional docstring, is exactly one `with <expr>.session_root(...)`."""
    body = function.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], ast.With):
        return False
    return any(
        isinstance(item.context_expr, ast.Call)
        and isinstance(item.context_expr.func, ast.Attribute)
        and item.context_expr.func.attr == _ROOT_METHOD
        for item in body[0].items
    )


def find_unrooted_worker_violations(file_path: Path, tree: ast.Module) -> list[str]:
    thread_targets = _thread_target_names(tree)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        is_worker = any(_is_work_decorator(decorator) for decorator in node.decorator_list)
        if (is_worker or node.name in thread_targets) and not _opens_a_session_root(node):
            kind = "thread worker" if is_worker else "thread target"
            violations.append(
                f"{location(file_path, node)}: {kind} {node.name}() must be one `with ....{_ROOT_METHOD}(...)` "
                f"block, so its work is one trace under the session span ({CONVENTION_DOC})"
            )
    return violations


def test_every_tui_thread_worker_opens_a_session_root() -> None:
    all_violations: list[str] = []
    for path, tree in walk_src():
        if TUI_ROOT in path.parents:
            all_violations.extend(find_unrooted_worker_violations(path, tree))
    if all_violations:
        pytest.fail("\n".join(["TUI thread workers without a session root:", *all_violations]))


def test_the_tui_has_thread_workers_and_a_raw_thread_for_the_rule_to_check() -> None:
    """The scan is real: it finds the decorated workers and the agent matrix's raw thread."""
    workers = 0
    targets: set[str] = set()
    for path, tree in walk_src():
        if TUI_ROOT not in path.parents:
            continue
        targets |= _thread_target_names(tree)
        workers += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and any(_is_work_decorator(d) for d in node.decorator_list)
        )
    assert workers >= 10
    assert "_run_ws_init" in targets


def test_fixture_violation_is_detected() -> None:
    fixture = Path(__file__).parent / "fixtures" / "violating_tui_worker.py"
    tree = ast.parse(fixture.read_text(encoding="utf-8"), filename=str(fixture))
    violations = find_unrooted_worker_violations(fixture, tree)
    assert len(violations) == 3
    assert any("refresh_without_a_root" in v and "thread worker" in v for v in violations)
    assert any("refresh_with_work_outside_the_root" in v for v in violations)
    assert any("raw_thread_without_a_root" in v and "thread target" in v for v in violations)
    assert not any("compliant" in v or "async_worker" in v for v in violations)
