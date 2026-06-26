from __future__ import annotations

from pathlib import Path

from tests.conftest import FakeFilesystem, FakeInitReporter
from winter_cli.config.models import AdoptExtensions, WorkspaceConfig
from winter_cli.modules.workspace.workspace_skill_service import WorkspaceSkillService

WORKSPACE_ROOT = Path("/ws")


def _config(prefix: str | None = "ws") -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        session_prefix="t",
        main_branch="main",
        adopt_extensions=AdoptExtensions.winter,
        skill_prefix=prefix,
    )


def _service(config: WorkspaceConfig, fs: FakeFilesystem) -> WorkspaceSkillService:
    return WorkspaceSkillService(config=config, fs=fs)


def _seed_skill(fs: FakeFilesystem, name: str, body: str = "---\ndescription: x\n---\n") -> Path:
    """Plant a skill directory under workspace_root/skills/<name>/SKILL.md."""
    skill_dir = WORKSPACE_ROOT / "skills" / name
    fs.directories.add(skill_dir)
    fs.files[skill_dir / "SKILL.md"] = body
    for parent in skill_dir.parents:
        fs.directories.add(parent)
    return skill_dir


# ── Core projection across all three vendors ──────────────────────────────────


def test_reconcile_projects_skill_into_claude_skills(init_reporter: FakeInitReporter) -> None:
    """Workspace skills appear as symlinks under .claude/skills/<prefix>-<name>."""
    fs = FakeFilesystem()
    _seed_skill(fs, "do-thing")
    svc = _service(_config("ws"), fs)

    ok = svc.reconcile(init_reporter)

    assert ok is True
    link = WORKSPACE_ROOT / ".claude" / "skills" / "ws-do-thing"
    assert fs.is_symlink(link)


def test_reconcile_projects_skill_into_codex_skills(init_reporter: FakeInitReporter) -> None:
    """Workspace skills appear as symlinks under .codex/skills/<prefix>-<name>."""
    fs = FakeFilesystem()
    _seed_skill(fs, "do-thing")
    svc = _service(_config("ws"), fs)

    ok = svc.reconcile(init_reporter)

    assert ok is True
    link = WORKSPACE_ROOT / ".codex" / "skills" / "ws-do-thing"
    assert fs.is_symlink(link)


def test_reconcile_projects_skill_into_opencode_skill(init_reporter: FakeInitReporter) -> None:
    """Workspace skills appear as real directories under .opencode/skill/<prefix>-<name>."""
    fs = FakeFilesystem()
    _seed_skill(fs, "do-thing", "---\ndescription: x\n---\n# do-thing\n")
    svc = _service(_config("ws"), fs)

    ok = svc.reconcile(init_reporter)

    assert ok is True
    dest = WORKSPACE_ROOT / ".opencode" / "skill" / "ws-do-thing"
    assert fs.is_dir(dest)
    assert not fs.is_symlink(dest)
    assert fs.is_file(dest / "SKILL.md")


def test_reconcile_all_three_vendors_in_one_pass(init_reporter: FakeInitReporter) -> None:
    """A single reconcile call projects into ClaudeCode, Codex, and OpenCode."""
    fs = FakeFilesystem()
    _seed_skill(fs, "my-skill")
    svc = _service(_config("myprefix"), fs)

    ok = svc.reconcile(init_reporter)

    assert ok is True
    assert fs.is_symlink(WORKSPACE_ROOT / ".claude" / "skills" / "myprefix-my-skill")
    assert fs.is_symlink(WORKSPACE_ROOT / ".codex" / "skills" / "myprefix-my-skill")
    assert fs.is_dir(WORKSPACE_ROOT / ".opencode" / "skill" / "myprefix-my-skill")


def test_reconcile_reports_action(init_reporter: FakeInitReporter) -> None:
    """A successful projection reports a workspace_skills_installed action."""
    fs = FakeFilesystem()
    _seed_skill(fs, "alpha")
    svc = _service(_config("ws"), fs)

    svc.reconcile(init_reporter)

    actions = [(a[0], a[2]) for a in init_reporter.actions]
    assert ("workspace", "workspace_skills_installed") in actions


# ── No-op cases ───────────────────────────────────────────────────────────────


