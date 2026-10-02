"""`ContextThreadPoolExecutor` runs each task in a copy of the submitter's context."""

from __future__ import annotations

import contextvars
import threading

from winter_cli.core.context_thread_pool import ContextThreadPoolExecutor

_VALUE: contextvars.ContextVar[str] = contextvars.ContextVar("test_context_thread_pool_value", default="unset")


def test_a_value_set_before_submit_is_visible_in_the_task() -> None:
    _VALUE.set("from-submitter")
    with ContextThreadPoolExecutor(max_workers=2) as pool:
        assert pool.submit(_VALUE.get).result() == "from-submitter"


def test_a_plain_pool_would_not_carry_the_value() -> None:
    """Pins what the helper fixes: a bare `ThreadPoolExecutor` thread starts with an empty context."""
    from concurrent.futures import ThreadPoolExecutor

    _VALUE.set("from-submitter")
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(_VALUE.get).result() == "unset"


def test_each_submit_carries_the_value_set_at_its_own_submit_time() -> None:
    with ContextThreadPoolExecutor(max_workers=1) as pool:
        _VALUE.set("first")
        first = pool.submit(_VALUE.get)
        _VALUE.set("second")
        second = pool.submit(_VALUE.get)
        assert (first.result(), second.result()) == ("first", "second")


def test_a_value_set_inside_a_task_is_invisible_to_the_submitter_and_to_other_tasks() -> None:
    _VALUE.set("from-submitter")
    inside_set = threading.Barrier(2, timeout=5)
    observed_by_other: list[str] = []

    def setter() -> str:
        _VALUE.set("from-task")
        inside_set.wait()
        return _VALUE.get()

    def bystander() -> None:
        inside_set.wait()
        observed_by_other.append(_VALUE.get())

    with ContextThreadPoolExecutor(max_workers=2) as pool:
        setter_future = pool.submit(setter)
        bystander_future = pool.submit(bystander)
        assert setter_future.result() == "from-task"
        bystander_future.result()

    assert observed_by_other == ["from-submitter"]
    assert _VALUE.get() == "from-submitter"


def test_a_value_set_inside_a_task_does_not_leak_into_the_next_task_on_the_same_thread() -> None:
    _VALUE.set("from-submitter")
    with ContextThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_VALUE.set, "from-task").result()
        assert pool.submit(_VALUE.get).result() == "from-submitter"


def test_map_carries_the_context() -> None:
    _VALUE.set("from-submitter")
    with ContextThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: _VALUE.get(), range(3))) == ["from-submitter"] * 3


def test_a_task_exception_surfaces_at_result() -> None:
    def boom() -> None:
        raise ValueError("boom")

    with ContextThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(boom)
        try:
            future.result()
        except ValueError as exc:
            assert str(exc) == "boom"
        else:
            raise AssertionError("expected ValueError")
