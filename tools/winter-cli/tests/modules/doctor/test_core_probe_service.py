from __future__ import annotations

from pathlib import Path
from typing import cast

from tests.conftest import FakeFilesystem
from winter_cli.config.models import WorkspaceConfig
from winter_cli.core.config_file import IConfigFileReader
from winter_cli.core.filesystem import IFilesystemReader
from winter_cli.core.subprocess_runner import ISubprocessRunner
from winter_cli.modules.doctor.core_probe_service import CORE_SOURCE, CoreProbeService
from winter_cli.modules.doctor.models import ProbeStatus
from winter_cli.modules.workspace.repo_repository import IWriteRepoRepository
from winter_cli.modules.workspace.repository_factory import RepositoryFactory
from winter_cli.modules.workspace.workspace_repository import IReadWorkspaceRepository

WORKSPACE_ROOT = Path("/ws")
CLAUDE_AGENTS = WORKSPACE_ROOT / ".claude" / "agents"
CLAUDE_SKILLS = WORKSPACE_ROOT / ".claude" / "skills"
CODEX_SKILLS = WORKSPACE_ROOT / ".codex" / "skills"


def _build_service(fs: FakeFilesystem) -> CoreProbeService:
    """Construct a CoreProbeService for direct-method tests.

    The claude-symlinks probe only touches `config` and `fs`; the remaining
    collaborators are stubbed because exercising `run()` would drag in
    git/repo plumbing irrelevant to this probe.
    """
    config = WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        session_prefix="t",
        main_branch="main",
    )
    return CoreProbeService(
        config=config,
        fs=cast(IFilesystemReader, fs),
        subprocess_runner=cast(ISubprocessRunner, None),
        config_file_reader=cast(IConfigFileReader, None),
        repo_factory=cast(RepositoryFactory, None),
        worktree_repo=cast(IReadWorkspaceRepository, None),
        repo_repo=cast(IWriteRepoRepository, None),
    )


def test_claude_symlinks_probe_returns_none_when_directories_absent() -> None:
    fs = FakeFilesystem(directories={WORKSPACE_ROOT})
    svc = _build_service(fs)

    assert svc._probe_claude_symlinks() is None


def test_claude_symlinks_probe_passes_when_all_symlinks_resolve() -> None:
    agent_target = WORKSPACE_ROOT / "context" / "harness" / "agents" / "code-reviewer.md"
    skill_target = WORKSPACE_ROOT / "context" / "harness" / "skills" / "verify"
    skill_marker = skill_target / "SKILL.md"
    fs = FakeFilesystem(
        files={agent_target: "...", skill_marker: "..."},
        directories={CLAUDE_AGENTS, CLAUDE_SKILLS, skill_target},
        symlinks={
            CLAUDE_AGENTS / "wh-code-reviewer.md": Path("../../context/harness/agents/code-reviewer.md"),
            CLAUDE_SKILLS / "wh-verify": Path("../../context/harness/skills/verify"),
        },
    )
    svc = _build_service(fs)

    result = svc._probe_claude_symlinks()

    assert result is not None
    assert result.status == ProbeStatus.pass_
    assert result.source == CORE_SOURCE
    assert result.name == "extension symlinks"


def test_claude_symlinks_probe_fails_and_names_every_orphan() -> None:
    fs = FakeFilesystem(
        directories={CLAUDE_AGENTS, CLAUDE_SKILLS},
        symlinks={
            CLAUDE_AGENTS / "wf-agentic-development-manager.md": Path(
                "../../context/workflow/agents/agentic-development-manager.md"
            ),
            CLAUDE_SKILLS / "wf-old-skill": Path("../../context/workflow/skills/old-skill"),
        },
    )
    svc = _build_service(fs)

    result = svc._probe_claude_symlinks()

    assert result is not None
    assert result.status == ProbeStatus.fail
    assert ".claude/agents/wf-agentic-development-manager.md" in result.message
    assert ".claude/skills/wf-old-skill" in result.message
    assert result.remediation is not None
    assert "winter ws init" in result.remediation


def test_claude_symlinks_probe_flags_broken_codex_skill_symlink() -> None:
    """A broken symlink under `.codex/skills` is audited alongside `.claude/`."""
    fs = FakeFilesystem(
        directories={CODEX_SKILLS},
        symlinks={CODEX_SKILLS / "wf-gone": Path("../../missing/skill")},
    )
    svc = _build_service(fs)

    result = svc._probe_claude_symlinks()

    assert result is not None
    assert result.status == ProbeStatus.fail
    assert ".codex/skills/wf-gone" in result.message