def test_reconcile_noop_when_prefix_absent(init_reporter: FakeInitReporter) -> None:
    """No prefix configured → reconcile is a no-op and returns True."""
    fs = FakeFilesystem()
    _seed_skill(fs, "do-thing")
    svc = _service(_config(None), fs)

    ok = svc.reconcile(init_reporter)

    assert ok is True
    assert not fs.is_symlink(WORKSPACE_ROOT / ".claude" / "skills" / "ws-do-thing")
    assert not init_reporter.actions


def test_reconcile_noop_when_skills_dir_absent(init_reporter: FakeInitReporter) -> None:
    """skills/ directory absent → reconcile returns True (and prunes stale entries if any)."""
    fs = FakeFilesystem()
    # No skills/ directory seeded.
    svc = _service(_config("ws"), fs)

    ok = svc.reconcile(init_reporter)

    assert ok is True
    assert not init_reporter.actions


# ── Top-level prefix config parsing ──────────────────────────────────────────


def test_skill_prefix_parsed_from_config() -> None:
    """WorkspaceConfig.skill_prefix reflects the top-level prefix key."""
    cfg = WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        session_prefix="t",
        main_branch="main",
        skill_prefix="ws",
    )
    assert cfg.skill_prefix == "ws"


def test_skill_prefix_defaults_to_none() -> None:
    """WorkspaceConfig.skill_prefix is None when not set."""
    cfg = WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        session_prefix="t",
        main_branch="main",
    )
    assert cfg.skill_prefix is None


# ── Stale-prune on rename / removal ──────────────────────────────────────────


def test_reconcile_prunes_stale_symlinks_on_rename(init_reporter: FakeInitReporter) -> None:
    """When a skill directory is renamed, the old symlink is pruned and the new one installed."""
    fs = FakeFilesystem()
    _seed_skill(fs, "new-name")
    # Pre-plant the stale symlink from the old name.
    claude_skills = WORKSPACE_ROOT / ".claude" / "skills"
    fs.directories.add(claude_skills)
    fs.symlinks[claude_skills / "ws-old-name"] = Path("../../skills/old-name")
    codex_skills = WORKSPACE_ROOT / ".codex" / "skills"
    fs.directories.add(codex_skills)
    fs.symlinks[codex_skills / "ws-old-name"] = Path("../../skills/old-name")

    svc = _service(_config("ws"), fs)
    ok = svc.reconcile(init_reporter)

    assert ok is True
    # New name is installed.
    assert fs.is_symlink(claude_skills / "ws-new-name")
    assert fs.is_symlink(codex_skills / "ws-new-name")
    # Old stale symlink is pruned.
    assert not fs.is_symlink(claude_skills / "ws-old-name")
    assert not fs.is_symlink(codex_skills / "ws-old-name")


def test_reconcile_prunes_stale_copy_on_removal(init_reporter: FakeInitReporter) -> None:
    """When a skill directory is removed, the stale OpenCode copy is pruned."""
    fs = FakeFilesystem()
    _seed_skill(fs, "keep")
    # Pre-plant the stale copy for a removed skill.
    opencode_skill = WORKSPACE_ROOT / ".opencode" / "skill"
    stale_copy = opencode_skill / "ws-removed"
    fs.directories.add(stale_copy)
    fs.files[stale_copy / "SKILL.md"] = "---\nname: ws-removed\n---\n"
    for parent in stale_copy.parents:
        fs.directories.add(parent)

    svc = _service(_config("ws"), fs)
    ok = svc.reconcile(init_reporter)

    assert ok is True
    # Surviving skill is present.
    assert fs.is_dir(opencode_skill / "ws-keep")
    # Stale copy is removed.
    assert not fs.is_dir(stale_copy)


def test_reconcile_leaves_other_prefix_entries_untouched(init_reporter: FakeInitReporter) -> None:
    """Pruning only removes entries that match the workspace prefix; other prefixes survive."""
    fs = FakeFilesystem()
    _seed_skill(fs, "do-thing")
    # Pre-plant a symlink owned by a different prefix.
    claude_skills = WORKSPACE_ROOT / ".claude" / "skills"
    fs.directories.add(claude_skills)
    fs.symlinks[claude_skills / "ext-other-skill"] = Path("../../other-ext/skills/other-skill")
    for parent in (claude_skills / "ext-other-skill").parents:
        fs.directories.add(parent)

    svc = _service(_config("ws"), fs)
    ok = svc.reconcile(init_reporter)

    assert ok is True
    assert fs.is_symlink(claude_skills / "ws-do-thing")
    assert fs.is_symlink(claude_skills / "ext-other-skill")  # untouched — different prefix


