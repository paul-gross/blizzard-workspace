from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from queue import Full, Queue
from threading import Event, Thread
from typing import Any

import pytest

from tests.conftest import FakeServiceReporter, FakeSubprocessRunner, process_running
from tests.modules.service.test_phase4_routing import WS, _make_two_provider_registry
from winter_cli.modules.service.models import LogOptions
from winter_cli.modules.service.service_logs_service import ServiceLogsService


class BlockingProcess:
    def __init__(self) -> None:
        self.items: Queue[str | int | BaseException] = Queue()
        self.started = Event()
        self.terminated = False
        self.closed = False
        self.code = 0

    @property
    def stdout_lines(self) -> Iterator[str]:
        self.started.set()
        while True:
            item = self.items.get(timeout=5)
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, int):
                self.code = item
                return
            yield item

    def wait(self) -> int:
        return self.code

    def terminate(self) -> None:
        self.terminated = True
        self.items.put(0)


class BlockingRunner(FakeSubprocessRunner):
    def __init__(self, *, fail_start: bool = False) -> None:
        super().__init__()
        self.processes = [BlockingProcess(), BlockingProcess()]
        self.calls = 0
        self.fail_start = fail_start

    @contextmanager
    def popen(self, cmd: Any, **kwargs: Any) -> Iterator[Any]:
        assert kwargs["manage_process_group"] is True
        assert kwargs["merge_stderr"] is False
        index = self.calls
        self.calls += 1
        if index == 1 and self.fail_start:
            raise RuntimeError("startup failed")
        proc = self.processes[index]
        try:
            yield proc
        finally:
            proc.closed = True


class ObservingReporter(FakeServiceReporter):
    def __init__(self) -> None:
        super().__init__()
        self.observed: Queue[tuple[str, Any]] = Queue()

    def log_line(self, rendered: str) -> None:
        super().log_line(rendered)
        self.observed.put(("line", rendered))

    def log_provider_failed(self, provider_name: str, exit_code: int) -> None:
        super().log_provider_failed(provider_name, exit_code)
        self.observed.put(("failure", (provider_name, exit_code)))


def setup(runner: BlockingRunner, options: LogOptions) -> tuple[Thread, Queue[Any], ObservingReporter, list[str]]:
    _, resolver = _make_two_provider_registry()
    providers = resolver.resolve_all()
    names = [p.extension_name for p in providers]
    service = ServiceLogsService(runner, resolver, None, WS, "winter")  # type: ignore[arg-type]
    reporter = ObservingReporter()
    result: Queue[Any] = Queue()

    def run() -> None:
        try:
            result.put(service._follow_many(providers, {name: [] for name in names}, options, reporter))
        except BaseException as exc:
            result.put(exc)

    thread = Thread(target=run, daemon=True)
    thread.start()
    return thread, result, reporter, names


def opts(**changes: Any) -> LogOptions:
    values: dict[str, Any] = {
        "patterns": ("alpha/*",),
        "follow": True,
        "tail": 200,
        "since_rfc3339": "",
        "until_rfc3339": "",
        "timestamps": False,
    }
    values.update(changes)
    return LogOptions(**values)


def line(svc: str, msg: str, ts: str | None = None, env: str = "alpha") -> str:
    value = {"env": env, "svc": svc, "msg": msg}
    if ts is not None:
        value["ts"] = ts
    return json.dumps(value)


def finish(thread: Thread, result: Queue[Any]) -> Any:
    value = result.get(timeout=5)
    thread.join(timeout=5)
    assert not thread.is_alive()
    return value


