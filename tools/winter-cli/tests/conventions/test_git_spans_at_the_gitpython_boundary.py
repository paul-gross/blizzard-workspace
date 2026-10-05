"""Convention test — GitPython repositories open only in the workspace adapters, each declaring its git operation.

Convention: `winter-context:/architecture/winter-cli.md` §Tracing, git spans.

Every git command winter runs through GitPython runs on a repository one of the adapters in
`modules/workspace/internal/` opened, so those adapters are where a `git <operation>` span is
opened. Each function there that opens a repository carries `GitOperationDeclaration` (which opens
the span) or `GitOperationExemption` (env discovery, which opens none). A repository opened
anywhere else would run git with no span at all.

Detection walks every module for an open of a GitPython repository: `git.Repo(...)`, a class
method such as `git.Repo.clone_from(...)`, or the same through `from git import Repo`. Inside the
adapter package an open must sit in a function that carries one of the two declarations, directly
or through an enclosing function. Everywhere else an open is a violation.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.conventions.conftest import SRC_ROOT, location, walk_src

CONVENTION_DOC = "winter-context:/architecture/winter-cli.md"

# The package, relative to `src/winter_cli/`, that owns every GitPython repository open.
ADAPTER_PACKAGE = "modules/workspace/internal/"

_DECLARATIONS = frozenset({"GitOperationDeclaration", "GitOperationExemption"})


def _relative_module(file_path: Path) -> str | None:
    """Return the path relative to `src/winter_cli/`, or None for out-of-tree files."""
    try:
        return file_path.relative_to(SRC_ROOT).as_posix()
    except ValueError:
        return None


def _git_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """The names a module binds to the `git` package and to `git.Repo`."""
    modules: set[str] = set()
    repo_classes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update((alias.asname or alias.name) for alias in node.names if alias.name == "git")
        elif isinstance(node, ast.ImportFrom) and node.module == "git":
            repo_classes.update((alias.asname or alias.name) for alias in node.names if alias.name == "Repo")
    return modules, repo_classes


def _opens_repository(call: ast.Call, modules: set[str], repo_classes: set[str]) -> bool:
    """Whether `call` is `git.Repo(...)` or a class method of it, such as `git.Repo.clone_from(...)`."""
    func = call.func
    if isinstance(func, ast.Attribute) and not _is_repo_class(func, modules, repo_classes):
        func = func.value
    return _is_repo_class(func, modules, repo_classes)


def _is_repo_class(node: ast.expr, modules: set[str], repo_classes: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in repo_classes
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "Repo"
        and isinstance(node.value, ast.Name)
        and node.value.id in modules
    )


def _declares(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in function.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
        if name in _DECLARATIONS:
            return True
    return False


def _opens(tree: ast.Module) -> list[tuple[ast.Call, list[ast.FunctionDef | ast.AsyncFunctionDef]]]:
    """Every repository open in the module, with the chain of functions enclosing it."""
    modules, repo_classes = _git_names(tree)
    found: list[tuple[ast.Call, list[ast.FunctionDef | ast.AsyncFunctionDef]]] = []

    def visit(node: ast.AST, enclosing: list[ast.FunctionDef | ast.AsyncFunctionDef]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call) and _opens_repository(child, modules, repo_classes):
                found.append((child, enclosing))
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                visit(child, [*enclosing, child])
            else:
                visit(child, enclosing)

    visit(tree, [])
    return found


def find_git_boundary_violations(file_path: Path, tree: ast.Module, *, in_adapter_package: bool) -> list[str]:
    violations: list[str] = []
    for call, enclosing in _opens(tree):
        if not in_adapter_package:
            violations.append(
                f"{location(file_path, call)}: opens a GitPython repository outside `{ADAPTER_PACKAGE}`; "
                f"open it in an adapter there, where the git operation is declared ({CONVENTION_DOC})"
            )
        elif not any(_declares(function) for function in enclosing):
            violations.append(
                f"{location(file_path, call)}: opens a GitPython repository in a function that carries neither "
                f"`GitOperationDeclaration` nor `GitOperationExemption`; declare the git operation it performs "
                f"({CONVENTION_DOC})"
            )
    return violations


def _init_provides_tracer(cls: ast.ClassDef) -> bool:
    """Whether the class's `__init__` assigns `self._tracer`, or hands construction to a base via `super().__init__`."""
    for item in cls.body:
        if not (isinstance(item, ast.FunctionDef) and item.name == "__init__"):
            continue
        for node in ast.walk(item):
            if isinstance(node, ast.Assign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(
                    isinstance(t, ast.Attribute)
                    and t.attr == "_tracer"
                    and isinstance(t.value, ast.Name)
                    and t.value.id == "self"
                    for t in targets
                ):
                    return True
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "__init__"
                and isinstance(node.func.value, ast.Call)
                and getattr(node.func.value.func, "id", None) == "super"
            ):
                return True
    return False


