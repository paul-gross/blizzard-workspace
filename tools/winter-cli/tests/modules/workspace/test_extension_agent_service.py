"""Integration tests for ExtensionAgentService.

Drives ExtensionAgentService against a temp in-memory workspace through the
FakeFilesystem seam, mirroring the style of test_workspace_skill_service.py.
Asserts on actual emitted file contents to verify structural validity of each
format.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import cast

import yaml

from tests.conftest import FakeConfigFileReader, FakeFilesystem, FakeInitReporter
from winter_cli.config.models import AdoptExtensions, WorkspaceConfig
from winter_cli.modules.workspace.agent_install import ExtensionAgentService
from winter_cli.modules.workspace.agent_transform.agent_enumerator import CanonicalAgentEnumerator
from winter_cli.modules.workspace.extension_manifest import ExtensionManifestLoader
from winter_cli.modules.workspace.models import StandaloneRepository

WORKSPACE_ROOT = Path("/ws")

# A minimal canonical agent file used by most test cases.
_CANONICAL_AGENT = """\
---
name: reviewer
description: Reviews code changes
model: sonnet
---
You are a code reviewer.
"""

# A second agent used in multi-agent and prune tests.
_CANONICAL_AGENT_2 = """\
---
name: planner
description: Plans tasks
model: haiku
---
You are a planner.
"""

# An agent that carries a tools field so lossy-projection warnings fire for
# Codex and OpenCode (which have no tools equivalent).
_CANONICAL_AGENT_WITH_TOOLS = """\
---
name: helper
description: General helper
model: sonnet
tools:
  - Bash
  - Read
---
You are a helper.
"""


def _config(adopt_extensions: AdoptExtensions = AdoptExtensions.winter) -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        service_prefix="t",
        main_branch="main",
        adopt_extensions=adopt_extensions,
    )


def _service(
    config: WorkspaceConfig,
    fs: FakeFilesystem,
    config_files: dict[Path, dict] | None = None,
) -> ExtensionAgentService:
    loader = ExtensionManifestLoader(config_file_reader=FakeConfigFileReader(config_files or {}))
    return ExtensionAgentService(
        config=config,
        fs=fs,
        manifest_loader=loader,
        agent_enumerator=CanonicalAgentEnumerator(fs=fs, manifest_loader=loader),
    )


def _seed_extension(
    fs: FakeFilesystem,
    config_files: dict[Path, dict],
    name: str = "wf",
    *,
    agent_files: dict[str, str] | None = None,
) -> StandaloneRepository:
    """Plant an extension with canonical agent files in the fake filesystem.

    `name` is both the extension name and the prefix. `agent_files` maps
    filename → content for files placed under ``<ext>/agents/``; defaults to
    placing ``_CANONICAL_AGENT`` as ``reviewer.md``.
    """
    ext_path = WORKSPACE_ROOT / name
    fs.directories.add(ext_path)
    for parent in ext_path.parents:
        fs.directories.add(parent)

    manifest_path = ext_path / "winter-ext.toml"
    fs.files[manifest_path] = ""
    config_files[manifest_path] = {"name": name}

    agents_dir = ext_path / "agents"
    fs.directories.add(agents_dir)

    files = agent_files if agent_files is not None else {"reviewer.md": _CANONICAL_AGENT}
    for filename, content in files.items():
        fs.files[agents_dir / filename] = content

    return StandaloneRepository(name=name, path=ext_path)


def _parse_frontmatter(text: str) -> dict:
    """Extract the YAML frontmatter from a ``---`` delimited Markdown file."""
    lines = text.split("\n")
    assert lines[0] == "---", f"no opening --- in: {text!r}"
    end_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break
    assert end_idx is not None, "frontmatter not closed"
    fm_yaml = "\n".join(lines[1:end_idx])
    return yaml.safe_load(fm_yaml) or {}


# ── Core: three-vendor materialization from one canonical source ──────────────


def test_process_creates_claude_copy(init_reporter: FakeInitReporter) -> None:
    """Canonical agent renders to .claude/agents/<prefix>-<name>.md."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files)
    svc = _service(_config(), fs, config_files)

    ok = svc.process(ext, init_reporter)

    assert ok is True
    dest = WORKSPACE_ROOT / ".claude" / "agents" / "wf-reviewer.md"
    assert fs.is_file(dest)
    assert not fs.is_symlink(dest)


