from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import pytest

from winter_cli.modules.workspace.env_reset_service import EnvResetService, looks_like_sha
from winter_cli.modules.workspace.models import (
    FeatureEnvironment,
    FeatureWorktree,
    ProjectRepository,
    ResetMode,
    ResetResult,
    Workspace,
)
from winter_cli.modules.workspace.worktree_safety import WorktreeSafetyService

WORKSPACE_ROOT = Path("/ws")


@pytest.fixture
def workspace() -> Workspace:
    return Workspace(root_path=WORKSPACE_ROOT, service_prefix="t", main_branch="main")


class FakeWriteRepoRepository:
    """Stub for the `IWriteRepoRepository` Protocol — records every call.

    Pre-seed before the call:
    - `dirty_worktree_repos` / `repos_with_commits_not_in`: `(env, repo)` pairs;
      the matching query returns True/non-zero for pairs in the set.
    - `missing_refs`: `(repo_name, ref)` pairs absent from the local store.
    - `upstreams`: repo-name → current upstream (e.g. `origin/feature-123`), or
      absent (None) for a disconnected repo.
    """

    def __init__(self) -> None:
        self.reset_to_calls: list[tuple[str, str, ResetMode, str]] = []
        self.count_commits_not_in_calls: list[tuple[str, str]] = []
        self.missing_refs: set[tuple[str, str]] = set()
        self.dirty_worktree_repos: set[tuple[str, str]] = set()
        self.repos_with_commits_not_in: set[tuple[str, str]] = set()
        self.upstreams: dict[str, str] = {}

    def has_local_ref(self, worktree: FeatureWorktree, ref: str) -> bool:
        return (worktree.repository.name, ref) not in self.missing_refs

    def is_worktree_dirty(self, worktree: FeatureWorktree) -> bool:
        return (worktree.environment.name, worktree.repository.name) in self.dirty_worktree_repos

    def count_commits_not_in(self, worktree: FeatureWorktree, ref: str) -> int:
        self.count_commits_not_in_calls.append((worktree.repository.name, ref))
        key = (worktree.environment.name, worktree.repository.name)
        return 1 if key in self.repos_with_commits_not_in else 0

    def get_worktree_upstream(self, worktree: FeatureWorktree) -> str | None:
        return self.upstreams.get(worktree.repository.name)

    def reset_to(self, worktree: FeatureWorktree, mode: ResetMode, target_ref: str) -> None:
        self.reset_to_calls.append((worktree.environment.name, worktree.repository.name, mode, target_ref))

    # Methods touched by other IWriteRepoRepository code paths — raise to
    # surface accidental fan-out beyond the call under test.
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"FakeWriteRepoRepository.{name} called unexpectedly")


@pytest.fixture
def fake_repo_repo() -> FakeWriteRepoRepository:
    return FakeWriteRepoRepository()


@pytest.fixture
def service(fake_repo_repo: FakeWriteRepoRepository) -> EnvResetService:
    return EnvResetService(
        repo_repo=fake_repo_repo,  # type: ignore[arg-type]
        worktree_safety_svc=WorktreeSafetyService(repo_repo=fake_repo_repo),  # type: ignore[arg-type]
    )


def _wt(workspace: Workspace, repo_name: str, env_name: str = "alpha", main_branch: str = "main") -> FeatureWorktree:
    env = FeatureEnvironment(workspace=workspace, name=env_name, index=1, path=workspace.root_path / env_name)
    project_repo = ProjectRepository(name=repo_name, main_path=workspace.root_path / repo_name, main_branch=main_branch)
    return FeatureWorktree(workspace=workspace, environment=env, repository=project_repo)


# ── looks_like_sha ────────────────────────────────────────────────────────────


def test_looks_like_sha_accepts_abbreviated_and_full_hex() -> None:
    assert looks_like_sha("abc1234")
    assert looks_like_sha("a" * 40)
    assert looks_like_sha("dead")


