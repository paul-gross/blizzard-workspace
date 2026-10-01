"""Trace-context propagation through `LocalSubprocessRunner`, against real child processes.

A child writes the `TRACEPARENT` it sees to a file, so each test reads what a real child
received rather than what the runner meant to pass.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from opentelemetry import trace

from winter_cli.core.internal import local_subprocess_runner
from winter_cli.core.internal.local_subprocess_runner import LocalSubprocessRunner
from winter_cli.core.internal.noop_command_tracer import NoopCommandTracer
from winter_cli.core.internal.otel_command_tracer import OtelCommandTracer
from winter_cli.core.tracing import ITracePropagator, TracingSettings

_CALLER_TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
_UNSET = "<unset>"

_WRITE_TRACE_CONTEXT = (
    "import os; open(os.environ['TRACE_OUT'], 'w')"
    ".write(os.environ.get('TRACEPARENT', '<unset>') + '|' + os.environ.get('TRACESTATE', '<unset>'))"
)
_CALLER_TRACESTATE = "caller=state"

_WRITE_TRACEPARENT = "import os; open(os.environ['TRACE_OUT'], 'w').write(os.environ.get('TRACEPARENT', '<unset>'))"


class _FixedPropagator:
    """A propagator that always injects one fixed `TRACEPARENT`: tracing on, without the SDK."""

    traceparent = "00-11111111111111111111111111111111-2222222222222222-01"

    def inject(self, env: dict[str, str]) -> None:
        env["TRACEPARENT"] = self.traceparent


class _FixedPropagatorWithState:
    """A propagator that injects a fixed `TRACEPARENT` and `TRACESTATE` pair."""

    tracestate = "vendor=state"

    def inject(self, env: dict[str, str]) -> None:
        env["TRACEPARENT"] = _FixedPropagator.traceparent
        env["TRACESTATE"] = self.tracestate


@pytest.fixture
def out_file(tmp_path: Path) -> Path:
    return tmp_path / "traceparent.out"


@pytest.fixture
def command_span_tracer(monkeypatch: pytest.MonkeyPatch) -> Iterator[OtelCommandTracer]:
    """A real OpenTelemetry tracer with its command span open (never exported)."""
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace._TRACER_PROVIDER_SET_ONCE, "_done", False)
    monkeypatch.delenv("TRACEPARENT", raising=False)
    tracer = OtelCommandTracer(TracingSettings(otlp_endpoint="http://127.0.0.1:9"))
    tracer.start_command("winter test")
    yield tracer
    tracer.end_command(None)


def _command_span_ids() -> tuple[str, str]:
    """The (trace id, span id) of the open command span, as they appear in a `traceparent`."""
    context = trace.get_current_span().get_span_context()
    return trace.format_trace_id(context.trace_id), trace.format_span_id(context.span_id)


def _ids_seen_by_child(out_file: Path) -> tuple[str, str]:
    """The (trace id, span id) of the `TRACEPARENT` a child wrote, `<unset>` parts if it saw none."""
    _version, trace_id, span_id, _flags = out_file.read_text().split("-")
    return trace_id, span_id


def _child_env(out_file: Path, **extra: str) -> dict[str, str]:
    return {**os.environ, "TRACE_OUT": str(out_file), **extra}


def _seen_by_run(runner: LocalSubprocessRunner, env: dict[str, str] | None) -> None:
    assert runner.run([sys.executable, "-c", _WRITE_TRACEPARENT], env=env).returncode == 0


def _seen_by_call(runner: LocalSubprocessRunner, env: dict[str, str] | None) -> None:
    assert runner.call([sys.executable, "-c", _WRITE_TRACEPARENT], env=env) == 0


def _seen_by_popen(runner: LocalSubprocessRunner, env: dict[str, str] | None) -> None:
    with runner.popen([sys.executable, "-c", _WRITE_TRACEPARENT], env=env) as proc:
        assert proc.wait() == 0


_SHAPES = {"run": _seen_by_run, "call": _seen_by_call, "popen": _seen_by_popen}


# ── tracing on: the active span's context reaches every child ────────────────


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_child_gets_the_command_spans_traceparent_over_a_stale_one(
    shape: str, command_span_tracer: OtelCommandTracer, out_file: Path
) -> None:
    runner = LocalSubprocessRunner(command_span_tracer)

    _SHAPES[shape](runner, _child_env(out_file, TRACEPARENT=_CALLER_TRACEPARENT))

    assert _ids_seen_by_child(out_file) == _command_span_ids()


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_child_with_no_env_inherits_the_command_spans_traceparent_not_the_callers(
    shape: str,
    command_span_tracer: OtelCommandTracer,
    out_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACE_OUT", str(out_file))
    monkeypatch.setenv("TRACEPARENT", _CALLER_TRACEPARENT)
    runner = LocalSubprocessRunner(command_span_tracer)

    _SHAPES[shape](runner, None)

    assert _ids_seen_by_child(out_file) == _command_span_ids()


def test_call_from_a_worker_thread_carries_the_command_spans_traceparent(
    command_span_tracer: OtelCommandTracer, out_file: Path
) -> None:
    runner = LocalSubprocessRunner(command_span_tracer)
    expected = _command_span_ids()
    seen_in_thread: list[bool] = []

    def worker() -> None:
        # OpenTelemetry context does not cross threads: this thread carries no span.
        seen_in_thread.append(trace.get_current_span().get_span_context().is_valid)
        _seen_by_call(runner, _child_env(out_file))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert seen_in_thread == [False]
    assert _ids_seen_by_child(out_file) == expected


# ── tracing off: the environment passes through exactly as today ─────────────


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_with_tracing_off_the_callers_traceparent_reaches_the_child_unchanged(shape: str, out_file: Path) -> None:
    runner = LocalSubprocessRunner(NoopCommandTracer())

    _SHAPES[shape](runner, _child_env(out_file, TRACEPARENT=_CALLER_TRACEPARENT))

    assert out_file.read_text() == _CALLER_TRACEPARENT


def test_with_tracing_off_an_absent_env_stays_absent_and_a_given_env_is_copied_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_subprocess = MagicMock()
    fake_subprocess.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(local_subprocess_runner, "subprocess", fake_subprocess)
    runner = LocalSubprocessRunner(NoopCommandTracer())
    given = {"K": "V", "TRACEPARENT": _CALLER_TRACEPARENT}

    runner.run(["x"])
    runner.call(["x"])
    runner.run(["x"], env=given)
    runner.call(["x"], env=given)

    envs = [call.kwargs["env"] for call in fake_subprocess.run.call_args_list]
    assert envs == [None, None, given, given]
    assert envs[2] is not given


def test_a_non_interactive_runner_with_tracing_off_keeps_the_callers_traceparent(
    out_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRACE_OUT", str(out_file))
    monkeypatch.setenv("TRACEPARENT", _CALLER_TRACEPARENT)

    LocalSubprocessRunner(NoopCommandTracer(), non_interactive=True).run([sys.executable, "-c", _WRITE_TRACEPARENT])

    assert out_file.read_text() == _CALLER_TRACEPARENT


# ── a detached call strips TRACEPARENT, tracing on or off ────────────────────


def _propagators() -> dict[str, ITracePropagator]:
    return {"tracing-off": NoopCommandTracer(), "tracing-on": _FixedPropagator()}


@pytest.mark.parametrize("tracing", ["tracing-off", "tracing-on"])
def test_detached_call_carries_no_traceparent_from_a_given_env(tracing: str, out_file: Path) -> None:
    runner = LocalSubprocessRunner(_propagators()[tracing])

    code = runner.call(
        [sys.executable, "-c", _WRITE_TRACEPARENT],
        env=_child_env(out_file, TRACEPARENT=_CALLER_TRACEPARENT),
        detach_trace=True,
    )

    assert code == 0
    assert out_file.read_text() == _UNSET


@pytest.mark.parametrize("tracing", ["tracing-off", "tracing-on"])
def test_detached_call_carries_no_traceparent_from_the_inherited_environment(
    tracing: str, out_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRACE_OUT", str(out_file))
    monkeypatch.setenv("TRACEPARENT", _CALLER_TRACEPARENT)
    runner = LocalSubprocessRunner(_propagators()[tracing])

    assert runner.call([sys.executable, "-c", _WRITE_TRACEPARENT], detach_trace=True) == 0

    assert out_file.read_text() == _UNSET


def test_detached_call_from_a_non_interactive_runner_carries_no_traceparent(
    out_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRACE_OUT", str(out_file))
    monkeypatch.setenv("TRACEPARENT", _CALLER_TRACEPARENT)
    runner = LocalSubprocessRunner(_FixedPropagator(), non_interactive=True)

    assert runner.call([sys.executable, "-c", _WRITE_TRACEPARENT], detach_trace=True) == 0

    assert out_file.read_text() == _UNSET


def test_detached_call_does_not_mutate_the_callers_env(out_file: Path) -> None:
    runner = LocalSubprocessRunner(NoopCommandTracer())
    env = _child_env(out_file, TRACEPARENT=_CALLER_TRACEPARENT)

    runner.call([sys.executable, "-c", _WRITE_TRACEPARENT], env=env, detach_trace=True)

    assert env["TRACEPARENT"] == _CALLER_TRACEPARENT


def test_a_call_that_is_not_detached_gets_the_propagated_traceparent(out_file: Path) -> None:
    runner = LocalSubprocessRunner(_FixedPropagator())

    _seen_by_call(runner, _child_env(out_file, TRACEPARENT=_CALLER_TRACEPARENT))

    assert out_file.read_text() == _FixedPropagator.traceparent


# ── TRACESTATE travels with TRACEPARENT ──────────────────────────────────────


def _trace_context_seen_by_call(
    runner: LocalSubprocessRunner, env: dict[str, str], *, detach_trace: bool = False
) -> str:
    assert runner.call([sys.executable, "-c", _WRITE_TRACE_CONTEXT], env=env, detach_trace=detach_trace) == 0
    return Path(env["TRACE_OUT"]).read_text()


def test_a_propagated_tracestate_replaces_the_callers_beside_the_propagated_traceparent(out_file: Path) -> None:
    runner = LocalSubprocessRunner(_FixedPropagatorWithState())
    env = _child_env(out_file, TRACEPARENT=_CALLER_TRACEPARENT, TRACESTATE=_CALLER_TRACESTATE)

    seen = _trace_context_seen_by_call(runner, env)

    assert seen == f"{_FixedPropagator.traceparent}|{_FixedPropagatorWithState.tracestate}"


def test_the_callers_tracestate_is_dropped_when_the_propagated_context_has_none(out_file: Path) -> None:
    runner = LocalSubprocessRunner(_FixedPropagator())
    env = _child_env(out_file, TRACEPARENT=_CALLER_TRACEPARENT, TRACESTATE=_CALLER_TRACESTATE)

    seen = _trace_context_seen_by_call(runner, env)

    assert seen == f"{_FixedPropagator.traceparent}|{_UNSET}"


def test_the_inherited_tracestate_is_dropped_when_the_propagated_context_has_none(
    out_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRACE_OUT", str(out_file))
    monkeypatch.setenv("TRACESTATE", _CALLER_TRACESTATE)
    runner = LocalSubprocessRunner(_FixedPropagator())

    assert runner.call([sys.executable, "-c", _WRITE_TRACE_CONTEXT]) == 0

    assert out_file.read_text() == f"{_FixedPropagator.traceparent}|{_UNSET}"


def test_with_tracing_off_the_callers_tracestate_reaches_the_child_unchanged(out_file: Path) -> None:
    runner = LocalSubprocessRunner(NoopCommandTracer())
    env = _child_env(out_file, TRACEPARENT=_CALLER_TRACEPARENT, TRACESTATE=_CALLER_TRACESTATE)

    seen = _trace_context_seen_by_call(runner, env)

    assert seen == f"{_CALLER_TRACEPARENT}|{_CALLER_TRACESTATE}"


@pytest.mark.parametrize("tracing", ["tracing-off", "tracing-on"])
def test_detached_call_carries_no_tracestate_from_a_given_env(tracing: str, out_file: Path) -> None:
    runner = LocalSubprocessRunner(_propagators()[tracing])
    env = _child_env(out_file, TRACEPARENT=_CALLER_TRACEPARENT, TRACESTATE=_CALLER_TRACESTATE)

    seen = _trace_context_seen_by_call(runner, env, detach_trace=True)

    assert seen == f"{_UNSET}|{_UNSET}"


@pytest.mark.parametrize("tracing", ["tracing-off", "tracing-on"])
def test_detached_call_carries_no_tracestate_from_the_inherited_environment(
    tracing: str, out_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRACE_OUT", str(out_file))
    monkeypatch.setenv("TRACEPARENT", _CALLER_TRACEPARENT)
    monkeypatch.setenv("TRACESTATE", _CALLER_TRACESTATE)
    runner = LocalSubprocessRunner(_propagators()[tracing])

    assert runner.call([sys.executable, "-c", _WRITE_TRACE_CONTEXT], detach_trace=True) == 0

    assert out_file.read_text() == f"{_UNSET}|{_UNSET}"