def test_process_creates_codex_copy(init_reporter: FakeInitReporter) -> None:
    """Canonical agent renders to .codex/agents/<prefix>-<name>.toml."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files)
    svc = _service(_config(), fs, config_files)

    ok = svc.process(ext, init_reporter)

    assert ok is True
    dest = WORKSPACE_ROOT / ".codex" / "agents" / "wf-reviewer.toml"
    assert fs.is_file(dest)
    assert not fs.is_symlink(dest)


def test_process_creates_opencode_copy(init_reporter: FakeInitReporter) -> None:
    """Canonical agent renders to .opencode/agent/<prefix>-<name>.md."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files)
    svc = _service(_config(), fs, config_files)

    ok = svc.process(ext, init_reporter)

    assert ok is True
    dest = WORKSPACE_ROOT / ".opencode" / "agent" / "wf-reviewer.md"
    assert fs.is_file(dest)
    assert not fs.is_symlink(dest)


def test_process_all_three_vendors_in_one_call(init_reporter: FakeInitReporter) -> None:
    """A single process() call materializes copies for all three harnesses."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files)
    svc = _service(_config(), fs, config_files)

    ok = svc.process(ext, init_reporter)

    assert ok is True
    assert fs.is_file(WORKSPACE_ROOT / ".claude" / "agents" / "wf-reviewer.md")
    assert fs.is_file(WORKSPACE_ROOT / ".codex" / "agents" / "wf-reviewer.toml")
    assert fs.is_file(WORKSPACE_ROOT / ".opencode" / "agent" / "wf-reviewer.md")


# ── Structural content validation ─────────────────────────────────────────────


def test_claude_md_has_valid_frontmatter(init_reporter: FakeInitReporter) -> None:
    """Claude copy has YAML frontmatter with name, description, and model fields."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files)
    svc = _service(_config(), fs, config_files)

    svc.process(ext, init_reporter)

    dest = WORKSPACE_ROOT / ".claude" / "agents" / "wf-reviewer.md"
    fm = _parse_frontmatter(fs.read_text(dest))
    assert fm["name"] == "reviewer"
    assert fm["description"] == "Reviews code changes"
    assert fm["model"] == "sonnet"


def test_codex_toml_is_parseable(init_reporter: FakeInitReporter) -> None:
    """Codex copy is valid TOML with name, description, model, and developer_instructions."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files)
    svc = _service(_config(), fs, config_files)

    svc.process(ext, init_reporter)

    dest = WORKSPACE_ROOT / ".codex" / "agents" / "wf-reviewer.toml"
    doc = tomllib.loads(fs.read_text(dest))
    assert doc["name"] == "reviewer"
    assert doc["description"] == "Reviews code changes"
    assert "model" in doc
    assert doc["developer_instructions"] == "You are a code reviewer.\n"


def test_opencode_md_has_valid_frontmatter(init_reporter: FakeInitReporter) -> None:
    """OpenCode copy has YAML frontmatter with description and model (no name field)."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files)
    svc = _service(_config(), fs, config_files)

    svc.process(ext, init_reporter)

    dest = WORKSPACE_ROOT / ".opencode" / "agent" / "wf-reviewer.md"
    fm = _parse_frontmatter(fs.read_text(dest))
    assert "description" in fm
    assert "model" in fm
    # OpenCode does not use a `name` frontmatter field; identity comes from filename.
    assert "name" not in fm


def test_body_is_preserved_in_claude_copy(init_reporter: FakeInitReporter) -> None:
    """The body text is copied verbatim into the Claude artifact."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files)
    svc = _service(_config(), fs, config_files)

    svc.process(ext, init_reporter)

    dest = WORKSPACE_ROOT / ".claude" / "agents" / "wf-reviewer.md"
    content = fs.read_text(dest)
    assert "You are a code reviewer." in content


def test_body_appears_as_developer_instructions_in_codex(init_reporter: FakeInitReporter) -> None:
    """Codex TOML carries the body as `developer_instructions`."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files)
    svc = _service(_config(), fs, config_files)

    svc.process(ext, init_reporter)

    dest = WORKSPACE_ROOT / ".codex" / "agents" / "wf-reviewer.toml"
    doc = tomllib.loads(fs.read_text(dest))
    assert "You are a code reviewer." in doc["developer_instructions"]


# ── Idempotency ───────────────────────────────────────────────────────────────


