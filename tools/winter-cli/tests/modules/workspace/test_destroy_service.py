from __future__ import annotations

from pathlib import Path
from typing import Any

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
from tests.modules.workspace.conftest import (
    FakeNestedWorkspaceRunner,
    make_nested_service,
    nested_env,
    nested_status,
    nested_wt,
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
    nested_runner: FakeNestedWorkspaceRunner | None = None,
    provision_svc: Any | None = None,
    nested_layers: dict[Path, tuple[dict[str, Any], dict[str, Any]]] | None = None,
    malformed_nested_config: bool = False,
) -> DestroyService:
    """A `DestroyService` over fakes.

    *nested_layers* maps a nested root to its committed and local config
    layers; *malformed_nested_config* makes every nested config unreadable.
    """
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
        provision_svc=provision_svc,
        nested_svc=(
            make_nested_service(
                nested_runner,
                config_files=_config_files(nested_layers or {}),
                broken_config_files=_config_files(nested_layers or {}) if malformed_nested_config else (),
            )
            if nested_runner is not None
            else None
        ),
    )


def _config_files(layers: dict[Path, tuple[dict[str, Any], dict[str, Any]]]) -> dict[Path, dict[str, Any]]:
    """Each nested root's committed and local layers, keyed by the config file each is read from."""
    files: dict[Path, dict[str, Any]] = {}
    for root, (committed, local) in layers.items():
        files[root / ".winter" / "config.toml"] = committed
        files[root / ".winter" / "config.local.toml"] = local
    return files


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


# ── nested workspaces ─────────────────────────────────────────────────────────

LAB_MAIN = WORKSPACE_ROOT / "projects" / "lab"
ALPHA = WORKSPACE_ROOT / "alpha"
LAB_ALPHA = ALPHA / "lab"
TWO_ENVS = nested_status(LAB_ALPHA, nested_env("n1"), nested_env("n2"))


def _nested_config() -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        service_prefix="t",
        main_branch="main",
        adopt_extensions=AdoptExtensions.winter,
        project_repos=[
            ProjectRepositoryConfig(name="lab", url="git@example.com:org/lab.git", nested=True, extension=False),
        ],
    )


def _nested_fs() -> FakeFilesystem:
    return FakeFilesystem(
        directories=[WORKSPACE_ROOT / "projects", LAB_MAIN, ALPHA, LAB_ALPHA],
        files={WORKSPACE_ROOT / ".git" / "info" / "exclude": ""},
    )


class _LoggingGit(FakeGitRepository):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self._events = events

    def remove_worktree(
        self, source: Path, worktree_path: Path, force: bool, *, repo_name: str, env: str | None
    ) -> None:
        self._events.append(f"remove_worktree:{repo_name}")
        super().remove_worktree(source, worktree_path, force, repo_name=repo_name, env=env)


class _LoggingProvisionService:
    """Records each teardown subtarget into the shared event log; always succeeds."""

    def __init__(self, events: list[str]) -> None:
        self._events = events

    def run(self, *, env_name: str, subtarget: str | None, reporter: Any, **_: Any) -> Any:
        self._events.append(f"provision:{subtarget}")

        class _Ok:
            status = "ok"

        return _Ok()


def test_destroy_env_destroys_nested_envs_before_teardown_and_worktree_removal(init_reporter: FakeInitReporter) -> None:
    events: list[str] = []
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: TWO_ENVS}, events=events)
    git = _LoggingGit(events)
    git.clean_worktrees.add(LAB_ALPHA)
    fs = _nested_fs()

    svc = _service(_nested_config(), fs, git, nested_runner=runner, provision_svc=_LoggingProvisionService(events))
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is True
    assert events == [
        "nested_destroy:n1",
        "nested_destroy:n2",
        "provision:data",
        "provision:resource",
        "remove_worktree:lab",
    ]
    assert [a[3] for a in init_reporter.actions if a[2] == "nested_env_destroyed"] == ["n1", "n2"]
    assert not fs.exists(ALPHA)


