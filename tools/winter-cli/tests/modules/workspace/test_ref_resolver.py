from __future__ import annotations

from pathlib import Path

import click
import pytest

from winter_cli.modules.workspace.models import ProjectRepository, StandaloneRepository
from winter_cli.modules.workspace.ref_resolver import ALIASES, resolve_ref

WORKSPACE_ROOT = Path("/ws")


def _repo(main_branch: str) -> ProjectRepository:
    return ProjectRepository(name="demo", main_path=WORKSPACE_ROOT / "demo", main_branch=main_branch)


# ── aliases ─────────────────────────────────────────────────────────────────


def test_resolve_ref_main_alias_expands_to_main_branch() -> None:
    assert resolve_ref("origin/{main}", _repo("trunk")) == "origin/trunk"


def test_resolve_ref_master_alias_expands_to_main_branch() -> None:
    assert resolve_ref("origin/{master}", _repo("trunk")) == "origin/trunk"


def test_resolve_ref_default_alias_expands_to_main_branch() -> None:
    assert resolve_ref("origin/{default}", _repo("trunk")) == "origin/trunk"


def test_all_aliases_are_interchangeable() -> None:
    repo = _repo("develop")
    expansions = {resolve_ref(f"origin/{{{alias}}}", repo) for alias in ALIASES}
    assert expansions == {"origin/develop"}


# ── per-repo override beats workspace default ────────────────────────────────


def test_resolve_ref_uses_repo_specific_main_branch() -> None:
    """A per-repo `main_branch` override (already folded in by RepositoryFactory) wins."""
    override_repo = _repo("master")
    default_repo = _repo("main")

    assert resolve_ref("{main}", override_repo) == "master"
    assert resolve_ref("{main}", default_repo) == "main"


def test_resolve_ref_works_for_standalone_repos_too() -> None:
    """Standalone repos carry their own `main_branch` and resolve just like project repos."""
    standalone = StandaloneRepository(name="stand", path=WORKSPACE_ROOT / "stand", main_branch="trunk")
    assert resolve_ref("origin/{main}", standalone) == "origin/trunk"


# ── passthrough ───────────────────────────────────────────────────────────────


def test_resolve_ref_no_token_passes_through_unchanged() -> None:
    assert resolve_ref("origin/feature/widget", _repo("main")) == "origin/feature/widget"


def test_resolve_ref_empty_ref_passes_through_unchanged() -> None:
    assert resolve_ref("", _repo("main")) == ""


def test_resolve_ref_literal_ref_with_no_braces_passes_through() -> None:
    assert resolve_ref("alpha", _repo("main")) == "alpha"


# ── token position ────────────────────────────────────────────────────────────


def test_resolve_ref_bare_token() -> None:
    assert resolve_ref("{main}", _repo("main")) == "main"


def test_resolve_ref_prefixed_token() -> None:
    assert resolve_ref("origin/{main}", _repo("main")) == "origin/main"


# ── git's own @{...} syntax passes through ──────────────────────────────────


def test_resolve_ref_at_upstream_token_passes_through_unchanged() -> None:
    assert resolve_ref("@{u}", _repo("main")) == "@{u}"


def test_resolve_ref_at_reflog_token_passes_through_unchanged() -> None:
    assert resolve_ref("HEAD@{1}", _repo("main")) == "HEAD@{1}"


def test_resolve_ref_at_date_token_passes_through_unchanged() -> None:
    assert resolve_ref("master@{yesterday}", _repo("main")) == "master@{yesterday}"


# ── unknown token refusal ──────────────────────────────────────────────────────


def test_resolve_ref_unknown_token_raises_click_exception() -> None:
    with pytest.raises(click.ClickException, match=r"Unknown ref token '\{trunk\}'"):
        resolve_ref("origin/{trunk}", _repo("main"))


def test_resolve_ref_unknown_token_lists_accepted_aliases() -> None:
    with pytest.raises(click.ClickException) as excinfo:
        resolve_ref("{trunk}", _repo("main"))
    message = str(excinfo.value)
    for alias in ALIASES:
        assert f"{{{alias}}}" in message


# ── missing main_branch refusal ────────────────────────────────────────────────


def test_resolve_ref_missing_main_branch_raises_click_exception() -> None:
    repo = ProjectRepository(name="demo", main_path=WORKSPACE_ROOT / "demo", main_branch=None)
    with pytest.raises(click.ClickException, match="demo"):
        resolve_ref("origin/{main}", repo)
