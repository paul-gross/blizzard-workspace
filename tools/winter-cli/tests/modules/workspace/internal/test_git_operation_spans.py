"""Git spans at the GitPython boundary, through a fake `IOperationTracer`.

The adapters run real git against temporary repos; the fake tracer records which span each
repository-opening call opened and what attributes it carried. `winter.env` is read from the
call's own arguments, so a source checkout and a standalone carry `winter.repo` only, even when
the standalone lives at a path that looks like a feature worktree.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from tests.conftest import FakeOperationTracer
from tests.modules.workspace.conftest import add_env_worktree, git_cmd, init_project, init_repo
from winter_cli.modules.workspace.internal.git_operation import GitOperationDeclaration, GitOperationExemption
from winter_cli.modules.workspace.internal.git_ops_service import GitOpsService
from winter_cli.modules.workspace.internal.gitpython_repository import GitPythonRepository
from winter_cli.modules.workspace.internal.read_repo_repository import ReadRepoRepository
from winter_cli.modules.workspace.internal.read_workspace_repository import ReadWorkspaceRepository
from winter_cli.modules.workspace.internal.repo_error_factory import RepoErrorFactory
from winter_cli.modules.workspace.internal.write_repo_repository import WriteRepoRepository
from winter_cli.modules.workspace.models import (
    FeatureEnvironment,
    FeatureWorktree,
    ProjectRepository,
    StandaloneRepository,
    Workspace,
)


@pytest.fixture
def tracer() -> FakeOperationTracer:
    return FakeOperationTracer()


def _worktree(workspace: Workspace, project: ProjectRepository, env_name: str, tmp_path: Path) -> FeatureWorktree:
    env = FeatureEnvironment(workspace=workspace, name=env_name, index=1, path=tmp_path / env_name)
    return FeatureWorktree(workspace=workspace, environment=env, repository=project)


# ── winter.env comes from the call's own arguments ───────────────────────────


def test_feature_worktree_call_on_read_repo_carries_repo_and_env(tmp_path: Path, tracer: FakeOperationTracer) -> None:
    workspace, project = init_project(tmp_path)
    add_env_worktree(project.main_path, tmp_path, "alpha", "main")
    reader = ReadRepoRepository(RepoErrorFactory(), tracer)

    reader.get_worktree_status(_worktree(workspace, project, "alpha", tmp_path))

    assert [(span.name, span.attributes) for span in tracer.spans] == [
        ("git status", {"winter.repo": "demo", "winter.env": "alpha"})
    ]


def test_feature_worktree_call_on_write_repo_carries_repo_and_env(tmp_path: Path, tracer: FakeOperationTracer) -> None:
    workspace, project = init_project(tmp_path)
    add_env_worktree(project.main_path, tmp_path, "alpha", "main")
    error_factory = RepoErrorFactory()
    writer = WriteRepoRepository(error_factory, GitOpsService(error_factory), tracer)

    assert writer.has_local_ref(_worktree(workspace, project, "alpha", tmp_path), "main") is True

    assert [(span.name, span.attributes) for span in tracer.spans] == [
        ("git rev-parse", {"winter.repo": "demo", "winter.env": "alpha"})
    ]


def test_source_checkout_call_carries_repo_only(tmp_path: Path, tracer: FakeOperationTracer) -> None:
    _workspace, project = init_project(tmp_path)
    reader = ReadRepoRepository(RepoErrorFactory(), tracer)

    reader.get_project_status(project)

    assert [(span.name, span.attributes) for span in tracer.spans] == [("git status", {"winter.repo": "demo"})]


def test_standalone_call_carries_repo_only(tmp_path: Path, tracer: FakeOperationTracer) -> None:
    standalone_path = tmp_path / "ext"
    init_repo(standalone_path)
    reader = ReadRepoRepository(RepoErrorFactory(), tracer)

    reader.get_standalone_status(StandaloneRepository(name="ext", path=standalone_path))
    reader.get_standalone_detail(StandaloneRepository(name="ext", path=standalone_path))

    assert [(span.name, span.attributes) for span in tracer.spans] == [
        ("git status", {"winter.repo": "ext"}),
        ("git status", {"winter.repo": "ext"}),
    ]


def test_standalone_at_a_path_that_looks_like_a_worktree_still_carries_no_env(
    tmp_path: Path, tracer: FakeOperationTracer
) -> None:
    """The env is never derived from a path: a standalone configured at `<dir>/<name>` is not a worktree."""
    mimic = tmp_path / "alpha" / "demo"
    init_repo(mimic)
    reader = ReadRepoRepository(RepoErrorFactory(), tracer)

    reader.get_standalone_status(StandaloneRepository(name="demo", path=mimic))

    assert [span.attributes for span in tracer.spans] == [{"winter.repo": "demo"}]


def test_standalone_call_on_write_repo_carries_repo_only(tmp_path: Path, tracer: FakeOperationTracer) -> None:
    standalone_path = tmp_path / "ext"
    init_repo(standalone_path)
    error_factory = RepoErrorFactory()
    writer = WriteRepoRepository(error_factory, GitOpsService(error_factory), tracer)

    writer.get_standalone_upstream(StandaloneRepository(name="ext", path=standalone_path))

    assert [(span.name, span.attributes) for span in tracer.spans] == [("git config", {"winter.repo": "ext"})]


# ── path-taking private openers receive the env ──────────────────────────────


def test_workspace_repository_feature_branch_read_carries_the_env_it_was_given(
    tmp_path: Path, tracer: FakeOperationTracer
) -> None:
    workspace, project = init_project(tmp_path)
    add_env_worktree(project.main_path, tmp_path, "alpha", "main")
    env = FeatureEnvironment(workspace=workspace, name="alpha", index=1, path=tmp_path / "alpha")
    reader = ReadWorkspaceRepository(RepoErrorFactory(), tracer)

    reader.get_environment_status(env, [project])

    assert [(span.name, span.attributes) for span in tracer.spans] == [
        ("git config", {"winter.repo": "demo", "winter.env": "alpha"})
    ]


# ── IGitRepository: the caller names the repo and the env ────────────────────


def test_worktree_call_through_igitrepository_carries_the_repo_and_env_it_was_given(
    tmp_path: Path, tracer: FakeOperationTracer
) -> None:
    _workspace, project = init_project(tmp_path)
    git_repo = GitPythonRepository(RepoErrorFactory(), tracer)

    git_repo.add_worktree(
        project.main_path, tmp_path / "alpha" / "demo", "alpha", base_branch="main", repo_name="demo", env="alpha"
    )

    assert [(span.name, span.attributes) for span in tracer.spans] == [
        ("git worktree add", {"winter.repo": "demo", "winter.env": "alpha"})
    ]


def test_igitrepository_call_outside_a_worktree_carries_the_repo_only(
    tmp_path: Path, tracer: FakeOperationTracer
) -> None:
    _workspace, project = init_project(tmp_path)
    git_repo = GitPythonRepository(RepoErrorFactory(), tracer)

    branches = git_repo.get_local_branches(project.main_path, repo_name="demo", env=None)

    assert branches == ["main"]
    assert [(span.name, span.attributes) for span in tracer.spans] == [("git branch", {"winter.repo": "demo"})]


def test_clone_carries_the_repo_only(tmp_path: Path, tracer: FakeOperationTracer) -> None:
    _workspace, project = init_project(tmp_path)
    git_repo = GitPythonRepository(RepoErrorFactory(), tracer)

    git_repo.clone(str(project.main_path), tmp_path / "copy", repo_name="demo")

    assert (tmp_path / "copy" / ".git").exists()
    assert [(span.name, span.attributes) for span in tracer.spans] == [("git clone", {"winter.repo": "demo"})]


def test_every_spanned_igitrepository_method_takes_the_repo_name() -> None:
    """A path is never read for the repo name, so every method that opens a span must be told it."""
    missing = [
        name
        for name, member in vars(GitPythonRepository).items()
        if callable(member)
        and not name.startswith("_")
        and name != "list_worktrees"
        and "repo_name" not in inspect.signature(member).parameters
    ]

    assert missing == []


def test_list_worktrees_opens_no_span(tmp_path: Path, tracer: FakeOperationTracer) -> None:
    """Env discovery is workspace discovery: PD-4 exempts it from spanning."""
    _workspace, project = init_project(tmp_path)
    worktree_path = add_env_worktree(project.main_path, tmp_path, "alpha", "main")
    git_repo = GitPythonRepository(RepoErrorFactory(), tracer)

    paths = git_repo.list_worktrees(project.main_path)

    assert worktree_path.resolve() in [path.resolve() for path in paths]
    assert tracer.spans == []


def test_failing_igitrepository_call_marks_its_span_failed_and_reraises(
    tmp_path: Path, tracer: FakeOperationTracer
) -> None:
    """The error passes through the declaration unchanged, and the span it opened is marked failed."""
    _workspace, project = init_project(tmp_path)
    git_repo = GitPythonRepository(RepoErrorFactory(), tracer)

    with pytest.raises(Exception) as caught:
        git_repo.checkout_detached(project.main_path, "no-such-commit", repo_name="demo", env=None)

    assert type(caught.value).__name__ == "RepoError"
    (span,) = tracer.spans
    assert (span.name, span.failed, span.error_type) == ("git checkout", True, "RepoError")
    assert git_cmd(project.main_path, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


# ── The declaration itself ───────────────────────────────────────────────────


class _Adapter:
    def __init__(self, tracer: FakeOperationTracer) -> None:
        self._tracer = tracer

    @GitOperationDeclaration("fetch")
    def outer(self, worktree: FeatureWorktree, ref: str, *, flag: bool = False) -> tuple[str, bool]:
        """Docstring survives."""
        self.inner(worktree.repository, env=worktree.environment.name)
        return ref, flag

    @GitOperationDeclaration("status")
    def inner(self, repo: ProjectRepository, *, env: str | None) -> None:
        return None

    @GitOperationDeclaration("rev-list")
    def explode(self, repo_name: str, *, env: str | None) -> None:
        raise ValueError("boom")

    @GitOperationExemption("not an operation on a repository")
    def exempt(self, value: int) -> int:
        return value + 1


def test_declaration_passes_arguments_and_the_return_value_through(tmp_path: Path) -> None:
    tracer = FakeOperationTracer()
    workspace, project = init_project(tmp_path)

    result = _Adapter(tracer).outer(_worktree(workspace, project, "alpha", tmp_path), "main", flag=True)

    assert result == ("main", True)
    assert _Adapter.outer.__name__ == "outer"
    assert _Adapter.outer.__doc__ == "Docstring survives."


def test_declared_function_that_calls_another_nests_it(tmp_path: Path) -> None:
    tracer = FakeOperationTracer()
    workspace, project = init_project(tmp_path)

    _Adapter(tracer).outer(_worktree(workspace, project, "alpha", tmp_path), "main")

    assert [(span.name, span.attributes) for span in tracer.spans] == [
        ("git fetch", {"winter.repo": "demo", "winter.env": "alpha"}),
        ("git status", {"winter.repo": "demo", "winter.env": "alpha"}),
    ]


def test_declared_function_reads_repo_name_and_env_from_a_path_taking_opener(tmp_path: Path) -> None:
    tracer = FakeOperationTracer()
    adapter = _Adapter(tracer)

    with pytest.raises(ValueError, match="boom"):
        adapter.explode("demo", env="beta")

    (span,) = tracer.spans
    assert (span.name, span.attributes, span.failed, span.error_type) == (
        "git rev-list",
        {"winter.repo": "demo", "winter.env": "beta"},
        True,
        "ValueError",
    )


def test_exemption_opens_no_span_and_leaves_the_function_unchanged() -> None:
    tracer = FakeOperationTracer()

    assert _Adapter(tracer).exempt(1) == 2
    assert tracer.spans == []
