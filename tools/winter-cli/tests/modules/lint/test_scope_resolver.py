from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import FakeSubprocessRunner
from winter_cli.config.models import (
    ProjectRepositoryConfig,
    StandaloneRepositoryConfig,
    WorkspaceConfig,
)
from winter_cli.core.subprocess_runner import SubprocessResult
from winter_cli.modules.lint.models import LintScopeError, LintScopeKind, LintScopeRequest
from winter_cli.modules.lint.scope_resolver import LintScopeResolver, parse_porcelain_z
from winter_cli.modules.workspace.models import FeatureEnvironment, Workspace
from winter_cli.modules.workspace.repository_factory import RepositoryFactory

WS = Path("/ws")


def _config(*, project: tuple[str, ...] = ("app",), standalone: tuple[str, ...] = ("ext",)) -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=WS,
        session_prefix="t",
        main_branch="main",
        project_repos=[ProjectRepositoryConfig(name=n, url=f"git@x:o/{n}.git") for n in project],
        standalone_repos=[StandaloneRepositoryConfig(name=n, url=f"git@x:o/{n}.git", path=n) for n in standalone],
    )


class _FakeRepoRepo:
    def get_workspace(self, root: Path, prefix: str, main: str) -> Workspace:
        return Workspace(root_path=root, session_prefix=prefix, main_branch=main)


class _FakeWorktreeRepo:
    def __init__(self, env_names: list[str]) -> None:
        self._env_names = env_names

    def get_environments(self, workspace: Workspace, project_repos: list) -> list[FeatureEnvironment]:
        return [
            FeatureEnvironment(workspace=workspace, name=name, index=i + 1, path=WS / name)
            for i, name in enumerate(self._env_names)
        ]


def _resolver(
    config: WorkspaceConfig | None = None,
    *,
    env_names: list[str] | None = None,
    runner: FakeSubprocessRunner | None = None,
) -> LintScopeResolver:
    config = config or _config()
    return LintScopeResolver(
        config=config,
        repo_factory=RepositoryFactory(config=config),
        worktree_repo=_FakeWorktreeRepo(env_names or ["alpha"]),  # type: ignore[arg-type]
        repo_repo=_FakeRepoRepo(),  # type: ignore[arg-type]
        subprocess_runner=runner or FakeSubprocessRunner(),
    )


def test_default_and_all_resolve_to_workspace_root() -> None:
    resolver = _resolver()
    for request in (LintScopeRequest(), LintScopeRequest(all=True)):
        scope = resolver.resolve(request)
        assert scope.kind == LintScopeKind.all
        assert scope.paths == [WS]


def test_mutually_exclusive_sources_raise() -> None:
    resolver = _resolver()
    with pytest.raises(LintScopeError, match="mutually exclusive"):
        resolver.resolve(LintScopeRequest(name="app", all=True))


def test_project_repo_name_resolves_to_main_path() -> None:
    scope = _resolver().resolve(LintScopeRequest(name="app"))
    assert scope.kind == LintScopeKind.repo
    assert scope.paths == [WS / "projects" / "app"]


def test_standalone_repo_name_resolves_to_its_path() -> None:
    scope = _resolver().resolve(LintScopeRequest(name="ext"))
    assert scope.kind == LintScopeKind.repo
    assert scope.paths == [WS / "ext"]


def test_env_name_resolves_to_each_worktree_path() -> None:
    scope = _resolver(env_names=["alpha"]).resolve(LintScopeRequest(name="alpha"))
    assert scope.kind == LintScopeKind.env
    assert scope.paths == [WS / "alpha" / "app"]


def test_unknown_name_raises() -> None:
    with pytest.raises(LintScopeError, match="unknown scope"):
        _resolver().resolve(LintScopeRequest(name="nope"))


def test_name_matching_both_repo_and_env_is_ambiguous() -> None:
    config = _config(project=("alpha",), standalone=())
    resolver = _resolver(config, env_names=["alpha"])
    with pytest.raises(LintScopeError, match="matches both"):
        resolver.resolve(LintScopeRequest(name="alpha"))


# ── --changed ────────────────────────────────────────────────────────────

REPO = Path("/repo")


def _git_responses(extra: dict[str, SubprocessResult]) -> FakeSubprocessRunner:
    base = {f"git -C {REPO} rev-parse --show-toplevel": SubprocessResult(0, f"{REPO}\n", "")}
    base.update(extra)
    return FakeSubprocessRunner(run_responses=base)


