from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from tests.conftest import FakeStreamingProcess, FakeSubprocessRunner
from winter_cli.core.internal.local_subprocess_runner import LocalSubprocessRunner
from winter_cli.core.internal.noop_command_tracer import NoopCommandTracer
from winter_cli.core.subprocess_runner import SubprocessResult
from winter_cli.modules.workspace.internal.repo_error_factory import RepoErrorFactory
from winter_cli.modules.workspace.internal.subprocess_nested_workspace_runner import SubprocessNestedWorkspaceRunner
from winter_cli.modules.workspace.models import RepoError

ROOT = Path("/ws/alpha/lab")
STATUS = "winter ws status --json"
ENV = {"PATH": "/usr/bin", "WINTER_NESTED_CHAIN": "/ws"}


def _runner(subprocess: FakeSubprocessRunner) -> SubprocessNestedWorkspaceRunner:
    return SubprocessNestedWorkspaceRunner(subprocess_runner=subprocess, error_factory=RepoErrorFactory())


# ── status_json ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("returncode", [0, 1])
def test_status_json_runs_in_the_nested_root_with_the_given_env_and_returns_stdout(returncode: int) -> None:
    subprocess = FakeSubprocessRunner(
        run_responses={STATUS: SubprocessResult(returncode=returncode, stdout='{"x": 1}', stderr="")}
    )

    stdout = _runner(subprocess).status_json(ROOT, ENV)

    assert stdout == '{"x": 1}'
    assert subprocess.run_calls == [(["winter", "ws", "status", "--json"], ROOT)]
    assert subprocess.run_envs == [ENV]


@pytest.mark.parametrize(
    "result",
    [
        SubprocessResult(returncode=2, stdout="", stderr="Error: boom"),
        SubprocessResult(returncode=-1, stdout="", stderr="No such file"),
    ],
)
def test_status_json_raises_when_the_status_call_fails(result: SubprocessResult) -> None:
    subprocess = FakeSubprocessRunner(run_responses={STATUS: result})

    with pytest.raises(RepoError, match="status failed") as excinfo:
        _runner(subprocess).status_json(ROOT, ENV)

    assert excinfo.value.exit_code == result.returncode
    assert excinfo.value.cwd == str(ROOT)
    assert excinfo.value.stderr == result.stderr


# ── run ───────────────────────────────────────────────────────────────────────


def test_run_streams_the_command_in_the_nested_root_with_the_given_env_and_returns_its_exit_code() -> None:
    subprocess = FakeSubprocessRunner(popen_responses={"winter ws destroy n1 --force": (["→ n1", "done"], 3)})
    lines: list[str] = []

    returncode = _runner(subprocess).run(ROOT, ["ws", "destroy", "n1", "--force"], ENV, lines.append)

    assert returncode == 3
    assert lines == ["→ n1", "done"]
    assert subprocess.popen_calls == [(["winter", "ws", "destroy", "n1", "--force"], ROOT)]
    assert subprocess.popen_envs == [ENV]


class _UnstartableSubprocessRunner(FakeSubprocessRunner):
    @contextmanager
    def popen(self, cmd: Any, **kwargs: Any) -> Iterator[FakeStreamingProcess]:
        raise FileNotFoundError(2, "No such file or directory", "winter")
        yield  # pragma: no cover


def test_a_process_that_cannot_start_raises_repo_error() -> None:
    with pytest.raises(RepoError, match="could not start"):
        _runner(_UnstartableSubprocessRunner()).run(ROOT, ["ws", "init"], ENV, lambda _line: None)


# ── a real process ────────────────────────────────────────────────────────────

_STUB = dedent(
    """
    import os, sys
    args = sys.argv[1:]
    if args == ["ws", "status", "--json"]:
        print('{"cwd": "' + os.getcwd() + '", "chain": "' + os.environ.get("WINTER_NESTED_CHAIN", "") + '"}')
        sys.exit(1)
    print("ran " + " ".join(args))
    print("cwd " + os.getcwd())
    sys.exit(0)
    """
)


def test_a_real_child_process_reads_status_and_streams_a_command(tmp_path: Path) -> None:
    root = tmp_path / "lab"
    root.mkdir()
    stub = tmp_path / "stub_winter.py"
    stub.write_text(_STUB)
    runner = SubprocessNestedWorkspaceRunner(
        subprocess_runner=LocalSubprocessRunner(NoopCommandTracer()),
        error_factory=RepoErrorFactory(),
        command=(sys.executable, str(stub)),
    )
    lines: list[str] = []

    stdout = runner.status_json(root, {"WINTER_NESTED_CHAIN": "/outer"})
    returncode = runner.run(root, ["ws", "init"], {}, lines.append)

    assert stdout.strip() == f'{{"cwd": "{root.resolve()}", "chain": "/outer"}}'
    assert returncode == 0
    assert lines == ["ran ws init", f"cwd {root.resolve()}"]
