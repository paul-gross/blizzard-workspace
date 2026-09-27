"""Unit tests for AgentMatrixService: the `winter agents` assembly service.

Covers every model and effort `effective_layer` value, a tier-string agent
override, a custom tier label, a local-over-shared override, an override that
targets no installed agent, a stale on-disk copy, a missing copy, and an
unresolvable cell.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import cast

import pytest

from tests.conftest import FakeConfigFileReader, FakeFilesystem, make_workspace_config
from winter_cli.config.models import (
    AdoptExtensions,
    AgentModelOverrideProfile,
    AgentModelOverridesConfig,
    CodeAgentVendor,
    ModelTiersConfig,
    StandaloneRepositoryConfig,
    WorkspaceConfig,
)
from winter_cli.config.workspace import WorkspaceConfigService
from winter_cli.core.filesystem import IFilesystemReader
from winter_cli.modules.agents.agent_matrix_service import AgentMatrixService
from winter_cli.modules.agents.models import AgentMatrixEntry
from winter_cli.modules.doctor.agent_probe_service import AgentProbeService
from winter_cli.modules.workspace.agent_transform.agent_copy_inspector import AgentCopyInspector, CopyStatus
from winter_cli.modules.workspace.agent_transform.agent_enumerator import CanonicalAgentEnumerator
from winter_cli.modules.workspace.agent_transform.model_tiers import build_effective_tier_table
from winter_cli.modules.workspace.agent_transform.models import ConfigSource, EffortLayer, ModelLayer
from winter_cli.modules.workspace.agent_transform.registry import PARSER, RENDERERS
from winter_cli.modules.workspace.extension_manifest import ExtensionManifestLoader
from winter_cli.modules.workspace.models import StandaloneRepository
from winter_cli.modules.workspace.repository_factory import RepositoryFactory

WORKSPACE_ROOT = Path("/ws")
EXT_NAME = "wf"
EXT_PATH = WORKSPACE_ROOT / EXT_NAME
CLAUDE_AGENTS = WORKSPACE_ROOT / ".claude" / "agents"


class _FakeWorkspaceConfigService(WorkspaceConfigService):
    """Stands in for `WorkspaceConfigService` — a fixed config, no I/O.

    Subclasses (rather than duck-types) `WorkspaceConfigService` since
    `AgentMatrixService` types the dependency concretely, matching
    `DashboardSnapshotService`'s own constructor.
    """

    def __init__(self, config: WorkspaceConfig) -> None:  # pylint: disable=super-init-not-called
        self._config = config

    def load(self) -> WorkspaceConfig:
        return self._config


def _seed_agent(fs: FakeFilesystem, config_files: dict[Path, dict], content: str, name: str = "reviewer") -> None:
    """Plant a single-agent extension in the fake filesystem."""
    fs.directories.add(EXT_PATH)
    for parent in EXT_PATH.parents:
        fs.directories.add(parent)
    manifest_path = EXT_PATH / "winter-ext.toml"
    fs.files[manifest_path] = ""
    config_files[manifest_path] = {"name": EXT_NAME}
    agents_dir = EXT_PATH / "agents"
    fs.directories.add(agents_dir)
    fs.files[agents_dir / f"{name}.md"] = content


def _service(
    fs: FakeFilesystem,
    config_files: dict[Path, dict],
    *,
    model_tiers: ModelTiersConfig | None = None,
    agent_model_overrides: AgentModelOverridesConfig | None = None,
    adopt_extensions: AdoptExtensions = AdoptExtensions.winter,
) -> AgentMatrixService:
    loader = ExtensionManifestLoader(config_file_reader=FakeConfigFileReader(config_files))
    fs_reader = cast(IFilesystemReader, fs)
    enumerator = CanonicalAgentEnumerator(fs=fs_reader, manifest_loader=loader)
    inspector = AgentCopyInspector(fs=fs_reader, manifest_loader=loader, agent_enumerator=enumerator)
    config = make_workspace_config(
        workspace_root=WORKSPACE_ROOT,
        standalone_repos=[StandaloneRepositoryConfig(name=EXT_NAME)],
        model_tiers=model_tiers or ModelTiersConfig(),
        agent_model_overrides=agent_model_overrides or AgentModelOverridesConfig(),
        adopt_extensions=adopt_extensions,
    )
    return AgentMatrixService(
        workspace_config_svc=_FakeWorkspaceConfigService(config),
        agent_copy_inspector=inspector,
        repo_factory_for=lambda cfg: RepositoryFactory(config=cfg, fs=fs_reader),
    )


def _claude_entry(entries: list[AgentMatrixEntry], agent_name: str = "reviewer") -> AgentMatrixEntry:
    return next(e for e in entries if e.harness == "claude" and e.agent == agent_name)


_BASE_AGENT_MD = """\
---
name: reviewer
description: Reviews code changes
model: sonnet
---
You are a code reviewer.
"""


# ── Model effective_layer: code_default ──────────────────────────────────────


def test_model_layer_code_default() -> None:
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    matrix = _service(fs, config_files).build()

    entry = _claude_entry(matrix.agents)
    assert entry.resolution is not None
    assert entry.resolution.model.effective == "sonnet"
    assert entry.resolution.model.effective_layer is ModelLayer.code_default


# ── Model effective_layer: tier_override + custom tier label ────────────────


def test_model_layer_tier_override_on_builtin_label() -> None:
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    model_tiers = ModelTiersConfig(tiers={"sonnet": {"claude": "claude-remapped"}})
    matrix = _service(fs, config_files, model_tiers=model_tiers).build()

    entry = _claude_entry(matrix.agents)
    assert entry.resolution is not None
    assert entry.resolution.model.effective == "claude-remapped"
    assert entry.resolution.model.effective_layer is ModelLayer.tier_override
    assert entry.resolution.model.tier_override is not None
    assert entry.resolution.model.tier_override.source is ConfigSource.config_toml


def test_model_layer_custom_tier_label() -> None:
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    agent_md = _BASE_AGENT_MD.replace("model: sonnet", "model: big-thinker")
    _seed_agent(fs, config_files, agent_md)
    model_tiers = ModelTiersConfig(tiers={"big-thinker": {"claude": "claude-opus-5-5"}})
    matrix = _service(fs, config_files, model_tiers=model_tiers).build()

    entry = _claude_entry(matrix.agents)
    assert entry.resolution is not None
    assert entry.resolution.model.declared_tier == "big-thinker"
    assert entry.resolution.model.code_default is None
    assert entry.resolution.model.effective == "claude-opus-5-5"
    assert entry.resolution.model.effective_layer is ModelLayer.tier_override

    # The tiers section also carries the custom label, all-null for a vendor
    # the workspace config leaves unmapped.
    codex_entries = [e for e in matrix.tiers if e.label == "big-thinker" and e.harness == "codex"]
    assert len(codex_entries) == 1
    assert codex_entries[0].cell is None


# ── Model effective_layer: harness_block ─────────────────────────────────────


def test_model_layer_harness_block() -> None:
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    agent_md = """\
---
name: reviewer
description: Reviews code changes
model: sonnet
claude:
  model: claude-native-override
