"""Unit tests for AgentCopyInspector: the shared canonical-agent-to-installed-copy walk.

Covers the three ``CopyStatus`` outcomes (``in_sync``, ``stale``, ``missing``),
an unresolvable agent (``error`` set, no bytes/status), a canonical file that
fails to parse (logged and skipped), and that ``known_prefixes`` reports an
extension's prefix even when it has no agents directory.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import pytest

from tests.conftest import FakeConfigFileReader, FakeFilesystem
from winter_cli.config.models import AdoptExtensions, AgentModelOverridesConfig, CodeAgentVendor
from winter_cli.core.filesystem import IFilesystemReader
from winter_cli.modules.workspace.agent_transform.agent_copy_inspector import AgentCopyInspector, CopyStatus
from winter_cli.modules.workspace.agent_transform.agent_enumerator import CanonicalAgentEnumerator
from winter_cli.modules.workspace.agent_transform.model_tiers import build_effective_tier_table
from winter_cli.modules.workspace.agent_transform.models import AgentResolution
from winter_cli.modules.workspace.agent_transform.registry import PARSER, RENDERERS
from winter_cli.modules.workspace.extension_manifest import ExtensionManifestLoader
from winter_cli.modules.workspace.models import StandaloneRepository

WORKSPACE_ROOT = Path("/ws")
CLAUDE_AGENTS = WORKSPACE_ROOT / ".claude" / "agents"

_REVIEWER_AGENT_MD = """\
---
name: reviewer
description: Reviews code changes
model: sonnet
---
You are a code reviewer.
"""

_NO_OVERRIDES = AgentModelOverridesConfig()
_TIER_TABLE = build_effective_tier_table({})


def _inspector(fs: FakeFilesystem, config_files: dict[Path, dict] | None = None) -> AgentCopyInspector:
    loader = ExtensionManifestLoader(config_file_reader=FakeConfigFileReader(config_files or {}))
    return AgentCopyInspector(
        fs=cast(IFilesystemReader, fs),
        manifest_loader=loader,
        agent_enumerator=CanonicalAgentEnumerator(fs=cast(IFilesystemReader, fs), manifest_loader=loader),
    )


def _seed_extension(
    fs: FakeFilesystem,
    config_files: dict[Path, dict],
    agent_content: str | None = _REVIEWER_AGENT_MD,
    name: str = "wf",
) -> StandaloneRepository:
    """Plant an extension in the fake filesystem, optionally without an agents dir."""
    ext_path = WORKSPACE_ROOT / name
    fs.directories.add(ext_path)
    for parent in ext_path.parents:
        fs.directories.add(parent)

    manifest_path = ext_path / "winter-ext.toml"
    fs.files[manifest_path] = ""
    config_files[manifest_path] = {"name": name}

    if agent_content is not None:
        agents_dir = ext_path / "agents"
        fs.directories.add(agents_dir)
        fs.files[agents_dir / "reviewer.md"] = agent_content

    return StandaloneRepository(name=name, path=ext_path)


def _render_claude(content: str, resolution: AgentResolution | None) -> bytes:
    """The bytes the installer writes for ``content`` on claude, rendered with the shared renderer."""
    assert resolution is not None
    agent = PARSER.parse(content, default_name="reviewer")
    renderer = RENDERERS[CodeAgentVendor.ClaudeCode.agent_format]
    return renderer.render(agent, warn=lambda *_: None, resolution=resolution).text.encode("utf-8")


def _expected_claude_bytes(fs: FakeFilesystem, config_files: dict[Path, dict], ext: StandaloneRepository) -> bytes:
    """Render the reviewer agent for claude via the inspector itself, to avoid duplicating renderer logic."""
    inspector = _inspector(fs, config_files)
    entries = list(
        inspector.inspect(
            inspector.known_agents([ext], mode=AdoptExtensions.winter),
            CodeAgentVendor.ClaudeCode,
            tier_table=_TIER_TABLE,
            agent_model_overrides=_NO_OVERRIDES,
            agents_dir=CLAUDE_AGENTS,
        )
    )
    assert len(entries) == 1
    return _render_claude(_REVIEWER_AGENT_MD, entries[0].resolution)


def test_inspect_reports_in_sync_when_bytes_match() -> None:
    """An installed copy whose bytes match the render is in_sync."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files)
    expected_bytes = _expected_claude_bytes(fs, config_files, ext)
    fs.files[CLAUDE_AGENTS / "wf-reviewer.md"] = expected_bytes.decode("utf-8")

    inspector = _inspector(fs, config_files)
    entries = list(
        inspector.inspect(
            inspector.known_agents([ext], mode=AdoptExtensions.winter),
            CodeAgentVendor.ClaudeCode,
            tier_table=_TIER_TABLE,
            agent_model_overrides=_NO_OVERRIDES,
            agents_dir=CLAUDE_AGENTS,
        )
    )

    assert len(entries) == 1
    entry = entries[0]
    assert entry.error is None
    assert entry.copy_status is CopyStatus.in_sync
    assert entry.resolution is not None
    assert entry.extension == "wf"
    assert entry.agent_name == "reviewer"
    assert entry.installed_path == CLAUDE_AGENTS / "wf-reviewer.md"