def test_looks_like_sha_rejects_branch_names_and_tokens() -> None:
    assert not looks_like_sha("main")
    assert not looks_like_sha("origin/main")
    assert not looks_like_sha("{main}")
    assert not looks_like_sha("feature/widget")
    assert not looks_like_sha("ab")  # shorter than git's min abbreviation


# ── each mode calls reset_to with the right ResetMode ─────────────────────────


@pytest.mark.parametrize("mode", [ResetMode.soft, ResetMode.mixed, ResetMode.hard])
def test_reset_calls_reset_to_with_requested_mode(
    workspace: Workspace, service: EnvResetService, fake_repo_repo: FakeWriteRepoRepository, mode: ResetMode
) -> None:
    wt = _wt(workspace, "demo")

    report = service.reset(targets=[wt], ref="origin/main", mode=mode, force=False, dry_run=False)

    assert report.aborted is False
    assert fake_repo_repo.reset_to_calls == [("alpha", "demo", mode, "origin/main")]
    assert [(o.repo_name, o.result, o.ref) for o in report.repos] == [("demo", ResetResult.reset, "origin/main")]


def test_reset_soft_and_mixed_apply_no_dirty_or_abandonment_guard(
    workspace: Workspace, service: EnvResetService, fake_repo_repo: FakeWriteRepoRepository
) -> None:
    wt = _wt(workspace, "demo")
    fake_repo_repo.dirty_worktree_repos = {("alpha", "demo")}
    fake_repo_repo.repos_with_commits_not_in = {("alpha", "demo")}

    for mode in (ResetMode.soft, ResetMode.mixed):
        fake_repo_repo.reset_to_calls.clear()
        report = service.reset(targets=[wt], ref="origin/main", mode=mode, force=False, dry_run=False)
        assert report.aborted is False
        assert fake_repo_repo.reset_to_calls == [("alpha", "demo", mode, "origin/main")]


# ── {main} token resolution ────────────────────────────────────────────────────


def test_reset_main_token_resolves_per_repo(
    workspace: Workspace, service: EnvResetService, fake_repo_repo: FakeWriteRepoRepository
) -> None:
    wt1 = _wt(workspace, "r1", main_branch="main")
    wt2 = _wt(workspace, "r2", main_branch="master")

    report = service.reset(targets=[wt1, wt2], ref="origin/{main}", mode=ResetMode.mixed, force=False, dry_run=False)

    assert report.aborted is False
    assert fake_repo_repo.reset_to_calls == [
        ("alpha", "r1", ResetMode.mixed, "origin/main"),
        ("alpha", "r2", ResetMode.mixed, "origin/master"),
    ]


def test_reset_unknown_token_refuses_before_any_git_op(
    workspace: Workspace, service: EnvResetService, fake_repo_repo: FakeWriteRepoRepository
) -> None:
    wt = _wt(workspace, "demo")

    with pytest.raises(click.ClickException, match=r"Unknown ref token '\{trunk\}'"):
        service.reset(targets=[wt], ref="{trunk}", mode=ResetMode.mixed, force=False, dry_run=False)

    assert fake_repo_repo.reset_to_calls == []


# ── missing ref refusal ────────────────────────────────────────────────────────


def test_reset_refuses_when_ref_resolves_in_no_matched_repo(
    workspace: Workspace, service: EnvResetService, fake_repo_repo: FakeWriteRepoRepository
) -> None:
    wt = _wt(workspace, "demo")
    fake_repo_repo.missing_refs = {("demo", "origin/does-not-exist")}

    report = service.reset(targets=[wt], ref="origin/does-not-exist", mode=ResetMode.mixed, force=False, dry_run=False)

    assert report.aborted is True
    assert [(o.repo_name, o.result) for o in report.repos] == [("demo", ResetResult.refused_missing_ref)]
    assert fake_repo_repo.reset_to_calls == []