---
You are a code reviewer.
"""
    _seed_agent(fs, config_files, agent_md)
    matrix = _service(fs, config_files).build()

    entry = _claude_entry(matrix.agents)
    assert entry.resolution is not None
    assert entry.resolution.model.harness_block == "claude-native-override"
    assert entry.resolution.model.effective == "claude-native-override"
    assert entry.resolution.model.effective_layer is ModelLayer.harness_block


# ── Model effective_layer: agent_override (+ tier-string form) ──────────────


def test_model_layer_agent_override_per_vendor() -> None:
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    overrides = AgentModelOverridesConfig(overrides={"reviewer": {"claude": "claude-opus-5-5"}})
    matrix = _service(fs, config_files, agent_model_overrides=overrides).build()

    entry = _claude_entry(matrix.agents)
    assert entry.resolution is not None
    assert entry.resolution.model.effective == "claude-opus-5-5"
    assert entry.resolution.model.effective_layer is ModelLayer.agent_override
    assert entry.resolution.model.agent_override is not None
    assert entry.resolution.model.agent_override.tier is None


def test_model_layer_agent_override_tier_string() -> None:
    """A bare-string `[agent_model_overrides]` entry: applies to every vendor,
    resolving through the tier table, and records the label as `tier`."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    overrides = AgentModelOverridesConfig(overrides={"reviewer": "haiku"})
    matrix = _service(fs, config_files, agent_model_overrides=overrides).build()

    entry = _claude_entry(matrix.agents)
    assert entry.resolution is not None
    assert entry.resolution.model.effective == "haiku"
    assert entry.resolution.model.effective_layer is ModelLayer.agent_override
    assert entry.resolution.model.agent_override is not None
    assert entry.resolution.model.agent_override.tier == "haiku"

    # And the agent_overrides section carries the same tier label + resolved
    # per-harness breakdown, independent of the agent's own frontmatter.
    (override_entry,) = matrix.agent_overrides
    assert override_entry.tier == "haiku"
    assert override_entry.harnesses["claude"] is not None
    assert override_entry.harnesses["claude"].model == "haiku"
    assert override_entry.harnesses["codex"] is not None
    assert override_entry.harnesses["codex"].model == "gpt-6-luna"


