"""Real-git tests for winter#159: `ws checkout --force` recovering a detached worktree.

Mocking `WriteRepoRepository` (as `test_env_checkout_service.py` does) would only
exercise `EnvCheckoutService`'s call sequencing, not the actual GitPython behavior
that crashed on a detached HEAD — these build actual worktrees in `tmp_path` and
drive `EnvCheckoutService` against the real `WriteRepoRepository`, so an unguarded
`r.active_branch.name` read would raise `TypeError` here exactly as it did live.

Three scenarios, matching the issue's acceptance criteria:
  - `checkout_env` against a detached worktree succeeds, leaving HEAD attached
    and tracking the target ref (`test_checkout_env_reattaches_detached_worktree`)
  - re-running the same checkout is a convergent no-op, not a second crash
    (`test_checkout_env_rerun_on_already_attached_worktree_is_idempotent`)
  - `disconnect_env` against a detached worktree clears tracking instead of
    raising (`test_disconnect_env_clears_tracking_on_detached_worktree`)
"""

from __future__ import annotations

from pathlib import Path

import git
import pytest

from winter_cli.modules.workspace.env_checkout_service import EnvCheckoutService
from winter_cli.modules.workspace.internal.git_ops_service import GitOpsService
from winter_cli.modules.workspace.internal.repo_error_factory import RepoErrorFactory
from winter_cli.modules.workspace.internal.write_repo_repository import WriteRepoRepository
from winter_cli.modules.workspace.models import (
    CheckoutResult,
    FeatureEnvironment,
    FeatureEnvironmentWorktrees,
    FeatureWorktree,
    ProjectRepository,
    Workspace,
)
from winter_cli.modules.workspace.worktree_safety import WorktreeSafetyService


@pytest.fixture
def service() -> EnvCheckoutService:
    error_factory = RepoErrorFactory()
    git_ops = GitOpsService(error_factory, sleep=lambda _: None, jitter=lambda: 0.0)
    repo_repo = WriteRepoRepository(error_factory=error_factory, git_ops=git_ops)
    return EnvCheckoutService(repo_repo=repo_repo, worktree_safety_svc=WorktreeSafetyService(repo_repo=repo_repo))


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


def _env_worktree(tmp_path: Path) -> tuple[FeatureEnvironmentWorktrees, Path]:
    """A bare `origin` (main + feature/widget), a `main_path` clone, and an `alpha`
    feature worktree checked out on branch `alpha` tracking `origin/feature/widget` —
    the same shape `winter ws init` + a prior `winter ws checkout` leave behind.
    """
    seed = _configure(git.Repo.init(str(tmp_path / "seed"), initial_branch="main"))
    _commit(seed, "README", "initial\n", "initial")
    seed.git.checkout("-b", "feature/widget")
    _commit(seed, "widget.txt", "widget\n", "add widget")
    seed.git.checkout("main")

    origin = tmp_path / "origin.git"
    seed.git.clone("--bare", str(_working_dir(seed)), str(origin))

    main_repo = _configure(git.Repo.clone_from(str(origin), str(tmp_path / "demo")))

    env_root = tmp_path / "alpha"
    env_root.mkdir()
    worktree_path = env_root / "demo"
    main_repo.git.worktree("add", "-b", "alpha", str(worktree_path), "origin/main")
    _configure(git.Repo(str(worktree_path)))
    with git.Repo(str(worktree_path)) as wr:
        wr.git.config("branch.alpha.remote", "origin")
        wr.git.config("branch.alpha.merge", "refs/heads/feature/widget")

    workspace = Workspace(root_path=tmp_path, service_prefix="t", main_branch="main")
    env = FeatureEnvironment(workspace=workspace, name="alpha", index=1, path=env_root)
    project_repo = ProjectRepository(name="demo", main_path=tmp_path / "demo", main_branch="main")
    wt = FeatureWorktree(workspace=workspace, environment=env, repository=project_repo)
    env_wts = FeatureEnvironmentWorktrees(environment=env, worktrees=[wt])
    return env_wts, worktree_path