def test_process_is_idempotent(init_reporter: FakeInitReporter) -> None:
    """Running process() twice produces the same files without errors."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files)
    svc = _service(_config(), fs, config_files)

    ok1 = svc.process(ext, init_reporter)
    ok2 = svc.process(ext, init_reporter)

    assert ok1 is True
    assert ok2 is True
    assert not init_reporter.errors


def test_second_process_does_not_rewrite_unchanged_files(init_reporter: FakeInitReporter) -> None:
    """Re-running process() with unchanged source leaves files byte-identical."""

    class TrackingFS(FakeFilesystem):
        def __init__(self) -> None:
            super().__init__()
            self.write_calls: list[Path] = []

        def write_text(self, path: Path, data: str) -> None:
            self.write_calls.append(path)
            super().write_text(path, data)

    fs = TrackingFS()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files)
    svc = _service(_config(), fs, config_files)

    svc.process(ext, init_reporter)
    writes_after_first = len(fs.write_calls)

    svc.process(ext, init_reporter)
    writes_after_second = len(fs.write_calls)

    # Second pass must not write anything (byte-compare skips unchanged files).
    assert writes_after_second == writes_after_first


# ── Staleness refresh ─────────────────────────────────────────────────────────


def test_changed_source_triggers_rewrite(init_reporter: FakeInitReporter) -> None:
    """Modifying the canonical source causes the copy to be refreshed on next process()."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files)
    svc = _service(_config(), fs, config_files)

    svc.process(ext, init_reporter)

    claude_dest = WORKSPACE_ROOT / ".claude" / "agents" / "wf-reviewer.md"
    content_before = fs.read_text(claude_dest)

    # Update the source file with a different description.
    updated = _CANONICAL_AGENT.replace("Reviews code changes", "Reviews code thoroughly")
    fs.files[ext.path / "agents" / "reviewer.md"] = updated

    svc.process(ext, init_reporter)

    content_after = fs.read_text(claude_dest)
    assert content_before != content_after
    assert "Reviews code thoroughly" in content_after


# ── Pruning ───────────────────────────────────────────────────────────────────


def test_deleting_source_prunes_copies_from_all_dirs(init_reporter: FakeInitReporter) -> None:
    """Removing the canonical source file causes its rendered copies to be pruned."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(
        fs,
        config_files,
        agent_files={"reviewer.md": _CANONICAL_AGENT, "planner.md": _CANONICAL_AGENT_2},
    )
    svc = _service(_config(), fs, config_files)

    svc.process(ext, init_reporter)

    # Both agents materialized.
    assert fs.is_file(WORKSPACE_ROOT / ".claude" / "agents" / "wf-reviewer.md")
    assert fs.is_file(WORKSPACE_ROOT / ".claude" / "agents" / "wf-planner.md")

    # Remove the planner source.
    del fs.files[ext.path / "agents" / "planner.md"]

    svc.process(ext, init_reporter)

    # Reviewer copy survives; planner copy pruned from all three dirs.
    assert fs.is_file(WORKSPACE_ROOT / ".claude" / "agents" / "wf-reviewer.md")
    assert not fs.is_file(WORKSPACE_ROOT / ".claude" / "agents" / "wf-planner.md")
    assert not fs.is_file(WORKSPACE_ROOT / ".codex" / "agents" / "wf-planner.toml")
    assert not fs.is_file(WORKSPACE_ROOT / ".opencode" / "agent" / "wf-planner.md")


def test_prune_removes_stale_symlinks_from_prior_scheme(init_reporter: FakeInitReporter) -> None:
    """Stale <prefix>-* symlinks left by the old symlink-based agent install are pruned."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files)

    # Pre-plant old-style symlink in .claude/agents (from the pre-Phase-2 scheme).
    agents_dir = WORKSPACE_ROOT / ".claude" / "agents"
    fs.directories.add(agents_dir)
    fs.symlinks[agents_dir / "wf-old-agent.md"] = Path("../../wf/agents/old-agent.md")
    # Also plant a different-prefix symlink that must survive.
    fs.symlinks[agents_dir / "other-keep.md"] = Path("../../other/agents/keep.md")
    for p in [agents_dir / "wf-old-agent.md", agents_dir / "other-keep.md"]:
        for parent in p.parents:
            fs.directories.add(parent)

    svc = _service(_config(), fs, config_files)
    ok = svc.process(ext, init_reporter)

    assert ok is True
    # Stale own-prefix symlink pruned.
    assert not fs.is_symlink(agents_dir / "wf-old-agent.md")
    # Different-prefix symlink untouched.
    assert fs.is_symlink(agents_dir / "other-keep.md")
    # New canonical copy rendered.
    assert fs.is_file(agents_dir / "wf-reviewer.md")


# ── No-op modes ───────────────────────────────────────────────────────────────


