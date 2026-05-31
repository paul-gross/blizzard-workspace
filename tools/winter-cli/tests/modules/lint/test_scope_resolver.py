from __future__ import annotations

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
from winter_cli.modules.lint.scope_resolver import LintScopeResolver
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