def test_interleaves_live_with_shared_filter_and_warnings() -> None:
    runner = BlockingRunner()
    thread, result, reporter, _ = setup(
        runner, opts(timestamps=True, since_rfc3339="2026-01-02T00:00:00Z", until_rfc3339="2026-01-04T00:00:00Z")
    )
    a, b = runner.processes
    try:
        assert a.started.wait(5) and b.started.wait(5)
        a.items.put(line("api", "old", "2026-01-01T00:00:00Z"))
        a.items.put(line("api", "wrong", env="other"))
        a.items.put(line("api", "a1", "2026-01-03T00:00:00Z"))
        assert reporter.observed.get(timeout=5) == ("line", "2026-01-03T00:00:00Z alpha/api | a1")
        b.items.put(line("db", "b1"))
        assert reporter.observed.get(timeout=5) == ("line", "alpha/db | b1")
        a.items.put(line("api", "a2"))
        assert reporter.observed.get(timeout=5) == ("line", "alpha/api | a2")
        b.items.put(line("db", "b2", "2026-01-03T01:00:00Z"))
        assert reporter.observed.get(timeout=5) == ("line", "2026-01-03T01:00:00Z alpha/db | b2")
        a.items.put(0)
        b.items.put(0)
        assert finish(thread, result) == 0
        assert reporter.timestamps_warning_called == reporter.time_filter_warning_called == 1
    finally:
        a.terminate()
        b.terminate()
        thread.join(timeout=5)


@pytest.mark.parametrize("first_code", [0, 7])
def test_early_exit_does_not_stop_peer_and_first_failure_wins(first_code: int) -> None:
    runner = BlockingRunner()
    thread, result, reporter, names = setup(runner, opts())
    a, b = runner.processes
    try:
        assert a.started.wait(5) and b.started.wait(5)
        a.items.put(first_code)
        if first_code:
            assert reporter.observed.get(timeout=5) == ("failure", (names[0], first_code))
        b.items.put(line("db", "still running"))
        assert reporter.observed.get(timeout=5) == ("line", "alpha/db | still running")
        assert not b.terminated
        b.items.put(9)
        assert finish(thread, result) == (first_code or 9)
        assert reporter.log_provider_failed_calls == ([(names[0], first_code)] if first_code else []) + [(names[1], 9)]
    finally:
        a.terminate()
        b.terminate()
        thread.join(timeout=5)


@pytest.mark.parametrize("error", [RuntimeError("reader failed"), KeyboardInterrupt()])
def test_reader_failure_cleans_all_and_preserves_exception(error: BaseException) -> None:
    runner = BlockingRunner()
    thread, result, _, _ = setup(runner, opts())
    a, b = runner.processes
    try:
        assert a.started.wait(5) and b.started.wait(5)
        a.items.put(error)
        value = finish(thread, result)
        assert value == 130 if isinstance(error, KeyboardInterrupt) else value is error
        assert all(p.terminated and p.closed for p in runner.processes)
    finally:
        a.terminate()
        b.terminate()
        thread.join(timeout=5)


@pytest.mark.parametrize("error", [RuntimeError("reader failed"), KeyboardInterrupt()])
def test_cleanup_retries_each_handle_before_join(error: BaseException) -> None:
    runner = BlockingRunner()
    a, b = runner.processes
    calls = [0, 0]
    originals = [a.terminate, b.terminate]

    def interrupted() -> None:
        calls[0] += 1
        if calls[0] <= 2:
            raise KeyboardInterrupt
        originals[0]()

    def transient() -> None:
        calls[1] += 1
        if calls[1] == 1:
            raise RuntimeError("transient termination failure")
        # Both handles have been attempted before retrying either.
        assert calls[0] >= 1
        originals[1]()

    a.terminate = interrupted
    b.terminate = transient
    thread, result, _, _ = setup(runner, opts())
    try:
        assert a.started.wait(5) and b.started.wait(5)
        a.items.put(error)
        value = finish(thread, result)
        assert value == 130 if isinstance(error, KeyboardInterrupt) else value is error
        assert calls == [3, 2]
        assert all(p.terminated and p.closed for p in runner.processes)
    finally:
        originals[0]()
        originals[1]()
        thread.join(timeout=5)