def test_claude_symlinks_probe_ignores_regular_files_and_dirs() -> None:
    """Plain README.md / regular subdirs under .claude/ must not register as orphans."""
    valid_target = WORKSPACE_ROOT / "context" / "harness" / "agents" / "code-reviewer.md"
    readme = CLAUDE_AGENTS / "README.md"
    plain_subdir = CLAUDE_AGENTS / "docs"
    fs = FakeFilesystem(
        files={valid_target: "...", readme: "extension-installed notes"},
        directories={CLAUDE_AGENTS, plain_subdir},
        symlinks={
            CLAUDE_AGENTS / "wh-code-reviewer.md": Path("../../context/harness/agents/code-reviewer.md"),
        },
    )
    svc = _build_service(fs)

    result = svc._probe_claude_symlinks()

    assert result is not None
    assert result.status == ProbeStatus.pass_


def test_claude_symlinks_probe_runs_when_only_one_subdir_present() -> None:
    """`.claude/agents` alone (no `.claude/skills`) still gets walked."""
    fs = FakeFilesystem(
        directories={CLAUDE_AGENTS},
        symlinks={CLAUDE_AGENTS / "wf-gone.md": Path("../../missing/agent.md")},
    )
    svc = _build_service(fs)

    result = svc._probe_claude_symlinks()

    assert result is not None
    assert result.status == ProbeStatus.fail
    assert ".claude/agents/wf-gone.md" in result.message


# ── _probe_agents_shim ────────────────────────────────────────────────────────

CLAUDE_MD = WORKSPACE_ROOT / "CLAUDE.md"
AGENTS_MD = WORKSPACE_ROOT / "AGENTS.md"


def test_agents_shim_probe_returns_none_when_neither_file_exists() -> None:
    fs = FakeFilesystem(directories={WORKSPACE_ROOT})
    svc = _build_service(fs)

    assert svc._probe_agents_shim() is None


def test_agents_shim_probe_passes_when_both_exist_and_claude_is_shim() -> None:
    fs = FakeFilesystem(
        files={
            CLAUDE_MD: "@AGENTS.md",
            AGENTS_MD: "# canonical body\n",
        }
    )
    svc = _build_service(fs)

    result = svc._probe_agents_shim()

    assert result is not None
    assert result.status == ProbeStatus.pass_
    assert result.source == CORE_SOURCE
    assert result.name == "AGENTS.md shim"


def test_agents_shim_probe_passes_with_trailing_newline_in_shim() -> None:
    """Shim content with a trailing newline should still count as the correct shim."""
    fs = FakeFilesystem(
        files={
            CLAUDE_MD: "@AGENTS.md\n",
            AGENTS_MD: "# canonical body\n",
        }
    )
    svc = _build_service(fs)

    result = svc._probe_agents_shim()

    assert result is not None
    assert result.status == ProbeStatus.pass_


def test_agents_shim_probe_fails_when_claude_has_real_body() -> None:
    """CLAUDE.md containing real documentation content is the core drift case."""
    fs = FakeFilesystem(
        files={
            CLAUDE_MD: "# CLAUDE.md\nThis is the real documentation body.\n",
            AGENTS_MD: "# canonical body\n",
        }
    )
    svc = _build_service(fs)

    result = svc._probe_agents_shim()

    assert result is not None
    assert result.status == ProbeStatus.fail
    assert result.remediation is not None
    assert "AGENTS.md" in result.remediation


def test_agents_shim_probe_fails_when_agents_md_missing_but_claude_present() -> None:
    fs = FakeFilesystem(files={CLAUDE_MD: "@AGENTS.md\n"})
    svc = _build_service(fs)

    result = svc._probe_agents_shim()

    assert result is not None
    assert result.status == ProbeStatus.fail
    assert "AGENTS.md is missing" in result.message
    assert result.remediation is not None


def test_agents_shim_probe_fails_when_claude_md_missing_but_agents_present() -> None:
    fs = FakeFilesystem(files={AGENTS_MD: "# canonical body\n"})
    svc = _build_service(fs)

    result = svc._probe_agents_shim()

    assert result is not None
    assert result.status == ProbeStatus.fail
    assert "CLAUDE.md is missing" in result.message
    assert result.remediation is not None


def test_agents_shim_probe_fails_when_shim_points_at_wrong_target() -> None:
    """Shim that imports @OTHER.md instead of @AGENTS.md must be flagged."""
    fs = FakeFilesystem(
        files={
            CLAUDE_MD: "@OTHER.md\n",
            AGENTS_MD: "# canonical body\n",
        }
    )
    svc = _build_service(fs)

    result = svc._probe_agents_shim()

    assert result is not None
    assert result.status == ProbeStatus.fail
    assert result.remediation is not None
    assert "@AGENTS.md" in result.remediation
