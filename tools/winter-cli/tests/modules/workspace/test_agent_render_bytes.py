"""Characterization tests: rendered agent bytes are pinned literal-for-literal.

Drives ``ExtensionAgentService.process`` (the same seam as
``test_extension_agent_service.py``) through the ``FakeFilesystem`` fixtures and
asserts that the bytes written for each vendor (Claude Code, Codex, OpenCode)
equal a literal expected string — not a structural/keys-only check like
``test_agent_transform.py`` and ``test_agent_model_overrides.py`` use.

It drives only ``ExtensionAgentService.process`` plus its constructor and the
config/model types — never a renderer directly — so a change to how model and
effort are resolved or handed to the renderers must keep this file green
without editing it.

Two fixture choices make precedence and key position observable: the
``[agent_model_overrides]`` model cases run on an agent that also carries a
per-harness ``model:`` key, so the override must beat it; and each native
effort key is followed by another override-block key, so an in-place
overwrite is distinguishable from a remove-and-append.

Two families of cases, one per resolution axis:

- **model** — one case per layer that can supply the resolved model id: the
  built-in code default, a workspace ``[model_tiers]`` remap, the agent's own
  per-harness ``model:`` key, a per-vendor ``[agent_model_overrides]`` entry,
  and a bare tier-label override (one built-in label, one custom label).
- **effort** — the ``effort``/``model_reasoning_effort``/``reasoningEffort``
  key each vendor supports natively in its override block, and how a
  workspace ``[agent_model_overrides]`` profile's ``effort`` field interacts
  with it (overwrites it, adds it fresh, or leaves it alone when the profile
  only carries a model).
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from tests.conftest import FakeConfigFileReader, FakeFilesystem, FakeInitReporter
from winter_cli.config.models import (
    AdoptExtensions,
    AgentModelOverridesConfig,
    ModelTiersConfig,
    WorkspaceConfig,
)
from winter_cli.modules.workspace.agent_install import ExtensionAgentService
from winter_cli.modules.workspace.agent_transform.agent_enumerator import CanonicalAgentEnumerator
from winter_cli.modules.workspace.agent_transform.models import AgentModelOverrideProfile
from winter_cli.modules.workspace.extension_manifest import ExtensionManifestLoader
from winter_cli.modules.workspace.models import StandaloneRepository

WORKSPACE_ROOT = Path("/ws")
CLAUDE_DEST = WORKSPACE_ROOT / ".claude" / "agents" / "wf-reviewer.md"
CODEX_DEST = WORKSPACE_ROOT / ".codex" / "agents" / "wf-reviewer.toml"
OPENCODE_DEST = WORKSPACE_ROOT / ".opencode" / "agent" / "wf-reviewer.md"


def _config() -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        service_prefix="t",
        main_branch="main",
        adopt_extensions=AdoptExtensions.winter,
    )


def _service(
    config: WorkspaceConfig,
    fs: FakeFilesystem,
    config_files: dict[Path, dict],
) -> ExtensionAgentService:
    loader = ExtensionManifestLoader(config_file_reader=FakeConfigFileReader(config_files))
    return ExtensionAgentService(
        config=config,
        fs=fs,
        manifest_loader=loader,
        agent_enumerator=CanonicalAgentEnumerator(fs=fs, manifest_loader=loader),
    )


def _seed_extension(fs: FakeFilesystem, config_files: dict[Path, dict], agent_md: str) -> StandaloneRepository:
    """Plant one extension named ``wf`` with a single canonical agent ``reviewer.md``."""
    ext_path = WORKSPACE_ROOT / "wf"
    fs.directories.add(ext_path)
    for parent in ext_path.parents:
        fs.directories.add(parent)

    manifest_path = ext_path / "winter-ext.toml"
    fs.files[manifest_path] = ""
    config_files[manifest_path] = {"name": "wf"}

    agents_dir = ext_path / "agents"
    fs.directories.add(agents_dir)
    fs.files[agents_dir / "reviewer.md"] = agent_md

    return StandaloneRepository(name="wf", path=ext_path)


def _render(agent_md: str, config: WorkspaceConfig) -> tuple[bool, FakeFilesystem, FakeInitReporter]:
    """Run ``ExtensionAgentService.process`` over ``agent_md`` and return the fake fs it wrote to."""
    fs = FakeFilesystem()
    config_files: dict[Path, dict] = {}
    ext = _seed_extension(fs, config_files, agent_md)
    svc = _service(config, fs, config_files)
    reporter = FakeInitReporter()
    ok = svc.process(ext, reporter)
    return ok, fs, reporter


# ── Model layer: built-in code default ─────────────────────────────────────

_AGENT_SONNET = dedent("""\
    ---
    name: reviewer
    description: Reviews code changes
    model: sonnet
    ---
    You are a code reviewer.
    """)


def test_model_code_default() -> None:
    """No config and no per-harness override: model resolves via the built-in tier table."""
    ok, fs, reporter = _render(_AGENT_SONNET, _config())

    assert ok is True
    assert not reporter.errors
    assert fs.read_text(CLAUDE_DEST) == (
        "---\nname: reviewer\ndescription: Reviews code changes\nmodel: sonnet\n---\n\nYou are a code reviewer.\n"
    )
    assert fs.read_text(CODEX_DEST) == (
        'name = "reviewer"\n'
        'description = "Reviews code changes"\n'
        'model = "gpt-5.6-terra"\n'
        'developer_instructions = "You are a code reviewer.\\n"\n'
    )
    assert fs.read_text(OPENCODE_DEST) == (
        "---\ndescription: Reviews code changes\nmodel: anthropic/claude-sonnet-5-5\nmode: subagent\n---\n\n"
        "You are a code reviewer.\n"
    )


# ── Model layer: workspace [model_tiers] remap ──────────────────────────────


def test_model_tier_remap() -> None:
    """A [model_tiers] entry for the agent's own tier label wins over the built-in default."""
    config = _config().model_copy(
        update={
            "model_tiers": ModelTiersConfig(
                tiers={
                    "sonnet": {
                        "claude": "claude-remap-sonnet",
                        "codex": "gpt-remap-sonnet",
                        "opencode": "opencode/remap-sonnet",
                    }
                }
            )
        }
    )
    ok, fs, reporter = _render(_AGENT_SONNET, config)

    assert ok is True
    assert not reporter.errors
    assert fs.read_text(CLAUDE_DEST) == (
        "---\nname: reviewer\ndescription: Reviews code changes\nmodel: claude-remap-sonnet\n---\n\n"
        "You are a code reviewer.\n"
    )
    assert fs.read_text(CODEX_DEST) == (
        'name = "reviewer"\n'
        'description = "Reviews code changes"\n'
        'model = "gpt-remap-sonnet"\n'
        'developer_instructions = "You are a code reviewer.\\n"\n'
    )
    assert fs.read_text(OPENCODE_DEST) == (
        "---\ndescription: Reviews code changes\nmodel: opencode/remap-sonnet\nmode: subagent\n---\n\n"
        "You are a code reviewer.\n"
    )