# ── Effort effective_layer: harness_block / agent_override / inherited ──────


def test_effort_layer_harness_block() -> None:
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    agent_md = """\
---
name: reviewer
description: Reviews code changes
model: sonnet
claude:
  effort: high
---
You are a code reviewer.
"""
    _seed_agent(fs, config_files, agent_md)
    matrix = _service(fs, config_files).build()

    entry = _claude_entry(matrix.agents)
    assert entry.resolution is not None
    assert entry.resolution.effort.effective == "high"
    assert entry.resolution.effort.effective_layer is EffortLayer.harness_block


def test_effort_layer_agent_override() -> None:
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    overrides = AgentModelOverridesConfig(
        overrides={"reviewer": {"claude": AgentModelOverrideProfile(model="claude-opus-5-5", effort="max")}}
    )
    matrix = _service(fs, config_files, agent_model_overrides=overrides).build()

    entry = _claude_entry(matrix.agents)
    assert entry.resolution is not None
    assert entry.resolution.effort.effective == "max"
    assert entry.resolution.effort.effective_layer is EffortLayer.agent_override
    assert entry.resolution.effort.agent_override is not None
    assert entry.resolution.effort.agent_override.source is ConfigSource.config_toml


def test_effort_layer_inherited() -> None:
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    matrix = _service(fs, config_files).build()

    entry = _claude_entry(matrix.agents)
    assert entry.resolution is not None
    assert entry.resolution.effort.effective is None
    assert entry.resolution.effort.effective_layer is EffortLayer.inherited


# ── local-over-shared override source ────────────────────────────────────────


def test_local_over_shared_override_source() -> None:
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    overrides = AgentModelOverridesConfig(
        overrides={"reviewer": "haiku"},
        override_sources={"reviewer": ConfigSource.config_local_toml},
    )
    matrix = _service(fs, config_files, agent_model_overrides=overrides).build()

    entry = _claude_entry(matrix.agents)
    assert entry.resolution is not None
    assert entry.resolution.model.agent_override is not None
    assert entry.resolution.model.agent_override.source is ConfigSource.config_local_toml

    (override_entry,) = matrix.agent_overrides
    assert override_entry.source is ConfigSource.config_local_toml


# ── An override targeting no installed agent ─────────────────────────────────


def test_override_targeting_no_installed_agent_is_flagged() -> None:
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    overrides = AgentModelOverridesConfig(overrides={"ghost-agent": "haiku"})
    matrix = _service(fs, config_files, agent_model_overrides=overrides).build()

    (override_entry,) = matrix.agent_overrides
    assert override_entry.agent == "ghost-agent"
    assert override_entry.matches_installed is False
    # Still fully resolved — matching is independent of the breakdown.
    assert override_entry.harnesses["claude"] is not None
    assert override_entry.harnesses["claude"].model == "haiku"


