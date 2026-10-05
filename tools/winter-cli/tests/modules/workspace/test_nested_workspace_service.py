from __future__ import annotations

import contextlib
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from tests.conftest import FakeFilesystem, FakeInitReporter, make_workspace_config
from tests.modules.workspace.conftest import (
    FakeNestedWorkspaceRunner,
    make_nested_service,
    nested_env,
    nested_status,
    nested_wt,
)
from winter_cli.modules.workspace.models import NestedEnvState, NestedWorkspaceState, ProjectRepository, RepoError
from winter_cli.modules.workspace.nested_env import NESTED_CHAIN_VAR

OUTER = Path("/ws")
ROOT = OUTER / "alpha" / "lab"
LAB = ProjectRepository(name="lab", main_path=Path("/ws/projects/lab"), main_branch="main", nested=True)
TWO_ENVS = NestedWorkspaceState(envs=(NestedEnvState(name="n1"), NestedEnvState(name="n2")))


def test_reconcile_runs_nested_init_and_streams_its_output_as_the_repo(init_reporter: FakeInitReporter) -> None:
    runner = FakeNestedWorkspaceRunner(init_lines=["→ projects/", "✓ projects/ done"])

    make_nested_service(runner).reconcile(LAB, ROOT, init_reporter)

    assert runner.status_calls == [ROOT]
    assert runner.calls == [("init", ROOT)]
    assert init_reporter.cmds_started == [("lab", "winter ws init")]
    assert init_reporter.cmd_output == [("lab", "→ projects/"), ("lab", "✓ projects/ done")]
    assert init_reporter.cmds_completed == [("lab", "winter ws init", 0)]


def test_reconcile_raises_when_nested_init_exits_non_zero(init_reporter: FakeInitReporter) -> None:
    runner = FakeNestedWorkspaceRunner(init_returncode=1)

    with pytest.raises(RepoError, match="exited with code 1"):
        make_nested_service(runner).reconcile(LAB, ROOT, init_reporter)

    assert init_reporter.cmds_completed == [("lab", "winter ws init", 1)]


def test_state_reads_the_nested_status_json() -> None:
    runner = FakeNestedWorkspaceRunner(statuses={ROOT: nested_status(ROOT, nested_env("n1"), nested_env("n2"))})

    assert make_nested_service(runner).state(ROOT) == TWO_ENVS
    assert runner.status_calls == [ROOT]


def test_destroy_envs_destroys_each_env_and_passes_the_flags_through(init_reporter: FakeInitReporter) -> None:
    runner = FakeNestedWorkspaceRunner()

    ok = make_nested_service(runner).destroy_envs(
        LAB, ROOT, TWO_ENVS, force=True, strict=True, provision_teardown=False, reporter=init_reporter
    )

    assert ok is True
    assert runner.calls == [
        ("destroy_env", ROOT, "n1", True, True, False),
        ("destroy_env", ROOT, "n2", True, True, False),
    ]
    destroyed = [(a[1], a[3]) for a in init_reporter.actions if a[2] == "nested_env_destroyed"]
    assert destroyed == [(str(ROOT / "n1"), "n1"), (str(ROOT / "n2"), "n2")]
    assert ("lab", "destroying n1") in init_reporter.cmd_output


def test_destroy_envs_attempts_every_env_and_reports_failure(init_reporter: FakeInitReporter) -> None:
    runner = FakeNestedWorkspaceRunner(
        destroy_returncodes={"n1": 1}, destroy_errors={"n2": RepoError("winter resolved another workspace")}
    )
    three = NestedWorkspaceState(envs=(*TWO_ENVS.envs, NestedEnvState(name="n3")))

    ok = make_nested_service(runner).destroy_envs(
        LAB, ROOT, three, force=False, strict=False, provision_teardown=True, reporter=init_reporter
    )

    assert ok is False
    assert [c[2] for c in runner.calls] == ["n1", "n2", "n3"]
    assert [a[3] for a in init_reporter.actions if a[2] == "nested_env_destroyed"] == ["n3"]
    errors = [e for repo, e in init_reporter.errors if repo == "lab"]
    assert any("winter ws destroy n1" in e and "code 1" in e for e in errors)
    assert any("n2" in e and "another workspace" in e for e in errors)