def test_inspect_reports_stale_when_bytes_differ() -> None:
    """An installed copy whose bytes differ from the current render is stale."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files)
    fs.directories.add(CLAUDE_AGENTS)
    fs.files[CLAUDE_AGENTS / "wf-reviewer.md"] = "stale content from a previous render\n"

    inspector = _inspector(fs, config_files)
    entries = list(
        inspector.inspect(
            inspector.known_agents([ext], mode=AdoptExtensions.winter),
            CodeAgentVendor.ClaudeCode,
            tier_table=_TIER_TABLE,
            agent_model_overrides=_NO_OVERRIDES,
            agents_dir=CLAUDE_AGENTS,
        )
    )

    assert len(entries) == 1
    assert entries[0].copy_status is CopyStatus.stale


def test_inspect_reports_missing_when_no_copy_on_disk() -> None:
    """No installed copy at all → missing."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files)

    inspector = _inspector(fs, config_files)
    entries = list(
        inspector.inspect(
            inspector.known_agents([ext], mode=AdoptExtensions.winter),
            CodeAgentVendor.ClaudeCode,
            tier_table=_TIER_TABLE,
            agent_model_overrides=_NO_OVERRIDES,
            agents_dir=CLAUDE_AGENTS,
        )
    )

    assert len(entries) == 1
    assert entries[0].copy_status is CopyStatus.missing


def test_inspect_yields_error_entry_for_unresolvable_tier() -> None:
    """An agent whose tier can't be resolved for this vendor yields an error entry, no bytes, no status."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(
        fs,
        config_files,
        agent_content="---\nname: reviewer\ndescription: d\nmodel: typo-tier\n---\n\nBody.\n",
    )

    inspector = _inspector(fs, config_files)
    entries = list(
        inspector.inspect(
            inspector.known_agents([ext], mode=AdoptExtensions.winter),
            CodeAgentVendor.ClaudeCode,
            tier_table=_TIER_TABLE,
            agent_model_overrides=_NO_OVERRIDES,
            agents_dir=CLAUDE_AGENTS,
        )
    )

    assert len(entries) == 1
    entry = entries[0]
    assert entry.resolution is None
    assert entry.copy_status is None
    assert entry.error is not None
    assert "typo-tier" in str(entry.error)


def test_known_prefixes_includes_extension_with_no_agents_dir() -> None:
    """An extension whose manifest loads but has no agents directory still reports its prefix."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files, agent_content=None, name="no-agents-ext")

    inspector = _inspector(fs, config_files)
    prefixes = inspector.known_prefixes([ext], mode=AdoptExtensions.winter)

    assert prefixes == {"no-agents-ext"}

    # And inspect() yields nothing for it, since there is no agents directory to walk.
    entries = list(
        inspector.inspect(
            inspector.known_agents([ext], mode=AdoptExtensions.winter),
            CodeAgentVendor.ClaudeCode,
            tier_table=_TIER_TABLE,
            agent_model_overrides=_NO_OVERRIDES,
            agents_dir=CLAUDE_AGENTS,
        )
    )
    assert entries == []


def test_inspect_logs_and_skips_unparseable_agent(caplog: pytest.LogCaptureFixture) -> None:
    """A canonical file that fails to parse is logged at WARNING, naming the extension and file, and yields nothing."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files, agent_content="no frontmatter at all\n")

    inspector = _inspector(fs, config_files)
    with caplog.at_level(logging.WARNING, logger="winter_cli.modules.workspace.agent_transform.agent_copy_inspector"):
        entries = list(
            inspector.inspect(
                inspector.known_agents([ext], mode=AdoptExtensions.winter),
                CodeAgentVendor.ClaudeCode,
                tier_table=_TIER_TABLE,
                agent_model_overrides=_NO_OVERRIDES,
                agents_dir=CLAUDE_AGENTS,
            )
        )

    assert entries == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "wf" in warnings[0].getMessage()
    assert "reviewer.md" in warnings[0].getMessage()


def test_inspect_logs_and_skips_agent_file_that_raises_oserror_on_read(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unreadable canonical file (OSError) is logged and skipped, and the walk continues past it.

    Mirrors the parse-error case: the installer (`ExtensionAgentService.process`)
    is permissive about a per-agent OSError too, so this collaborator must be as
    well rather than aborting the whole walk.
    """
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files, agent_content=_REVIEWER_AGENT_MD)
    second_agent_md = _REVIEWER_AGENT_MD.replace("name: reviewer", "name: second-agent")
    fs.files[ext.path / "agents" / "second-agent.md"] = second_agent_md

    real_read_text = fs.read_text

    def _flaky_read_text(path: Path) -> str:
        if path.name == "reviewer.md":
            raise OSError("file vanished mid-walk")
        return real_read_text(path)

    fs.read_text = _flaky_read_text  # type: ignore[method-assign]

    inspector = _inspector(fs, config_files)
    with caplog.at_level(logging.WARNING, logger="winter_cli.modules.workspace.agent_transform.agent_copy_inspector"):
        entries = list(
            inspector.inspect(
                inspector.known_agents([ext], mode=AdoptExtensions.winter),
                CodeAgentVendor.ClaudeCode,
                tier_table=_TIER_TABLE,
                agent_model_overrides=_NO_OVERRIDES,
                agents_dir=CLAUDE_AGENTS,
            )
        )

    assert [entry.agent_name for entry in entries] == ["second-agent"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "reviewer.md" in warnings[0].getMessage()
    assert "file vanished mid-walk" in warnings[0].getMessage()