def test_checkout_env_reattaches_detached_worktree(tmp_path: Path, service: EnvCheckoutService) -> None:
    """A worktree detached out from under winter (e.g. a manual `git checkout
    --detach HEAD`) is recovered by `ws checkout --force`: HEAD ends attached to
    the worktree's env branch, on the target ref's tip, tracking it.
    """
    env_wts, worktree_path = _env_worktree(tmp_path)
    with git.Repo(str(worktree_path)) as r:
        r.git.checkout("--detach", "HEAD")
        assert r.head.is_detached is True

    report = service.checkout_env(env_wts, feature_branch="feature/widget", force=True)

    assert report.aborted is False
    assert [(o.repo_name, o.result) for o in report.repos] == [("demo", CheckoutResult.reset_feature)]
    with git.Repo(str(worktree_path)) as r:
        assert r.head.is_detached is False
        assert r.active_branch.name == "alpha"
        assert r.head.commit.hexsha == r.git.rev_parse("origin/feature/widget")
        assert r.git.config("--get", "branch.alpha.merge") == "refs/heads/feature/widget"


def test_checkout_env_rerun_on_reattached_worktree_is_idempotent(tmp_path: Path, service: EnvCheckoutService) -> None:
    """Re-running the same checkout after recovery is a convergent no-op success —
    no wedge on the second invocation.
    """
    env_wts, worktree_path = _env_worktree(tmp_path)
    with git.Repo(str(worktree_path)) as r:
        r.git.checkout("--detach", "HEAD")

    first = service.checkout_env(env_wts, feature_branch="feature/widget", force=True)
    second = service.checkout_env(env_wts, feature_branch="feature/widget", force=True)

    assert first.aborted is False
    assert second.aborted is False
    assert [(o.repo_name, o.result) for o in second.repos] == [("demo", CheckoutResult.reset_feature)]
    with git.Repo(str(worktree_path)) as r:
        assert r.head.is_detached is False
        assert r.active_branch.name == "alpha"
        assert r.head.commit.hexsha == r.git.rev_parse("origin/feature/widget")


def test_checkout_env_refuses_when_detached_worktree_env_branch_has_unpushed_commits(
    tmp_path: Path, service: EnvCheckoutService
) -> None:
    """winter#159 B1 regression: force-moving `refs/heads/<env>` must not silently
    abandon commits that sit on that branch when HEAD itself is detached elsewhere.

    HEAD is detached at `origin/main`; the worktree's own `alpha` branch carries
    one unpushed commit no other ref reaches. The HEAD-relative abandonment guard
    alone sees zero commits (HEAD has nothing of its own), so without the
    branch-relative check `checkout_env` would refuse nothing and
    `force_checkout_env_branch`'s `checkout --force -B alpha` would force the
    `alpha` branch pointer onto the feature ref, stranding the unpushed commit
    with no ref reaching it.
    """
    env_wts, worktree_path = _env_worktree(tmp_path)
    with git.Repo(str(worktree_path)) as r:
        path = _working_dir(r) / "local-work.txt"
        path.write_text("unpushed\n")
        r.index.add(["local-work.txt"])
        unpushed_sha = r.index.commit("unpushed local work").hexsha
        r.git.checkout("--detach", "origin/main")
        assert r.head.is_detached is True

    report = service.checkout_env(env_wts, feature_branch="feature/widget", force=False)

    assert report.aborted is True
    assert [(o.repo_name, o.result) for o in report.repos] == [("demo", CheckoutResult.refused_abandonment)]
    with git.Repo(str(worktree_path)) as r:
        # Refused — nothing moved. The unpushed commit is still reachable from
        # the alpha branch, and HEAD is still detached exactly where it was.
        r.git.rev_parse("--verify", "--quiet", f"{unpushed_sha}")
        r.git.merge_base("--is-ancestor", unpushed_sha, "refs/heads/alpha")
        assert r.head.is_detached is True


def test_disconnect_env_clears_tracking_on_detached_worktree(tmp_path: Path, service: EnvCheckoutService) -> None:
    """`winter ws disconnect` against a detached worktree clears tracking instead
    of raising `TypeError` on the unguarded `active_branch` read.
    """
    env_wts, worktree_path = _env_worktree(tmp_path)
    with git.Repo(str(worktree_path)) as r:
        r.git.checkout("--detach", "HEAD")
        assert r.head.is_detached is True

    disconnected = service.disconnect_env(env_wts, patterns=["alpha"])

    assert disconnected == ["demo"]
    with git.Repo(str(worktree_path)) as r:
        # Still detached — disconnect doesn't attach, it just clears tracking.
        assert r.head.is_detached is True
        with pytest.raises(git.GitCommandError):
            r.git.config("--get", "branch.alpha.remote")
