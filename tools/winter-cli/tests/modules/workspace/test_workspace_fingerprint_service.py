"""Tests for `WorkspaceFingerprintService` and `winter ws fingerprint`.

Two layers:

- Fake-seam tests pin the digest's serialization contract — what is in the
  preimage, how standalones are ordered, how the dirty flag is derived — where
  a real repo would only obscure which input moved the digest.
- Real-git tests build two actual workspaces (a workspace repo plus standalone
  clones) and assert each acceptance criterion against real trees, because
  whether a temp-index `git add -u` really observes a second edit to an
  already-dirty file, and really ignores untracked files, is only provable
  against real git.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.conftest import FakeGitRepository
from tests.modules.workspace.conftest import commit, git_cmd, init_repo
from winter_cli.config.models import (
    AdoptExtensions,
    SingletonRepository,
    SingletonType,
    StandaloneRepositoryConfig,
    WorkspaceConfig,
)
from winter_cli.core.internal.noop_command_tracer import NoopCommandTracer
from winter_cli.modules.workspace.command import ws_fingerprint
from winter_cli.modules.workspace.handlers.fingerprint_handler import FingerprintHandler, FingerprintParams
from winter_cli.modules.workspace.internal.gitpython_repository import GitPythonRepository
from winter_cli.modules.workspace.internal.repo_error_factory import RepoErrorFactory
from winter_cli.modules.workspace.models import RepoError, RepoFingerprint
from winter_cli.modules.workspace.repository_factory import RepositoryFactory
from winter_cli.modules.workspace.workspace_fingerprint_service import WorkspaceFingerprintService


def _config(root: Path, standalone_names: list[str]) -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=root,
        service_prefix="t",
        main_branch="main",
        git_excludes=[],
        git_identity=None,
        adopt_extensions=AdoptExtensions.winter,
        singleton_repos=[SingletonRepository(name=root.name, type=SingletonType.workspace)],
        project_repos=[],
        standalone_repos=[
            StandaloneRepositoryConfig(name=n, url=f"git@example.com:org/{n}.git") for n in standalone_names
        ],
    )


# ── Fake-seam tests: serialization contract ─────────────────────────────────


def _fake_service(
    tmp_path: Path,
    standalones: dict[str, str],
    workspace_tree: str = "w" * 40,
    content_trees: dict[str, str] | None = None,
) -> tuple[WorkspaceFingerprintService, FakeGitRepository]:
    """A service over `FakeGitRepository`; `standalones` maps name -> HEAD tree."""
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    git_repo = FakeGitRepository()
    git_repo.head_commits[root] = "c" * 40
    git_repo.head_trees[root] = workspace_tree
    for name, tree in standalones.items():
        path = root / name
        path.mkdir(exist_ok=True)
        git_repo.head_commits[path] = name[0] * 40
        git_repo.head_trees[path] = tree
        if content_trees and name in content_trees:
            git_repo.content_trees[path] = content_trees[name]
    service = WorkspaceFingerprintService(
        repo_factory=RepositoryFactory(_config(root, list(standalones))), git_repo=git_repo
    )
    return service, git_repo


def test_digest_is_independent_of_standalone_declaration_order(tmp_path: Path) -> None:
    forward, _ = _fake_service(tmp_path, {"alpha": "a" * 40, "beta": "b" * 40})
    reverse, _ = _fake_service(tmp_path, {"beta": "b" * 40, "alpha": "a" * 40})

    assert forward.compute().digest == reverse.compute().digest


def test_standalones_are_listed_sorted_by_name(tmp_path: Path) -> None:
    service, _ = _fake_service(tmp_path, {"zed": "z" * 40, "alpha": "a" * 40, "mid": "m" * 40})

    assert [s.name for s in service.compute().standalones] == ["alpha", "mid", "zed"]


def test_digest_includes_standalone_name(tmp_path: Path) -> None:
    named_a, _ = _fake_service(tmp_path, {"alpha": "a" * 40})
    named_b, _ = _fake_service(tmp_path, {"beta": "a" * 40})

    assert named_a.compute().digest != named_b.compute().digest


def test_swapping_trees_between_standalones_changes_the_digest(tmp_path: Path) -> None:
    straight, _ = _fake_service(tmp_path, {"alpha": "a" * 40, "beta": "b" * 40})
    swapped, _ = _fake_service(tmp_path, {"alpha": "b" * 40, "beta": "a" * 40})

    assert straight.compute().digest != swapped.compute().digest


def test_digest_ignores_commit_and_depends_on_tree(tmp_path: Path) -> None:
    service, git_repo = _fake_service(tmp_path, {"alpha": "a" * 40})
    before = service.compute().digest
    git_repo.head_commits[tmp_path / "ws"] = "d" * 40  # different history, same tree

    assert service.compute().digest == before


def test_digest_is_a_sha256_hex_string(tmp_path: Path) -> None:
    service, _ = _fake_service(tmp_path, {})

    digest = service.compute().digest

    assert len(digest) == 64
    assert int(digest, 16) >= 0


def test_clean_repo_reports_head_tree_and_not_dirty(tmp_path: Path) -> None:
    service, _ = _fake_service(tmp_path, {"alpha": "a" * 40})

    fingerprint = service.compute()

    assert fingerprint.workspace.tree == "w" * 40
    assert fingerprint.workspace.dirty is False
    assert fingerprint.standalones[0].tree == "a" * 40
    assert fingerprint.standalones[0].dirty is False


def test_modified_repo_reports_content_tree_and_dirty(tmp_path: Path) -> None:
    clean, _ = _fake_service(tmp_path, {"alpha": "a" * 40})
    modified, _ = _fake_service(tmp_path, {"alpha": "a" * 40}, content_trees={"alpha": "e" * 40})

    result = modified.compute()

    assert result.standalones[0].tree == "e" * 40
    assert result.standalones[0].dirty is True
    assert result.digest != clean.compute().digest


def test_dirty_comes_from_tracked_status_not_from_tree_comparison(tmp_path: Path) -> None:
    service, git_repo = _fake_service(tmp_path, {"alpha": "a" * 40})
    git_repo.tracked_changes.add(tmp_path / "ws" / "alpha")

    standalone = service.compute().standalones[0]

    assert standalone.dirty is True
    assert standalone.tree == "a" * 40


def test_repo_missing_on_disk_raises_naming_the_repo(tmp_path: Path) -> None:
    service, _ = _fake_service(tmp_path, {"alpha": "a" * 40})
    (tmp_path / "ws" / "alpha").rmdir()

    with pytest.raises(RepoError, match="alpha"):
        service.compute()


def test_missing_workspace_singleton_raises(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    config = _config(root, []).model_copy(update={"singleton_repos": []})
    service = WorkspaceFingerprintService(repo_factory=RepositoryFactory(config), git_repo=FakeGitRepository())

    with pytest.raises(RepoError, match="workspace repository"):
        service.compute()


# ── Real-git tests: the acceptance criteria ─────────────────────────────────


class _World:
    """One workspace: a workspace repo at `root` plus standalone repos beneath it."""

    def __init__(self, root: Path, standalone_names: list[str]) -> None:
        self.root = root
        self.names = standalone_names
        error_factory = RepoErrorFactory()
        self._git = GitPythonRepository(error_factory=error_factory, tracer=NoopCommandTracer())
        self.service = WorkspaceFingerprintService(
            repo_factory=RepositoryFactory(_config(root, standalone_names)), git_repo=self._git
        )

    def standalone(self, name: str) -> Path:
        return self.root / name

    def digest(self) -> str:
        return self.service.compute().digest


def _seed_world(base: Path, label: str, standalone_names: list[str] | None = None) -> _World:
    """A workspace repo with `config.toml`, plus one standalone repo per name, all committed."""
    names = standalone_names if standalone_names is not None else ["ext-a", "ext-b"]
    root = base / label
    init_repo(root)
    commit(root, "config.toml", "workspace = 1\n", "workspace config")
    # Standalones are nested repos; ignore them so they never count as workspace content.
    commit(root, ".gitignore", "".join(f"/{n}/\n" for n in names) + "/envs/\n", "ignore nested")
    for name in names:
        init_repo(root / name)
        commit(root / name, "ext.toml", f"name = {name!r}\n", f"{name} initial")
    return _World(root, names)


def _clone_world(source: _World, base: Path, label: str) -> _World:
    """A clone of every repo in `source`, preserving trees but not commit ids."""
    root = base / label
    subprocess.run(["git", "clone", "-q", str(source.root), str(root)], check=True, capture_output=True)
    for name in source.names:
        subprocess.run(["git", "clone", "-q", str(source.standalone(name)), str(root / name)], check=True)
        git_cmd(root / name, "config", "user.email", "t@t.com")
        git_cmd(root / name, "config", "user.name", "tester")
        git_cmd(root / name, "config", "commit.gpgsign", "false")
    git_cmd(root, "config", "user.email", "t@t.com")
    git_cmd(root, "config", "user.name", "tester")
    git_cmd(root, "config", "commit.gpgsign", "false")
    return _World(root, source.names)


def _rewrite_history(path: Path) -> None:
    """Recommit HEAD with a different message: new commit id, identical tree."""
    git_cmd(path, "commit", "-q", "--amend", "-m", "same content, different history")


def test_identical_trees_and_standalones_give_identical_digests(tmp_path: Path) -> None:
    a = _seed_world(tmp_path, "a")
    b = _clone_world(a, tmp_path, "b")

    assert a.digest() == b.digest()


def test_identical_trees_with_different_commit_histories_give_identical_digests(tmp_path: Path) -> None:
    a = _seed_world(tmp_path, "a")
    b = _clone_world(a, tmp_path, "b")
    _rewrite_history(b.root)
    _rewrite_history(b.standalone("ext-a"))
    assert git_cmd(a.root, "rev-parse", "HEAD") != git_cmd(b.root, "rev-parse", "HEAD")
    assert git_cmd(a.root, "rev-parse", "HEAD^{tree}") == git_cmd(b.root, "rev-parse", "HEAD^{tree}")

    assert a.digest() == b.digest()


def test_changing_a_committed_workspace_file_changes_the_digest(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    before = world.digest()

    commit(world.root, "config.toml", "workspace = 2\n", "change workspace config")

    assert world.digest() != before


def test_moving_a_standalones_commit_changes_the_digest(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    before = world.digest()

    commit(world.standalone("ext-a"), "ext.toml", "name = 'changed'\n", "move standalone")

    assert world.digest() != before


def test_editing_a_tracked_workspace_file_changes_the_digest(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    before = world.digest()

    (world.root / "config.toml").write_text("workspace = edited\n")

    assert world.digest() != before


def test_editing_a_tracked_standalone_file_changes_the_digest(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    before = world.digest()

    (world.standalone("ext-b") / "ext.toml").write_text("name = 'edited'\n")

    assert world.digest() != before


def test_a_second_different_edit_to_an_already_dirty_file_changes_the_digest(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    config = world.root / "config.toml"

    config.write_text("workspace = first-edit\n")
    after_first = world.digest()
    config.write_text("workspace = second-edit!\n")
    after_second = world.digest()

    assert after_first != after_second


def test_a_second_same_size_edit_within_one_timestamp_tick_changes_the_digest(tmp_path: Path) -> None:
    """Same size, same mtime: a stat-only comparison would call the file unchanged."""
    world = _seed_world(tmp_path, "a")
    config = world.root / "config.toml"
    config.write_text("workspace = AAAAA\n")
    stamp = config.stat().st_mtime_ns
    after_first = world.digest()

    config.write_text("workspace = BBBBB\n")
    os.utime(config, ns=(stamp, stamp))

    assert world.digest() != after_first


def test_two_copies_with_different_uncommitted_edits_to_the_same_file_differ(tmp_path: Path) -> None:
    a = _seed_world(tmp_path, "a")
    b = _clone_world(a, tmp_path, "b")
    assert a.digest() == b.digest()

    (a.root / "config.toml").write_text("workspace = edit-one\n")
    (b.root / "config.toml").write_text("workspace = edit-two\n")

    assert a.digest() != b.digest()


def test_reverting_an_edit_restores_the_original_digest(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    before = world.digest()
    config = world.root / "config.toml"

    config.write_text("workspace = edited\n")
    config.write_text("workspace = 1\n")

    assert world.digest() == before


def test_deleting_a_tracked_file_changes_the_digest(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    before = world.digest()

    (world.root / "config.toml").unlink()

    assert world.digest() != before


def test_untracked_files_do_not_change_the_digest(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    before = world.digest()

    (world.root / "scratch.txt").write_text("untracked\n")
    (world.standalone("ext-a") / "scratch.txt").write_text("untracked\n")

    assert world.digest() == before


def test_feature_environment_directories_do_not_change_the_digest(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    before = world.digest()

    (world.root / "envs" / "alpha" / "project").mkdir(parents=True)
    (world.root / "envs" / "alpha" / "project" / "file.py").write_text("print()\n")
    git_cmd(world.root / "envs" / "alpha" / "project", "init", "-q")

    assert world.digest() == before


def test_a_workspace_in_use_fingerprints_like_a_fresh_clone(tmp_path: Path) -> None:
    a = _seed_world(tmp_path, "a")
    b = _clone_world(a, tmp_path, "b")
    (b.root / "generated.md").write_text("projection\n")
    (b.root / "envs").mkdir()
    (b.root / "envs" / "note").write_text("x\n")

    assert a.digest() == b.digest()


def test_computing_the_fingerprint_leaves_the_real_index_untouched(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    (world.root / "config.toml").write_text("workspace = edited\n")
    status_before = git_cmd(world.root, "status", "--porcelain=v1")
    index_before = (world.root / ".git" / "index").read_bytes()

    world.service.compute()

    assert git_cmd(world.root, "status", "--porcelain=v1") == status_before
    assert git_cmd(world.root, "diff", "--cached", "--name-only") == ""
    assert (world.root / ".git" / "index").read_bytes() == index_before


def test_a_dirty_repo_reports_dirty_with_a_content_tree_that_differs_from_head(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    (world.standalone("ext-a") / "ext.toml").write_text("name = 'edited'\n")

    fingerprint = world.service.compute()

    by_name = {s.name: s for s in fingerprint.standalones}
    assert by_name["ext-a"].dirty is True
    assert by_name["ext-a"].tree != git_cmd(world.standalone("ext-a"), "rev-parse", "HEAD^{tree}").strip()
    assert by_name["ext-b"].dirty is False
    assert fingerprint.workspace.dirty is False


def test_an_untracked_only_repo_is_not_dirty(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    (world.root / "scratch.txt").write_text("untracked\n")

    assert world.service.compute().workspace.dirty is False


def test_components_report_head_commit_and_clean_tree(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")

    fingerprint = world.service.compute()

    assert fingerprint.workspace.commit == git_cmd(world.root, "rev-parse", "HEAD").strip()
    assert fingerprint.workspace.tree == git_cmd(world.root, "rev-parse", "HEAD^{tree}").strip()
    assert [s.name for s in fingerprint.standalones] == ["ext-a", "ext-b"]


# ── Handler and CLI ─────────────────────────────────────────────────────────


def test_handler_prints_only_the_digest_without_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    world = _seed_world(tmp_path, "a")

    FingerprintHandler(world.service).run(FingerprintParams(output_json=False))

    assert capsys.readouterr().out == world.digest() + "\n"


def test_handler_json_includes_digest_and_every_component(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    world = _seed_world(tmp_path, "a")
    (world.standalone("ext-b") / "ext.toml").write_text("name = 'edited'\n")

    FingerprintHandler(world.service).run(FingerprintParams(output_json=True))

    payload = json.loads(capsys.readouterr().out)
    assert payload["digest"] == world.digest()
    for entry in (payload["workspace"], *payload["standalones"]):
        assert set(entry) == {"name", "commit", "tree", "dirty"}
    assert [s["name"] for s in payload["standalones"]] == ["ext-a", "ext-b"]
    assert payload["workspace"]["dirty"] is False
    assert [s["dirty"] for s in payload["standalones"]] == [False, True]


def test_command_dispatches_to_the_container_handler(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    handler = FingerprintHandler(world.service)

    class _Container:
        def fingerprint_handler(self) -> FingerprintHandler:
            return handler

    class _Ctx:
        container = _Container()

    result = CliRunner().invoke(ws_fingerprint, ["--json"], obj=_Ctx())

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["digest"] == world.digest()


# ── Real-git tests: staged state ────────────────────────────────────────────


def _standalone_fingerprint(world: _World, name: str) -> RepoFingerprint:
    return {s.name: s for s in world.service.compute().standalones}[name]


def test_a_staged_new_file_changes_the_digest_and_sets_dirty(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    repo = world.standalone("ext-a")
    before = _standalone_fingerprint(world, "ext-a")
    digest_before = world.digest()

    (repo / "added.txt").write_text("new\n")
    git_cmd(repo, "add", "added.txt")

    after = _standalone_fingerprint(world, "ext-a")
    assert after.dirty is True
    assert after.tree != before.tree
    assert world.digest() != digest_before


def test_a_staged_rename_keeps_the_renamed_file_in_the_tree(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    repo = world.standalone("ext-a")
    digest_before = world.digest()

    git_cmd(repo, "mv", "ext.toml", "renamed.toml")

    fingerprint = _standalone_fingerprint(world, "ext-a")
    names = git_cmd(repo, "ls-tree", "--name-only", fingerprint.tree).split()
    assert names == ["renamed.toml"]
    assert fingerprint.dirty is True
    assert world.digest() != digest_before


def test_rm_cached_removes_the_file_from_the_tree_and_sets_dirty(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    repo = world.standalone("ext-a")
    digest_before = world.digest()

    git_cmd(repo, "rm", "-q", "--cached", "ext.toml")

    fingerprint = _standalone_fingerprint(world, "ext-a")
    assert git_cmd(repo, "ls-tree", "--name-only", fingerprint.tree).split() == []
    assert fingerprint.dirty is True
    assert world.digest() != digest_before


def test_a_file_staged_then_edited_again_contributes_its_working_tree_content(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    repo = world.standalone("ext-a")
    (repo / "ext.toml").write_text("name = 'staged'\n")
    git_cmd(repo, "add", "ext.toml")
    staged_only = _standalone_fingerprint(world, "ext-a")

    (repo / "ext.toml").write_text("name = 'edited again'\n")

    fingerprint = _standalone_fingerprint(world, "ext-a")
    assert fingerprint.dirty is True
    assert fingerprint.tree != staged_only.tree
    blob = git_cmd(repo, "rev-parse", f"{fingerprint.tree}:ext.toml").strip()
    assert git_cmd(repo, "cat-file", "blob", blob) == "name = 'edited again'\n"


def test_a_change_staged_and_then_reverted_in_the_working_tree_is_dirty_with_the_head_tree(tmp_path: Path) -> None:
    """`ws status` calls it dirty (index differs from HEAD); the content equals HEAD's."""
    world = _seed_world(tmp_path, "a")
    repo = world.standalone("ext-a")
    (repo / "ext.toml").write_text("name = 'staged'\n")
    git_cmd(repo, "add", "ext.toml")
    (repo / "ext.toml").write_text("name = 'ext-a'\n")

    fingerprint = _standalone_fingerprint(world, "ext-a")

    assert fingerprint.dirty is True
    assert fingerprint.tree == git_cmd(repo, "rev-parse", "HEAD^{tree}").strip()