# ── Model layer: agent's own per-harness `model:` key ───────────────────────

_AGENT_HARNESS_MODEL = dedent("""\
    ---
    name: reviewer
    description: Reviews code changes
    model: sonnet
    claude:
      model: claude-harness-model
    codex:
      model: codex-harness-model
    opencode:
      model: opencode-harness-model
    ---
    You are a code reviewer.
    """)


def test_model_harness_key() -> None:
    """A per-harness `model:` key in the agent's own override block beats the tier table."""
    ok, fs, reporter = _render(_AGENT_HARNESS_MODEL, _config())

    assert ok is True
    assert not reporter.errors
    assert fs.read_text(CLAUDE_DEST) == (
        "---\nname: reviewer\ndescription: Reviews code changes\nmodel: claude-harness-model\n---\n\n"
        "You are a code reviewer.\n"
    )
    assert fs.read_text(CODEX_DEST) == (
        'name = "reviewer"\n'
        'description = "Reviews code changes"\n'
        'model = "codex-harness-model"\n'
        'developer_instructions = "You are a code reviewer.\\n"\n'
    )
    assert fs.read_text(OPENCODE_DEST) == (
        "---\ndescription: Reviews code changes\nmodel: opencode-harness-model\nmode: subagent\n---\n\n"
        "You are a code reviewer.\n"
    )