def test_process_noop_when_adopt_extensions_none(init_reporter: FakeInitReporter) -> None:
    """adopt_extensions=none: process() returns True without writing anything."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files)
    svc = _service(_config(AdoptExtensions.none), fs, config_files)

    ok = svc.process(ext, init_reporter)

    assert ok is True
    assert not fs.is_file(WORKSPACE_ROOT / ".claude" / "agents" / "wf-reviewer.md")


def test_process_noop_when_no_manifest_in_winter_mode(init_reporter: FakeInitReporter) -> None:
    """adopt_extensions=winter: repos without winter-ext.toml are skipped."""
    fs = FakeFilesystem()
    ext_path = WORKSPACE_ROOT / "my-ext"
    fs.directories.add(ext_path)
    agents_dir = ext_path / "agents"
    fs.directories.add(agents_dir)
    fs.files[agents_dir / "reviewer.md"] = _CANONICAL_AGENT
    ext = StandaloneRepository(name="my-ext", path=ext_path)
    svc = _service(_config(AdoptExtensions.winter), fs, {})

    ok = svc.process(ext, init_reporter)

    assert ok is True
    assert not fs.is_file(WORKSPACE_ROOT / ".claude" / "agents" / "my-ext-reviewer.md")


def test_process_handles_missing_agents_dir(init_reporter: FakeInitReporter) -> None:
    """An extension with no agents/ dir processes successfully (nothing to render)."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext_path = WORKSPACE_ROOT / "wf"
    fs.directories.add(ext_path)
    manifest_path = ext_path / "winter-ext.toml"
    fs.files[manifest_path] = ""
    config_files[manifest_path] = {"name": "wf"}
    ext = StandaloneRepository(name="wf", path=ext_path)
    svc = _service(_config(), fs, config_files)

    ok = svc.process(ext, init_reporter)

    assert ok is True
    assert not init_reporter.errors


def test_process_skips_readme_in_agents_dir(init_reporter: FakeInitReporter) -> None:
    """README.md in the agents dir is not rendered as a canonical agent."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(
        fs,
        config_files,
        agent_files={"README.md": "# Agent docs\n", "reviewer.md": _CANONICAL_AGENT},
    )
    svc = _service(_config(), fs, config_files)

    svc.process(ext, init_reporter)

    # reviewer rendered; README not.
    assert fs.is_file(WORKSPACE_ROOT / ".claude" / "agents" / "wf-reviewer.md")
    assert not fs.is_file(WORKSPACE_ROOT / ".claude" / "agents" / "wf-README.md")


# ── Lossy-field warnings ──────────────────────────────────────────────────────


def test_tools_field_triggers_render_warning_for_codex(init_reporter: FakeInitReporter) -> None:
    """An agent with `tools` causes a render warning for Codex (no equivalent field)."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files, agent_files={"helper.md": _CANONICAL_AGENT_WITH_TOOLS})
    svc = _service(_config(), fs, config_files)

    ok = svc.process(ext, init_reporter)

    assert ok is True
    # Codex copy still created despite the lossy field.
    assert fs.is_file(WORKSPACE_ROOT / ".codex" / "agents" / "wf-helper.toml")
    # Warning reported via repo_action.
    warning_actions = [a for a in init_reporter.actions if a[2] == "agent_render_warning"]
    assert any("tools" in a[3] for a in warning_actions)


# ── Symlink replacement (real filesystem regression) ──────────────────────────


def test_sync_file_replaces_symlink_without_corrupting_source(tmp_path: Path) -> None:
    """Regression: a residual symlink from the old symlink-based install must be
    REPLACED by the rendered copy, never written *through*.

    Exercised against the real ``LocalFilesystem`` (not ``FakeFilesystem``)
    because the bug is a real-disk behavior: ``is_file``/``write_text`` follow a
    symlink, so writing to ``.claude/agents/wf-foo.md`` (a symlink into the
    canonical source) overwrote the source agent file and left the symlink in
    place. Caught only by a genuine ``winter ws init`` run.
    """
    from winter_cli.core.internal.local_filesystem import LocalFilesystem

    source = tmp_path / "ext" / "agents" / "developer.md"
    source.parent.mkdir(parents=True)
    source.write_text("ORIGINAL SOURCE CONTENT\n")

    dest = tmp_path / "workspace" / ".claude" / "agents" / "wf-developer.md"
    dest.parent.mkdir(parents=True)
    dest.symlink_to(source)  # legacy symlink-install state

    # _sync_file only touches the filesystem seam; config/manifest_loader/agent_enumerator are unused here.
    svc = ExtensionAgentService(
        config=cast(WorkspaceConfig, None),
        fs=LocalFilesystem(),
        manifest_loader=cast(ExtensionManifestLoader, None),
        agent_enumerator=cast(CanonicalAgentEnumerator, None),
    )
    svc._sync_file(dest, "RENDERED COPY CONTENT\n")

    # The canonical source must be untouched — no write-through.
    assert source.read_text() == "ORIGINAL SOURCE CONTENT\n"
    # The destination is now a real copy, not a symlink.
    assert not dest.is_symlink()
    assert dest.is_file()
    assert dest.read_text() == "RENDERED COPY CONTENT\n"


