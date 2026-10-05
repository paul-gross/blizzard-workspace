from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import (
    FakeConfigFileReader,
    FakeEnvIndexRegistry,
    FakeFilesystem,
    FakeGitRepository,
    FakeInitReporter,
    FakeSubprocessRunner,
    FakeWorkspaceExcludeLocator,
)
from winter_cli.config.models import (
    AdoptExtensions,
    ProjectRepositoryConfig,
    WorkspaceConfig,
)
from winter_cli.modules.workspace.destroy_service import DestroyService
from winter_cli.modules.workspace.extension_hook_service import ExtensionHookService
from winter_cli.modules.workspace.extension_manifest import ExtensionManifestLoader
from winter_cli.modules.workspace.models import RepoError
from winter_cli.modules.workspace.repository_factory import RepositoryFactory

WORKSPACE_ROOT = Path("/ws")
DEMO_MAIN = WORKSPACE_ROOT / "projects" / "demo"


@pytest.fixture
def workspace_config() -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        service_prefix="t",
        main_branch="main",
        adopt_extensions=AdoptExtensions.winter,
        project_repos=[
            ProjectRepositoryConfig(name="demo", url="git@example.com:org/demo.git"),
        ],
    )


def _service(
    workspace_config: WorkspaceConfig,
    fs: FakeFilesystem,
    git: FakeGitRepository,
    registry: FakeEnvIndexRegistry | None = None,
    exclude_path: Path | None = None,
    exclude_error: RepoError | None = None,
) -> DestroyService:
    hook_svc = ExtensionHookService(
        config=workspace_config,
        fs=fs,
        subprocess_runner=FakeSubprocessRunner(),
        manifest_loader=ExtensionManifestLoader(config_file_reader=FakeConfigFileReader({})),
    )
    return DestroyService(
        config=workspace_config,
        repo_factory=RepositoryFactory(workspace_config),
        extension_hook_svc=hook_svc,
        fs=fs,
        git_repo=git,
        registry=registry or FakeEnvIndexRegistry(),
        exclude_locator=FakeWorkspaceExcludeLocator(
            fs, workspace_config.workspace_root, override=exclude_path, error=exclude_error
        ),
    )


