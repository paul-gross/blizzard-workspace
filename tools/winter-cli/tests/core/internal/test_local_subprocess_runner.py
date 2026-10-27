from __future__ import annotations

import io
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.conftest import process_running
from winter_cli.core.internal import local_subprocess_runner
from winter_cli.core.internal.local_subprocess_runner import LocalSubprocessRunner
from winter_cli.core.internal.noop_command_tracer import NoopCommandTracer


def test_run_passes_cmd_cwd_env_to_subprocess(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_subprocess = MagicMock()
    fake_subprocess.run.return_value = MagicMock(returncode=0, stdout="hello\n", stderr="")
    monkeypatch.setattr(local_subprocess_runner, "subprocess", fake_subprocess)

    result = LocalSubprocessRunner(NoopCommandTracer()).run(["echo", "hi"], cwd=tmp_path, env={"K": "V"})

    fake_subprocess.run.assert_called_once_with(
        ["echo", "hi"],
        cwd=str(tmp_path),
        env={"K": "V"},
        stdin=None,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == "hello\n"


def test_run_without_cwd_or_env_passes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_subprocess = MagicMock()
    fake_subprocess.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(local_subprocess_runner, "subprocess", fake_subprocess)

    result = LocalSubprocessRunner(NoopCommandTracer()).run(["git", "status"])

    fake_subprocess.run.assert_called_once_with(
        ["git", "status"],
        cwd=None,
        env=None,
        stdin=None,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    assert result.returncode == 0


def test_run_returns_failure_result_when_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_subprocess = MagicMock()
    fake_subprocess.run.side_effect = OSError("no such file")
    monkeypatch.setattr(local_subprocess_runner, "subprocess", fake_subprocess)

    result = LocalSubprocessRunner(NoopCommandTracer()).run(["does-not-exist"])

    assert result.returncode == -1
    assert result.stdout == ""
    assert "no such file" in result.stderr


def test_run_decodes_non_utf8_output_instead_of_raising() -> None:
    """Output that isn't valid UTF-8 comes back as a result, not an exception.

    Real subprocess, no mock: the decode happens inside `subprocess.run`, so
    a fake `subprocess` module would assert nothing. Callers inspect
    `returncode` and are not prepared for `UnicodeDecodeError` from a command
    that succeeded — `git check-ignore` printing a filename from a vendored
    tree is the case that surfaced it.
    """
    result = LocalSubprocessRunner(NoopCommandTracer()).run(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'a\\xffb')"]
    )

    assert result.returncode == 0
    assert "\ufffd" in result.stdout


def test_call_inherits_stdio_and_returns_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`call` does not capture output (stdio inherited) and returns the exit code."""
    fake_subprocess = MagicMock()
    fake_subprocess.run.return_value = MagicMock(returncode=3)
    monkeypatch.setattr(local_subprocess_runner, "subprocess", fake_subprocess)

    code = LocalSubprocessRunner(NoopCommandTracer()).call(["svc", "up", "alpha"], cwd=tmp_path, env={"K": "V"})

    fake_subprocess.run.assert_called_once_with(
        ["svc", "up", "alpha"],
        cwd=str(tmp_path),
        env={"K": "V"},
        stdin=None,
        check=False,
    )
    assert code == 3


def test_call_returns_126_on_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_subprocess = MagicMock()
    fake_subprocess.run.side_effect = OSError("not executable")
    monkeypatch.setattr(local_subprocess_runner, "subprocess", fake_subprocess)

    assert LocalSubprocessRunner(NoopCommandTracer()).call(["does-not-exist"]) == 126


def test_popen_passes_cmd_cwd_env_shell_to_popen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_proc = MagicMock()
    fake_proc.stdout = io.StringIO("line1\nline2\n")
    fake_proc.wait.return_value = 0
    fake_proc.poll.return_value = 0

    fake_subprocess = MagicMock()
    fake_subprocess.Popen.return_value = fake_proc
    fake_subprocess.PIPE = -1
    fake_subprocess.STDOUT = -2
    monkeypatch.setattr(local_subprocess_runner, "subprocess", fake_subprocess)

    runner = LocalSubprocessRunner(NoopCommandTracer())
    with runner.popen(["bash", "-c", "echo hi"], cwd=tmp_path, env={"X": "1"}, shell=False) as proc:
        lines = list(proc.stdout_lines)

    fake_subprocess.Popen.assert_called_once_with(
        ["bash", "-c", "echo hi"],
        cwd=str(tmp_path),
        env={"X": "1"},
        shell=False,
        stdin=None,
        stdout=fake_subprocess.PIPE,
        stderr=fake_subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=False,
    )
    assert lines == ["line1", "line2"]


def test_popen_merge_stderr_false_passes_none_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """`popen(merge_stderr=False)` sets stderr=None so the child inherits the parent's stderr fd."""
    fake_proc = MagicMock()
    fake_proc.stdout = io.StringIO()
    fake_proc.wait.return_value = 0
    fake_proc.poll.return_value = 0

    fake_subprocess = MagicMock()
    fake_subprocess.Popen.return_value = fake_proc
    fake_subprocess.PIPE = -1
    fake_subprocess.STDOUT = -2
    monkeypatch.setattr(local_subprocess_runner, "subprocess", fake_subprocess)

    runner = LocalSubprocessRunner(NoopCommandTracer())
    with runner.popen(["svc", "logs", "alpha"], merge_stderr=False) as _proc:
        pass

    fake_subprocess.Popen.assert_called_once_with(
        ["svc", "logs", "alpha"],
        cwd=None,
        env=None,
        shell=False,
        stdin=None,
        stdout=fake_subprocess.PIPE,
        stderr=None,
        text=True,
        bufsize=1,
        start_new_session=False,
    )


# A child that reports whether it could prompt: stdin at EOF (from /dev/null)
# and the git prompt switch it sees, plus a caller-supplied variable.
_PROBE = "import os, sys; print(sys.stdin.read() == '', os.environ.get('GIT_TERMINAL_PROMPT'), os.environ.get('K'))"


def test_non_interactive_run_gives_the_child_devnull_stdin_and_no_git_prompt() -> None:
    runner = LocalSubprocessRunner(NoopCommandTracer(), non_interactive=True)

    assert runner.run([sys.executable, "-c", _PROBE], env={"K": "V"}).stdout.split() == ["True", "0", "V"]


def test_non_interactive_run_layers_over_the_inherited_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K", "inherited")
    runner = LocalSubprocessRunner(NoopCommandTracer(), non_interactive=True)

    assert runner.run([sys.executable, "-c", _PROBE]).stdout.split() == ["True", "0", "inherited"]


def test_non_interactive_popen_and_call_give_the_child_devnull_stdin_and_no_git_prompt() -> None:
    runner = LocalSubprocessRunner(NoopCommandTracer(), non_interactive=True)

    with runner.popen([sys.executable, "-c", _PROBE], env={"K": "V"}) as proc:
        assert list(proc.stdout_lines) == ["True 0 V"]
    exit_if_prompting = (
        "import os, sys; sys.exit(0 if sys.stdin.read() == '' and os.environ['GIT_TERMINAL_PROMPT'] == '0' else 1)"
    )
    assert runner.call([sys.executable, "-c", exit_if_prompting]) == 0


@pytest.fixture
def watched_processes(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[subprocess.Popen[str]]]:
    """Independent deadline and direct OS teardown, even if adapter cleanup hangs."""
    processes: list[subprocess.Popen[str]] = []
    popen = subprocess.Popen

    def track(*args, **kwargs):
        proc = popen(*args, **kwargs)
        processes.append(proc)
        return proc

    def expired(_signum, _frame):
        for proc in processes:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            if proc.poll() is None:
                proc.kill()
        pytest.fail("subprocess lifecycle exceeded independent 8-second watchdog")

    monkeypatch.setattr(local_subprocess_runner.subprocess, "Popen", track)
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 8)
    try:
        yield processes
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        for proc in processes:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=2)
            if proc.stdout is not None:
                proc.stdout.close()