def test_reset_missing_ref_refusal_not_bypassed_by_force(
    workspace: Workspace, service: EnvResetService, fake_repo_repo: FakeWriteRepoRepository
) -> None:
    wt = _wt(workspace, "demo")
    fake_repo_repo.missing_refs = {("demo", "origin/does-not-exist")}

    report = service.reset(targets=[wt], ref="origin/does-not-exist", mode=ResetMode.hard, force=True, dry_run=False)

    assert report.aborted is True
    assert fake_repo_repo.reset_to_calls == []


# ── ambiguous bare-SHA refusal ──────────────────────────────────────────────────


def test_reset_bare_sha_refuses_when_multiple_worktrees_matched(
    workspace: Workspace, service: EnvResetService, fake_repo_repo: FakeWriteRepoRepository
) -> None:
    wt1 = _wt(workspace, "r1")
    wt2 = _wt(workspace, "r2")

    report = service.reset(targets=[wt1, wt2], ref="abc1234", mode=ResetMode.mixed, force=False, dry_run=False)

    assert report.aborted is True
    assert [o.result for o in report.repos] == [ResetResult.refused_ambiguous_sha, ResetResult.refused_ambiguous_sha]
    assert fake_repo_repo.reset_to_calls == []


def test_reset_bare_sha_allowed_for_single_matched_worktree(
    workspace: Workspace, service: EnvResetService, fake_repo_repo: FakeWriteRepoRepository
) -> None:
    wt = _wt(workspace, "demo")

    report = service.reset(targets=[wt], ref="abc1234", mode=ResetMode.mixed, force=False, dry_run=False)

    assert report.aborted is False
    assert fake_repo_repo.reset_to_calls == [("alpha", "demo", ResetMode.mixed, "abc1234")]


def test_reset_bare_sha_refusal_not_bypassed_by_force(
    workspace: Workspace, service: EnvResetService, fake_repo_repo: FakeWriteRepoRepository
) -> None:
    wt1 = _wt(workspace, "r1")
    wt2 = _wt(workspace, "r2")

    report = service.reset(targets=[wt1, wt2], ref="abc1234", mode=ResetMode.hard, force=True, dry_run=False)

    assert report.aborted is True
    assert fake_repo_repo.reset_to_calls == []


# ── --hard dirty / abandonment guard ────────────────────────────────────────────


def test_reset_hard_refuses_dirty_repo_without_force(
    workspace: Workspace, service: EnvResetService, fake_repo_repo: FakeWriteRepoRepository
) -> None:
    wt = _wt(workspace, "demo")
    fake_repo_repo.dirty_worktree_repos = {("alpha", "demo")}

    report = service.reset(targets=[wt], ref="origin/main", mode=ResetMode.hard, force=False, dry_run=False)

    assert report.aborted is True
    assert [(o.repo_name, o.result) for o in report.repos] == [("demo", ResetResult.refused_dirty)]
    assert fake_repo_repo.reset_to_calls == []


def test_reset_hard_refuses_abandonment_against_own_upstream(
    workspace: Workspace, service: EnvResetService, fake_repo_repo: FakeWriteRepoRepository
) -> None:
    wt = _wt(workspace, "demo")
    fake_repo_repo.upstreams = {"demo": "origin/feature-123"}
    fake_repo_repo.repos_with_commits_not_in = {("alpha", "demo")}

    report = service.reset(targets=[wt], ref="origin/main", mode=ResetMode.hard, force=False, dry_run=False)

    assert report.aborted is True
    assert [(o.repo_name, o.result) for o in report.repos] == [("demo", ResetResult.refused_abandonment)]
    assert fake_repo_repo.count_commits_not_in_calls == [("demo", "origin/feature-123")]
    assert fake_repo_repo.reset_to_calls == []