def test_override_matching_installed_agent_is_flagged_true() -> None:
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    overrides = AgentModelOverridesConfig(overrides={"reviewer": "haiku"})
    matrix = _service(fs, config_files, agent_model_overrides=overrides).build()

    (override_entry,) = matrix.agent_overrides
    assert override_entry.matches_installed is True


# ── on_disk: stale / missing / in_sync ───────────────────────────────────────


def test_on_disk_stale_copy() -> None:
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    fs.files[CLAUDE_AGENTS / "wf-reviewer.md"] = "not the rendered content"
    matrix = _service(fs, config_files).build()

    entry = _claude_entry(matrix.agents)
    assert entry.on_disk is CopyStatus.stale


def test_on_disk_missing_copy() -> None:
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    matrix = _service(fs, config_files).build()

    entry = _claude_entry(matrix.agents)
    assert entry.on_disk is CopyStatus.missing


def test_on_disk_in_sync_copy() -> None:
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    loader = ExtensionManifestLoader(config_file_reader=FakeConfigFileReader(config_files))
    fs_reader = cast(IFilesystemReader, fs)
    enumerator = CanonicalAgentEnumerator(fs=fs_reader, manifest_loader=loader)
    inspector = AgentCopyInspector(fs=fs_reader, manifest_loader=loader, agent_enumerator=enumerator)
    tier_table = build_effective_tier_table({})
    (entry,) = list(
        inspector.inspect(
            inspector.known_agents([StandaloneRepository(name=EXT_NAME, path=EXT_PATH)], mode=AdoptExtensions.winter),
            CodeAgentVendor.ClaudeCode,
            tier_table=tier_table,
            agent_model_overrides=AgentModelOverridesConfig(),
            agents_dir=CLAUDE_AGENTS,
        )
    )
    assert entry.resolution is not None
    agent = PARSER.parse(_BASE_AGENT_MD, default_name="reviewer")
    renderer = RENDERERS[CodeAgentVendor.ClaudeCode.agent_format]
    fs.files[CLAUDE_AGENTS / "wf-reviewer.md"] = renderer.render(
        agent, warn=lambda *_: None, resolution=entry.resolution
    ).text

    matrix = _service(fs, config_files).build()
    result = _claude_entry(matrix.agents)
    assert result.on_disk is CopyStatus.in_sync


# ── Unresolvable cell ─────────────────────────────────────────────────────────


def test_unresolvable_cell_is_listed_with_error() -> None:
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    agent_md = _BASE_AGENT_MD.replace("model: sonnet", "model: no-such-tier")
    _seed_agent(fs, config_files, agent_md)
    matrix = _service(fs, config_files).build()

    entry = _claude_entry(matrix.agents)
    assert entry.resolution is None
    assert entry.error is not None
    assert "no-such-tier" in entry.error
    assert entry.on_disk is None


# ── Entry identity ────────────────────────────────────────────────────────────


def test_agent_entries_carry_extension_installed_name_and_every_harness() -> None:
    """`extension` is the repo name and `installed_name` carries the manifest prefix — distinct here."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    config_files[EXT_PATH / "winter-ext.toml"] = {"name": EXT_NAME, "prefix": "px"}
    matrix = _service(fs, config_files).build()

    assert sorted(e.harness for e in matrix.agents) == ["claude", "codex", "opencode"]
    for entry in matrix.agents:
        assert entry.agent == "reviewer"
        assert entry.extension == EXT_NAME
        assert entry.installed_name == "px-reviewer"


# ── adopt_extensions = "none" ────────────────────────────────────────────────


def test_adopt_extensions_none_lists_no_agents() -> None:
    """Nothing is installed under `none`, so no agent is listed (never as a `missing` copy)."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    overrides = AgentModelOverridesConfig(overrides={"reviewer": "haiku"})
    matrix = _service(fs, config_files, agent_model_overrides=overrides, adopt_extensions=AdoptExtensions.none).build()

    assert matrix.agents == []
    (override_entry,) = matrix.agent_overrides
    assert override_entry.matches_installed is False
    assert matrix.tiers