def test_nested_workspace_state_counts_envs_dirtiness_and_unpushed_work() -> None:
    clean = NestedWorkspaceState(envs=(NestedEnvState(name="n1"),))
    dirty = NestedWorkspaceState(envs=(NestedEnvState(name="n1"), NestedEnvState(name="n2", dirty=("app",))))
    unpushed = NestedWorkspaceState(
        envs=(NestedEnvState(name="n1", unpushed=("app", "web")),), checkouts=("projects/app",)
    )

    assert (clean.env_count, clean.dirty, clean.unpushed) == (1, False, ())
    assert (dirty.env_count, dirty.dirty) == (2, True)
    assert unpushed.unpushed == ("n1/app", "n1/web", "projects/app")
    assert NestedWorkspaceState(envs=()).dirty is False


# ── guards: preflight, verification, chain ────────────────────────────────────


def test_a_root_path_mismatch_raises_and_runs_no_nested_command(init_reporter: FakeInitReporter) -> None:
    runner = FakeNestedWorkspaceRunner(statuses={ROOT: nested_status(OUTER, nested_env("n1"))})
    service = make_nested_service(runner)

    for call in (
        lambda: service.state(ROOT),
        lambda: service.reconcile(LAB, ROOT, init_reporter),
    ):
        with pytest.raises(RepoError, match="resolved workspace '/ws', not the nested workspace"):
            call()
    destroyed = service.destroy_envs(
        LAB, ROOT, TWO_ENVS, force=True, strict=False, provision_teardown=True, reporter=init_reporter
    )

    assert destroyed is False
    assert runner.calls == []
    assert sum("not the nested workspace" in msg for _, msg in init_reporter.errors) == 2


def test_a_missing_root_path_raises() -> None:
    runner = FakeNestedWorkspaceRunner(statuses={ROOT: {"schema_version": 1, "workspace": {}, "environments": []}})

    with pytest.raises(RepoError, match="not the nested workspace"):
        make_nested_service(runner).state(ROOT)


def test_a_root_without_a_winter_config_raises_before_running_anything(init_reporter: FakeInitReporter) -> None:
    runner = FakeNestedWorkspaceRunner()

    with pytest.raises(RepoError, match=r"no \.winter/config\.toml"):
        make_nested_service(runner, fs=FakeFilesystem()).reconcile(LAB, ROOT, init_reporter)

    assert runner.status_calls == []
    assert runner.calls == []


@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        ("not json", "is not JSON"),
        (json.dumps({**nested_status(ROOT), "schema_version": 2}), "schema_version 2"),
        ("[]", "the status document is list"),
        (json.dumps({**nested_status(ROOT), "environments": [{"name": "n1"}]}), r"malformed: .*worktrees"),
        (json.dumps(nested_status(ROOT, nested_env("n1", {**nested_wt(), "dirty": None}))), r"malformed: .*dirty"),
    ],
)
def test_an_unusable_status_raises_repo_error(stdout: str, message: str) -> None:
    runner = FakeNestedWorkspaceRunner(statuses={ROOT: stdout})

    with pytest.raises(RepoError, match=message) as excinfo:
        make_nested_service(runner).state(ROOT)

    assert excinfo.value.cwd == str(ROOT)
    assert "winter ws status --json" in str(excinfo.value)


def test_a_failed_status_call_propagates() -> None:
    runner = FakeNestedWorkspaceRunner(statuses={ROOT: RepoError("nested workspace status failed")})

    with pytest.raises(RepoError, match="status failed"):
        make_nested_service(runner).state(ROOT)


def test_a_root_is_verified_once_and_reused_by_every_later_call(init_reporter: FakeInitReporter) -> None:
    runner = FakeNestedWorkspaceRunner(statuses={ROOT: nested_status(ROOT, nested_env("n1"), nested_env("n2"))})
    service = make_nested_service(runner)

    state = service.state(ROOT)
    service.destroy_envs(LAB, ROOT, state, force=False, strict=False, provision_teardown=True, reporter=init_reporter)
    service.reconcile(LAB, ROOT, init_reporter)

    assert runner.status_calls == [ROOT]
    assert [c[0] for c in runner.calls] == ["destroy_env", "destroy_env", "init"]


