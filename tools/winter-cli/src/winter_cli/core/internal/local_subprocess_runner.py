from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, suppress
from pathlib import Path
from types import FrameType

from winter_cli.core.subprocess_runner import IStreamingProcess, ISubprocessRunner, SubprocessResult
from winter_cli.core.tracing import ITracePropagator

# The W3C trace-context variable a child process reads its parent span from.
TRACEPARENT_ENV = "TRACEPARENT"
# Its companion: vendor trace state travels with the trace context it belongs to.
TRACESTATE_ENV = "TRACESTATE"

# Environment a non-interactive child gets on top of its own: git fails a
# credential prompt instead of reading the terminal.
NON_INTERACTIVE_ENV: dict[str, str] = {"GIT_TERMINAL_PROMPT": "0"}


@contextmanager
def _defer_acquisition_sigint(enabled: bool) -> Iterator[None]:
    """Keep a newly spawned group owned until its cleanup is registered."""
    if not enabled or threading.current_thread() is not threading.main_thread():
        yield
        return
    original = signal.getsignal(signal.SIGINT)
    pending: tuple[int, FrameType | None] | None = None

    def defer(signum: int, frame: FrameType | None) -> None:
        nonlocal pending
        pending = (signum, frame)

    signal.signal(signal.SIGINT, defer)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, original)
        if pending is not None:
            if callable(original):
                original(*pending)
            elif original == signal.SIG_DFL:
                signal.raise_signal(signal.SIGINT)


def _cleanup_unwrapped_group(proc: subprocess.Popen[str]) -> None:
    """Reap a group whose streaming handle could not be constructed."""
    deferred: BaseException | None = None
    for action in (lambda: os.killpg(proc.pid, signal.SIGKILL), proc.wait):
        while True:
            try:
                action()
                break
            except ProcessLookupError:
                break
            except (KeyboardInterrupt, SystemExit) as exc:
                if deferred is None:
                    deferred = exc
    if deferred is not None:
        raise deferred


