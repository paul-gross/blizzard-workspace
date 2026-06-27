from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import (
    FakeConfigFileReader,
    FakeFilesystem,
    FakeGitRepository,
)
from winter_cli.config.models import (
    AdoptExtensions,
    ProjectRepositoryConfig,
    WorkspaceConfig,
)
from winter_cli.modules.workspace.extension_exclude_service import ExtensionExcludeService
from winter_cli.modules.workspace.extension_manifest import ExtensionManifestLoader
from winter_cli.modules.workspace.prune_service import PruneOrphan, PruneService
from winter_cli.modules.workspace.repository_factory import RepositoryFactory

WORKSPACE_ROOT = Path("/ws")
PROJECTS_DIR = WORKSPACE_ROOT / "projects"


@pytest.fixture
def workspace_config() -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        session_prefix="t",
        main_branch="main",
        adopt_extensions=AdoptExtensions.winter,
        project_repos=[
            ProjectRepositoryConfig(name="kept", url="git@example.com:org/kept.git"),
        ],
    )


def _service(
    workspace_config: WorkspaceConfig,
    fs: FakeFilesystem,
    git: FakeGitRepository | None = None,
) -> PruneService:
    git = git or FakeGitRepository()
    # ExtensionExcludeService is only used here for finalize_excludes
    # (re-aggregation), which the prune tests don't exercise; pass in fakes
    # for completeness.
    exclude_svc = ExtensionExcludeService(
        config=workspace_config,
        fs=fs,
        manifest_loader=ExtensionManifestLoader(config_file_reader=FakeConfigFileReader({})),
    )
    return PruneService(
        config=workspace_config,
        repo_factory=RepositoryFactory(workspace_config),
        extension_exclude_svc=exclude_svc,
        fs=fs,
        git_repo=git,
    )


def test_find_orphans_returns_empty_when_projects_dir_missing(workspace_config: WorkspaceConfig) -> None:
    fs = FakeFilesystem()
    svc = _service(workspace_config, fs)
    assert svc.find_orphans() == []


def test_find_orphans_flags_undeclared_clean_clone_as_safe(workspace_config: WorkspaceConfig) -> None:
    """An undeclared clone with a clean tree and no linked worktrees is safe to remove."""
    orphan_path = PROJECTS_DIR / "ghost"
    fs = FakeFilesystem(
        directories=[PROJECTS_DIR, orphan_path],
        files={orphan_path / ".git" / "HEAD": "ref: refs/heads/main\n"},
    )
    git = FakeGitRepository()
    git.clean_worktrees.add(orphan_path)
    svc = _service(workspace_config, fs, git)

    orphans = svc.find_orphans()
    project_orphans = [o for o in orphans if o.kind == "project_clone"]
    assert len(project_orphans) == 1
    assert project_orphans[0].path == orphan_path
    assert project_orphans[0].safe_to_remove is True


def test_find_orphans_flags_dirty_clone_as_unsafe(workspace_config: WorkspaceConfig) -> None:
    orphan_path = PROJECTS_DIR / "dirty"
    fs = FakeFilesystem(
        directories=[PROJECTS_DIR, orphan_path],
        files={orphan_path / ".git" / "HEAD": "ref: refs/heads/main\n"},
    )
    git = FakeGitRepository()  # nothing added to clean_worktrees → dirty
    svc = _service(workspace_config, fs, git)

    [orphan] = [o for o in svc.find_orphans() if o.kind == "project_clone"]
    assert orphan.safe_to_remove is False
    assert "uncommitted or untracked" in orphan.notes


def test_find_orphans_flags_clone_with_linked_worktrees_as_unsafe(workspace_config: WorkspaceConfig) -> None:
    orphan_path = PROJECTS_DIR / "linked"
    linked_wt_dir = orphan_path / ".git" / "worktrees" / "alpha"
    fs = FakeFilesystem(
        directories=[PROJECTS_DIR, orphan_path, orphan_path / ".git" / "worktrees", linked_wt_dir],
        files={orphan_path / ".git" / "HEAD": ""},
    )
    git = FakeGitRepository()
    git.clean_worktrees.add(orphan_path)  # clean — still blocked by linked worktrees
    svc = _service(workspace_config, fs, git)

    [orphan] = [o for o in svc.find_orphans() if o.kind == "project_clone"]
    assert orphan.safe_to_remove is False
    assert "linked worktrees" in orphan.notes


def test_remove_orphan_deletes_safe_clone(workspace_config: WorkspaceConfig) -> None:
    orphan_path = PROJECTS_DIR / "ghost"
    fs = FakeFilesystem(
        directories=[PROJECTS_DIR, orphan_path],
        files={orphan_path / ".git" / "HEAD": ""},
    )
    git = FakeGitRepository()
    git.clean_worktrees.add(orphan_path)
    svc = _service(workspace_config, fs, git)

    [orphan] = svc.find_orphans()
    svc.remove_orphan(orphan)
    assert not fs.exists(orphan_path)


