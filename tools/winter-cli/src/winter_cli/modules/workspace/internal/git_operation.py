from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Mapping
from typing import Any, TypeVar, cast

from winter_cli.core.tracing import ATTR_ENV, ATTR_REPO, AttributeValue
from winter_cli.modules.workspace.models import FeatureWorktree, ProjectRepository, StandaloneRepository

_Opener = TypeVar("_Opener", bound=Callable[..., Any])


class GitOperationDeclaration:
    """Declares the git `<operation>` a repository-opening function performs, and opens its span.

    Decorate a method of a GitPython adapter that opens a repository with
    `@GitOperationDeclaration("fetch")`. Each call then runs inside a `git fetch` span opened
    through the instance's `_tracer` (an `IOperationTracer`), so the span is the active one for
    every git command the call runs, and a function the call reaches that takes the open
    repository needs no span of its own. A decorated method that calls another decorated method
    nests the inner span in the outer one.

    The span's attributes come from the opener's own arguments, never from a path. A
    `FeatureWorktree` argument supplies `winter.repo` and `winter.env`; a `ProjectRepository` or
    `StandaloneRepository` supplies `winter.repo` only. A path-taking private opener names what
    it acts on with `repo_name` and `env` parameters, and its callers pass them from the domain
    object they hold (`env` is `None` for anything outside a feature environment).

    A class that declares a method must assign `self._tracer` in its `__init__` (or delegate to a
    base class's `__init__` that does); `tests/conventions/test_git_spans_at_the_gitpython_boundary.py`
    checks it. An instance without a tracer still runs the call, untraced.

    The declaration never changes the call: arguments, return value and exceptions pass through.
    """

    def __init__(self, operation: str) -> None:
        self.operation = operation

    def __call__(self, opener: _Opener) -> _Opener:
        signature = inspect.signature(opener)
        span_name = f"git {self.operation}"

        @functools.wraps(opener)
        def traced(adapter: Any, *args: Any, **kwargs: Any) -> Any:
            tracer = getattr(adapter, "_tracer", None)
            if tracer is None:
                return opener(adapter, *args, **kwargs)
            arguments = signature.bind(adapter, *args, **kwargs).arguments
            with tracer.operation(span_name, _attributes(arguments)):
                return opener(adapter, *args, **kwargs)

        return cast(_Opener, traced)


class GitOperationExemption:
    """Declares that a repository-opening function opens no span, and why.

    The one exemption is env discovery: the read that lists a source checkout's worktrees finds
    out which environments exist, which is workspace discovery rather than an operation on a
    repository. The reason is part of the declaration so the exemption is never anonymous.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __call__(self, opener: _Opener) -> _Opener:
        return opener


def _attributes(arguments: Mapping[str, Any]) -> dict[str, AttributeValue]:
    """The span's `winter.repo` and `winter.env`, read from the opener's bound arguments."""
    attributes: dict[str, AttributeValue] = {}
    for value in arguments.values():
        if isinstance(value, FeatureWorktree):
            attributes[ATTR_REPO] = value.repository.name
            attributes[ATTR_ENV] = value.environment.name
        elif isinstance(value, (ProjectRepository, StandaloneRepository)):
            attributes[ATTR_REPO] = value.name
    repo_name = arguments.get("repo_name")
    if isinstance(repo_name, str):
        attributes[ATTR_REPO] = repo_name
    env = arguments.get("env")
    if isinstance(env, str):
        attributes[ATTR_ENV] = env
    return attributes