# ── One resolution path ──────────────────────────────────────────────────────


def test_agent_overrides_breakdown_equals_the_agent_resolution_override_layer() -> None:
    """The override table and the agent rows read the same resolver output, per harness."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    overrides = AgentModelOverridesConfig(
        overrides={
            "reviewer": {
                "claude": AgentModelOverrideProfile(model="claude-opus-5-5", effort="max"),
                "codex": AgentModelOverrideProfile(model=None, effort="low"),
                "opencode": "openai/gpt-6-luna",
            }
        }
    )
    matrix = _service(fs, config_files, agent_model_overrides=overrides).build()

    (override_entry,) = matrix.agent_overrides
    for entry in matrix.agents:
        assert entry.resolution is not None
        model_layer = entry.resolution.model.agent_override
        effort_layer = entry.resolution.effort.agent_override
        breakdown = override_entry.harnesses[entry.harness]
        assert breakdown is not None
        assert breakdown.model == (model_layer.value if model_layer is not None else None)
        assert breakdown.effort == (effort_layer.value if effort_layer is not None else None)
    codex = override_entry.harnesses["codex"]
    assert codex is not None
    assert (codex.model, codex.effort) == (None, "low")


def test_unresolvable_override_cell_carries_its_error_and_others_still_resolve() -> None:
    """A bare-string override whose tier has no mapping for one harness carries that harness's error, not `null`."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    model_tiers = ModelTiersConfig(tiers={"claude-only": {"claude": "claude-only-id"}})
    overrides = AgentModelOverridesConfig(overrides={"reviewer": "claude-only"})
    matrix = _service(fs, config_files, model_tiers=model_tiers, agent_model_overrides=overrides).build()

    (override_entry,) = matrix.agent_overrides
    claude = override_entry.harnesses["claude"]
    assert claude is not None
    assert claude.model == "claude-only-id"
    assert claude.error is None
    for harness in ("codex", "opencode"):
        cell = override_entry.harnesses[harness]
        assert cell is not None
        assert cell.model is None
        assert cell.effort is None
        assert cell.error is not None
        assert "claude-only" in cell.error
    codex_row = next(e for e in matrix.agents if e.harness == "codex")
    assert codex_row.resolution is None
    assert codex_row.error is not None


def test_override_harness_not_listed_by_the_entry_stays_null() -> None:
    """A per-vendor dict form that omits a harness leaves it `None` — not an error."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    overrides = AgentModelOverridesConfig(overrides={"reviewer": {"claude": "claude-opus-5-5"}})
    matrix = _service(fs, config_files, agent_model_overrides=overrides).build()

    (override_entry,) = matrix.agent_overrides
    assert override_entry.harnesses["claude"] is not None
    assert override_entry.harnesses["claude"].error is None
    assert override_entry.harnesses["codex"] is None
    assert override_entry.harnesses["opencode"] is None


# ── Agreement with `winter doctor` ────────────────────────────────────────────


def test_doctor_and_agents_agree_on_which_overrides_match_no_installed_agent() -> None:
    """Both read `AgentCopyInspector.known_agents`, so an unreadable source is skipped by both alike."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    # An agent source that fails to parse is not installed, for either consumer.
    fs.files[EXT_PATH / "agents" / "broken.md"] = "no frontmatter at all\n"
    overrides = AgentModelOverridesConfig(overrides={"reviewer": "haiku", "broken": "haiku", "ghost": "haiku"})

    matrix = _service(fs, config_files, agent_model_overrides=overrides).build()

    loader = ExtensionManifestLoader(config_file_reader=FakeConfigFileReader(config_files))
    fs_reader = cast(IFilesystemReader, fs)
    enumerator = CanonicalAgentEnumerator(fs=fs_reader, manifest_loader=loader)
    probe = AgentProbeService(
        config=make_workspace_config(workspace_root=WORKSPACE_ROOT, agent_model_overrides=overrides),
        fs=fs_reader,
        manifest_loader=loader,
        agent_copy_inspector=AgentCopyInspector(fs=fs_reader, manifest_loader=loader, agent_enumerator=enumerator),
    )
    (targets,) = [
        r for r in probe.run([StandaloneRepository(name=EXT_NAME, path=EXT_PATH)]) if r.name.endswith("targets")
    ]

    assert sorted(matrix.unmatched_override_agents()) == ["broken", "ghost"]
    assert sorted(re.findall(r"unknown agent '([^']+)'", targets.message)) == ["broken", "ghost"]