def test_every_child_runs_with_the_scrubbed_env_and_the_chain_extended_by_this_workspace_root(
    init_reporter: FakeInitReporter,
) -> None:
    environ = {
        "WINTER_INVOCATION_CWD": "/outer",
        "WINTER_PORT_BASE": "4020",
        "WINTER_LOG_LEVEL": "debug",
        "VIRTUAL_ENV": "/outer/.venv",
        "PATH": "/outer/.venv/bin:/usr/bin",
        NESTED_CHAIN_VAR: "/up",
    }
    runner = FakeNestedWorkspaceRunner()

    make_nested_service(runner, environ=environ).reconcile(LAB, ROOT, init_reporter)

    assert len(runner.envs) == 2
    for env in runner.envs:
        assert env == {"WINTER_LOG_LEVEL": "debug", "PATH": "/usr/bin", NESTED_CHAIN_VAR: f"/up:{OUTER}"}


def test_a_process_whose_own_root_is_on_the_chain_refuses_every_nested_call(init_reporter: FakeInitReporter) -> None:
    runner = FakeNestedWorkspaceRunner()
    service = make_nested_service(runner, environ={NESTED_CHAIN_VAR: str(OUTER)})

    with pytest.raises(RepoError, match="which an enclosing nested call already runs in"):
        service.state(ROOT)
    with pytest.raises(RepoError, match="which an enclosing nested call already runs in"):
        service.reconcile(LAB, ROOT, init_reporter)

    assert runner.status_calls == []
    assert runner.calls == []


def test_a_root_already_on_the_chain_is_refused_without_running_anything() -> None:
    runner = FakeNestedWorkspaceRunner()
    service = make_nested_service(runner, environ={NESTED_CHAIN_VAR: f"/elsewhere:{ROOT}"})

    with pytest.raises(RepoError, match="already being run by an enclosing winter call"):
        service.state(ROOT)

    assert runner.status_calls == []


def test_a_legitimate_nested_child_still_reads_its_own_nested_workspaces() -> None:
    deep = ROOT / "n1" / "deep"
    runner = FakeNestedWorkspaceRunner()
    child = make_nested_service(
        runner, config=make_workspace_config(workspace_root=ROOT), environ={NESTED_CHAIN_VAR: str(OUTER)}
    )

    assert child.state(deep) == NestedWorkspaceState(envs=())
    assert runner.envs[0][NESTED_CHAIN_VAR] == f"{OUTER}:{ROOT}"


SIBLINGS = tuple(OUTER / env / "lab" for env in ("alpha", "beta", "gamma"))


class _MisResolvingWinter(FakeNestedWorkspaceRunner):
    """Every `winter` started in a nested root resolves the outer workspace instead.

    Each status call stands in for one spawned child process: a winter at the
    outer root, running with the env it was started with, whose own
    `ws status` reads every sibling nested worktree before reporting the outer
    root.
    """

    def status_json(self, root: Path, env: Mapping[str, str]) -> str:
        super().status_json(root, env)
        child = make_nested_service(self, environ=env)
        for sibling in SIBLINGS:
            with contextlib.suppress(RepoError):
                child.state(sibling)
        return json.dumps(nested_status(OUTER))


def test_a_mis_resolved_child_stops_at_once_instead_of_fanning_out_to_every_sibling() -> None:
    runner = _MisResolvingWinter()
    parent = make_nested_service(runner)

    for root in SIBLINGS:
        with pytest.raises(RepoError, match="not the nested workspace"):
            parent.state(root)

    # One spawn per sibling: each child finds its own root (the outer one) on
    # the chain and refuses its own nested reads. Chaining target roots only,
    # the children would spawn 3 + 3x2 + 3x2x1 = 15 times.
    assert runner.status_calls == list(SIBLINGS)