@pytest.mark.parametrize(
    ("force", "strict", "provision_teardown"),
    [(False, False, True), (True, True, False)],
)
def test_destroy_env_passes_flags_through_to_the_nested_destroy(
    init_reporter: FakeInitReporter, force: bool, strict: bool, provision_teardown: bool
) -> None:
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: TWO_ENVS})
    git = FakeGitRepository()
    git.clean_worktrees.add(LAB_ALPHA)

    svc = _service(_nested_config(), _nested_fs(), git, nested_runner=runner)
    svc.destroy_env(
        "alpha",
        force=force,
        strict=strict,
        dry_run=False,
        reporter=init_reporter,
        provision_teardown=provision_teardown,
    )

    destroys = [c for c in runner.calls if c[0] == "destroy_env"]
    assert destroys == [
        ("destroy_env", LAB_ALPHA, "n1", force, strict, provision_teardown),
        ("destroy_env", LAB_ALPHA, "n2", force, strict, provision_teardown),
    ]


def test_destroy_env_refuses_a_dirty_nested_env_without_force(init_reporter: FakeInitReporter) -> None:
    dirty = nested_status(LAB_ALPHA, nested_env("n1"), nested_env("n2", nested_wt(dirty=2)))
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: dirty})
    git = FakeGitRepository()
    git.clean_worktrees.add(LAB_ALPHA)  # the outer worktree itself is clean
    fs = _nested_fs()

    svc = _service(_nested_config(), fs, git, nested_runner=runner)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is False
    assert [c for c in runner.calls if c[0] == "destroy_env"] == []
    assert git.removed_worktrees == []
    assert fs.exists(LAB_ALPHA)
    assert any("dirty worktrees" in msg and "lab (nested: n2)" in msg for _, msg in init_reporter.errors)


def test_destroy_env_force_destroys_despite_a_dirty_nested_env(init_reporter: FakeInitReporter) -> None:
    dirty = nested_status(LAB_ALPHA, nested_env("n1", nested_wt(dirty=4)))
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: dirty})

    svc = _service(_nested_config(), _nested_fs(), FakeGitRepository(), nested_runner=runner)
    ok = svc.destroy_env("alpha", force=True, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is True
    assert ("destroy_env", LAB_ALPHA, "n1", True, False, True) in runner.calls


@pytest.mark.parametrize(
    ("status", "named"),
    [
        (
            nested_status(LAB_ALPHA, nested_env("n1", nested_wt("app", ahead=2))),
            "lab (nested: n1/app (unpushed branch))",
        ),
        (
            nested_status(
                LAB_ALPHA, nested_env("n1", nested_wt("app", ahead=2, tracking_ahead=1, upstream="origin/f"))
            ),
            "lab (nested: n1/app (unpushed commits))",
        ),
        (
            nested_status(LAB_ALPHA, projects=[{"repo": "app", "dirty": 1, "ahead_origin": 0}]),
            "lab (nested: projects/app (uncommitted changes))",
        ),
        (
            nested_status(
                LAB_ALPHA,
                projects=[{"repo": "app", "dirty": 0, "ahead_origin": 0, "local_only_commits": 2, "stashes": 0}],
            ),
            "lab (nested: projects/app (local-only commits))",
        ),
        (
            nested_status(LAB_ALPHA, standalones=[{"repo": "kit", "dirty": 0, "ahead_origin": 0, "stashes": 1}]),
            "lab (nested: standalone kit (stashes))",
        ),
        (
            nested_status(
                LAB_ALPHA,
                nested_env(
                    "n1", nested_wt("deep", nested={"env_count": 1, "dirty": False, "unpushed": True, "error": None})
                ),
            ),
            "lab (nested: n1/deep (unpushed work in its nested workspace))",
        ),
    ],
)
def test_destroy_env_refuses_unpushed_nested_work_without_force(
    init_reporter: FakeInitReporter, status: dict[str, Any], named: str
) -> None:
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: status})
    git = FakeGitRepository()
    git.clean_worktrees.add(LAB_ALPHA)
    fs = _nested_fs()

    svc = _service(_nested_config(), fs, git, nested_runner=runner)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is False
    assert runner.calls == []
    assert git.removed_worktrees == []
    assert fs.exists(LAB_ALPHA)
    assert any("unpushed work in a nested workspace" in msg and named in msg for _, msg in init_reporter.errors)


