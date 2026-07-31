from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import git
import pytest

from winter_cli.modules.workspace.internal import read_repo_repository, write_repo_repository
from winter_cli.modules.workspace.internal.git_ops_service import GitOpsService
from winter_cli.modules.workspace.internal.repo_error_factory import RepoErrorFactory
from winter_cli.modules.workspace.internal.write_repo_repository import WriteRepoRepository
from winter_cli.modules.workspace.models import (
    FeatureEnvironment,
    FeatureWorktree,
    ProjectRepository,
    RepoError,
    ResetMode,
    StandaloneRepository,
    Workspace,
)

_ROOT = Path("/fake/workspace")
_REPO_PATH = _ROOT / "demo"
_STAND_PATH = _ROOT / "stand"


def _fake_git_repo(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    git_mock = MagicMock()
    git_mock.GitCommandError = git.GitCommandError
    git_mock.InvalidGitRepositoryError = git.InvalidGitRepositoryError
    git_mock.NoSuchPathError = git.NoSuchPathError
    # The implementation uses `with git.Repo(...) as r:`, so __enter__ must return
    # the same mock that tests assert against.
    git_mock.Repo.return_value.__enter__.return_value = git_mock.Repo.return_value
    monkeypatch.setattr(write_repo_repository, "git", git_mock)
    monkeypatch.setattr(read_repo_repository, "git", git_mock)
    return git_mock


@pytest.fixture
def error_factory() -> RepoErrorFactory:
    return RepoErrorFactory()


@pytest.fixture
def git_ops(error_factory: RepoErrorFactory) -> GitOpsService:
    return GitOpsService(error_factory, sleep=lambda _: None, jitter=lambda: 0.0)


@pytest.fixture
def repo(error_factory: RepoErrorFactory, git_ops: GitOpsService) -> WriteRepoRepository:
    return WriteRepoRepository(error_factory=error_factory, git_ops=git_ops)


def _wt(path: Path, name: str = "demo", main_branch: str = "main") -> FeatureWorktree:
    workspace = Workspace(root_path=path.parent, service_prefix="t", main_branch=main_branch)
    env = FeatureEnvironment(workspace=workspace, name="alpha", index=1, path=path.parent)
    project_repo = ProjectRepository(name=name, main_path=path, main_branch=main_branch)
    return FeatureWorktree(workspace=workspace, environment=env, repository=project_repo)


def test_fetch_raises_structured_repo_error_on_missing_remote(
    monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository
) -> None:
    git_mock = _fake_git_repo(monkeypatch)
    git_mock.Repo.return_value.git.fetch.side_effect = git.GitCommandError(
        ("git", "fetch", "origin"), 128, stderr=b"no such remote 'origin'"
    )
    wt = _wt(_REPO_PATH)

    with pytest.raises(RepoError) as ei:
        repo.fetch(wt)

    err = ei.value
    assert err.subcommand == "fetch"
    assert "origin" in err.cmd_args
    assert err.cwd is not None and "demo" in err.cwd
    assert err.exit_code is not None and err.exit_code != 0
    assert err.stderr


def test_count_commits_not_in_raises_for_bogus_ref(monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository) -> None:
    git_mock = _fake_git_repo(monkeypatch)
    git_mock.Repo.return_value.git.rev_list.side_effect = git.GitCommandError(
        ("git", "rev-list", "--count"), 128, stderr=b"unknown revision"
    )
    wt = _wt(_REPO_PATH)

    with pytest.raises(RepoError) as ei:
        repo.count_commits_not_in(wt, "refs/heads/does-not-exist")

    assert ei.value.subcommand == "rev-list"


def test_force_checkout_env_branch_raises_for_bogus_ref(
    monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository
) -> None:
    git_mock = _fake_git_repo(monkeypatch)
    git_mock.Repo.return_value.git.checkout.side_effect = git.GitCommandError(
        ("git", "checkout", "--force", "-B", "alpha", "--no-track"), 128, stderr=b"ambiguous argument"
    )
    wt = _wt(_REPO_PATH)

    with pytest.raises(RepoError) as ei:
        repo.force_checkout_env_branch(wt, "refs/heads/does-not-exist")

    assert ei.value.subcommand == "checkout"


def test_force_checkout_env_branch_checks_out_env_branch_forced_untracked(
    monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository
) -> None:
    """`force_checkout_env_branch` re-attaches HEAD to `worktree.environment.name` via `checkout -B`,
    not a bare `git reset --hard` — the fix for winter#159 (a detached HEAD
    stays detached after a plain reset). `--no-track` keeps it from writing its
    own upstream config; `set_upstream` remains the sole writer of tracking.
    """
    git_mock = _fake_git_repo(monkeypatch)
    r = git_mock.Repo.return_value
    wt = _wt(_REPO_PATH)

    repo.force_checkout_env_branch(wt, "origin/feature/widget")

    r.git.checkout.assert_called_once_with("--force", "-B", "alpha", "--no-track", "origin/feature/widget")
    r.git.reset.assert_not_called()


@pytest.mark.parametrize(
    ("mode", "flag"),
    [(ResetMode.soft, "--soft"), (ResetMode.mixed, "--mixed"), (ResetMode.hard, "--hard")],
)
def test_reset_to_runs_the_literal_git_reset_flag(
    monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository, mode: ResetMode, flag: str
) -> None:
    """`reset_to` is the plain `git reset --soft|--mixed|--hard <ref>` primitive —
    unlike `force_checkout_env_branch`, it never calls `checkout` and never touches the
    checked-out branch name.
    """
    git_mock = _fake_git_repo(monkeypatch)
    r = git_mock.Repo.return_value
    wt = _wt(_REPO_PATH)

    repo.reset_to(wt, mode, "origin/feature/widget")

    r.git.reset.assert_called_once_with(flag, "origin/feature/widget")
    r.git.checkout.assert_not_called()


def test_reset_to_raises_for_bogus_ref(monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository) -> None:
    git_mock = _fake_git_repo(monkeypatch)
    git_mock.Repo.return_value.git.reset.side_effect = git.GitCommandError(
        ("git", "reset", "--hard"), 128, stderr=b"ambiguous argument"
    )
    wt = _wt(_REPO_PATH)

    with pytest.raises(RepoError) as ei:
        repo.reset_to(wt, ResetMode.hard, "refs/heads/does-not-exist")

    assert ei.value.subcommand == "reset"


def test_push_standalone_raises_when_no_upstream(monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository) -> None:
    git_mock = _fake_git_repo(monkeypatch)
    git_mock.Repo.return_value.active_branch.tracking_branch.return_value = None
    standalone = StandaloneRepository(name="stand", path=_STAND_PATH)

    with pytest.raises(RepoError) as ei:
        repo.push_standalone(standalone)

    assert "no upstream" in ei.value.message
    assert ei.value.cwd is not None


def test_sync_ff_only_raises_on_failure(monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository) -> None:
    git_mock = _fake_git_repo(monkeypatch)
    git_mock.Repo.return_value.git.fetch.side_effect = git.GitCommandError(
        ("git", "fetch", "origin"), 128, stderr=b"no such remote"
    )
    project = ProjectRepository(name="demo", main_path=_REPO_PATH, main_branch="main")

    with pytest.raises(RepoError) as ei:
        repo.sync_ff_only(project)

    assert ei.value.subcommand in {"fetch", "merge"}


def test_push_returns_commit_count_against_remote_ref_when_present(
    monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository
) -> None:
    """Push count is computed from origin/<feature_branch>..HEAD when the remote ref exists.

    Regression: a feature with 14 commits already on `origin/feature/foo` plus
    1 fresh commit must report `1` pushed, not `15`.
    """
    git_mock = _fake_git_repo(monkeypatch)
    r = git_mock.Repo.return_value
    # rev_parse succeeds → remote ref exists
    r.git.rev_parse.return_value = "abc123"
    # rev_list returns the count of commits in the range
    r.git.rev_list.return_value = "1"
    wt = _wt(_REPO_PATH)

    result = repo.push(wt, feature_branch="feature/foo")

    assert result == 1
    # Must have checked for the remote ref...
    r.git.rev_parse.assert_called_with("--verify", "--quiet", "origin/feature/foo")
    # ...and counted HEAD..origin/feature/foo
    r.git.rev_list.assert_called_with("--count", "origin/feature/foo..HEAD")


def test_push_falls_back_to_main_branch_count_when_no_remote_ref(
    monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository
) -> None:
    """First push to a new remote branch: count commits past origin/<main_branch>."""
    git_mock = _fake_git_repo(monkeypatch)
    r = git_mock.Repo.return_value
    # rev_parse raises → remote ref does not exist yet
    r.git.rev_parse.side_effect = git.GitCommandError(("git", "rev-parse"), 128, stderr=b"unknown")
    r.git.rev_list.return_value = "3"
    wt = _wt(_REPO_PATH)

    result = repo.push(wt, feature_branch="feature/foo")

    assert result == 3
    r.git.rev_list.assert_called_with("--count", "origin/main..HEAD")


def test_set_upstream_writes_config_for_attached_branch(
    monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository
) -> None:
    git_mock = _fake_git_repo(monkeypatch)
    r = git_mock.Repo.return_value
    r.active_branch.name = "main"
    wt = _wt(_REPO_PATH)

    repo.set_upstream(wt, "origin/feature/widget")

    r.git.config.assert_any_call("branch.main.remote", "origin")
    r.git.config.assert_any_call("branch.main.merge", "refs/heads/feature/widget")


def test_set_upstream_falls_back_to_env_branch_when_head_detached(
    monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository
) -> None:
    """winter#159: a detached HEAD must not crash `set_upstream` — it configures
    tracking for `worktree.environment.name`, the branch `force_checkout_env_branch`
    re-attaches a detached worktree onto, instead of raising `TypeError` on `active_branch`.
    """
    git_mock = _fake_git_repo(monkeypatch)
    r = git_mock.Repo.return_value
    type(r).active_branch = PropertyMock(side_effect=TypeError("HEAD is a detached symbolic reference"))
    wt = _wt(_REPO_PATH)  # env name is "alpha"

    repo.set_upstream(wt, "origin/feature/widget")

    r.git.config.assert_any_call("branch.alpha.remote", "origin")
    r.git.config.assert_any_call("branch.alpha.merge", "refs/heads/feature/widget")


def test_set_upstream_warns_when_head_detached(
    monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository, caplog: pytest.LogCaptureFixture
) -> None:
    """winter#159 B5: a bare `winter ws connect` against a detached worktree still
    writes tracking config (see the fallback test above) but must surface a warning
    that the worktree stays detached and untracked — `EnvCheckoutService.checkout_env`
    never hits this path since it force-attaches HEAD before calling `set_upstream`.
    """
    git_mock = _fake_git_repo(monkeypatch)
    r = git_mock.Repo.return_value
    type(r).active_branch = PropertyMock(side_effect=TypeError("HEAD is a detached symbolic reference"))
    wt = _wt(_REPO_PATH)

    with caplog.at_level("WARNING"):
        repo.set_upstream(wt, "origin/feature/widget")

    assert "detached" in caplog.text


def test_unset_upstream_is_idempotent_when_no_upstream(
    monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository
) -> None:
    git_mock = _fake_git_repo(monkeypatch)
    r = git_mock.Repo.return_value
    r.active_branch.name = "main"
    config_not_found = git.GitCommandError(("git", "config", "--get"), 1, stderr=b"")
    config_not_found.status = 1
    r.git.config.side_effect = config_not_found
    wt = _wt(_REPO_PATH)

    repo.unset_upstream(wt)

    r.git.branch.assert_not_called()


def test_unset_upstream_falls_back_to_env_branch_when_head_detached(
    monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository
) -> None:
    """winter#159: `winter ws disconnect` against a detached worktree must not
    raise `TypeError` — it probes/unsets tracking for `worktree.environment.name`
    instead of the unreadable `active_branch`, and passes that name explicitly to
    `--unset-upstream` (the bare, HEAD-implicit form raises on a detached HEAD too).
    """
    git_mock = _fake_git_repo(monkeypatch)
    r = git_mock.Repo.return_value
    type(r).active_branch = PropertyMock(side_effect=TypeError("HEAD is a detached symbolic reference"))
    r.git.config.return_value = "origin"  # upstream is configured
    wt = _wt(_REPO_PATH)  # env name is "alpha"

    repo.unset_upstream(wt)

    r.git.config.assert_any_call("--get", "branch.alpha.remote")
    r.git.branch.assert_called_once_with("--unset-upstream", "alpha")


def test_get_worktree_upstream_returns_tracking_branch_name(
    monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository
) -> None:
    git_mock = _fake_git_repo(monkeypatch)
    r = git_mock.Repo.return_value
    tb = MagicMock()
    tb.name = "origin/feature-123"
    r.active_branch.tracking_branch.return_value = tb
    r.git.rev_parse.return_value = ""  # ref resolves in the local store
    wt = _wt(_REPO_PATH)

    assert repo.get_worktree_upstream(wt) == "origin/feature-123"


def test_get_worktree_upstream_returns_none_when_unset(
    monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository
) -> None:
    git_mock = _fake_git_repo(monkeypatch)
    r = git_mock.Repo.return_value
    r.active_branch.tracking_branch.return_value = None
    wt = _wt(_REPO_PATH)

    assert repo.get_worktree_upstream(wt) is None


def test_get_worktree_upstream_returns_none_when_configured_ref_does_not_resolve(
    monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository
) -> None:
    """A connected feature branch that origin doesn't have yet reads as no upstream.

    `winter ws connect` writes `branch.<head>.{remote,merge}` without a matching
    remote ref, so `tracking_branch()` reports a name that doesn't resolve. Pull
    must see None here (→ benign no_upstream skip), not a non-resolving ref it
    would try to integrate and mis-report as divergence.
    """
    git_mock = _fake_git_repo(monkeypatch)
    r = git_mock.Repo.return_value
    tb = MagicMock()
    tb.name = "origin/feature/ghost-branch"
    r.active_branch.tracking_branch.return_value = tb
    unknown_rev = git.GitCommandError(("git", "rev-parse"), 128, stderr=b"unknown revision")
    r.git.rev_parse.side_effect = unknown_rev
    wt = _wt(_REPO_PATH)

    assert repo.get_worktree_upstream(wt) is None


def test_get_worktree_push_branch_reads_bare_branch_from_config(
    monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository
) -> None:
    """Resolves the bare push branch from config — works even with no remote-tracking ref.

    Reads `branch.<head>.{remote,merge}` directly rather than via
    `tracking_branch()`, so a freshly connected, never-fetched feature
    branch (the first-push case) still resolves a target.
    """
    git_mock = _fake_git_repo(monkeypatch)
    r = git_mock.Repo.return_value
    r.active_branch.name = "alpha"
    r.active_branch.tracking_branch.return_value = None  # never fetched: no remote-tracking ref
    r.git.config.side_effect = ["origin", "refs/heads/feature/never-fetched"]
    wt = _wt(_REPO_PATH)

    assert repo.get_worktree_push_branch(wt) == "feature/never-fetched"


def test_get_worktree_push_branch_returns_none_when_no_upstream(
    monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository
) -> None:
    git_mock = _fake_git_repo(monkeypatch)
    r = git_mock.Repo.return_value
    r.active_branch.name = "alpha"
    config_not_found = git.GitCommandError(("git", "config", "--get"), 1, stderr=b"")
    config_not_found.status = 1
    r.git.config.side_effect = config_not_found
    wt = _wt(_REPO_PATH)

    assert repo.get_worktree_push_branch(wt) is None


def test_get_worktree_push_branch_returns_none_for_non_origin_remote(
    monkeypatch: pytest.MonkeyPatch, repo: WriteRepoRepository
) -> None:
    git_mock = _fake_git_repo(monkeypatch)
    r = git_mock.Repo.return_value
    r.active_branch.name = "alpha"
    r.git.config.side_effect = ["upstream", "refs/heads/feature/x"]
    wt = _wt(_REPO_PATH)

    assert repo.get_worktree_push_branch(wt) is None