# ── Bad frontmatter tier: skip-with-warning, not abort ───────────────────────


_AGENT_BAD_TIER = """\
---
name: broken
description: Broken agent with an unknown tier
model: nonexistent-tier
---
Body.
"""


def test_process_skips_agent_with_invalid_tier_and_does_not_abort(init_reporter: FakeInitReporter) -> None:
    """An agent with an unknown model tier is skipped with a warning; init returns True.

    A second valid agent in the same extension still installs — the RepoError
    raised at render time must NOT propagate out of process() and abort init.
    """
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(
        fs,
        config_files,
        agent_files={
            "broken.md": _AGENT_BAD_TIER,
            "reviewer.md": _CANONICAL_AGENT,
        },
    )
    svc = _service(_config(), fs, config_files)

    ok = svc.process(ext, init_reporter)

    # process() must not abort — return True.
    assert ok is True

    # The good agent still renders for all three vendors.
    assert fs.is_file(WORKSPACE_ROOT / ".claude" / "agents" / "wf-reviewer.md")
    assert fs.is_file(WORKSPACE_ROOT / ".codex" / "agents" / "wf-reviewer.toml")
    assert fs.is_file(WORKSPACE_ROOT / ".opencode" / "agent" / "wf-reviewer.md")

    # The broken agent produces no output files.
    assert not fs.is_file(WORKSPACE_ROOT / ".claude" / "agents" / "wf-broken.md")
    assert not fs.is_file(WORKSPACE_ROOT / ".codex" / "agents" / "wf-broken.toml")
    assert not fs.is_file(WORKSPACE_ROOT / ".opencode" / "agent" / "wf-broken.md")

    # A render warning was surfaced for the broken agent.
    render_warnings = [a for a in init_reporter.actions if a[2] == "agent_render_warning"]
    assert render_warnings, "expected agent_render_warning for the bad-tier agent"
    combined = " ".join(a[3] for a in render_warnings)
    assert "nonexistent-tier" in combined


_AGENT_PARTIAL_TIER = """\
---
name: partial
description: Agent using a tier that only maps the claude vendor
model: claude-only
---
Body.
"""


def test_process_does_not_partially_write_when_a_later_vendor_fails(init_reporter: FakeInitReporter) -> None:
    """A render failure on one vendor must not leave the agent installed for an earlier vendor.

    `CodeAgentVendor` renders claude before codex/opencode. A custom tier that
    maps only `claude` succeeds on the first vendor and fails on the second —
    regression coverage for the bug where the claude copy was written before
    the codex failure aborted the per-agent loop, leaving a half-installed
    agent (renders are computed for every vendor before anything is written).
    """
    from winter_cli.config.models import ModelTiersConfig

    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files, agent_files={"partial.md": _AGENT_PARTIAL_TIER})
    config = _config().model_copy(
        update={"model_tiers": ModelTiersConfig(tiers={"claude-only": {"claude": "claude-opus-4-20250514"}})}
    )
    svc = _service(config, fs, config_files)

    ok = svc.process(ext, init_reporter)

    assert ok is True
    # No vendor copy exists for the agent — not even claude, whose render
    # succeeded before codex's failure was discovered.
    assert not fs.is_file(WORKSPACE_ROOT / ".claude" / "agents" / "wf-partial.md")
    assert not fs.is_file(WORKSPACE_ROOT / ".codex" / "agents" / "wf-partial.toml")
    assert not fs.is_file(WORKSPACE_ROOT / ".opencode" / "agent" / "wf-partial.md")

    render_warnings = [a for a in init_reporter.actions if a[2] == "agent_render_warning"]
    assert render_warnings, "expected agent_render_warning for the partially-mapped tier"
    combined = " ".join(a[3] for a in render_warnings)
    assert "claude-only" in combined