# ── Model layer: per-vendor [agent_model_overrides] entry ───────────────────


def test_model_agent_override_per_vendor() -> None:
    """A per-vendor concrete-id entry in [agent_model_overrides] beats the agent's own harness `model:` key."""
    config = _config().model_copy(
        update={
            "agent_model_overrides": AgentModelOverridesConfig(
                overrides={
                    "reviewer": {
                        "claude": "override-claude-id",
                        "codex": "override-codex-id",
                        "opencode": "override-opencode-id",
                    }
                }
            )
        }
    )
    ok, fs, reporter = _render(_AGENT_HARNESS_MODEL, config)

    assert ok is True
    assert not reporter.errors
    assert fs.read_text(CLAUDE_DEST) == (
        "---\nname: reviewer\ndescription: Reviews code changes\nmodel: override-claude-id\n---\n\n"
        "You are a code reviewer.\n"
    )
    assert fs.read_text(CODEX_DEST) == (
        'name = "reviewer"\n'
        'description = "Reviews code changes"\n'
        'model = "override-codex-id"\n'
        'developer_instructions = "You are a code reviewer.\\n"\n'
    )
    assert fs.read_text(OPENCODE_DEST) == (
        "---\ndescription: Reviews code changes\nmodel: override-opencode-id\nmode: subagent\n---\n\n"
        "You are a code reviewer.\n"
    )


# ── Model layer: tier-string override, built-in label ───────────────────────


def test_model_tier_string_override_builtin_label() -> None:
    """A bare-string entry naming a built-in tier resolves via that tier, beating the harness `model:` key."""
    config = _config().model_copy(
        update={"agent_model_overrides": AgentModelOverridesConfig(overrides={"reviewer": "opus"})}
    )
    ok, fs, reporter = _render(_AGENT_HARNESS_MODEL, config)

    assert ok is True
    assert not reporter.errors
    assert fs.read_text(CLAUDE_DEST) == (
        "---\nname: reviewer\ndescription: Reviews code changes\nmodel: opus\n---\n\nYou are a code reviewer.\n"
    )
    assert fs.read_text(CODEX_DEST) == (
        'name = "reviewer"\n'
        'description = "Reviews code changes"\n'
        'model = "gpt-6-sol"\n'
        'developer_instructions = "You are a code reviewer.\\n"\n'
    )
    assert fs.read_text(OPENCODE_DEST) == (
        "---\ndescription: Reviews code changes\nmodel: anthropic/claude-opus-5-5\nmode: subagent\n---\n\n"
        "You are a code reviewer.\n"
    )


# ── Model layer: tier-string override, custom label ─────────────────────────


def test_model_tier_string_override_custom_label() -> None:
    """A bare-string entry naming a workspace-defined custom tier resolves via it, beating the harness `model:` key."""
    config = _config().model_copy(
        update={
            "model_tiers": ModelTiersConfig(
                tiers={
                    "big-thinker": {
                        "claude": "claude-big-thinker",
                        "codex": "gpt-big-thinker",
                        "opencode": "opencode/big-thinker",
                    }
                }
            ),
            "agent_model_overrides": AgentModelOverridesConfig(overrides={"reviewer": "big-thinker"}),
        }
    )
    ok, fs, reporter = _render(_AGENT_HARNESS_MODEL, config)

    assert ok is True
    assert not reporter.errors
    assert fs.read_text(CLAUDE_DEST) == (
        "---\nname: reviewer\ndescription: Reviews code changes\nmodel: claude-big-thinker\n---\n\n"
        "You are a code reviewer.\n"
    )
    assert fs.read_text(CODEX_DEST) == (
        'name = "reviewer"\n'
        'description = "Reviews code changes"\n'
        'model = "gpt-big-thinker"\n'
        'developer_instructions = "You are a code reviewer.\\n"\n'
    )
    assert fs.read_text(OPENCODE_DEST) == (
        "---\ndescription: Reviews code changes\nmodel: opencode/big-thinker\nmode: subagent\n---\n\n"
        "You are a code reviewer.\n"
    )


# ── Effort: native effort key only ──────────────────────────────────────────

