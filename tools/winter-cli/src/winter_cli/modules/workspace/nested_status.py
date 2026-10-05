"""Reads a nested workspace's `ws status --json` document into a `NestedWorkspaceState`, validating its shape."""

from __future__ import annotations

from typing import Any

from winter_cli.modules.workspace.models import NestedEnvState, NestedWorkspaceState

STATUS_SCHEMA_VERSION = 1
"""The only `ws status --json` schema version this reader accepts."""


class NestedStatusShapeError(ValueError):
    """The status document does not have the shape schema v1 declares; the message names the offending field."""


def reported_root(doc: Any) -> str | None:
    """The `workspace.root_path` *doc* reports, or None when it carries none.

    Raises `NestedStatusShapeError` when *doc* is not a schema-v1 document.
    """
    status = _object(doc, "the status document")
    version = status.get("schema_version")
    if version != STATUS_SCHEMA_VERSION:
        raise NestedStatusShapeError(f"has schema_version {version!r}; expected {STATUS_SCHEMA_VERSION}")
    workspace = status.get("workspace")
    root = workspace.get("root_path") if isinstance(workspace, dict) else None
    return root if isinstance(root, str) else None


def parse_state(doc: Any) -> NestedWorkspaceState:
    """The envs, dirt, and unpushed work *doc* reports; raises `NestedStatusShapeError` on a structural mismatch.

    A worktree is dirty when its own `dirty` count is non-zero, or when it
    holds a workspace of its own (`nested`) that is dirty or could not be
    read. It holds unpushed work when it has commits its upstream lacks
    (`tracking_ahead`), when it has commits beyond its main branch (`ahead`)
    and no upstream ref to hold them (`upstream` null or
    `tracking_ref_present` false), or when its own nested workspace reports
    unpushed work — so both signals carry up through every level. A branch
    pushed to its upstream but not merged is `ahead` yet unpushed nowhere.
    A source checkout or standalone holds unpushed work when it is dirty,
    `ahead_origin`, or holds `local_only_commits` or `stashes`; the last two
    are optional, read as 0 when absent.

    Each unpushed entry names the repo and why, e.g. `app (unpushed commits)`
    or `projects/app (local-only commits, stashes)`.
    """
    status = _object(doc, "the status document")
    envs: list[NestedEnvState] = []
    for i, raw_env in enumerate(_array(status.get("environments"), "environments")):
        env = _object(raw_env, f"environments[{i}]")
        name = _string(env.get("name"), f"environments[{i}].name")
        dirty: list[str] = []
        unpushed: list[str] = []
        for j, raw_wt in enumerate(_array(env.get("worktrees"), f"environments[{i}].worktrees")):
            where = f"environments[{i}].worktrees[{j}]"
            wt = _object(raw_wt, where)
            repo = _string(wt.get("repo"), f"{where}.repo")
            wt_dirty = _count(wt.get("dirty"), f"{where}.dirty") > 0
            reasons = _worktree_unpushed_reasons(wt, where)
            if "nested" in wt:
                inner = _object(wt["nested"], f"{where}.nested")
                inner_error = inner.get("error")
                if inner_error is not None and not isinstance(inner_error, str):
                    raise NestedStatusShapeError(f"{where}.nested.error is {inner_error!r}; expected a string or null")
                wt_dirty = wt_dirty or _flag(inner.get("dirty"), f"{where}.nested.dirty") or inner_error is not None
                if _flag(inner.get("unpushed"), f"{where}.nested.unpushed"):
                    reasons.append(_NESTED_UNPUSHED)
            if wt_dirty:
                dirty.append(repo)
            if reasons:
                unpushed.append(_labelled(repo, reasons))
        envs.append(NestedEnvState(name=name, dirty=tuple(dirty), unpushed=tuple(unpushed)))
    checkouts = [
        *_checkouts_with_local_work(status.get("projects"), "projects", "projects/{}"),
        *_checkouts_with_local_work(status.get("standalones"), "standalones", "standalone {}"),
    ]
    return NestedWorkspaceState(envs=tuple(envs), checkouts=tuple(checkouts))


_NESTED_UNPUSHED = "unpushed work in its nested workspace"


def _worktree_unpushed_reasons(wt: dict[str, Any], where: str) -> list[str]:
    """Why an env worktree holds commits no remote has: ahead of its upstream, or ahead with no upstream ref."""
    ahead = _count(wt.get("ahead"), f"{where}.ahead")
    tracking_ahead = _count(wt.get("tracking_ahead"), f"{where}.tracking_ahead")
    upstream = wt.get("upstream")
    if upstream is not None and not isinstance(upstream, str):
        raise NestedStatusShapeError(f"{where}.upstream is {upstream!r}; expected a string or null")
    upstream_present = upstream is not None and _flag(wt.get("tracking_ref_present"), f"{where}.tracking_ref_present")
    if tracking_ahead > 0:
        return ["unpushed commits"]
    if ahead > 0 and not upstream_present:
        return ["unpushed branch"]
    return []


def _checkouts_with_local_work(value: Any, field: str, label: str) -> list[str]:
    names: list[str] = []
    for i, raw in enumerate(_array(value, field)):
        where = f"{field}[{i}]"
        checkout = _object(raw, where)
        repo = _string(checkout.get("repo"), f"{where}.repo")
        reasons: list[str] = []
        if _count(checkout.get("dirty"), f"{where}.dirty") > 0:
            reasons.append("uncommitted changes")
        if _count(checkout.get("ahead_origin"), f"{where}.ahead_origin") > 0:
            reasons.append("ahead of origin")
        if _count(checkout.get("local_only_commits", 0), f"{where}.local_only_commits") > 0:
            reasons.append("local-only commits")
        if _count(checkout.get("stashes", 0), f"{where}.stashes") > 0:
            reasons.append("stashes")
        if reasons:
            names.append(_labelled(label.format(repo), reasons))
    return names


def _labelled(place: str, reasons: list[str]) -> str:
    return f"{place} ({', '.join(reasons)})"


def _object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NestedStatusShapeError(f"{where} is {type(value).__name__}; expected an object")
    return value


def _array(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise NestedStatusShapeError(f"{where} is {type(value).__name__}; expected an array")
    return value


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise NestedStatusShapeError(f"{where} is {value!r}; expected a string")
    return value


def _count(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NestedStatusShapeError(f"{where} is {value!r}; expected a non-negative integer")
    return value


def _flag(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise NestedStatusShapeError(f"{where} is {value!r}; expected a boolean")
    return value