def test_destroy_env_destroys_a_nested_env_whose_branch_is_pushed_but_not_merged(
    init_reporter: FakeInitReporter,
) -> None:
    pushed = nested_wt("app", ahead=3, tracking_ahead=0, upstream="origin/feature/x")
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: nested_status(LAB_ALPHA, nested_env("n1", pushed))})
    git = FakeGitRepository()
    git.clean_worktrees.add(LAB_ALPHA)

    svc = _service(_nested_config(), _nested_fs(), git, nested_runner=runner)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is True
    assert init_reporter.errors == []
    assert runner.calls == [("destroy_env", LAB_ALPHA, "n1", False, False, True)]


def test_destroy_env_force_discards_unpushed_nested_work(init_reporter: FakeInitReporter) -> None:
    status = nested_status(LAB_ALPHA, nested_env("n1", nested_wt("app", ahead=2)))
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: status})
    git = FakeGitRepository()
    fs = _nested_fs()

    svc = _service(_nested_config(), fs, git, nested_runner=runner)
    ok = svc.destroy_env("alpha", force=True, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is True
    assert runner.calls == [("destroy_env", LAB_ALPHA, "n1", True, False, True)]
    assert not fs.exists(ALPHA)


def test_destroy_env_refuses_a_deeply_dirty_nested_env_before_destroying_any_clean_sibling(
    init_reporter: FakeInitReporter,
) -> None:
    deep_dirty = {"env_count": 1, "dirty": True, "unpushed": False, "error": None}
    status = nested_status(LAB_ALPHA, nested_env("n1", nested_wt("deep", nested=deep_dirty)), nested_env("n2"))
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: status})
    git = FakeGitRepository()
    git.clean_worktrees.add(LAB_ALPHA)

    svc = _service(_nested_config(), _nested_fs(), git, nested_runner=runner)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is False
    assert runner.calls == []  # neither n1 nor the clean sibling n2 was destroyed
    assert any("dirty worktrees" in msg and "lab (nested: n1)" in msg for _, msg in init_reporter.errors)


def test_destroy_env_reads_each_nested_root_once(init_reporter: FakeInitReporter) -> None:
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: TWO_ENVS})
    git = FakeGitRepository()
    git.clean_worktrees.add(LAB_ALPHA)

    svc = _service(_nested_config(), _nested_fs(), git, nested_runner=runner)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is True
    assert runner.status_calls == [LAB_ALPHA]
    assert [c[2] for c in runner.calls] == ["n1", "n2"]


def test_destroy_env_keeps_the_worktree_when_a_nested_destroy_fails(init_reporter: FakeInitReporter) -> None:
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: TWO_ENVS}, destroy_returncodes={"n1": 1})
    git = FakeGitRepository()
    git.clean_worktrees.add(LAB_ALPHA)
    fs = _nested_fs()

    svc = _service(_nested_config(), fs, git, nested_runner=runner)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is False
    assert git.removed_worktrees == []
    assert fs.exists(LAB_ALPHA)
    assert any("aborting destroy" in msg for repo, msg in init_reporter.errors if repo == "alpha")


def test_destroy_env_with_force_removes_the_worktree_after_a_nested_destroy_fails(
    init_reporter: FakeInitReporter,
) -> None:
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: TWO_ENVS}, destroy_returncodes={"n1": 1})
    git = FakeGitRepository()
    fs = _nested_fs()

    svc = _service(_nested_config(), fs, git, nested_runner=runner)
    ok = svc.destroy_env("alpha", force=True, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is False  # the nested failure still surfaces in the result
    assert git.removed_worktrees == [(LAB_MAIN, LAB_ALPHA, True)]
    assert not fs.exists(ALPHA)


def test_destroy_env_refuses_when_the_nested_state_cannot_be_read(init_reporter: FakeInitReporter) -> None:
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: RepoError("winter resolved another workspace")})
    git = FakeGitRepository()
    git.clean_worktrees.add(LAB_ALPHA)
    fs = _nested_fs()

    svc = _service(_nested_config(), fs, git, nested_runner=runner)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is False
    assert git.removed_worktrees == []
    assert fs.exists(LAB_ALPHA)
    assert any(repo == "lab" and "another workspace" in msg for repo, msg in init_reporter.errors)
    assert any("cannot read nested workspace: lab" in msg for repo, msg in init_reporter.errors if repo == "alpha")