def test_remove_orphan_refuses_unsafe(workspace_config: WorkspaceConfig) -> None:
    fs = FakeFilesystem()
    svc = _service(workspace_config, fs)
    unsafe = PruneOrphan(kind="project_clone", path=WORKSPACE_ROOT / "x", safe_to_remove=False, notes="dirty")
    with pytest.raises(RuntimeError, match="unsafe orphan"):
        svc.remove_orphan(unsafe)


def test_find_broken_symlinks_under_claude_dirs(workspace_config: WorkspaceConfig) -> None:
    """Broken symlinks under .claude/{skills,agents} are flagged as safe-to-remove orphans."""
    claude_skills = WORKSPACE_ROOT / ".claude" / "skills"
    fs = FakeFilesystem(directories=[claude_skills])
    fs.symlinks[claude_skills / "ext-removed"] = Path("../../ext-removed/skills/x")
    svc = _service(workspace_config, fs)

    orphans = svc.find_orphans()
    broken = [o for o in orphans if o.kind == "broken_symlink"]
    assert len(broken) == 1
    assert broken[0].safe_to_remove is True


def test_find_broken_symlinks_under_codex_skills(workspace_config: WorkspaceConfig) -> None:
    """Broken symlinks under .codex/skills are healed alongside the .claude/ surfaces."""
    codex_skills = WORKSPACE_ROOT / ".codex" / "skills"
    fs = FakeFilesystem(directories=[codex_skills])
    fs.symlinks[codex_skills / "ext-removed"] = Path("../../ext-removed/skills/x")
    svc = _service(workspace_config, fs)

    orphans = svc.find_orphans()
    broken = [o for o in orphans if o.kind == "broken_symlink"]
    assert len(broken) == 1
    assert broken[0].path == codex_skills / "ext-removed"
    assert broken[0].safe_to_remove is True


# ---------------------------------------------------------------------------
# _find_orphan_agent_copies
# ---------------------------------------------------------------------------

# A minimal exclude file block for the "old-ext" extension.
# Written by ExtensionExcludeService with prefix "old" when the extension
# was last active; the repo has since been removed from config.
_EXCLUDE_WITH_OLD_EXT = """\
# >>> old-ext (managed by winter)
/old-ext/
.claude/skills/old-*
.codex/skills/old-*
.opencode/skill/old-*
.claude/agents/old-*
.codex/agents/old-*
.opencode/agent/old-*
# <<< old-ext
"""

_EXCLUDE_WITH_KEPT_EXT = """\
# >>> kept-ext (managed by winter)
/kept-ext/
.claude/skills/kept-*
.codex/skills/kept-*
.opencode/skill/kept-*
.claude/agents/kept-*
.codex/agents/kept-*
.opencode/agent/kept-*
# <<< kept-ext
"""


def _write_exclude(fs: FakeFilesystem, workspace_root: Path, content: str) -> None:
    exclude_path = workspace_root / ".git" / "info" / "exclude"
    fs.files[exclude_path] = content
    for parent in exclude_path.parents:
        fs.directories.add(parent)


def test_find_orphan_agent_copies_claude(workspace_config: WorkspaceConfig) -> None:
    """A <prefix>-* file in .claude/agents whose extension is removed → orphan_agent_copy."""
    claude_agents = WORKSPACE_ROOT / ".claude" / "agents"
    orphan_file = claude_agents / "old-reviewer.md"

    fs = FakeFilesystem(
        directories=[PROJECTS_DIR, claude_agents],
        files={orphan_file: "# old agent"},
    )
    _write_exclude(fs, WORKSPACE_ROOT, _EXCLUDE_WITH_OLD_EXT)
    svc = _service(workspace_config, fs)

    orphans = svc.find_orphans()
    agent_orphans = [o for o in orphans if o.kind == "orphan_agent_copy"]
    assert len(agent_orphans) == 1
    assert agent_orphans[0].path == orphan_file
    assert agent_orphans[0].safe_to_remove is True
    assert agent_orphans[0].notes == ""


def test_find_orphan_agent_copies_codex(workspace_config: WorkspaceConfig) -> None:
    """A <prefix>-* file in .codex/agents whose extension is removed → orphan_agent_copy."""
    codex_agents = WORKSPACE_ROOT / ".codex" / "agents"
    orphan_file = codex_agents / "old-reviewer.toml"

    fs = FakeFilesystem(
        directories=[PROJECTS_DIR, codex_agents],
        files={orphan_file: 'name = "reviewer"\n'},
    )
    _write_exclude(fs, WORKSPACE_ROOT, _EXCLUDE_WITH_OLD_EXT)
    svc = _service(workspace_config, fs)

    orphans = svc.find_orphans()
    agent_orphans = [o for o in orphans if o.kind == "orphan_agent_copy"]
    assert any(o.path == orphan_file for o in agent_orphans)
    assert all(o.safe_to_remove for o in agent_orphans)


