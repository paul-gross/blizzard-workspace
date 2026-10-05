from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.modules.workspace.conftest import nested_env, nested_status, nested_wt
from winter_cli.modules.workspace.models import NestedEnvState, NestedWorkspaceState
from winter_cli.modules.workspace.nested_status import NestedStatusShapeError, parse_state, reported_root

ROOT = Path("/ws/alpha/lab")


def _inner(*, dirty: bool = False, unpushed: bool = False, error: str | None = None) -> dict[str, Any]:
    """The `nested` object a worktree of a nested-in-nested repo carries."""
    return {"env_count": None if error else 1, "dirty": dirty, "unpushed": unpushed, "error": error}


def test_reported_root_reads_the_workspace_root_path() -> None:
    assert reported_root(nested_status(ROOT)) == str(ROOT)
    assert reported_root({"schema_version": 1, "workspace": {}}) is None


@pytest.mark.parametrize("doc", [{"schema_version": 2}, [], {"workspace": {"root_path": "/ws"}}])
def test_reported_root_refuses_anything_but_schema_v1(doc: Any) -> None:
    with pytest.raises(NestedStatusShapeError, match=r"schema_version|expected an object"):
        reported_root(doc)


def test_parse_state_names_dirty_worktrees_per_env() -> None:
    doc = nested_status(
        ROOT,
        nested_env("n1", nested_wt("app"), nested_wt("web")),
        nested_env("n2", nested_wt("app", dirty=2), nested_wt("web", dirty=3)),
    )

    state = parse_state(doc)

    assert state == NestedWorkspaceState(
        envs=(NestedEnvState(name="n1"), NestedEnvState(name="n2", dirty=("app", "web")))
    )
    assert (state.env_count, state.dirty, state.unpushed) == (2, True, ())


@pytest.mark.parametrize(
    ("wt", "named"),
    [
        (nested_wt("app", ahead=1), "n1/app (unpushed branch)"),
        ({**nested_wt("app", ahead=1, upstream="origin/f"), "tracking_ref_present": False}, "n1/app (unpushed branch)"),
        (nested_wt("app", ahead=3, tracking_ahead=2, upstream="origin/f"), "n1/app (unpushed commits)"),
        (nested_wt("app", tracking_ahead=2, upstream="origin/main"), "n1/app (unpushed commits)"),
    ],
    ids=["no-upstream", "upstream-ref-missing", "ahead-of-upstream", "ahead-of-upstream-at-main"],
)
def test_parse_state_names_env_worktrees_with_unpushed_commits(wt: dict[str, Any], named: str) -> None:
    state = parse_state(nested_status(ROOT, nested_env("n1", wt)))

    assert state.dirty is False
    assert state.unpushed == (named,)


def test_parse_state_a_branch_pushed_to_its_upstream_but_not_merged_is_not_unpushed() -> None:
    pushed = nested_wt("app", ahead=4, tracking_ahead=0, upstream="origin/feature/x")

    state = parse_state(nested_status(ROOT, nested_env("n1", pushed)))

    assert state.unpushed == ()


def test_parse_state_names_source_checkouts_and_standalones_holding_local_work() -> None:
    doc = nested_status(
        ROOT,
        projects=[
            {"repo": "app", "dirty": 0, "ahead_origin": 1},
            {"repo": "web", "dirty": 2, "ahead_origin": 0},
            {"repo": "kept", "dirty": 0, "ahead_origin": 0, "local_only_commits": 2, "stashes": 0},
            {"repo": "stashed", "dirty": 0, "ahead_origin": 0, "local_only_commits": 0, "stashes": 1},
            {"repo": "both", "dirty": 1, "ahead_origin": 0, "local_only_commits": 1, "stashes": 2},
            {"repo": "clean", "dirty": 0, "ahead_origin": 0, "local_only_commits": 0, "stashes": 0},
        ],
        standalones=[
            {"repo": "tools", "dirty": 0, "ahead_origin": 3},
            {"repo": "kit", "dirty": 0, "ahead_origin": 0, "local_only_commits": 1, "stashes": 0},
        ],
    )

    state = parse_state(doc)

    assert state.envs == ()
    assert state.unpushed == (
        "projects/app (ahead of origin)",
        "projects/web (uncommitted changes)",
        "projects/kept (local-only commits)",
        "projects/stashed (stashes)",
        "projects/both (uncommitted changes, local-only commits, stashes)",
        "standalone tools (ahead of origin)",
        "standalone kit (local-only commits)",
    )


@pytest.mark.parametrize(
    ("inner", "dirty", "unpushed"),
    [
        (_inner(dirty=True), ("deep",), ()),
        (_inner(error="resolved another workspace"), ("deep",), ()),
        (_inner(unpushed=True), (), ("deep (unpushed work in its nested workspace)",)),
        (_inner(), (), ()),
    ],
)
def test_parse_state_carries_a_deeper_nested_workspace_up_to_its_worktree(
    inner: dict[str, Any], dirty: tuple[str, ...], unpushed: tuple[str, ...]
) -> None:
    doc = nested_status(ROOT, nested_env("n1", nested_wt("deep", nested=inner)))

    state = parse_state(doc)

    assert state.envs == (NestedEnvState(name="n1", dirty=dirty, unpushed=unpushed),)


@pytest.mark.parametrize(
    ("doc", "field"),
    [
        ({**nested_status(ROOT), "environments": {"n1": {}}}, "environments is dict"),
        (nested_status(ROOT, {"worktrees": []}), r"environments\[0\]\.name"),
        ({**nested_status(ROOT), "environments": ["n1"]}, r"environments\[0\] is str"),
        (nested_status(ROOT, {"name": "n1", "worktrees": None}), r"environments\[0\]\.worktrees"),
        (nested_status(ROOT, nested_env("n1", {**nested_wt(), "dirty": None})), r"worktrees\[0\]\.dirty"),
        (nested_status(ROOT, nested_env("n1", {**nested_wt(), "ahead": True})), r"worktrees\[0\]\.ahead"),
        (nested_status(ROOT, nested_env("n1", {**nested_wt(), "nested": "dirty"})), r"worktrees\[0\]\.nested"),
        (nested_status(ROOT, nested_env("n1", nested_wt(nested={"dirty": True}))), r"nested\.unpushed"),
        (
            nested_status(ROOT, nested_env("n1", nested_wt(nested={"dirty": False, "unpushed": False, "error": 1}))),
            r"nested\.error",
        ),
        (nested_status(ROOT, projects=[{"repo": "app", "dirty": 0, "ahead_origin": "1"}]), r"ahead_origin"),
        (
            nested_status(ROOT, projects=[{"repo": "app", "dirty": 0, "ahead_origin": 0, "stashes": -1}]),
            r"projects\[0\]\.stashes",
        ),
        (
            nested_status(ROOT, standalones=[{"repo": "k", "dirty": 0, "ahead_origin": 0, "local_only_commits": None}]),
            r"standalones\[0\]\.local_only_commits",
        ),
        (nested_status(ROOT, nested_env("n1", {**nested_wt(), "upstream": 3})), r"worktrees\[0\]\.upstream"),
        (
            nested_status(ROOT, nested_env("n1", {**nested_wt(upstream="origin/f"), "tracking_ref_present": None})),
            r"worktrees\[0\]\.tracking_ref_present",
        ),
        ({**nested_status(ROOT), "standalones": None}, "standalones is NoneType"),
    ],
)
def test_parse_state_raises_on_a_structural_mismatch(doc: dict[str, Any], field: str) -> None:
    with pytest.raises(NestedStatusShapeError, match=field):
        parse_state(doc)