def test_destroy_env_dry_run_lists_nested_envs_and_destroys_nothing(init_reporter: FakeInitReporter) -> None:
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: TWO_ENVS})
    git = FakeGitRepository()
    git.clean_worktrees.add(LAB_ALPHA)
    fs = _nested_fs()

    svc = _service(_nested_config(), fs, git, nested_runner=runner)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=True, reporter=init_reporter)

    assert ok is True
    assert runner.status_calls == [LAB_ALPHA]
    assert runner.calls == []
    assert git.removed_worktrees == []
    assert fs.exists(LAB_ALPHA)
    listed = [(a[0], a[1], a[3]) for a in init_reporter.actions if a[2] == "would_destroy_nested_env"]
    assert listed == [("lab", str(LAB_ALPHA / "n1"), "n1"), ("lab", str(LAB_ALPHA / "n2"), "n2")]
    kinds = [a[2] for a in init_reporter.actions]
    assert kinds.index("would_destroy_nested_env") < kinds.index("would_remove_worktree")


def test_destroy_env_reads_no_nested_state_for_an_ordinary_repo(
    workspace_config: WorkspaceConfig, init_reporter: FakeInitReporter
) -> None:
    worktree_path = ALPHA / "demo"
    fs = FakeFilesystem(directories=[WORKSPACE_ROOT / "projects", DEMO_MAIN, ALPHA, worktree_path])
    git = FakeGitRepository()
    git.clean_worktrees.add(worktree_path)
    runner = FakeNestedWorkspaceRunner()

    svc = _service(workspace_config, fs, git, nested_runner=runner)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is True
    assert runner.calls == []


SERVICE_BOUND = {"capabilities": {"service": "winter-service-tmux"}}


def test_destroy_env_stops_nested_workspace_services_after_its_envs_and_before_teardown(
    init_reporter: FakeInitReporter,
) -> None:
    events: list[str] = []
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: TWO_ENVS}, events=events)
    git = _LoggingGit(events)
    git.clean_worktrees.add(LAB_ALPHA)
    fs = _nested_fs()

    svc = _service(
        _nested_config(),
        fs,
        git,
        nested_runner=runner,
        provision_svc=_LoggingProvisionService(events),
        nested_layers={LAB_ALPHA: (SERVICE_BOUND, {})},
    )
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is True
    assert events == [
        "nested_destroy:n1",
        "nested_destroy:n2",
        f"nested_service_down:{LAB_ALPHA}",
        "provision:data",
        "provision:resource",
        "remove_worktree:lab",
    ]
    stopped = [(a[0], a[1]) for a in init_reporter.actions if a[2] == "nested_workspace_services_stopped"]
    assert stopped == [("lab", str(LAB_ALPHA))]
    assert ("lab", "winter service down workspace", 0) in init_reporter.cmds_completed
    assert not fs.exists(ALPHA)


def test_destroy_env_stops_nested_services_bound_only_in_the_nested_local_overlay(
    init_reporter: FakeInitReporter,
) -> None:
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: nested_status(LAB_ALPHA)})
    git = FakeGitRepository()
    git.clean_worktrees.add(LAB_ALPHA)

    svc = _service(
        _nested_config(), _nested_fs(), git, nested_runner=runner, nested_layers={LAB_ALPHA: ({}, SERVICE_BOUND)}
    )
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is True
    assert runner.calls == [("service_down", LAB_ALPHA)]


@pytest.mark.parametrize(
    "layers",
    [
        ({}, {}),
        ({"capabilities": {"provision": "x"}}, {}),
        (SERVICE_BOUND, {"capabilities": {"service": []}}),
    ],
)
def test_destroy_env_runs_no_nested_service_down_without_a_bound_provider(
    init_reporter: FakeInitReporter, layers: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: TWO_ENVS})
    git = FakeGitRepository()
    git.clean_worktrees.add(LAB_ALPHA)

    svc = _service(_nested_config(), _nested_fs(), git, nested_runner=runner, nested_layers={LAB_ALPHA: layers})
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is True
    assert [c for c in runner.calls if c[0] == "service_down"] == []
    assert not any(a[2] == "nested_workspace_services_stopped" for a in init_reporter.actions)