def find_missing_tracer_violations(file_path: Path, tree: ast.Module) -> list[str]:
    """Every class with a `GitOperationDeclaration` method whose `__init__` does not assign `self._tracer`."""
    violations: list[str] = []
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        declares = any(
            isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
            and any(
                getattr(d.func if isinstance(d, ast.Call) else d, "id", None) == "GitOperationDeclaration"
                for d in item.decorator_list
            )
            for item in cls.body
        )
        if declares and not _init_provides_tracer(cls):
            violations.append(
                f"{location(file_path, cls)}: `{cls.name}` declares git operations but its `__init__` does not "
                f"assign `self._tracer`; the declaration opens its span through that tracer ({CONVENTION_DOC})"
            )
    return violations


def _violations_in_src() -> list[str]:
    violations: list[str] = []
    for path, tree in walk_src():
        relative = _relative_module(path)
        in_adapter_package = relative is not None and relative.startswith(ADAPTER_PACKAGE)
        violations.extend(find_git_boundary_violations(path, tree, in_adapter_package=in_adapter_package))
        violations.extend(find_missing_tracer_violations(path, tree))
    return violations


def test_every_repository_open_is_in_a_declared_adapter_function() -> None:
    violations = _violations_in_src()
    if violations:
        pytest.fail("\n".join(["GitPython repository boundary violations:", *violations]))


def test_the_adapters_open_repositories_and_declare_them() -> None:
    """The rule is not vacuous: each adapter has opens, and they sit under declarations."""
    adapters = {
        "read_repo_repository.py",
        "write_repo_repository.py",
        "gitpython_repository.py",
        "read_workspace_repository.py",
        "gitpython_workspace_exclude_locator.py",
    }
    seen: set[str] = set()
    for path, tree in walk_src():
        relative = _relative_module(path)
        if relative is None or not relative.startswith(ADAPTER_PACKAGE):
            continue
        opens = _opens(tree)
        if opens:
            seen.add(path.name)
            assert all(any(_declares(function) for function in enclosing) for _call, enclosing in opens), path
    assert seen == adapters


FIXTURE = Path(__file__).parent / "fixtures" / "violating_git_boundary.py"


def test_fixture_violations_inside_the_adapter_package_are_the_undeclared_opens() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"), filename=str(FIXTURE))

    violations = find_git_boundary_violations(FIXTURE, tree, in_adapter_package=True)

    # The module-level open and the two undeclared methods; the declared, exempt and nested-in-declared ones pass.
    assert len(violations) == 3
    assert all("GitOperationDeclaration" in v and "GitOperationExemption" in v for v in violations)


def test_fixture_violations_outside_the_adapter_package_are_every_open() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"), filename=str(FIXTURE))

    violations = find_git_boundary_violations(FIXTURE, tree, in_adapter_package=False)

    assert len(violations) == 6
    assert all(ADAPTER_PACKAGE in v for v in violations)


TRACERLESS_FIXTURE = Path(__file__).parent / "fixtures" / "violating_git_tracer.py"


def test_fixture_violations_are_the_declaring_classes_without_a_tracer() -> None:
    tree = ast.parse(TRACERLESS_FIXTURE.read_text(encoding="utf-8"), filename=str(TRACERLESS_FIXTURE))

    violations = find_missing_tracer_violations(TRACERLESS_FIXTURE, tree)

    # The class with no `__init__` assignment fails; the direct-assigning and super-delegating ones pass.
    assert len(violations) == 1
    assert "WithoutATracer" in violations[0]