def test_reconcile_prunes_when_skills_dir_removed(init_reporter: FakeInitReporter) -> None:
    """When skills/ is deleted wholesale, stale <prefix>-* symlinks are pruned."""
    fs = FakeFilesystem()
    # Pre-plant a stale symlink for a skill that was previously projected.
    claude_skills = WORKSPACE_ROOT / ".claude" / "skills"
    fs.directories.add(claude_skills)
    fs.symlinks[claude_skills / "ws-old-skill"] = Path("../../skills/old-skill")
    for parent in (claude_skills / "ws-old-skill").parents:
        fs.directories.add(parent)
    # No skills/ directory seeded — simulates wholesale removal.

    svc = _service(_config("ws"), fs)
    ok = svc.reconcile(init_reporter)

    assert ok is True
    # Stale symlink is pruned even though skills/ no longer exists.
    assert not fs.is_symlink(claude_skills / "ws-old-skill")


# ── Frontmatter validation ────────────────────────────────────────────────────


def test_reconcile_rejects_skill_md_with_name_frontmatter(init_reporter: FakeInitReporter) -> None:
    """A workspace skill whose SKILL.md sets `name:` causes reconcile to fail."""
    fs = FakeFilesystem()
    _seed_skill(fs, "bad-skill", "---\nname: overridden\ndescription: x\n---\n")
    svc = _service(_config("ws"), fs)

    ok = svc.reconcile(init_reporter)

    assert ok is False
    assert init_reporter.errors
    error_text = " ".join(e[1] for e in init_reporter.errors)
    assert "name" in error_text
    assert "overridden" in error_text
    # No symlink should have been created for the offending skill.
    assert not fs.is_symlink(WORKSPACE_ROOT / ".claude" / "skills" / "ws-bad-skill")


def test_reconcile_accepts_skill_md_without_name_frontmatter(init_reporter: FakeInitReporter) -> None:
    """A workspace skill whose SKILL.md omits `name:` is accepted and projected."""
    fs = FakeFilesystem()
    _seed_skill(fs, "good-skill", "---\ndescription: A clean skill\n---\n")
    svc = _service(_config("ws"), fs)

    ok = svc.reconcile(init_reporter)

    assert ok is True
    assert not init_reporter.errors
    assert fs.is_symlink(WORKSPACE_ROOT / ".claude" / "skills" / "ws-good-skill")


# ── Idempotency ───────────────────────────────────────────────────────────────


def test_reconcile_is_idempotent(init_reporter: FakeInitReporter) -> None:
    """Running reconcile twice produces the same result without errors."""
    fs = FakeFilesystem()
    _seed_skill(fs, "my-skill")
    svc = _service(_config("ws"), fs)

    ok1 = svc.reconcile(init_reporter)
    ok2 = svc.reconcile(init_reporter)

    assert ok1 is True
    assert ok2 is True
    assert not init_reporter.errors
    # Symlink is present after both runs.
    assert fs.is_symlink(WORKSPACE_ROOT / ".claude" / "skills" / "ws-my-skill")


def test_reconcile_idempotent_opencode_copy(init_reporter: FakeInitReporter) -> None:
    """Second reconcile does not re-copy OpenCode skill when content is unchanged."""
    fs = FakeFilesystem()
    _seed_skill(fs, "my-skill", "---\ndescription: x\n---\n# stable content\n")
    svc = _service(_config("ws"), fs)

    svc.reconcile(init_reporter)
    dest = WORKSPACE_ROOT / ".opencode" / "skill" / "ws-my-skill"
    # Capture the SKILL.md content after first run.
    content_after_first = fs.read_text(dest / "SKILL.md")

    svc.reconcile(init_reporter)
    content_after_second = fs.read_text(dest / "SKILL.md")

    # Content unchanged — no re-copy occurred.
    assert content_after_first == content_after_second
    assert not init_reporter.errors