def test_destroy_env_keeps_the_worktree_when_nested_services_cannot_be_stopped(
    init_reporter: FakeInitReporter,
) -> None:
    events: list[str] = []
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: TWO_ENVS}, service_down_returncode=1, events=events)
    git = _LoggingGit(events)
    git.clean_worktrees.add(LAB_ALPHA)
    fs = _nested_fs()

    svc = _service(
        _nested_config(),
        fs,
        git,
        nested_runner=runner,
        provision_svc=_LoggingProvisionService(events),
        nested_layers={LAB_ALPHA: (SERVICE_BOUND, {})},
    )
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is False
    assert events == ["nested_destroy:n1", "nested_destroy:n2", f"nested_service_down:{LAB_ALPHA}"]
    assert fs.exists(LAB_ALPHA)
    assert any("winter service down workspace` exited with code 1" in msg for repo, msg in init_reporter.errors)
    assert any(
        "aborting destroy — a nested workspace's services could not be stopped" in msg
        for repo, msg in init_reporter.errors
        if repo == "alpha"
    )


def test_destroy_env_keeps_the_worktree_when_the_nested_config_cannot_be_read(
    init_reporter: FakeInitReporter,
) -> None:
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: TWO_ENVS})
    git = FakeGitRepository()
    git.clean_worktrees.add(LAB_ALPHA)
    fs = _nested_fs()

    svc = _service(
        _nested_config(),
        fs,
        git,
        nested_runner=runner,
        nested_layers={LAB_ALPHA: (SERVICE_BOUND, {})},
        malformed_nested_config=True,
    )
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is False
    assert [c for c in runner.calls if c[0] == "service_down"] == []
    assert fs.exists(LAB_ALPHA)
    assert any(repo == "lab" and "nested workspace services" in msg for repo, msg in init_reporter.errors)


def test_destroy_env_with_force_removes_the_worktree_after_nested_services_fail_to_stop(
    init_reporter: FakeInitReporter,
) -> None:
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: TWO_ENVS}, service_down_returncode=1)
    git = FakeGitRepository()
    fs = _nested_fs()

    svc = _service(_nested_config(), fs, git, nested_runner=runner, nested_layers={LAB_ALPHA: (SERVICE_BOUND, {})})
    ok = svc.destroy_env("alpha", force=True, strict=False, dry_run=False, reporter=init_reporter)

    assert ok is False  # the failure still surfaces in the result
    assert ("service_down", LAB_ALPHA) in runner.calls
    assert git.removed_worktrees == [(LAB_MAIN, LAB_ALPHA, True)]
    assert not fs.exists(ALPHA)


def test_destroy_env_dry_run_lists_the_nested_services_stop_and_runs_nothing(
    init_reporter: FakeInitReporter,
) -> None:
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: TWO_ENVS})
    git = FakeGitRepository()
    git.clean_worktrees.add(LAB_ALPHA)
    fs = _nested_fs()

    svc = _service(_nested_config(), fs, git, nested_runner=runner, nested_layers={LAB_ALPHA: (SERVICE_BOUND, {})})
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=True, reporter=init_reporter)

    assert ok is True
    assert runner.calls == []
    assert fs.exists(LAB_ALPHA)
    listed = [(a[0], a[1]) for a in init_reporter.actions if a[2] == "would_stop_nested_workspace_services"]
    assert listed == [("lab", str(LAB_ALPHA))]
    kinds = [a[2] for a in init_reporter.actions]
    assert (
        kinds.index("would_destroy_nested_env")
        < kinds.index("would_stop_nested_workspace_services")
        < kinds.index("would_remove_worktree")
    )


def test_destroy_env_dry_run_lists_no_nested_services_stop_without_a_bound_provider(
    init_reporter: FakeInitReporter,
) -> None:
    runner = FakeNestedWorkspaceRunner(statuses={LAB_ALPHA: TWO_ENVS})
    git = FakeGitRepository()
    git.clean_worktrees.add(LAB_ALPHA)

    svc = _service(_nested_config(), _nested_fs(), git, nested_runner=runner)
    ok = svc.destroy_env("alpha", force=False, strict=False, dry_run=True, reporter=init_reporter)

    assert ok is True
    assert not any(a[2] == "would_stop_nested_workspace_services" for a in init_reporter.actions)