_AGENT_NATIVE_EFFORT = dedent("""\
    ---
    name: reviewer
    description: Reviews code changes
    model: sonnet
    claude:
      effort: high
      color: blue
    codex:
      model_reasoning_effort: high
      sandbox_mode: workspace-write
    opencode:
      reasoningEffort: high
      color: blue
    ---
    You are a code reviewer.
    """)


def test_effort_native_key_only() -> None:
    """Each vendor's own native effort key, declared in the agent's override block, passes through verbatim."""
    ok, fs, reporter = _render(_AGENT_NATIVE_EFFORT, _config())

    assert ok is True
    assert not reporter.errors
    assert fs.read_text(CLAUDE_DEST) == (
        "---\nname: reviewer\ndescription: Reviews code changes\nmodel: sonnet\neffort: high\ncolor: blue\n---\n\n"
        "You are a code reviewer.\n"
    )
    assert fs.read_text(CODEX_DEST) == (
        'name = "reviewer"\n'
        'description = "Reviews code changes"\n'
        'model = "gpt-5.6-terra"\n'
        'model_reasoning_effort = "high"\n'
        'sandbox_mode = "workspace-write"\n'
        'developer_instructions = "You are a code reviewer.\\n"\n'
    )
    assert fs.read_text(OPENCODE_DEST) == (
        "---\ndescription: Reviews code changes\nmodel: anthropic/claude-sonnet-5-5\nmode: subagent\n"
        "reasoningEffort: high\ncolor: blue\n---\n\nYou are a code reviewer.\n"
    )


# ── Effort: workspace effort overwrites a native key ────────────────────────


def test_effort_workspace_overwrites_native_key() -> None:
    """A workspace [agent_model_overrides] effort-only profile overwrites the agent's own native effort key."""
    config = _config().model_copy(
        update={
            "agent_model_overrides": AgentModelOverridesConfig(
                overrides={
                    "reviewer": {
                        "claude": AgentModelOverrideProfile(effort="low"),
                        "codex": AgentModelOverrideProfile(effort="low"),
                        "opencode": AgentModelOverrideProfile(effort="low"),
                    }
                }
            )
        }
    )
    ok, fs, reporter = _render(_AGENT_NATIVE_EFFORT, config)

    assert ok is True
    assert not reporter.errors
    assert fs.read_text(CLAUDE_DEST) == (
        "---\nname: reviewer\ndescription: Reviews code changes\nmodel: sonnet\neffort: low\ncolor: blue\n---\n\n"
        "You are a code reviewer.\n"
    )
    assert fs.read_text(CODEX_DEST) == (
        'name = "reviewer"\n'
        'description = "Reviews code changes"\n'
        'model = "gpt-5.6-terra"\n'
        'model_reasoning_effort = "low"\n'
        'sandbox_mode = "workspace-write"\n'
        'developer_instructions = "You are a code reviewer.\\n"\n'
    )
    assert fs.read_text(OPENCODE_DEST) == (
        "---\ndescription: Reviews code changes\nmodel: anthropic/claude-sonnet-5-5\nmode: subagent\n"
        "reasoningEffort: low\ncolor: blue\n---\n\nYou are a code reviewer.\n"
    )


# ── Effort: workspace effort with no native key to overwrite ────────────────


def test_effort_workspace_without_native_key() -> None:
    """An agent with no native effort key still gets one appended by a workspace effort-only profile."""
    config = _config().model_copy(
        update={
            "agent_model_overrides": AgentModelOverridesConfig(
                overrides={
                    "reviewer": {
                        "claude": AgentModelOverrideProfile(effort="medium"),
                        "codex": AgentModelOverrideProfile(effort="medium"),
                        "opencode": AgentModelOverrideProfile(effort="medium"),
                    }
                }
            )
        }
    )
    ok, fs, reporter = _render(_AGENT_SONNET, config)

    assert ok is True
    assert not reporter.errors
    assert fs.read_text(CLAUDE_DEST) == (
        "---\nname: reviewer\ndescription: Reviews code changes\nmodel: sonnet\neffort: medium\n---\n\n"
        "You are a code reviewer.\n"
    )
    assert fs.read_text(CODEX_DEST) == (
        'name = "reviewer"\n'
        'description = "Reviews code changes"\n'
        'model = "gpt-5.6-terra"\n'
        'model_reasoning_effort = "medium"\n'
        'developer_instructions = "You are a code reviewer.\\n"\n'
    )
    assert fs.read_text(OPENCODE_DEST) == (
        "---\ndescription: Reviews code changes\nmodel: anthropic/claude-sonnet-5-5\nmode: subagent\n"
        "reasoningEffort: medium\n---\n\nYou are a code reviewer.\n"
    )