def _unreadable_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "broken.md" in r.getMessage()]


def test_build_logs_an_unparseable_agent_file_once(caplog: pytest.LogCaptureFixture) -> None:
    """One walk per build: the bad file is warned about once, not once per harness."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    fs.files[EXT_PATH / "agents" / "broken.md"] = "no frontmatter at all\n"
    overrides = AgentModelOverridesConfig(overrides={"reviewer": "haiku"})

    with caplog.at_level(logging.WARNING):
        matrix = _service(fs, config_files, agent_model_overrides=overrides).build()

    assert len(_unreadable_warnings(caplog)) == 1
    assert {e.harness for e in matrix.agents} == {v.vendor_label for v in CodeAgentVendor}


def test_doctor_agent_probes_log_an_unparseable_agent_file_once(caplog: pytest.LogCaptureFixture) -> None:
    """Every doctor agent check reads one walk, so the bad file is warned about once per run."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    fs.files[EXT_PATH / "agents" / "broken.md"] = "no frontmatter at all\n"
    overrides = AgentModelOverridesConfig(overrides={"reviewer": "haiku"})
    loader = ExtensionManifestLoader(config_file_reader=FakeConfigFileReader(config_files))
    fs_reader = cast(IFilesystemReader, fs)
    enumerator = CanonicalAgentEnumerator(fs=fs_reader, manifest_loader=loader)
    probe = AgentProbeService(
        config=make_workspace_config(workspace_root=WORKSPACE_ROOT, agent_model_overrides=overrides),
        fs=fs_reader,
        manifest_loader=loader,
        agent_copy_inspector=AgentCopyInspector(fs=fs_reader, manifest_loader=loader, agent_enumerator=enumerator),
    )

    with caplog.at_level(logging.WARNING):
        results = probe.run([StandaloneRepository(name=EXT_NAME, path=EXT_PATH)])

    assert len(_unreadable_warnings(caplog)) == 1
    assert any(r.name.endswith("targets") for r in results)
    assert any(r.name == "agent names: uniqueness" for r in results)


def test_build_resolves_repos_through_the_injected_factory_per_build() -> None:
    """Each build hands its freshly loaded config to the injected factory."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    _seed_agent(fs, config_files, _BASE_AGENT_MD)
    loader = ExtensionManifestLoader(config_file_reader=FakeConfigFileReader(config_files))
    fs_reader = cast(IFilesystemReader, fs)
    enumerator = CanonicalAgentEnumerator(fs=fs_reader, manifest_loader=loader)
    config = make_workspace_config(
        workspace_root=WORKSPACE_ROOT, standalone_repos=[StandaloneRepositoryConfig(name=EXT_NAME)]
    )
    seen: list[WorkspaceConfig] = []

    def factory_for(cfg: WorkspaceConfig) -> RepositoryFactory:
        seen.append(cfg)
        return RepositoryFactory(config=cfg, fs=fs_reader)

    svc = AgentMatrixService(
        workspace_config_svc=_FakeWorkspaceConfigService(config),
        agent_copy_inspector=AgentCopyInspector(fs=fs_reader, manifest_loader=loader, agent_enumerator=enumerator),
        repo_factory_for=factory_for,
    )

    svc.build()
    svc.build()

    assert seen == [config, config]