def test_managed_termination_reaps_resistant_group(watched_processes: list[subprocess.Popen[str]]) -> None:

    script = """\
import os, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print("ready", flush=True); time.sleep(60)'], stdout=subprocess.PIPE, text=True)
assert child.stdout.readline().strip() == 'ready'
print(os.getpid(), child.pid, flush=True)
time.sleep(60)
"""
    runner = LocalSubprocessRunner(NoopCommandTracer())
    with runner.popen([sys.executable, "-c", script], manage_process_group=True) as handle:
        _leader, child = map(int, next(handle.stdout_lines).split())
        start = time.monotonic()
        handle.terminate()
        assert time.monotonic() - start < 3
        assert handle.wait() == handle.wait() == -signal.SIGKILL
        handle.terminate()
        assert list(handle.stdout_lines) == []
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not process_running(child):
                break
            time.sleep(0.02)
        else:
            pytest.fail("descendant survived termination")
    assert watched_processes[0].stdout is not None and watched_processes[0].stdout.closed


@pytest.mark.parametrize("error", [ValueError, KeyboardInterrupt])
def test_managed_exception_cleans_up(
    watched_processes: list[subprocess.Popen[str]], error: type[BaseException]
) -> None:
    runner = LocalSubprocessRunner(NoopCommandTracer())
    with (
        pytest.raises(error),
        runner.popen(
            [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(60)"],
            manage_process_group=True,
        ) as handle,
    ):
        assert next(handle.stdout_lines) == "ready"
        raise error("abort")
    assert handle.wait() != 0
    assert watched_processes[0].stdout is not None and watched_processes[0].stdout.closed


def test_managed_termination_stops_descendant_after_leader_exits(
    watched_processes: list[subprocess.Popen[str]],
) -> None:
    script = """\
import subprocess, sys
child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print("ready", flush=True); time.sleep(60)'])
"""
    runner = LocalSubprocessRunner(NoopCommandTracer())
    with runner.popen([sys.executable, "-c", script], manage_process_group=True) as handle:
        assert next(handle.stdout_lines) == "ready"
        assert handle.wait() == 0
        handle.terminate()
        assert list(handle.stdout_lines) == []
        assert handle.wait() == 0


def test_unmanaged_termination_is_bounded_and_repeatable(watched_processes: list[subprocess.Popen[str]]) -> None:
    runner = LocalSubprocessRunner(NoopCommandTracer())
    script = (
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(60)"
    )
    with runner.popen([sys.executable, "-c", script]) as handle:
        assert next(handle.stdout_lines) == "ready"
        handle.terminate()
        handle.terminate()
        assert handle.wait() == handle.wait() == -signal.SIGKILL
    assert watched_processes[0].stdout is not None and watched_processes[0].stdout.closed


@pytest.mark.parametrize("managed", [False, True])
def test_completed_process_preserves_status_and_closes_pipe(
    watched_processes: list[subprocess.Popen[str]], managed: bool
) -> None:
    runner = LocalSubprocessRunner(NoopCommandTracer())
    with runner.popen(
        [sys.executable, "-c", "print('done', flush=True); raise SystemExit(7)"], manage_process_group=managed
    ) as handle:
        assert list(handle.stdout_lines) == ["done"]
        assert handle.wait() == handle.wait() == 7
    assert handle.wait() == 7
    assert watched_processes[0].stdout is not None and watched_processes[0].stdout.closed


def test_lifecycle_watchdog_breaks_blocked_termination(
    watched_processes: list[subprocess.Popen[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The safety net must fail and clean up even when terminate never signals."""

    def broken_terminate(self: local_subprocess_runner._StreamingProcess) -> None:
        self.wait()

    monkeypatch.setattr(local_subprocess_runner._StreamingProcess, "terminate", broken_terminate)
    runner = LocalSubprocessRunner(NoopCommandTracer())
    with (
        pytest.raises(pytest.fail.Exception, match="independent 8-second watchdog"),
        runner.popen(
            [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(60)"],
            manage_process_group=True,
        ) as handle,
    ):
        assert next(handle.stdout_lines) == "ready"
        signal.setitimer(signal.ITIMER_REAL, 0.1)
        handle.terminate()
    assert handle.wait() == -signal.SIGKILL
    assert watched_processes[0].stdout is not None and watched_processes[0].stdout.closed


def test_normal_exit_cleans_group_after_nonreaping_wait(watched_processes: list[subprocess.Popen[str]]) -> None:
    script = """\
import subprocess, sys
child = subprocess.Popen([sys.executable, '-c', 'import os,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print(os.getpid(), flush=True); os.close(1); time.sleep(60)'])
"""
    runner = LocalSubprocessRunner(NoopCommandTracer())
    with runner.popen([sys.executable, "-c", script], manage_process_group=True) as handle:
        child = int(next(handle.stdout_lines))
        assert handle.wait() == 0
        assert watched_processes[0].returncode is None
    assert handle.wait() == 0
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not process_running(child):
            break
        time.sleep(0.01)
    else:
        pytest.fail("descendant survived normal context exit")


def test_concurrent_wait_and_terminate(watched_processes: list[subprocess.Popen[str]]) -> None:
    runner = LocalSubprocessRunner(NoopCommandTracer())
    with runner.popen(
        [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(60)"],
        manage_process_group=True,
    ) as handle:
        assert next(handle.stdout_lines) == "ready"
        entered = threading.Event()
        result: list[int] = []

        def wait() -> None:
            entered.set()
            result.append(handle.wait())

        thread = threading.Thread(target=wait, daemon=True)
        thread.start()
        assert entered.wait(2)
        handle.terminate()
        thread.join(2)
        assert not thread.is_alive()
        assert result == [handle.wait()]


@pytest.mark.parametrize("stage", ["signal", "grace", "reap"])
def test_interrupt_cleanup_and_retire_before_reap(
    watched_processes: list[subprocess.Popen[str]], monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    runner = LocalSubprocessRunner(NoopCommandTracer())
    with runner.popen(
        [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(60)"],
        manage_process_group=True,
    ) as handle:
        assert next(handle.stdout_lines) == "ready"
        proc = watched_processes[0]
        original_signal = os.killpg
        original_sleep = time.sleep
        original_wait = proc.wait
        signals: list[int] = []
        interrupted = False

        def killpg(pid: int, sig: int) -> None:
            nonlocal interrupted
            assert proc.returncode is None
            signals.append(sig)
            if stage == "signal" and not interrupted:
                interrupted = True
                raise KeyboardInterrupt()
            original_signal(pid, sig)

        def sleep(seconds: float) -> None:
            nonlocal interrupted
            if stage == "grace" and not interrupted:
                interrupted = True
                raise KeyboardInterrupt()
            original_sleep(seconds)

        def wait(*args, **kwargs):
            nonlocal interrupted
            assert signals[-1] == signal.SIGKILL
            if stage == "reap" and not interrupted:
                interrupted = True
                raise KeyboardInterrupt()
            return original_wait(*args, **kwargs)

        monkeypatch.setattr(os, "killpg", killpg)
        monkeypatch.setattr(time, "sleep", sleep)
        monkeypatch.setattr(proc, "wait", wait)
        with pytest.raises(KeyboardInterrupt):
            handle.terminate()
        count = len(signals)
        handle.terminate()
        assert len(signals) == count
        assert handle.wait() == proc.returncode
        monkeypatch.setattr(os, "killpg", original_signal)


def test_term_error_still_kills_and_reaps(
    watched_processes: list[subprocess.Popen[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = LocalSubprocessRunner(NoopCommandTracer())
    with runner.popen(
        [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(60)"],
        manage_process_group=True,
    ) as handle:
        assert next(handle.stdout_lines) == "ready"
        original = os.killpg
        signals: list[int] = []

        def killpg(pid: int, sig: int) -> None:
            signals.append(sig)
            if sig == signal.SIGTERM:
                raise PermissionError("injected TERM failure")
            original(pid, sig)

        monkeypatch.setattr(os, "killpg", killpg)
        with pytest.raises(PermissionError, match="injected TERM failure"):
            handle.terminate()
        assert signals == [signal.SIGTERM, signal.SIGKILL]
        assert handle.wait() == -signal.SIGKILL
        monkeypatch.setattr(os, "killpg", original)


@pytest.mark.parametrize("stage", ["popen", "constructor", "constructor_failure"])
def test_acquisition_interrupt_owns_group(
    watched_processes: list[subprocess.Popen[str]], monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    popen = subprocess.Popen
    constructor = local_subprocess_runner._StreamingProcess
    child_pid: list[int] = []

    def spawn(*args, **kwargs):
        proc = popen(*args, **kwargs)
        assert proc.stdout is not None
        child_pid.append(int(proc.stdout.readline()))
        if stage == "popen":
            os.kill(os.getpid(), signal.SIGINT)
        return proc

    def construct(proc, *, managed):
        if stage != "popen":
            os.kill(os.getpid(), signal.SIGINT)
        if stage == "constructor_failure":
            raise ValueError("construction failed")
        return constructor(proc, managed=managed)

    monkeypatch.setattr(local_subprocess_runner.subprocess, "Popen", spawn)
    monkeypatch.setattr(local_subprocess_runner, "_StreamingProcess", construct)
    script = """\
import subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import os,time; print(os.getpid(), flush=True); time.sleep(60)'], stdout=subprocess.PIPE, text=True)
print(child.stdout.readline().strip(), flush=True)
time.sleep(60)
"""
    with (
        pytest.raises(KeyboardInterrupt),
        LocalSubprocessRunner(NoopCommandTracer()).popen([sys.executable, "-c", script], manage_process_group=True),
    ):
        pytest.fail("interrupt must precede yield")
    assert watched_processes[0].returncode is not None
    deadline = time.monotonic() + 3
    while process_running(child_pid[0]):
        assert time.monotonic() < deadline, "descendant survived acquisition interruption"
        time.sleep(0.01)
