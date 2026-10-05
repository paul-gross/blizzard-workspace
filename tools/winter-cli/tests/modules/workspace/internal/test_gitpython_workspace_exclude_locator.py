"""Real-git coverage of the workspace exclude locator: a normal clone, a linked worktree, siblings, and a bare common dir.

Each test drives a real `git` repository under `tmp_path`, because the behavior under test is
what git itself does with `--git-dir`, `--git-common-dir`, and `git config --worktree`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.conftest import (
    FakeConfigFileReader,
    FakeEnvIndexRegistry,
    FakeGitRepository,
    FakeInitReporter,
    FakeSubprocessRunner,
)
from tests.modules.workspace.conftest import commit, git_cmd, init_repo
from winter_cli.config.models import AdoptExtensions, WorkspaceConfig
from winter_cli.core.internal.local_filesystem import LocalFilesystem
from winter_cli.core.internal.noop_command_tracer import NoopCommandTracer
from winter_cli.modules.workspace.destroy_service import DestroyService
from winter_cli.modules.workspace.extension_agentsmd_service import ExtensionAgentsMdService
from winter_cli.modules.workspace.extension_exclude_service import ExtensionExcludeService
from winter_cli.modules.workspace.extension_hook_service import ExtensionHookService
from winter_cli.modules.workspace.extension_manifest import ExtensionManifestLoader
from winter_cli.modules.workspace.extension_symlink_service import ExtensionSymlinkService
from winter_cli.modules.workspace.init_service import InitService
from winter_cli.modules.workspace.internal.git_ops_service import GitOpsService
from winter_cli.modules.workspace.internal.gitpython_workspace_exclude_locator import GitPythonWorkspaceExcludeLocator
from winter_cli.modules.workspace.internal.managed_block import GITIGNORE_BEGIN
from winter_cli.modules.workspace.internal.repo_error_factory import RepoErrorFactory
from winter_cli.modules.workspace.models import StandaloneRepository
from winter_cli.modules.workspace.repository_factory import RepositoryFactory
from winter_cli.modules.workspace.workspace_skill_service import WorkspaceSkillService


def _locator(workspace_root: Path) -> GitPythonWorkspaceExcludeLocator:
    return GitPythonWorkspaceExcludeLocator(workspace_root, RepoErrorFactory(), NoopCommandTracer())


def _main_checkout(tmp_path: Path) -> Path:
    main = tmp_path / "main"
    init_repo(main)
    commit(main, "README.md", "workspace\n", "root")
    return main


def _linked_worktree(main: Path, name: str) -> Path:
    path = main.parent / name
    git_cmd(main, "worktree", "add", "-q", "-b", name, str(path))
    return path


def _git_dir(root: Path) -> Path:
    return Path(git_cmd(root, "rev-parse", "--absolute-git-dir").strip())


def _config_value(config_file: Path, key: str) -> str | None:
    result = subprocess.run(
        ["git", "config", "--file", str(config_file), "--get", key], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _append_block(exclude_path: Path, name: str, *lines: str) -> None:
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text() if exclude_path.exists() else ""
    exclude_path.write_text(existing + "\n".join([GITIGNORE_BEGIN.format(name=name), *lines, f"# <<< {name}"]) + "\n")


# ── normal clone ───────────────────────────────────────────────────────────


def test_normal_clone_resolves_dot_git_info_exclude_and_configures_nothing(tmp_path: Path) -> None:
    main = _main_checkout(tmp_path)
    locator = _locator(main)

    assert locator.locate() == main / ".git" / "info" / "exclude"
    assert locator.locate_for_write() == main / ".git" / "info" / "exclude"

    config = (main / ".git" / "config").read_text()
    assert "worktreeConfig" not in config
    assert "excludesFile" not in config


def test_a_directory_that_is_not_a_git_repository_resolves_to_none(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    locator = _locator(plain)

    assert locator.locate() is None
    assert locator.locate_for_write() is None
    assert not (plain / ".git").exists()


# ── linked worktree ────────────────────────────────────────────────────────


def test_linked_worktree_resolves_its_own_git_dir_and_leaves_the_common_dir_alone(tmp_path: Path) -> None:
    main = _main_checkout(tmp_path)
    worktree = _linked_worktree(main, "inner")
    locator = _locator(worktree)

    own_exclude = _git_dir(worktree) / "info" / "exclude"
    assert own_exclude != main / ".git" / "info" / "exclude"
    assert locator.locate() == own_exclude
    assert "worktreeConfig" not in (main / ".git" / "config").read_text(), "locate() must not configure git"

    assert locator.locate_for_write() == own_exclude


def test_linked_worktree_blocks_apply_to_that_worktree_alone(tmp_path: Path) -> None:
    main = _main_checkout(tmp_path)
    worktree = _linked_worktree(main, "inner")
    common_exclude = main / ".git" / "info" / "exclude"
    common_before = common_exclude.read_text() if common_exclude.exists() else None

    exclude_path = _locator(worktree).locate_for_write()
    assert exclude_path is not None
    _append_block(exclude_path, "winter-dir/alpha", "/alpha/", "/projects/")
    (worktree / "alpha").mkdir()
    (worktree / "alpha" / "file.txt").write_text("x")
    (worktree / "projects").mkdir()
    (worktree / "projects" / "file.txt").write_text("x")
    (main / "alpha").mkdir()
    (main / "alpha" / "file.txt").write_text("x")

    assert git_cmd(worktree, "status", "--porcelain").strip() == ""
    assert "alpha/" in git_cmd(main, "status", "--porcelain"), (
        "the main checkout must not inherit the worktree's blocks"
    )
    assert git_cmd(worktree, "config", "--worktree", "core.excludesFile").strip() == str(exclude_path)
    assert git_cmd(main, "config", "extensions.worktreeConfig").strip() == "true"
    after = common_exclude.read_text() if common_exclude.exists() else None
    assert after == common_before


def test_locate_for_write_is_idempotent(tmp_path: Path) -> None:
    main = _main_checkout(tmp_path)
    worktree = _linked_worktree(main, "inner")
    locator = _locator(worktree)

    first = locator.locate_for_write()
    second = locator.locate_for_write()

    assert first == second
    assert git_cmd(worktree, "config", "--worktree", "--get-all", "core.excludesFile").splitlines() == [str(first)]


# ── sibling linked worktrees ───────────────────────────────────────────────


def test_sibling_worktrees_resolve_to_distinct_files_and_never_share_blocks(tmp_path: Path) -> None:
    main = _main_checkout(tmp_path)
    one = _linked_worktree(main, "one")
    two = _linked_worktree(main, "two")

    path_one = _locator(one).locate_for_write()
    path_two = _locator(two).locate_for_write()
    assert path_one is not None and path_two is not None
    assert path_one != path_two

    _append_block(path_one, "ext-a", "/ext-a/")
    _append_block(path_two, "ext-b", "/ext-b/")

    assert "ext-a" in path_one.read_text() and "ext-b" not in path_one.read_text()
    assert "ext-b" in path_two.read_text() and "ext-a" not in path_two.read_text()
    for worktree, own, other in ((one, "ext-a", "ext-b"), (two, "ext-b", "ext-a")):
        (worktree / own).mkdir()
        (worktree / own / "f").write_text("x")
        (worktree / other).mkdir()
        (worktree / other / "f").write_text("x")
        status = git_cmd(worktree, "status", "--porcelain")
        assert f"{own}/" not in status
        assert f"{other}/" in status


def _exclude_service(root: Path, ext_names: list[str]) -> tuple[ExtensionExcludeService, list[StandaloneRepository]]:
    manifests: dict[Path, dict] = {}
    repos: list[StandaloneRepository] = []
    for name in ext_names:
        ext_path = root / name
        ext_path.mkdir(exist_ok=True)
        (ext_path / "winter-ext.toml").write_text("")
        manifests[ext_path / "winter-ext.toml"] = {"name": name}
        repos.append(StandaloneRepository(name=name, path=ext_path))
    config = WorkspaceConfig(
        workspace_root=root, service_prefix="t", main_branch="main", adopt_extensions=AdoptExtensions.winter
    )
    service = ExtensionExcludeService(
        config=config,
        fs=LocalFilesystem(),
        manifest_loader=ExtensionManifestLoader(config_file_reader=FakeConfigFileReader(manifests)),
        exclude_locator=_locator(root),
    )
    return service, repos


def test_finalize_excludes_in_sibling_worktrees_never_touches_the_other_file(
    tmp_path: Path, init_reporter: FakeInitReporter
) -> None:
    main = _main_checkout(tmp_path)
    one = _linked_worktree(main, "one")
    two = _linked_worktree(main, "two")
    common_exclude = main / ".git" / "info" / "exclude"

    svc_one, repos_one = _exclude_service(one, ["ext-a"])
    svc_two, repos_two = _exclude_service(two, ["ext-b"])
    assert svc_one.finalize_excludes(repos_one, init_reporter) is True
    assert svc_two.finalize_excludes(repos_two, init_reporter) is True

    file_one = _git_dir(one) / "info" / "exclude"
    file_two = _git_dir(two) / "info" / "exclude"
    assert "ext-a (managed by winter)" in file_one.read_text()
    assert "ext-b" not in file_one.read_text()
    assert "ext-b (managed by winter)" in file_two.read_text()
    assert "ext-a" not in file_two.read_text()
    assert not common_exclude.exists() or "managed by winter" not in common_exclude.read_text()

    # A re-run in one worktree with its extension gone strips that worktree's block only.
    two_before = file_two.read_text()
    svc_one_again, _ = _exclude_service(one, [])
    assert svc_one_again.finalize_excludes([], init_reporter) is True
    assert "ext-a" not in file_one.read_text()
    assert file_two.read_text() == two_before


def test_main_checkout_with_linked_worktrees_still_uses_dot_git_info_exclude(tmp_path: Path) -> None:
    """A normal clone keeps writing `.git/info/exclude` even when linked worktrees of it exist."""
    main = _main_checkout(tmp_path)
    _linked_worktree(main, "inner")

    assert _locator(main).locate_for_write() == main / ".git" / "info" / "exclude"
    assert "excludesFile" not in (main / ".git" / "config").read_text()


# ── only write what differs ────────────────────────────────────────────────


def test_locate_for_write_leaves_the_shared_config_untouched_once_set_up(tmp_path: Path) -> None:
    """A repeat call reads, finds nothing to change, and takes no config lock — so parallel
    `ws init` runs in sibling worktrees do not fight over the shared `.git/config`."""
    main = _main_checkout(tmp_path)
    one = _linked_worktree(main, "one")
    two = _linked_worktree(main, "two")
    _locator(one).locate_for_write()
    shared_config = main / ".git" / "config"
    shared_before = (shared_config.read_bytes(), shared_config.stat().st_mtime_ns)
    own_config = _git_dir(one) / "config.worktree"
    own_before = (own_config.read_bytes(), own_config.stat().st_mtime_ns)

    _locator(one).locate_for_write()
    assert (shared_config.read_bytes(), shared_config.stat().st_mtime_ns) == shared_before
    assert (own_config.read_bytes(), own_config.stat().st_mtime_ns) == own_before

    # A sibling joining later needs only its own per-worktree setting; the shared config stays put.
    _locator(two).locate_for_write()
    assert (shared_config.read_bytes(), shared_config.stat().st_mtime_ns) == shared_before


# ── a bare common directory ────────────────────────────────────────────────


def _bare_repo_with_worktrees(tmp_path: Path, *names: str) -> tuple[Path, list[Path]]:
    source = _main_checkout(tmp_path)
    bare = tmp_path / "bare.git"
    git_cmd(tmp_path, "clone", "-q", "--bare", str(source), str(bare))
    worktrees = []
    for name in names:
        path = tmp_path / name
        git_cmd(bare, "worktree", "add", "-q", "-b", name, str(path))
        worktrees.append(path)
    return bare, worktrees


def test_bare_common_dir_keeps_its_layout_settings_per_worktree_and_siblings_keep_working(
    tmp_path: Path, init_reporter: FakeInitReporter
) -> None:
    """With `extensions.worktreeConfig` on, a `core.bare=true` left in the shared config would make
    every sibling fail with "this operation must be run in a work tree"."""
    bare, (root, sibling) = _bare_repo_with_worktrees(tmp_path, "root", "sibling")
    assert _config_value(bare / "config", "core.bare") == "true"

    svc, repos = _exclude_service(root, ["ext-a"])
    assert svc.finalize_excludes(repos, init_reporter) is True

    assert init_reporter.errors == []
    shared = bare / "config"
    assert _config_value(shared, "core.bare") is None
    assert _config_value(bare / "config.worktree", "core.bare") == "true"
    assert git_cmd(bare, "rev-parse", "--is-bare-repository").strip() == "true"
    for worktree in (root, sibling):
        assert git_cmd(worktree, "rev-parse", "--is-bare-repository").strip() == "false"
        assert git_cmd(worktree, "rev-parse", "--is-inside-work-tree").strip() == "true"
        (worktree / "scratch.txt").write_text("x")
        assert "scratch.txt" in git_cmd(worktree, "status", "--porcelain")
    (root / "ext-a" / "f").write_text("x")
    (root / "scratch.txt").unlink()
    assert git_cmd(root, "status", "--porcelain").strip() == ""
    # The sibling got no blocks and still commits.
    git_cmd(sibling, "add", "scratch.txt")
    git_cmd(sibling, "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-q", "-m", "x")


# ── the writers and destroy, end to end in a linked worktree ───────────────


def _workspace_config(root: Path) -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=root, service_prefix="t", main_branch="main", adopt_extensions=AdoptExtensions.winter
    )


def _init_service(root: Path) -> InitService:
    """An `InitService` over the real filesystem and the real locator; no project repos are declared."""
    config = _workspace_config(root)
    fs = LocalFilesystem()
    locator = _locator(root)
    manifest_loader = ExtensionManifestLoader(config_file_reader=FakeConfigFileReader({}))
    subprocess_runner = FakeSubprocessRunner()
    return InitService(
        config=config,
        repo_factory=RepositoryFactory(config, fs=fs),
        extension_symlink_svc=ExtensionSymlinkService(config=config, fs=fs, manifest_loader=manifest_loader),
        extension_hook_svc=ExtensionHookService(
            config=config, fs=fs, subprocess_runner=subprocess_runner, manifest_loader=manifest_loader
        ),
        extension_exclude_svc=ExtensionExcludeService(
            config=config, fs=fs, manifest_loader=manifest_loader, exclude_locator=locator
        ),
        extension_agentsmd_svc=ExtensionAgentsMdService(config=config, fs=fs, manifest_loader=manifest_loader),
        fs=fs,
        subprocess_runner=subprocess_runner,
        git_repo=FakeGitRepository(),
        git_ops=GitOpsService(RepoErrorFactory()),
        registry=FakeEnvIndexRegistry(),
        exclude_locator=locator,
        workspace_skill_svc=WorkspaceSkillService(config=config, fs=fs, exclude_locator=locator),
    )


def test_init_and_workspace_skill_excludes_leave_a_linked_worktree_clean(
    tmp_path: Path, init_reporter: FakeInitReporter
) -> None:
    """`ws init` in a linked-worktree root: everything it generates is ignored, so `git status` is clean."""
    main = _main_checkout(tmp_path)
    root = _linked_worktree(main, "ws")
    (root / "skills" / "deploy").mkdir(parents=True)
    (root / "skills" / "deploy" / "SKILL.md").write_text("---\ndescription: deploy\n---\n")
    git_cmd(root, "add", "skills")
    git_cmd(root, "commit", "-q", "-m", "skills")
    svc = _init_service(root)

    assert svc.reconcile_projects(init_reporter) is True
    assert svc.reconcile_env("alpha", init_reporter) is True

    assert init_reporter.errors == []
    (root / "alpha" / "demo").mkdir(parents=True, exist_ok=True)
    (root / "alpha" / "demo" / "f.txt").write_text("x")
    (root / "projects" / "demo").mkdir(parents=True, exist_ok=True)
    (root / "projects" / "demo" / "f.txt").write_text("x")
    (root / ".winter" / "logs").mkdir(parents=True)
    (root / ".winter" / "logs" / "svc.log").write_text("x")
    assert (root / ".claude" / "skills").is_dir(), "the workspace skill projection must have run"
    assert git_cmd(root, "status", "--porcelain").strip() == ""
    own_exclude = (_git_dir(root) / "info" / "exclude").read_text()
    assert "winter-dir/alpha" in own_exclude
    assert "winter-workspace/artifacts" in own_exclude
    common_exclude = main / ".git" / "info" / "exclude"
    assert not common_exclude.exists() or "managed by winter" not in common_exclude.read_text()


def test_destroy_in_one_sibling_worktree_leaves_the_other_siblings_exclude_unchanged(
    tmp_path: Path, init_reporter: FakeInitReporter
) -> None:
    main = _main_checkout(tmp_path)
    one = _linked_worktree(main, "one")
    two = _linked_worktree(main, "two")
    for root in (one, two):
        svc = _init_service(root)
        assert svc.reconcile_env("alpha", init_reporter) is True
    assert init_reporter.errors == []
    exclude_one = _git_dir(one) / "info" / "exclude"
    exclude_two = _git_dir(two) / "info" / "exclude"
    assert "winter-dir/alpha" in exclude_one.read_text()
    two_before = exclude_two.read_text()
    assert "winter-dir/alpha" in two_before

    config = _workspace_config(one)
    destroy = DestroyService(
        config=config,
        repo_factory=RepositoryFactory(config),
        extension_hook_svc=ExtensionHookService(
            config=config,
            fs=LocalFilesystem(),
            subprocess_runner=FakeSubprocessRunner(),
            manifest_loader=ExtensionManifestLoader(config_file_reader=FakeConfigFileReader({})),
        ),
        fs=LocalFilesystem(),
        git_repo=FakeGitRepository(),
        registry=FakeEnvIndexRegistry(),
        exclude_locator=_locator(one),
    )
    destroy_reporter = FakeInitReporter()
    assert destroy.destroy_env("alpha", force=False, strict=False, dry_run=False, reporter=destroy_reporter) is True

    assert destroy_reporter.errors == []
    assert "winter-dir/alpha" not in exclude_one.read_text()
    assert not (one / "alpha").exists()
    assert exclude_two.read_text() == two_before
    assert (two / "alpha").is_dir()