def test_destroy_env_removes_worktree_dir_and_env_dir(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    env_root = WORKSPACE_ROOT / "alpha"
    worktree_path = env_root / "demo"
    fs = FakeFilesystem(
        directories=[WORKSPACE_ROOT / "projects", DEMO_MAIN, env_root, worktree_path],
        files={
            env_root / ".winter.env": "WINTER_ENV=alpha\n",
            WORKSPACE_ROOT / ".git" / "info" / "exclude": "",
        },
    )
    git = FakeGitRepository()
    git.clean_worktrees.add(worktree_path)  # so the dirty-check passes

    svc = _service(workspace_config, fs, git)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is True
    # IGitRepository.remove_worktree called against the source checkout.
    assert git.removed_worktrees == [(DEMO_MAIN, worktree_path, False)]
    # rmtree-equivalent on the env root: the FakeFilesystem drops it.
    assert not fs.exists(env_root)
    # Reporter saw the env_removed action.
    assert any(a[2] == "env_removed" for a in init_reporter.actions)


def test_destroy_env_names_the_env_and_repo_on_every_worktree_git_call(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    env_root = WORKSPACE_ROOT / "alpha"
    worktree_path = env_root / "demo"
    fs = FakeFilesystem(
        directories=[WORKSPACE_ROOT / "projects", DEMO_MAIN, env_root, worktree_path],
        files={WORKSPACE_ROOT / ".git" / "info" / "exclude": ""},
    )
    git = FakeGitRepository()
    git.clean_worktrees.add(worktree_path)

    svc = _service(workspace_config, fs, git)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is True
    assert git.env_calls == [
        ("is_worktree_clean", worktree_path, "alpha"),
        ("remove_worktree", worktree_path, "alpha"),
    ]
    assert git.repo_name_calls == [
        ("is_worktree_clean", worktree_path, "demo"),
        ("remove_worktree", worktree_path, "demo"),
    ]


def test_destroy_env_refuses_dirty_worktree_without_force(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    env_root = WORKSPACE_ROOT / "alpha"
    worktree_path = env_root / "demo"
    fs = FakeFilesystem(
        directories=[WORKSPACE_ROOT / "projects", DEMO_MAIN, env_root, worktree_path],
    )
    git = FakeGitRepository()  # worktree_path NOT marked clean → dirty

    svc = _service(workspace_config, fs, git)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is False
    # Nothing was removed.
    assert git.removed_worktrees == []
    assert fs.exists(env_root)
    error_messages = [error for _, error in init_reporter.errors]
    assert any("dirty worktrees" in msg for msg in error_messages)


def test_destroy_env_dry_run_emits_actions_but_does_not_remove(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    env_root = WORKSPACE_ROOT / "alpha"
    worktree_path = env_root / "demo"
    exclude_path = WORKSPACE_ROOT / ".git" / "info" / "exclude"
    fs = FakeFilesystem(
        directories=[WORKSPACE_ROOT / "projects", DEMO_MAIN, env_root, worktree_path],
        files={
            exclude_path: "# >>> winter-dir/alpha (managed by winter)\n/alpha/\n# <<< winter-dir/alpha (managed by winter)\n"
        },
    )
    git = FakeGitRepository()
    git.clean_worktrees.add(worktree_path)  # dirty-check passes; dry-run only checks intent

    svc = _service(workspace_config, fs, git)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=True, reporter=init_reporter)

    assert ok is True
    assert git.removed_worktrees == []  # nothing actually removed
    assert fs.exists(env_root)
    action_kinds = [a[2] for a in init_reporter.actions]
    assert "would_remove_worktree" in action_kinds
    assert "would_remove_env" in action_kinds
    assert "would_remove_workspace_exclude" in action_kinds


def test_destroy_env_strips_workspace_exclude_block(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """The `winter-dir/<env>` managed block is removed from .git/info/exclude."""
    env_root = WORKSPACE_ROOT / "alpha"
    worktree_path = env_root / "demo"
    exclude_path = WORKSPACE_ROOT / ".git" / "info" / "exclude"
    initial = (
        "# unrelated user line\n"
        "# >>> winter-dir/alpha (managed by winter)\n"
        "/alpha/\n"
        "# <<< winter-dir/alpha (managed by winter)\n"
    )
    fs = FakeFilesystem(
        directories=[WORKSPACE_ROOT / "projects", DEMO_MAIN, env_root, worktree_path],
        files={exclude_path: initial},
    )
    git = FakeGitRepository()
    git.clean_worktrees.add(worktree_path)

    svc = _service(workspace_config, fs, git)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is True
    remaining = fs.files[exclude_path]
    assert "winter-dir/alpha" not in remaining
    assert "# unrelated user line" in remaining


def test_destroy_env_strips_the_block_from_the_located_exclude_file_only(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """A linked-worktree root keeps its block in its own git dir; the shared `.git/info/exclude` is not touched."""
    env_root = WORKSPACE_ROOT / "alpha"
    worktree_path = env_root / "demo"
    own_exclude = Path("/common/.git/worktrees/ws/info/exclude")
    shared_exclude = WORKSPACE_ROOT / ".git" / "info" / "exclude"
    block = "# >>> winter-dir/alpha (managed by winter)\n/alpha/\n# <<< winter-dir/alpha (managed by winter)\n"
    fs = FakeFilesystem(
        directories=[WORKSPACE_ROOT / "projects", DEMO_MAIN, env_root, worktree_path],
        files={own_exclude: "# keep\n" + block, shared_exclude: block},
    )
    git = FakeGitRepository()
    git.clean_worktrees.add(worktree_path)

    svc = _service(workspace_config, fs, git, exclude_path=own_exclude)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is True
    assert fs.files[own_exclude] == "# keep\n"
    assert fs.files[shared_exclude] == block


def test_destroy_env_with_missing_source_checkout_falls_back_to_rmtree(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """When the source checkout is gone too, the worktree dir is removed via rmtree."""
    env_root = WORKSPACE_ROOT / "alpha"
    worktree_path = env_root / "demo"
    fs = FakeFilesystem(directories=[env_root, worktree_path])  # no source checkout
    git = FakeGitRepository()
    git.clean_worktrees.add(worktree_path)

    svc = _service(workspace_config, fs, git)
    ok = svc.destroy_env("alpha", force=True, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is True
    assert git.removed_worktrees == []  # IGitRepository.remove_worktree not called
    assert not fs.exists(env_root)
    actions = [(a[0], a[2], a[3]) for a in init_reporter.actions]
    assert ("demo", "worktree_removed", "no source checkout") in actions


def test_destroy_env_removes_registry_entry(workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter) -> None:
    """Phase 6: destroy_env always removes the env's registry entry so the index can be reused."""
    env_root = WORKSPACE_ROOT / "alpha"
    worktree_path = env_root / "demo"
    fs = FakeFilesystem(
        directories=[WORKSPACE_ROOT / "projects", DEMO_MAIN, env_root, worktree_path],
        files={
            env_root / ".winter.env": "WINTER_ENV=alpha\n",
            WORKSPACE_ROOT / ".git" / "info" / "exclude": "",
        },
    )
    git = FakeGitRepository()
    git.clean_worktrees.add(worktree_path)

    registry = FakeEnvIndexRegistry(assignments={"alpha": 1})
    svc = _service(workspace_config, fs, git, registry=registry)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is True
    # Registry entry removed so the index can be reassigned.
    assert "alpha" not in registry.assignments
    assert "alpha" in registry.removed


class _ExplodingRemoveGit(FakeGitRepository):
    """FakeGitRepository whose remove_worktree raises — exercises the per-repo wrap site."""

    def remove_worktree(
        self, source: Path, worktree_path: Path, force: bool, *, repo_name: str, env: str | None
    ) -> None:
        raise RepoError("worktree busy")


def test_remove_git_worktree_failure_caught_at_wrap_site(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """A leaf RepoError from `remove_worktree` is caught once per repo;
    the aggregator records failure but continues to subsequent phases."""
    env_root = WORKSPACE_ROOT / "alpha"
    worktree_path = env_root / "demo"
    fs = FakeFilesystem(
        directories=[WORKSPACE_ROOT / "projects", DEMO_MAIN, env_root, worktree_path],
    )
    git = _ExplodingRemoveGit()
    git.clean_worktrees.add(worktree_path)  # dirty-check passes

    svc = _service(workspace_config, fs, git)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is False
    demo_errors = [msg for repo, msg in init_reporter.errors if repo == "demo"]
    assert len(demo_errors) == 1
    assert "worktree busy" in demo_errors[0]
    # Phase 4 still ran — env directory was removed despite the worktree failure.
    assert not fs.exists(env_root)


_DUBIOUS_OWNERSHIP = "detected dubious ownership in repository at '/ws'"


def test_destroy_env_still_finishes_when_the_exclude_file_cannot_be_located(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    """A git refusal while resolving the exclude file is reported and fails the env, but the
    registry entry is still removed and the target still completes, so a multi-env destroy goes on."""
    env_root = WORKSPACE_ROOT / "alpha"
    worktree_path = env_root / "demo"
    fs = FakeFilesystem(directories=[WORKSPACE_ROOT / "projects", DEMO_MAIN, env_root, worktree_path])
    git = FakeGitRepository()
    git.clean_worktrees.add(worktree_path)
    registry = FakeEnvIndexRegistry({"alpha": 1})

    svc = _service(workspace_config, fs, git, registry=registry, exclude_error=RepoError(_DUBIOUS_OWNERSHIP))
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is False
    assert not fs.exists(env_root)
    assert registry.removed == ["alpha"]
    assert init_reporter.targets_completed == [("alpha", False)]
    assert [msg for repo, msg in init_reporter.errors if repo == "alpha"] == [
        f"workspace exclude — {_DUBIOUS_OWNERSHIP}"
    ]


def test_destroy_env_dry_run_reports_an_unlocatable_exclude_file_without_raising(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    env_root = WORKSPACE_ROOT / "alpha"
    worktree_path = env_root / "demo"
    fs = FakeFilesystem(directories=[WORKSPACE_ROOT / "projects", DEMO_MAIN, env_root, worktree_path])
    git = FakeGitRepository()
    git.clean_worktrees.add(worktree_path)

    svc = _service(workspace_config, fs, git, exclude_error=RepoError(_DUBIOUS_OWNERSHIP))
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=True, reporter=init_reporter)

    assert ok is False
    assert fs.exists(env_root)
    assert init_reporter.targets_completed == [("alpha", False)]
    assert not any(action == "would_remove_workspace_exclude" for _, _, action, _ in init_reporter.actions)
    assert [msg for repo, msg in init_reporter.errors if repo == "alpha"] == [
        f"workspace exclude — {_DUBIOUS_OWNERSHIP}"
    ]