def test_find_orphan_agent_copies_opencode(workspace_config: WorkspaceConfig) -> None:
    """A <prefix>-* file in .opencode/agent whose extension is removed → orphan_agent_copy."""
    opencode_agents = WORKSPACE_ROOT / ".opencode" / "agent"
    orphan_file = opencode_agents / "old-reviewer.md"

    fs = FakeFilesystem(
        directories=[PROJECTS_DIR, opencode_agents],
        files={orphan_file: "# old opencode agent"},
    )
    _write_exclude(fs, WORKSPACE_ROOT, _EXCLUDE_WITH_OLD_EXT)
    svc = _service(workspace_config, fs)

    orphans = svc.find_orphans()
    agent_orphans = [o for o in orphans if o.kind == "orphan_agent_copy"]
    assert any(o.path == orphan_file for o in agent_orphans)


def test_find_orphan_agent_copies_all_three_vendors(workspace_config: WorkspaceConfig) -> None:
    """Copies across all three vendor dirs are reported when the extension is removed."""
    claude_agents = WORKSPACE_ROOT / ".claude" / "agents"
    codex_agents = WORKSPACE_ROOT / ".codex" / "agents"
    opencode_agents = WORKSPACE_ROOT / ".opencode" / "agent"

    claude_file = claude_agents / "old-reviewer.md"
    codex_file = codex_agents / "old-reviewer.toml"
    oc_file = opencode_agents / "old-reviewer.md"

    fs = FakeFilesystem(
        directories=[PROJECTS_DIR, claude_agents, codex_agents, opencode_agents],
        files={
            claude_file: "# claude",
            codex_file: "# codex",
            oc_file: "# opencode",
        },
    )
    _write_exclude(fs, WORKSPACE_ROOT, _EXCLUDE_WITH_OLD_EXT)
    svc = _service(workspace_config, fs)

    orphans = svc.find_orphans()
    agent_orphans = [o for o in orphans if o.kind == "orphan_agent_copy"]
    assert len(agent_orphans) == 3
    paths = {o.path for o in agent_orphans}
    assert claude_file in paths
    assert codex_file in paths
    assert oc_file in paths


def test_find_orphan_agent_copies_skips_live_extension(workspace_config: WorkspaceConfig) -> None:
    """Agent copies for an extension still in config are NOT flagged as orphans."""
    from winter_cli.config.models import StandaloneRepositoryConfig

    claude_agents = WORKSPACE_ROOT / ".claude" / "agents"
    kept_file = claude_agents / "kept-reviewer.md"

    fs = FakeFilesystem(
        directories=[PROJECTS_DIR, claude_agents],
        files={kept_file: "# kept"},
    )
    _write_exclude(fs, WORKSPACE_ROOT, _EXCLUDE_WITH_KEPT_EXT)

    # Build a config where "kept-ext" IS a live standalone repo.
    config_with_live_ext = WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        session_prefix="t",
        main_branch="main",
        adopt_extensions=AdoptExtensions.winter,
        standalone_repos=[StandaloneRepositoryConfig(name="kept-ext")],
    )
    svc = _service(config_with_live_ext, fs)
    orphans = svc.find_orphans()
    agent_orphans = [o for o in orphans if o.kind == "orphan_agent_copy"]
    # "kept-ext" is live → its copies must NOT be flagged.
    assert not any(o.path == kept_file for o in agent_orphans), (
        f"Live extension copy wrongly flagged: {[o.path for o in agent_orphans]}"
    )


def test_find_orphan_agent_copies_no_exclude_file(workspace_config: WorkspaceConfig) -> None:
    """With no exclude file, _find_orphan_agent_copies returns nothing."""
    fs = FakeFilesystem(directories=[PROJECTS_DIR])
    svc = _service(workspace_config, fs)
    orphans = svc.find_orphans()
    agent_orphans = [o for o in orphans if o.kind == "orphan_agent_copy"]
    assert agent_orphans == []


def test_remove_orphan_agent_copy(workspace_config: WorkspaceConfig) -> None:
    """remove_orphan() deletes a safe orphan_agent_copy file."""
    claude_agents = WORKSPACE_ROOT / ".claude" / "agents"
    orphan_file = claude_agents / "old-reviewer.md"

    fs = FakeFilesystem(
        directories=[PROJECTS_DIR, claude_agents],
        files={orphan_file: "# old"},
    )
    _write_exclude(fs, WORKSPACE_ROOT, _EXCLUDE_WITH_OLD_EXT)
    svc = _service(workspace_config, fs)

    orphans = svc.find_orphans()
    agent_orphans = [o for o in orphans if o.kind == "orphan_agent_copy"]
    assert len(agent_orphans) >= 1

    # Remove the claude copy.
    target = next(o for o in agent_orphans if o.path == orphan_file)
    svc.remove_orphan(target)
    assert not fs.is_file(orphan_file)
