"""A thread pool whose tasks run in the context of whoever submitted them."""

from __future__ import annotations

import contextvars
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import ParamSpec, TypeVar

_P = ParamSpec("_P")
_T = TypeVar("_T")


class ContextThreadPoolExecutor(ThreadPoolExecutor):
    """A `ThreadPoolExecutor` whose every `submit` runs the task in a copy of the submitter's context.

    A plain pool starts each worker thread with an empty context, so the active trace span, and
    anything else held in a `contextvars.ContextVar`, is lost at the thread boundary. This pool
    snapshots the submitting thread's context at `submit` time and runs the task inside that
    snapshot, so a task sees every value the submitter had set when it submitted.

    Each `submit` takes its own copy, because one `contextvars.Context` cannot be entered by two
    threads at once. A value a task sets stays inside its own copy: it is visible to neither the
    submitter nor any other task. `map` goes through `submit`, so it carries the context too.
    """

    def submit(self, fn: Callable[_P, _T], /, *args: _P.args, **kwargs: _P.kwargs) -> Future[_T]:
        context = contextvars.copy_context()
        return super().submit(context.run, fn, *args, **kwargs)