def test_reset_hard_abandonment_falls_back_to_main_when_disconnected(
    workspace: Workspace, service: EnvResetService, fake_repo_repo: FakeWriteRepoRepository
) -> None:
    wt = _wt(workspace, "demo", main_branch="main")

    service.reset(targets=[wt], ref="origin/main", mode=ResetMode.hard, force=False, dry_run=False)

    assert fake_repo_repo.count_commits_not_in_calls == [("demo", "origin/main")]


def test_reset_hard_force_bypasses_dirty_and_abandonment(
    workspace: Workspace, service: EnvResetService, fake_repo_repo: FakeWriteRepoRepository
) -> None:
    wt = _wt(workspace, "demo")
    fake_repo_repo.dirty_worktree_repos = {("alpha", "demo")}
    fake_repo_repo.repos_with_commits_not_in = {("alpha", "demo")}

    report = service.reset(targets=[wt], ref="origin/main", mode=ResetMode.hard, force=True, dry_run=False)

    assert report.aborted is False
    assert fake_repo_repo.reset_to_calls == [("alpha", "demo", ResetMode.hard, "origin/main")]


def test_reset_hard_all_or_nothing_one_dirty_repo_blocks_every_repo(
    workspace: Workspace, service: EnvResetService, fake_repo_repo: FakeWriteRepoRepository
) -> None:
    wt1 = _wt(workspace, "r1")
    wt2 = _wt(workspace, "r2")
    fake_repo_repo.dirty_worktree_repos = {("alpha", "r1")}

    report = service.reset(targets=[wt1, wt2], ref="origin/main", mode=ResetMode.hard, force=False, dry_run=False)

    assert report.aborted is True
    kinds = {(o.repo_name, o.result) for o in report.repos}
    assert ("r1", ResetResult.refused_dirty) in kinds
    assert fake_repo_repo.reset_to_calls == []


# ── --dry-run ────────────────────────────────────────────────────────────────────


def test_reset_dry_run_computes_plan_without_calling_reset_to(
    workspace: Workspace, service: EnvResetService, fake_repo_repo: FakeWriteRepoRepository
) -> None:
    wt = _wt(workspace, "demo")

    report = service.reset(targets=[wt], ref="origin/main", mode=ResetMode.hard, force=False, dry_run=True)

    assert report.aborted is False
    assert report.dry_run is True
    assert [(o.repo_name, o.result, o.ref) for o in report.repos] == [("demo", ResetResult.reset, "origin/main")]
    assert fake_repo_repo.reset_to_calls == []


def test_reset_dry_run_still_surfaces_refusals(
    workspace: Workspace, service: EnvResetService, fake_repo_repo: FakeWriteRepoRepository
) -> None:
    wt = _wt(workspace, "demo")
    fake_repo_repo.dirty_worktree_repos = {("alpha", "demo")}

    report = service.reset(targets=[wt], ref="origin/main", mode=ResetMode.hard, force=False, dry_run=True)

    assert report.aborted is True
    assert [(o.repo_name, o.result) for o in report.repos] == [("demo", ResetResult.refused_dirty)]


# ── multi-env targets ─────────────────────────────────────────────────────────


def test_reset_multi_env_targets_carry_their_own_env_label(
    workspace: Workspace, service: EnvResetService, fake_repo_repo: FakeWriteRepoRepository
) -> None:
    wt_alpha = _wt(workspace, "winter", env_name="alpha")
    wt_beta = _wt(workspace, "winter", env_name="beta")

    report = service.reset(
        targets=[wt_alpha, wt_beta], ref="origin/main", mode=ResetMode.mixed, force=False, dry_run=False
    )

    assert report.aborted is False
    assert {(o.env, o.repo_name) for o in report.repos} == {("alpha", "winter"), ("beta", "winter")}
    assert fake_repo_repo.reset_to_calls == [
        ("alpha", "winter", ResetMode.mixed, "origin/main"),
        ("beta", "winter", ResetMode.mixed, "origin/main"),
    ]
