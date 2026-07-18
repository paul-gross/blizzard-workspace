"""Real-git tests for `fast_forward_local_branch` — the post-push sync that keeps
the workspace's local copy of a just-pushed branch in step with the remote.

Mocking the worktree-list parse, `update-ref`, and `merge --ff-only` plumbing
would only test the mock; these build actual repos in `tmp_path` so the
in-sync / behind / dirty / checked-out / bare-ref branches reflect what git
really does. The source checkout shares an object store with the pushing side
via a common bare `origin`, mirroring how winter links env worktrees.
"""

from __future__ import annotations

from pathlib import Path

import git
import pytest

from winter_cli.modules.workspace.internal.git_ops_service import GitOpsService
from winter_cli.modules.workspace.internal.repo_error_factory import RepoErrorFactory
from winter_cli.modules.workspace.internal.write_repo_repository import WriteRepoRepository
from winter_cli.modules.workspace.models import ProjectRepository


@pytest.fixture
def repo_svc() -> WriteRepoRepository:
    error_factory = RepoErrorFactory()
    git_ops = GitOpsService(error_factory, sleep=lambda _: None, jitter=lambda: 0.0)
    return WriteRepoRepository(error_factory=error_factory, git_ops=git_ops)


def _configure(r: git.Repo) -> git.Repo:
    with r.config_writer() as cw:
        cw.set_value("user", "email", "test@example.com")
        cw.set_value("user", "name", "Test")
        cw.set_value("commit", "gpgsign", "false")
    return r


def _working_dir(r: git.Repo) -> Path:
    wtd = r.working_tree_dir
    assert wtd is not None, "test fixture initialized repo without a working tree"
    return Path(str(wtd))


def _commit(r: git.Repo, file_name: str, content: str, message: str) -> str:
    path = _working_dir(r) / file_name
    path.write_text(content)
    r.index.add([file_name])
    return r.index.commit(message).hexsha


def _rev(r: git.Repo, ref: str) -> str:
    return r.git.rev_parse("--verify", ref)


def _origin_and_src(tmp_path: Path) -> tuple[ProjectRepository, git.Repo, git.Repo]:
    """A bare `origin`, the source-checkout `src` on `main`, and a `pusher` clone.

    `src` is the checkout under test (its `main_path`); `pusher` advances
    `origin` out from under it, standing in for another env worktree's push.
    """
    seed = _configure(git.Repo.init(str(tmp_path / "seed"), initial_branch="main"))
    _commit(seed, "README", "initial\n", "initial")
    origin = tmp_path / "origin.git"
    seed.git.clone("--bare", str(_working_dir(seed)), str(origin))

    src = _configure(git.Repo.clone_from(str(origin), str(tmp_path / "src")))
    pusher = _configure(git.Repo.clone_from(str(origin), str(tmp_path / "pusher")))
    return ProjectRepository(name="demo", main_path=tmp_path / "src", main_branch="main"), src, pusher


def test_fast_forwards_checked_out_branch_when_in_sync(tmp_path: Path, repo_svc: WriteRepoRepository) -> None:
    """In-sync local main (checked out in src) advances to the pushed tip."""
    project, src, pusher = _origin_and_src(tmp_path)
    pre = _rev(src, "origin/main")

    new_tip = _commit(pusher, "a.txt", "a\n", "commit a")
    pusher.git.push("origin", "main")
    src.git.fetch("origin")  # src now sees origin/main move (shared-ref parity)

    result = repo_svc.fast_forward_local_branch(project, "main", pre)

    assert result is not None and result.advanced is True
    assert result.branch == "main" and result.commits == 1
    assert _rev(src, "main") == new_tip
    assert (project.main_path / "a.txt").exists()  # working tree moved with the ref


def test_advances_branch_not_checked_out_via_ref_update(tmp_path: Path, repo_svc: WriteRepoRepository) -> None:
    """A local branch no worktree holds is advanced by a pure ref update."""
    project, src, pusher = _origin_and_src(tmp_path)
    # A shared `release` branch that exists locally in src but stays un-checked-out
    # (src's working tree remains on main).
    pusher.git.checkout("-b", "release")
    pusher.git.push("origin", "release")
    src.git.fetch("origin")
    src.git.branch("release", "origin/release")
    pre = _rev(src, "release")

    new_tip = _commit(pusher, "r.txt", "r\n", "release commit")
    pusher.git.push("origin", "release")
    src.git.fetch("origin")

    result = repo_svc.fast_forward_local_branch(project, "release", pre)

    assert result is not None and result.advanced is True and result.commits == 1
    assert _rev(src, "release") == new_tip
    assert src.active_branch.name == "main"  # HEAD untouched


def test_leaves_local_branch_behind_when_not_in_sync(tmp_path: Path, repo_svc: WriteRepoRepository) -> None:
    """A local main that has diverged from the pre-push remote tip is left alone."""
    project, src, pusher = _origin_and_src(tmp_path)
    pre = _rev(src, "origin/main")
    # src's local main is no longer at `pre` — a local commit moved it.
    local_only = _commit(src, "local.txt", "l\n", "local commit")

    pusher.git.checkout("main")
    _commit(pusher, "a.txt", "a\n", "commit a")
    pusher.git.push("origin", "main")
    src.git.fetch("origin")

    result = repo_svc.fast_forward_local_branch(project, "main", pre)

    assert result is not None and result.advanced is False
    assert result.skipped_reason == "not in sync"
    assert _rev(src, "main") == local_only  # unchanged


def test_skips_dirty_checkout(tmp_path: Path, repo_svc: WriteRepoRepository) -> None:
    """An in-sync but dirty checkout is reported skipped, not force-advanced."""
    project, src, pusher = _origin_and_src(tmp_path)
    pre = _rev(src, "origin/main")

    _commit(pusher, "a.txt", "a\n", "commit a")
    pusher.git.push("origin", "main")
    src.git.fetch("origin")
    (project.main_path / "README").write_text("locally modified\n")  # tracked, uncommitted

    result = repo_svc.fast_forward_local_branch(project, "main", pre)

    assert result is not None and result.advanced is False
    assert result.skipped_reason == "dirty"
    assert _rev(src, "main") == pre  # unchanged


def test_returns_none_when_no_local_branch_of_that_name(tmp_path: Path, repo_svc: WriteRepoRepository) -> None:
    """No local branch of the pushed name ⇒ nothing to sync."""
    project, src, _pusher = _origin_and_src(tmp_path)

    assert repo_svc.fast_forward_local_branch(project, "no-such-branch", _rev(src, "main")) is None