def test_interruptions_during_finalization_and_join_preserve_reader_error(monkeypatch: Any) -> None:
    from winter_cli.modules.service import service_logs_service

    class InterruptingRunner(BlockingRunner):
        @contextmanager
        def popen(self, cmd: Any, **kwargs: Any) -> Iterator[Any]:
            with super().popen(cmd, **kwargs) as proc:
                try:
                    yield proc
                finally:
                    raise KeyboardInterrupt

    real_pool = service_logs_service.ContextThreadPoolExecutor
    joins = 0

    class InterruptingPool(real_pool):
        def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
            nonlocal joins
            joins += 1
            if joins <= 2:
                raise KeyboardInterrupt
            super().shutdown(wait=wait, cancel_futures=cancel_futures)

    monkeypatch.setattr(service_logs_service, "ContextThreadPoolExecutor", InterruptingPool)
    runner = InterruptingRunner()
    thread, result, _, _ = setup(runner, opts())
    a, b = runner.processes
    error = RuntimeError("reader failed")
    try:
        assert a.started.wait(5) and b.started.wait(5)
        a.items.put(error)
        assert finish(thread, result) is error
        assert joins == 3
        assert all(p.terminated and p.closed for p in runner.processes)
    finally:
        a.terminate()
        b.terminate()
        thread.join(timeout=5)


def test_startup_error_survives_interrupted_termination() -> None:
    runner = BlockingRunner(fail_start=True)
    proc = runner.processes[0]
    original = proc.terminate
    calls = 0

    def interrupted() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise KeyboardInterrupt
        original()

    proc.terminate = interrupted
    thread, result, _, _ = setup(runner, opts())
    try:
        value = finish(thread, result)
        assert isinstance(value, RuntimeError) and str(value) == "startup failed"
        assert calls == 2 and proc.closed
    finally:
        original()
        thread.join(timeout=5)


def test_partial_startup_failure_cleans_acquired_process() -> None:
    runner = BlockingRunner(fail_start=True)
    thread, result, _, _ = setup(runner, opts())
    try:
        value = finish(thread, result)
        assert isinstance(value, RuntimeError) and str(value) == "startup failed"
        assert runner.processes[0].terminated and runner.processes[0].closed
    finally:
        runner.processes[0].terminate()
        thread.join(timeout=5)