# ── Effort: a model-only profile leaves a native effort key untouched ───────


def test_effort_model_only_profile_keeps_native_effort() -> None:
    """A workspace profile that sets only `model` (no `effort`) leaves the agent's native effort key in place."""
    config = _config().model_copy(
        update={
            "agent_model_overrides": AgentModelOverridesConfig(
                overrides={
                    "reviewer": {
                        "claude": AgentModelOverrideProfile(model="workspace-model-claude"),
                        "codex": AgentModelOverrideProfile(model="workspace-model-codex"),
                        "opencode": AgentModelOverrideProfile(model="workspace-model-opencode"),
                    }
                }
            )
        }
    )
    ok, fs, reporter = _render(_AGENT_NATIVE_EFFORT, config)

    assert ok is True
    assert not reporter.errors
    assert fs.read_text(CLAUDE_DEST) == (
        "---\nname: reviewer\ndescription: Reviews code changes\nmodel: workspace-model-claude\n"
        "effort: high\ncolor: blue\n---\n\nYou are a code reviewer.\n"
    )
    assert fs.read_text(CODEX_DEST) == (
        'name = "reviewer"\n'
        'description = "Reviews code changes"\n'
        'model = "workspace-model-codex"\n'
        'model_reasoning_effort = "high"\n'
        'sandbox_mode = "workspace-write"\n'
        'developer_instructions = "You are a code reviewer.\\n"\n'
    )
    assert fs.read_text(OPENCODE_DEST) == (
        "---\ndescription: Reviews code changes\nmodel: workspace-model-opencode\nmode: subagent\n"
        "reasoningEffort: high\ncolor: blue\n---\n\nYou are a code reviewer.\n"
    )


# ── Effort: an effort-only profile alongside other vendor override keys ────

_AGENT_WITH_OTHER_OVERRIDE_KEYS = dedent("""\
    ---
    name: reviewer
    description: Reviews code changes
    model: sonnet
    codex:
      sandbox_mode: workspace-write
    opencode:
      permission:
        edit: allow
    ---
    You are a code reviewer.
    """)


def test_effort_only_profile() -> None:
    """An effort-only profile adds the effort key without disturbing the agent's other override-block keys."""
    config = _config().model_copy(
        update={
            "agent_model_overrides": AgentModelOverridesConfig(
                overrides={
                    "reviewer": {
                        "claude": AgentModelOverrideProfile(effort="max"),
                        "codex": AgentModelOverrideProfile(effort="max"),
                        "opencode": AgentModelOverrideProfile(effort="max"),
                    }
                }
            )
        }
    )
    ok, fs, reporter = _render(_AGENT_WITH_OTHER_OVERRIDE_KEYS, config)

    assert ok is True
    assert not reporter.errors
    assert fs.read_text(CLAUDE_DEST) == (
        "---\nname: reviewer\ndescription: Reviews code changes\nmodel: sonnet\neffort: max\n---\n\n"
        "You are a code reviewer.\n"
    )
    assert fs.read_text(CODEX_DEST) == (
        'name = "reviewer"\n'
        'description = "Reviews code changes"\n'
        'model = "gpt-5.6-terra"\n'
        'sandbox_mode = "workspace-write"\n'
        'model_reasoning_effort = "max"\n'
        'developer_instructions = "You are a code reviewer.\\n"\n'
    )
    assert fs.read_text(OPENCODE_DEST) == (
        "---\ndescription: Reviews code changes\nmodel: anthropic/claude-sonnet-5-5\nmode: subagent\n"
        "permission:\n  edit: allow\nreasoningEffort: max\n---\n\nYou are a code reviewer.\n"
    )