class _StreamingProcess:
    """Wraps `subprocess.Popen` so callers see only `stdout_lines` + `wait()`.

    Merged stdout+stderr; the runner always sets `stderr=STDOUT` because every
    consumer in winter wants interleaved output through a single reporter.
    """

    def __init__(self, proc: subprocess.Popen[str], *, managed: bool) -> None:
        self._proc = proc
        self._managed = managed
        self._termination_lock = threading.Lock()
        self._terminated = False
        self._group_active = managed
        self._returncode: int | None = None

    @property
    def stdout_lines(self) -> Iterator[str]:
        if self._proc.stdout is None:
            raise RuntimeError("stdout pipe is not open; _popen_cm always sets stdout=PIPE")
        for line in self._proc.stdout:
            yield line.rstrip("\n")

    def wait(self) -> int:
        if not self._managed:
            return self._proc.wait()
        while True:
            with self._termination_lock:
                if self._returncode is not None:
                    return self._returncode
                if not self._group_active:
                    self._returncode = self._proc.wait()
                    return self._returncode
                result = os.waitid(os.P_PID, self._proc.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
                if result is not None:
                    self._returncode = result.si_status if result.si_code == os.CLD_EXITED else -result.si_status
                    return self._returncode
            time.sleep(0.01)

    def terminate(self) -> None:
        if self._managed:
            self._terminate_group()
            return
        with self._termination_lock:
            if self._terminated:
                return
            if self._proc.poll() is None:
                self._proc.terminate()
            with suppress(subprocess.TimeoutExpired):
                self._proc.wait(timeout=0.5)
            if self._proc.poll() is None:
                self._proc.kill()
            self._proc.wait()
            self._terminated = True

    def _terminate_group(self) -> None:
        deferred: BaseException | None = None

        def attempt(action: Callable[[], object]) -> None:
            nonlocal deferred
            # Interruptions can land at any blocking operation. Retry those;
            # other errors are retained while the remaining cleanup proceeds.
            while True:
                try:
                    action()
                    return
                except (KeyboardInterrupt, SystemExit) as exc:
                    if deferred is None:
                        deferred = exc
                except Exception as exc:
                    if deferred is None or isinstance(deferred, (KeyboardInterrupt, SystemExit)):
                        deferred = exc
                    return

        attempt(self._termination_lock.acquire)
        try:
            if not self._terminated:
                if self._group_active:
                    deadline = time.monotonic() + 0.5
                    attempt(lambda: self._signal_group(signal.SIGTERM))

                    def grace() -> None:
                        remaining = deadline - time.monotonic()
                        if remaining > 0:
                            time.sleep(remaining)

                    attempt(grace)
                    attempt(lambda: self._signal_group(signal.SIGKILL))
                    # Never signal this PID again once reaping can begin.
                    self._group_active = False

                def reap() -> None:
                    code = self._proc.wait()
                    if self._returncode is None:
                        self._returncode = code
                    self._terminated = True

                attempt(reap)
        finally:
            self._termination_lock.release()
        if deferred is not None:
            raise deferred

    def _signal_group(self, sig: signal.Signals) -> None:
        with suppress(ProcessLookupError):
            os.killpg(self._proc.pid, sig)


class LocalSubprocessRunner:
    """`subprocess` adapter for ISubprocessRunner.

    All `subprocess.run` / `subprocess.Popen` usage is confined here.
    Subprocesses inherit the parent environment unless `env` is supplied;
    callers wanting an empty env pass `env={}`.

    `non_interactive=True` makes every child unable to prompt: stdin comes from
    `/dev/null` and `GIT_TERMINAL_PROMPT=0` is added to its environment, so a
    credential or confirmation prompt fails fast instead of waiting on a
    terminal that no one is watching (an in-process run inside the dashboard).

    Every child's environment carries the active trace context: the propagator writes
    `TRACEPARENT` into it when tracing is on, and with tracing off the environment passes
    through exactly as supplied (an absent `env` stays absent). `TRACESTATE` travels with
    `TRACEPARENT`. A detached `call` instead has both removed, tracing on or off.
    """

    def __init__(self, trace_propagator: ITracePropagator, *, non_interactive: bool = False) -> None:
        self._trace_propagator = trace_propagator
        self._non_interactive = non_interactive

    def _stdin(self) -> int | None:
        return subprocess.DEVNULL if self._non_interactive else None

    def _env(self, env: Mapping[str, str] | None, *, detach_trace: bool = False) -> dict[str, str] | None:
        child = dict(env) if env is not None else None
        if detach_trace:
            child = dict(os.environ if child is None else child)
            child.pop(TRACEPARENT_ENV, None)
            child.pop(TRACESTATE_ENV, None)
        else:
            carrier: dict[str, str] = {}
            self._trace_propagator.inject(carrier)
            if TRACEPARENT_ENV in carrier:
                child = dict(os.environ if child is None else child)
                child[TRACEPARENT_ENV] = carrier[TRACEPARENT_ENV]
                if TRACESTATE_ENV in carrier:
                    child[TRACESTATE_ENV] = carrier[TRACESTATE_ENV]
                else:
                    child.pop(TRACESTATE_ENV, None)
        if self._non_interactive:
            return {**(os.environ if child is None else child), **NON_INTERACTIVE_ENV}
        return child

    def run(
        self,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> SubprocessResult:
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(cwd) if cwd is not None else None,
                env=self._env(env),
                stdin=self._stdin(),
                capture_output=True,
                text=True,
                # Decode leniently: a subprocess may emit bytes that aren't
                # valid UTF-8 (git printing a filename from a vendored tree,
                # say). Strict decoding would raise `UnicodeDecodeError` out
                # of `run`, which `SubprocessResult` promises never happens —
                # every caller inspects `returncode` and none is prepared for
                # an exception from a successful command. An undecodable byte
                # becomes U+FFFD, so a caller matching output against a path
                # it holds simply fails to match rather than crashing.
                errors="replace",
                check=False,
            )
        except OSError as exc:
            return SubprocessResult(returncode=-1, stdout="", stderr=str(exc))
        return SubprocessResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    def call(
        self,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        detach_trace: bool = False,
    ) -> int:
        """Run a process with inherited stdio, returning only the exit code.

        No `capture_output` and no stream redirection: stdin/stdout/stderr are
        inherited from this process (stdin is `/dev/null` when non-interactive), so the child writes straight to the
        terminal (TTY, colors, and stdout/stderr separation preserved). An
        exec failure (missing or non-executable file) surfaces as `126`, the
        shell convention for "command found but not executable". `detach_trace`
        removes `TRACEPARENT` from the child's environment.
        """
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(cwd) if cwd is not None else None,
                env=self._env(env, detach_trace=detach_trace),
                stdin=self._stdin(),
                check=False,
            )
        except OSError:
            return 126
        return completed.returncode

    @contextmanager
    def _popen_cm(
        self,
        cmd: list[str] | str,
        cwd: Path | None,
        env: Mapping[str, str] | None,
        shell: bool,
        merge_stderr: bool,
        manage_process_group: bool,
    ) -> Iterator[IStreamingProcess]:
        proc: subprocess.Popen[str] | None = None
        handle: _StreamingProcess | None = None
        try:
            with _defer_acquisition_sigint(manage_process_group):
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(cwd) if cwd is not None else None,
                    env=self._env(env),
                    shell=shell,
                    stdin=self._stdin(),
                    stdout=subprocess.PIPE,
                    # When merge_stderr=True (default), merge stderr into stdout so
                    # callers see a single interleaved stream (init/destroy hook flow).
                    # When merge_stderr=False, leave stderr=None so it inherits the
                    # parent's stderr fd — the orchestrator's diagnostics reach the
                    # terminal without corrupting the NDJSON stdout (logs flow).
                    stderr=subprocess.STDOUT if merge_stderr else None,
                    text=True,
                    bufsize=1,
                    start_new_session=manage_process_group,
                )
                handle = _StreamingProcess(proc, managed=manage_process_group)
            yield handle
        finally:
            if proc is not None:
                try:
                    if manage_process_group:
                        if handle is None:
                            _cleanup_unwrapped_group(proc)
                        else:
                            handle.terminate()
                    elif proc.poll() is None:
                        proc.wait()
                finally:
                    if proc.stdout is not None:
                        proc.stdout.close()

    def popen(
        self,
        cmd: list[str] | str,
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        shell: bool = False,
        merge_stderr: bool = True,
        manage_process_group: bool = False,
    ) -> AbstractContextManager[IStreamingProcess]:
        return self._popen_cm(cmd, cwd, env, shell, merge_stderr, manage_process_group)


def _conforms_local_subprocess_runner(x: LocalSubprocessRunner) -> ISubprocessRunner:
    return x


def _conforms_streaming_process(x: _StreamingProcess) -> IStreamingProcess:
    return x