@pytest.mark.parametrize("count", [1, 2])
def test_cli_sigint_stops_resistant_followers_and_descendants(tmp_path: Any, count: int) -> None:
    import os
    import signal
    import subprocess
    import sys
    import time
    from contextlib import suppress
    from pathlib import Path

    root: Path = tmp_path
    (root / ".winter").mkdir()
    names = [f"provider-{i}" for i in range(count)]
    config = 'main_branch = "main"\nservice_prefix = "test"\n[capabilities]\nservice = ' + json.dumps(names) + "\n"
    for name in names:
        config += f'\n[[standalone_repository]]\nname = "{name}"\nurl = "https://example.invalid/{name}"\n'
        directory = root / name
        directory.mkdir()
        (directory / "winter-ext.toml").write_text(
            f'name = "{name}"\nprefix = "{name}"\norchestrate_services = "service"\n'
        )
        script = directory / "service"
        script.write_text(f"""#!{sys.executable}
import json, os, signal, subprocess, sys, time
if sys.argv[1] == 'describe':
    print(json.dumps({{"services": ["svc{names.index(name)}"]}}))
    sys.exit(0)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print("ready", flush=True); time.sleep(60)'], stdout=subprocess.PIPE, text=True)
assert child.stdout.readline().strip() == 'ready'
open({str(root / (name + ".pids"))!r}, 'w').write(f'{{os.getpid()}} {{child.pid}}')
print(json.dumps({{"env": "alpha", "svc": "svc{names.index(name)}", "msg": "ready"}}), flush=True)
time.sleep(60)
""")
        script.chmod(0o755)
    (root / ".winter" / "config.toml").write_text(config)
    proc = subprocess.Popen(
        [sys.executable, "-m", "winter_cli.cli", "service", "logs", "alpha/*", "-f"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    pids: list[int] = []
    finished = Event()

    def watchdog() -> None:
        if not finished.wait(12):
            for file in [root / (name + ".pids") for name in names]:
                if file.exists():
                    for value in file.read_text().split():
                        with suppress(ProcessLookupError):
                            os.kill(int(value), signal.SIGKILL)
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)

    guard = Thread(target=watchdog, daemon=True)
    guard.start()
    try:
        deadline = time.monotonic() + 8
        files = [root / (name + ".pids") for name in names]
        while not all(f.exists() and f.read_text().strip() for f in files):
            if proc.poll() is not None:
                out, err = proc.communicate()
                pytest.fail(f"CLI exited before readiness: {out} {err}")
            assert time.monotonic() < deadline, "providers did not become ready"
            time.sleep(0.02)
        pids = [int(pid) for f in files for pid in f.read_text().split()]
        os.kill(proc.pid, signal.SIGINT)
        # Signal again while resistant providers are in bounded termination.
        for _ in range(3):
            time.sleep(0.05)
            if proc.poll() is None:
                os.kill(proc.pid, signal.SIGINT)
        _, err = proc.communicate(timeout=8)
        assert proc.returncode == 130, err
        deadline = time.monotonic() + 3
        while True:
            alive = [pid for pid in pids if process_running(pid)]
            if not alive:
                break
            assert time.monotonic() < deadline, f"followers survived: {alive}"
            time.sleep(0.02)
    finally:
        finished.set()
        guard.join(timeout=1)
        for f in [root / (name + ".pids") for name in names]:
            if f.exists():
                for value in f.read_text().split():
                    with suppress(ProcessLookupError):
                        os.kill(int(value), signal.SIGKILL)
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate(timeout=3)


@pytest.mark.parametrize("reporter_class", ["StreamServiceReporter", "JsonServiceReporter"])
def test_provider_failure_diagnostic_is_stderr_once(reporter_class: str, capsys: Any) -> None:
    import click

    from winter_cli.core.internal.click_cli_output_service import ClickCliOutputService
    from winter_cli.modules.service import service_reporter

    reporter = getattr(service_reporter, reporter_class)(click, ClickCliOutputService())
    reporter.log_provider_failed("provider-a", 7)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: log provider 'provider-a' exited with status 7\n"


def test_wait_failure_cleans_all_and_propagates_original() -> None:
    runner = BlockingRunner()
    thread, result, _, _ = setup(runner, opts())
    a, b = runner.processes
    error = RuntimeError("wait failed")

    def fail_wait() -> int:
        raise error

    a.wait = fail_wait
    try:
        assert a.started.wait(5) and b.started.wait(5)
        a.items.put(0)
        assert finish(thread, result) is error
        assert all(p.terminated and p.closed for p in runner.processes)
    finally:
        a.terminate()
        b.terminate()
        thread.join(timeout=5)


@pytest.mark.parametrize("cancel", [False, True])
def test_full_queue_backpressure_and_cancellation(monkeypatch: Any, cancel: bool) -> None:
    from winter_cli.modules.service import service_logs_service

    full = Event()
    reporting = Event()
    release = Event()

    class SmallQueue(Queue[Any]):
        def __init__(self, maxsize: int) -> None:
            assert maxsize > 0
            super().__init__(maxsize=2)

        def put(self, item: Any, block: bool = True, timeout: float | None = None) -> None:
            try:
                super().put(item, block, timeout)
            except Full:
                full.set()
                raise

    class SlowReporter(ObservingReporter):
        def log_line(self, rendered: str) -> None:
            reporting.set()
            assert release.wait(5)
            if cancel:
                raise KeyboardInterrupt
            super().log_line(rendered)

    monkeypatch.setattr(service_logs_service, "Queue", SmallQueue)
    runner = BlockingRunner()
    _, resolver = _make_two_provider_registry()
    providers = resolver.resolve_all()
    service = ServiceLogsService(runner, resolver, None, WS, "winter")  # type: ignore[arg-type]
    reporter = SlowReporter()
    result: Queue[Any] = Queue()

    def run() -> None:
        try:
            result.put(service._follow_many(providers, {p.extension_name: [] for p in providers}, opts(), reporter))
        except BaseException as exc:
            result.put(exc)

    thread = Thread(target=run, daemon=True)
    thread.start()
    a, b = runner.processes
    try:
        assert a.started.wait(5) and b.started.wait(5)
        for i in range(100):
            a.items.put(line("api", str(i)))
        a.items.put(0)
        b.items.put(0)
        assert reporting.wait(5) and full.wait(5)
        assert a.items.qsize() > 0
        release.set()
        assert finish(thread, result) == (130 if cancel else 0)
        if cancel:
            assert all(p.terminated and p.closed for p in runner.processes)
        else:
            assert len(reporter.log_lines) == 100
    finally:
        release.set()
        a.terminate()
        b.terminate()
        thread.join(timeout=5)
        assert not thread.is_alive()