def test_staged_state_leaves_the_real_index_byte_identical(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    repo = world.standalone("ext-a")
    (repo / "added.txt").write_text("new\n")
    git_cmd(repo, "add", "added.txt")
    git_cmd(repo, "mv", "ext.toml", "renamed.toml")
    (repo / "added.txt").write_text("edited\n")
    index_before = (repo / ".git" / "index").read_bytes()
    status_before = git_cmd(repo, "status", "--porcelain=v1")

    world.service.compute()

    assert (repo / ".git" / "index").read_bytes() == index_before
    assert git_cmd(repo, "status", "--porcelain=v1") == status_before


def test_a_repo_with_no_index_file_fingerprints_as_its_head_tree(tmp_path: Path) -> None:
    world = _seed_world(tmp_path, "a")
    repo = world.standalone("ext-a")
    (repo / ".git" / "index").unlink()

    fingerprint = _standalone_fingerprint(world, "ext-a")

    assert fingerprint.tree == git_cmd(repo, "rev-parse", "HEAD^{tree}").strip()


def test_an_edit_racing_the_index_write_is_still_seen(tmp_path: Path) -> None:
    """Same size, entry mtime equal to the index's: git rehashes such an entry, and so must the copy."""
    world = _seed_world(tmp_path, "a")
    repo = world.standalone("ext-a")
    target = repo / "ext.toml"
    target.write_text("name = 'AAAAAAAA'\n")
    stamp = target.stat().st_mtime_ns - 100 * 10**9  # long enough ago that only the racy rule can doubt it
    os.utime(target, ns=(stamp, stamp))
    git_cmd(repo, "add", "ext.toml")
    target.write_text("name = 'BBBBBBBB'\n")
    os.utime(target, ns=(stamp, stamp))
    os.utime(repo / ".git" / "index", ns=(stamp, stamp))

    fingerprint = _standalone_fingerprint(world, "ext-a")

    blob = git_cmd(repo, "rev-parse", f"{fingerprint.tree}:ext.toml").strip()
    assert git_cmd(repo, "cat-file", "blob", blob) == "name = 'BBBBBBBB'\n"