def test_changed_unions_dirty_and_unpushed_paths_and_dedupes() -> None:
    # `-z` output: NUL-terminated, unquoted; a rename spans two fields (new, orig).
    runner = _git_responses(
        {
            f"git -C {REPO} status --porcelain -z": SubprocessResult(
                0, " M src/a.py\x00?? new file.txt\x00R  renamed.py\x00old.py\x00", ""
            ),
            f"git -C {REPO} rev-parse --abbrev-ref --symbolic-full-name @{{u}}": SubprocessResult(
                0, "origin/feature\n", ""
            ),
            f"git -C {REPO} diff --name-only -z origin/feature..HEAD": SubprocessResult(
                0, "src/b.py\x00src/a.py\x00", ""
            ),
        }
    )
    scope = _resolver(runner=runner).resolve(LintScopeRequest(changed=True, cwd=REPO))

    assert scope.kind == LintScopeKind.changed
    assert scope.label == "changed (repo)"
    # `old.py` (the rename source) is skipped; `src/a.py` dedupes across status + diff.
    assert scope.paths == [
        REPO / "src/a.py",
        REPO / "new file.txt",
        REPO / "renamed.py",
        REPO / "src/b.py",
    ]


def test_changed_falls_back_to_origin_main_without_upstream() -> None:
    runner = _git_responses(
        {
            f"git -C {REPO} status --porcelain -z": SubprocessResult(0, "", ""),
            f"git -C {REPO} rev-parse --abbrev-ref --symbolic-full-name @{{u}}": SubprocessResult(
                128, "", "no upstream"
            ),
            f"git -C {REPO} rev-parse --verify --quiet origin/main": SubprocessResult(0, "abc123\n", ""),
            f"git -C {REPO} diff --name-only -z origin/main..HEAD": SubprocessResult(0, "only.py\x00", ""),
        }
    )
    scope = _resolver(runner=runner).resolve(LintScopeRequest(changed=True, cwd=REPO))
    assert scope.paths == [REPO / "only.py"]


def test_changed_outside_a_git_repo_raises() -> None:
    runner = FakeSubprocessRunner(
        run_responses={f"git -C {REPO} rev-parse --show-toplevel": SubprocessResult(128, "", "not a git repo")}
    )
    with pytest.raises(LintScopeError, match="inside a git repository"):
        _resolver(runner=runner).resolve(LintScopeRequest(changed=True, cwd=REPO))


# ── parse_porcelain_z unit tests ──────────────────────────────────────────────


def test_parse_porcelain_z_modified_file() -> None:
    """A plain modified entry produces a single path."""
    assert parse_porcelain_z(" M src/foo.py\x00") == ["src/foo.py"]


def test_parse_porcelain_z_untracked_file() -> None:
    """An untracked entry (??) produces a single path."""
    assert parse_porcelain_z("?? new file.txt\x00") == ["new file.txt"]


def test_parse_porcelain_z_rename_keeps_new_path_skips_old() -> None:
    """A rename entry (R) keeps the new path and skips the original."""
    output = "R  renamed.py\x00old.py\x00"
    assert parse_porcelain_z(output) == ["renamed.py"]


def test_parse_porcelain_z_copy_keeps_new_path_skips_original() -> None:
    """A copy entry (C) keeps the new path and skips the source."""
    output = "C  copied.py\x00source.py\x00"
    assert parse_porcelain_z(output) == ["copied.py"]


def test_parse_porcelain_z_mixed_entries_in_order() -> None:
    """Rename in the middle does not desync subsequent entries."""
    output = " M src/a.py\x00R  renamed.py\x00old.py\x00?? new.txt\x00"
    assert parse_porcelain_z(output) == ["src/a.py", "renamed.py", "new.txt"]


def test_parse_porcelain_z_empty_output() -> None:
    """Empty output returns an empty list."""
    assert parse_porcelain_z("") == []


# ── real-git rename test ──────────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    """Run a git command in *cwd* and return stdout."""
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_parse_porcelain_z_with_real_git_staged_rename(tmp_path: Path) -> None:
    """parse_porcelain_z correctly parses a staged rename from a real git repo.

    This test exercises the actual ``git status --porcelain -z`` wire format so
    format drift is caught rather than relying on a hand-crafted mock string.
    The old index-walk logic (``i += 2`` on R entries) is what this test validates;
    if the parse logic desynced, subsequent entries after the rename would be dropped.
    """
    # Init a repo with a user identity so git doesn't complain.
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")

    # Create two files: one we'll rename (original.py) and one that stays (keep.py).
    original = tmp_path / "original.py"
    keep = tmp_path / "keep.py"
    original.write_text("# original\n")
    keep.write_text("# keep\n")
    _git(tmp_path, "add", "original.py", "keep.py")
    _git(tmp_path, "commit", "-m", "initial")

    # Stage a rename: original.py → renamed.py (also modify keep.py to verify
    # that entries after the rename are not dropped by the index-walk).
    renamed = tmp_path / "renamed.py"
    original.rename(renamed)
    keep.write_text("# modified\n")
    _git(tmp_path, "add", "-A")

    # Run the real git command and parse its output.
    raw = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    paths = parse_porcelain_z(raw)

    # The rename source (original.py) must not appear; the rename destination
    # (renamed.py) and the modified file (keep.py) must both be present.
    assert "original.py" not in paths
    assert "renamed.py" in paths
    assert "keep.py" in paths
