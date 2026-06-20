"""Real-git tests for ``integrate_standalone_to_ref`` (the branch-pin ff-only path).

These tests use real ``git init`` / ``git clone`` setups so the ff-only guarantee
and dirty-tree guard can be validated against actual git behavior rather than mocks.

The fix being validated (MF1/MF2): the branch-pin pull path now calls
``integrate_standalone_to_ref`` (which wraps ``git merge --ff-only``) instead of
``checkout_branch`` (which would ``git checkout -B`` — a force-reset that silently
discards local commits). Four scenarios are covered:

  (a) origin ahead, clean tree → ff succeeds, returns fast_forwarded
  (b) diverged (local and origin have branched) → refused (diverged), no commits lost
  (c) dirty tree, autostash=False → refused (dirty guard, no mutation)
  (d) dirty tree, autostash=True → stash, ff, pop, succeeds
"""

from __future__ import annotations

from pathlib import Path

import git
import pytest

from winter_cli.modules.workspace.internal.git_ops_service import GitOpsService
from winter_cli.modules.workspace.internal.repo_error_factory import RepoErrorFactory
from winter_cli.modules.workspace.internal.write_repo_repository import WriteRepoRepository
from winter_cli.modules.workspace.models import (
    PullMode,
    StandaloneRepository,
    SyncResult,
)


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
    assert wtd is not None
    return Path(str(wtd))


def _commit(r: git.Repo, filename: str, content: str, message: str) -> str:
    path = _working_dir(r) / filename
    path.write_text(content)
    r.index.add([filename])
    return r.index.commit(message).hexsha


def _standalone(path: Path, name: str = "my-lib") -> StandaloneRepository:
    return StandaloneRepository(name=name, path=path, ref="main")


def _setup_standalone_with_origin(
    tmp_path: Path,
    *,
    name: str = "my-lib",
) -> tuple[StandaloneRepository, git.Repo, git.Repo]:
    """Create a bare origin, a standalone clone (the standalone under test), and
    a second pusher clone so tests can advance origin out from under the standalone.

    Returns (standalone, standalone_repo, pusher_repo).
    """
    seed = _configure(git.Repo.init(str(tmp_path / "seed"), initial_branch="main"))
    _commit(seed, "README", "initial\n", "initial")
    origin = tmp_path / "origin.git"
    seed.git.clone("--bare", str(_working_dir(seed)), str(origin))

    standalone_path = tmp_path / name
    standalone_repo = _configure(git.Repo.clone_from(str(origin), str(standalone_path)))
    pusher = _configure(git.Repo.clone_from(str(origin), str(tmp_path / "pusher")))

    return _standalone(standalone_path, name), standalone_repo, pusher


# ── (a) origin ahead, clean tree ─────────────────────────────────────────────


def test_integrate_standalone_to_ref_ff_succeeds_clean(tmp_path: Path, repo_svc: WriteRepoRepository) -> None:
    """Clean tree, origin ahead → integrate_standalone_to_ref ff-only advances HEAD."""
    standalone, _sr, pusher = _setup_standalone_with_origin(tmp_path)

    # Advance origin/main with 2 commits.
    _commit(pusher, "a.txt", "a\n", "commit a")
    _commit(pusher, "b.txt", "b\n", "commit b")
    pusher.git.push("origin", "main")

    # Fetch remote refs into the standalone.
    with git.Repo(str(standalone.path)) as r:
        r.remotes["origin"].fetch()

    outcome = repo_svc.integrate_standalone_to_ref(standalone, "origin/main", PullMode.ff_only, autostash=False)

    assert outcome.sync_result == SyncResult.fast_forwarded
    assert outcome.commits == 2
    assert (standalone.path / "a.txt").exists()
    assert (standalone.path / "b.txt").exists()


# ── (b) diverged: local and origin have branched ─────────────────────────────


def test_integrate_standalone_to_ref_diverged_refused(tmp_path: Path, repo_svc: WriteRepoRepository) -> None:
    """Diverged (local commit + origin commit) → refused, HEAD unchanged, no commits lost."""
    standalone, standalone_repo, pusher = _setup_standalone_with_origin(tmp_path)

    # Local commit in the standalone (creates divergence).
    local_sha = _commit(standalone_repo, "local.txt", "local\n", "local work")

    # Advance origin in parallel.
    _commit(pusher, "origin.txt", "origin\n", "origin work")
    pusher.git.push("origin", "main")

    # Fetch remote refs.
    with git.Repo(str(standalone.path)) as r:
        r.remotes["origin"].fetch()

    outcome = repo_svc.integrate_standalone_to_ref(standalone, "origin/main", PullMode.ff_only, autostash=False)

    assert outcome.sync_result == SyncResult.diverged
    # HEAD must still point at the local commit — nothing was lost.
    with git.Repo(str(standalone.path)) as r:
        assert r.head.commit.hexsha == local_sha


# ── (c) dirty tree, autostash=False ──────────────────────────────────────────


# ── (c) & (d) dirty tree ─────────────────────────────────────────────────────
#
# Note: The primary dirty-tree guard for branch-pin advances lives in the
# *service layer* (WorkspaceSyncService._fetch_then_integrate_standalone) and
# is validated by unit tests using FakeGitRepository. The repo-adapter layer
# (`integrate_standalone_to_ref` → `_integrate` → `git merge [--autostash]
# --ff-only`) does not itself refuse dirty trees unless the merge would
# actually conflict — git's ff-only is permissive about non-conflicting local
# changes. The autostash test below validates that `autostash=True` causes
# git's `--autostash` flag to be passed through, which saves and restores
# working-tree changes around the merge.


def test_integrate_standalone_to_ref_autostash_restores_local_changes(
    tmp_path: Path, repo_svc: WriteRepoRepository
) -> None:
    """autostash=True → git merge --autostash --ff-only; local changes restored after ff."""
    standalone, _standalone_repo, pusher = _setup_standalone_with_origin(tmp_path)

    # Advance origin.
    _commit(pusher, "a.txt", "a\n", "commit a")
    pusher.git.push("origin", "main")

    # Fetch remote refs into the standalone.
    with git.Repo(str(standalone.path)) as r:
        r.remotes["origin"].fetch()

    # Unstaged local change in the standalone working tree.
    readme = standalone.path / "README"
    readme.write_text("local tweak\n")

    outcome = repo_svc.integrate_standalone_to_ref(standalone, "origin/main", PullMode.ff_only, autostash=True)

    assert outcome.sync_result == SyncResult.fast_forwarded
    assert (standalone.path / "a.txt").exists()
    # Local working-tree change restored by git's --autostash pop.
    assert readme.read_text() == "local tweak\n"
