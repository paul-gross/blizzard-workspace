from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from winter_cli.core.subprocess_runner import IStreamingProcess, ISubprocessRunner, SubprocessResult
from winter_cli.core.tracing import ITracePropagator

# The W3C trace-context variable a child process reads its parent span from.
TRACEPARENT_ENV = "TRACEPARENT"
# Its companion: vendor trace state travels with the trace context it belongs to.
TRACESTATE_ENV = "TRACESTATE"

# Environment a non-interactive child gets on top of its own: git fails a
# credential prompt instead of reading the terminal.
NON_INTERACTIVE_ENV: dict[str, str] = {"GIT_TERMINAL_PROMPT": "0"}


class _StreamingProcess:
    """Wraps `subprocess.Popen` so callers see only `stdout_lines` + `wait()`.

    Merged stdout+stderr; the runner always sets `stderr=STDOUT` because every
    consumer in winter wants interleaved output through a single reporter.
    """

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        self._proc = proc

    @property
    def stdout_lines(self) -> Iterator[str]:
        if self._proc.stdout is None:
            raise RuntimeError("stdout pipe is not open; _popen_cm always sets stdout=PIPE")
        for line in self._proc.stdout:
            yield line.rstrip("\n")

    def wait(self) -> int:
        return self._proc.wait()


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
    ) -> Iterator[IStreamingProcess]:
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
        )
        try:
            yield _StreamingProcess(proc)
        finally:
            if proc.poll() is None:
                proc.wait()

    def popen(
        self,
        cmd: list[str] | str,
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        shell: bool = False,
        merge_stderr: bool = True,
    ) -> AbstractContextManager[IStreamingProcess]:
        return self._popen_cm(cmd, cwd, env, shell, merge_stderr)


def _conforms_local_subprocess_runner(x: LocalSubprocessRunner) -> ISubprocessRunner:
    return x
