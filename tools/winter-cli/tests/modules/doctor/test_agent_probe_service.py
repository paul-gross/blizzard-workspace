"""Tests for AgentProbeService: per-vendor agent copy staleness probe.

Covers:
  1. Healthy state — all vendors pass when on-disk copies match the renderer's output.
  2. Stale copy — bytes on disk differ from the transform of the current source → WARN.
  3. Missing copy — canonical source exists but copy is absent → WARN.
  4. Orphaned copy — a <prefix>-* file with no live canonical source → WARN.
  5. Heal loop — after ExtensionAgentService.process() the probe goes clean.
  6. adopt_extensions = none — returns no results.
  7. Winter mode + no manifest — extension skipped silently.
  8. Empty-prefix orphan guard — no known prefixes → first-party agents never flagged (Fix A).
  9. Name uniqueness — duplicate canonical agent name across extensions → WARN (Fix F).
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from tests.conftest import FakeConfigFileReader, FakeFilesystem, FakeInitReporter
from winter_cli.config.models import AdoptExtensions, CodeAgentVendor, WorkspaceConfig
from winter_cli.core.filesystem import IFilesystemReader
from winter_cli.modules.doctor.agent_probe_service import AGENT_SOURCE, AgentProbeService
from winter_cli.modules.doctor.models import ProbeStatus
from winter_cli.modules.workspace.agent_install import ExtensionAgentService
from winter_cli.modules.workspace.agent_transform.agent_enumerator import CanonicalAgentEnumerator
from winter_cli.modules.workspace.extension_manifest import ExtensionManifestLoader
from winter_cli.modules.workspace.models import StandaloneRepository

WORKSPACE_ROOT = Path("/ws")

# Vendor agent dirs (derived from CodeAgentVendor.agents_subpath)
CLAUDE_AGENTS = WORKSPACE_ROOT / ".claude" / "agents"
CODEX_AGENTS = WORKSPACE_ROOT / ".codex" / "agents"
OPENCODE_AGENTS = WORKSPACE_ROOT / ".opencode" / "agent"

# Extension source layout
EXT_ROOT = WORKSPACE_ROOT / "wf"
EXT_AGENTS = EXT_ROOT / "agents"

# A minimal canonical agent file.
_CANONICAL_AGENT = """\
---
name: reviewer
description: Reviews code changes
model: sonnet
---
You are a code reviewer.
"""

# A second canonical agent for multi-agent tests.
_CANONICAL_AGENT_2 = """\
---
name: planner
description: Plans tasks
model: haiku
---
You are a planner.
"""


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def _config(adopt_extensions: AdoptExtensions = AdoptExtensions.all) -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_root=WORKSPACE_ROOT,
        session_prefix="t",
        main_branch="main",
        adopt_extensions=adopt_extensions,
    )


def _manifest_loader(
    config_files: dict[Path, dict] | None = None,
) -> ExtensionManifestLoader:
    return ExtensionManifestLoader(config_file_reader=FakeConfigFileReader(config_files or {}))


def _probe_svc(
    config: WorkspaceConfig,
    fs: FakeFilesystem,
    config_files: dict[Path, dict] | None = None,
) -> AgentProbeService:
    loader = _manifest_loader(config_files)
    return AgentProbeService(
        config=config,
        fs=cast(IFilesystemReader, fs),
        manifest_loader=loader,
        agent_enumerator=CanonicalAgentEnumerator(fs=cast(IFilesystemReader, fs), manifest_loader=loader),
    )


def _install_svc(
    config: WorkspaceConfig,
    fs: FakeFilesystem,
    config_files: dict[Path, dict] | None = None,
) -> ExtensionAgentService:
    loader = _manifest_loader(config_files)
    return ExtensionAgentService(
        config=config,
        fs=fs,
        manifest_loader=loader,
        agent_enumerator=CanonicalAgentEnumerator(fs=fs, manifest_loader=loader),
    )


def _repo(name: str = "wf", prefix: str | None = None) -> StandaloneRepository:
    return StandaloneRepository(name=name, path=WORKSPACE_ROOT / name, prefix=prefix)


def _seed_extension(
    fs: FakeFilesystem,
    config_files: dict[Path, dict],
    agent_files: dict[str, str] | None = None,
    name: str = "wf",
) -> StandaloneRepository:
    """Plant an extension with canonical agent files in the fake filesystem."""
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


# ---------------------------------------------------------------------------
# 1. Healthy state
# ---------------------------------------------------------------------------


class TestHealthyState:
    def test_all_vendors_pass_after_clean_install(self) -> None:
        """After ExtensionAgentService.process(), all vendors report PASS."""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(fs, config_files)
        cfg = _config()
        reporter = FakeInitReporter()

        # Install via the real installer.
        _install_svc(cfg, fs, config_files).process(ext, reporter)

        # Probe must agree that everything is in sync.
        svc = _probe_svc(cfg, fs, config_files)
        results = svc.run([ext])

        assert results, "expected one result per vendor"
        assert all(r.status == ProbeStatus.pass_ for r in results), (
            f"Expected all PASS, got: {[(r.name, r.status, r.message) for r in results]}"
        )

    def test_all_vendors_pass_when_no_extensions(self) -> None:
        """With no standalone repos, every vendor emits a 0-agent PASS."""
        fs = FakeFilesystem(directories={WORKSPACE_ROOT})
        svc = _probe_svc(_config(), fs)
        results = svc.run([])
        assert results, "expected results for each vendor"
        assert all(r.status == ProbeStatus.pass_ for r in results)

    def test_one_result_per_vendor_plus_uniqueness(self) -> None:
        """run() emits one result per CodeAgentVendor plus one name-uniqueness result."""
        fs = FakeFilesystem(directories={WORKSPACE_ROOT})
        svc = _probe_svc(_config(), fs)
        results = svc.run([])
        # One per vendor + one name-uniqueness check.
        assert len(results) == len(list(CodeAgentVendor)) + 1

    def test_result_source_is_agent_source(self) -> None:
        """All results carry the AGENT_SOURCE label."""
        fs = FakeFilesystem(directories={WORKSPACE_ROOT})
        svc = _probe_svc(_config(), fs)
        results = svc.run([])
        assert all(r.source == AGENT_SOURCE for r in results)


# ---------------------------------------------------------------------------
# 2. Stale copy
# ---------------------------------------------------------------------------


class TestStaleCopy:
    def test_stale_claude_copy_reports_warn(self) -> None:
        """A Claude copy with modified bytes → WARN with 'stale copy' for claude-code."""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(fs, config_files)
        cfg = _config()
        reporter = FakeInitReporter()

        # Install correctly first.
        _install_svc(cfg, fs, config_files).process(ext, reporter)

        # Corrupt the Claude copy.
        claude_copy = CLAUDE_AGENTS / "wf-reviewer.md"
        fs.files[claude_copy] = "# this is stale content that doesn't match the transform\n"

        svc = _probe_svc(cfg, fs, config_files)
        results = svc.run([ext])
        claude_result = next(r for r in results if "claude-code" in r.name)
        assert claude_result.status == ProbeStatus.warn
        assert "stale copy" in claude_result.message
        assert "wf-reviewer.md" in claude_result.message
        assert claude_result.remediation is not None
        assert "winter ws init" in claude_result.remediation

    def test_stale_codex_copy_reports_warn(self) -> None:
        """A Codex copy with modified bytes → WARN with 'stale copy' for codex."""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(fs, config_files)
        cfg = _config()
        reporter = FakeInitReporter()

        _install_svc(cfg, fs, config_files).process(ext, reporter)

        codex_copy = CODEX_AGENTS / "wf-reviewer.toml"
        fs.files[codex_copy] = "# stale toml\n"

        svc = _probe_svc(cfg, fs, config_files)
        results = svc.run([ext])
        codex_result = next(r for r in results if "codex" in r.name)
        assert codex_result.status == ProbeStatus.warn
        assert "stale copy" in codex_result.message

    def test_stale_opencode_copy_reports_warn(self) -> None:
        """An OpenCode copy with modified bytes → WARN with 'stale copy' for opencode."""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(fs, config_files)
        cfg = _config()
        reporter = FakeInitReporter()

        _install_svc(cfg, fs, config_files).process(ext, reporter)

        oc_copy = OPENCODE_AGENTS / "wf-reviewer.md"
        fs.files[oc_copy] = "# stale opencode content\n"

        svc = _probe_svc(cfg, fs, config_files)
        results = svc.run([ext])
        oc_result = next(r for r in results if "opencode" in r.name)
        assert oc_result.status == ProbeStatus.warn
        assert "stale copy" in oc_result.message

    def test_stale_from_source_change(self) -> None:
        """Updating the source after install → probe detects stale (bytes now differ)."""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(fs, config_files)
        cfg = _config()
        reporter = FakeInitReporter()

        _install_svc(cfg, fs, config_files).process(ext, reporter)

        # Modify the canonical source — copies are now stale.
        updated = _CANONICAL_AGENT.replace("Reviews code changes", "Reviews code thoroughly")
        fs.files[EXT_AGENTS / "reviewer.md"] = updated

        svc = _probe_svc(cfg, fs, config_files)
        results = svc.run([ext])
        stale_results = [r for r in results if r.status == ProbeStatus.warn]
        assert stale_results, "expected at least one stale-copy WARN after source change"
        assert all("stale copy" in r.message for r in stale_results)


# ---------------------------------------------------------------------------
# 3. Missing copy
# ---------------------------------------------------------------------------


class TestMissingCopy:
    def test_missing_claude_copy_reports_warn(self) -> None:
        """Canonical source exists but .claude/agents copy is absent → WARN 'missing copy'."""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(fs, config_files)
        cfg = _config()

        svc = _probe_svc(cfg, fs, config_files)
        results = svc.run([ext])
        claude_result = next(r for r in results if "claude-code" in r.name)
        assert claude_result.status == ProbeStatus.warn
        assert "missing copy" in claude_result.message
        assert "wf-reviewer.md" in claude_result.message
        assert claude_result.remediation is not None
        assert "winter ws init" in claude_result.remediation

    def test_missing_codex_copy_reports_warn(self) -> None:
        """Canonical source exists but .codex/agents copy is absent → WARN 'missing copy'."""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(fs, config_files)
        cfg = _config()

        svc = _probe_svc(cfg, fs, config_files)
        results = svc.run([ext])
        codex_result = next(r for r in results if "codex" in r.name)
        assert codex_result.status == ProbeStatus.warn
        assert "missing copy" in codex_result.message

    def test_missing_opencode_copy_reports_warn(self) -> None:
        """Canonical source exists but .opencode/agent copy is absent → WARN 'missing copy'."""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(fs, config_files)
        cfg = _config()

        svc = _probe_svc(cfg, fs, config_files)
        results = svc.run([ext])
        oc_result = next(r for r in results if "opencode" in r.name)
        assert oc_result.status == ProbeStatus.warn
        assert "missing copy" in oc_result.message


# ---------------------------------------------------------------------------
# 4. Orphaned copy
# ---------------------------------------------------------------------------


class TestOrphanedCopy:
    def test_orphaned_copy_warns_for_claude(self) -> None:
        """A wf-* file in .claude/agents with no canonical source → WARN 'orphaned copy'."""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(fs, config_files, agent_files={})  # no agents in extension

        # Plant an orphaned copy manually.
        fs.directories.add(CLAUDE_AGENTS)
        fs.files[CLAUDE_AGENTS / "wf-ghost.md"] = "# orphaned"

        svc = _probe_svc(_config(), fs, config_files)
        results = svc.run([ext])
        claude_result = next(r for r in results if "claude-code" in r.name)
        assert claude_result.status == ProbeStatus.warn
        assert "orphaned copy" in claude_result.message
        assert "wf-ghost.md" in claude_result.message

    def test_orphaned_copy_warns_for_codex(self) -> None:
        """A wf-* file in .codex/agents with no canonical source → WARN 'orphaned copy'."""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(fs, config_files, agent_files={})

        fs.directories.add(CODEX_AGENTS)
        fs.files[CODEX_AGENTS / "wf-ghost.toml"] = "# orphaned"

        svc = _probe_svc(_config(), fs, config_files)
        results = svc.run([ext])
        codex_result = next(r for r in results if "codex" in r.name)
        assert codex_result.status == ProbeStatus.warn
        assert "orphaned copy" in codex_result.message

    def test_orphaned_copy_warns_for_opencode(self) -> None:
        """A wf-* file in .opencode/agent with no canonical source → WARN 'orphaned copy'."""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(fs, config_files, agent_files={})

        fs.directories.add(OPENCODE_AGENTS)
        fs.files[OPENCODE_AGENTS / "wf-ghost.md"] = "# orphaned"

        svc = _probe_svc(_config(), fs, config_files)
        results = svc.run([ext])
        oc_result = next(r for r in results if "opencode" in r.name)
        assert oc_result.status == ProbeStatus.warn
        assert "orphaned copy" in oc_result.message

    def test_first_party_agents_not_flagged_as_orphans(self) -> None:
        """Agents without a known extension prefix are outside the probe's jurisdiction."""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(fs, config_files, agent_files={})  # wf prefix known, no agents

        # Plant a first-party agent that does not have the wf- prefix.
        fs.directories.add(CLAUDE_AGENTS)
        fs.files[CLAUDE_AGENTS / "developer.md"] = "# first-party agent"
        fs.files[CLAUDE_AGENTS / "ws-researcher.md"] = "# another first-party"

        svc = _probe_svc(_config(), fs, config_files)
        results = svc.run([ext])
        # All vendors should pass — the first-party files are not scoped to
        # the "wf" prefix and must not be flagged.
        assert all(r.status == ProbeStatus.pass_ for r in results), (
            f"First-party agents falsely flagged: {[(r.name, r.message) for r in results if r.status != ProbeStatus.pass_]}"
        )


# ---------------------------------------------------------------------------
# 5. Heal loop — probe goes clean after install
# ---------------------------------------------------------------------------


class TestHealLoop:
    def test_probe_goes_clean_after_process(self) -> None:
        """After a probe reports WARN, running ExtensionAgentService.process()
        fixes the copies and the probe subsequently reports PASS."""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(fs, config_files)
        cfg = _config()
        reporter = FakeInitReporter()

        # Probe before installation → some WARN (missing copies).
        probe = _probe_svc(cfg, fs, config_files)
        pre_results = probe.run([ext])
        assert any(r.status == ProbeStatus.warn for r in pre_results)

        # Install via the real installer.
        _install_svc(cfg, fs, config_files).process(ext, reporter)

        # Probe after installation → all PASS.
        post_results = _probe_svc(cfg, fs, config_files).run([ext])
        assert all(r.status == ProbeStatus.pass_ for r in post_results), (
            f"Expected all PASS after heal, got: {[(r.name, r.status, r.message) for r in post_results]}"
        )

    def test_stale_copy_heals_after_reinstall(self) -> None:
        """A stale copy detected by the probe becomes clean after process() re-syncs it."""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(fs, config_files)
        cfg = _config()
        reporter = FakeInitReporter()

        # Install cleanly.
        _install_svc(cfg, fs, config_files).process(ext, reporter)

        # Corrupt one copy.
        fs.files[CLAUDE_AGENTS / "wf-reviewer.md"] = "# corrupted"

        # Probe detects stale.
        pre = _probe_svc(cfg, fs, config_files).run([ext])
        claude_pre = next(r for r in pre if "claude-code" in r.name)
        assert claude_pre.status == ProbeStatus.warn

        # Re-install to heal.
        _install_svc(cfg, fs, config_files).process(ext, reporter)

        # Probe now passes.
        post = _probe_svc(cfg, fs, config_files).run([ext])
        claude_post = next(r for r in post if "claude-code" in r.name)
        assert claude_post.status == ProbeStatus.pass_

    def test_probe_and_installer_agree_on_bytes(self) -> None:
        """The probe considers an installer-written copy clean — single source of truth.

        This test explicitly verifies that the AgentProbeService and
        ExtensionAgentService use the SAME renderer so they always agree on
        what the expected bytes should be.
        """
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(fs, config_files)
        cfg = _config()
        reporter = FakeInitReporter()

        _install_svc(cfg, fs, config_files).process(ext, reporter)

        # All three vendors must pass — proving installer output == probe expectation.
        results = _probe_svc(cfg, fs, config_files).run([ext])
        failures = [r for r in results if r.status != ProbeStatus.pass_]
        assert not failures, f"Probe/installer mismatch for: {[(r.name, r.message) for r in failures]}"


# ---------------------------------------------------------------------------
# 6. adopt_extensions = none
# ---------------------------------------------------------------------------


class TestAdoptExtensionsNone:
    def test_returns_empty_when_adopt_none(self) -> None:
        """When adopt_extensions=none, no probe results are emitted."""
        fs = FakeFilesystem(directories={WORKSPACE_ROOT})
        svc = _probe_svc(_config(adopt_extensions=AdoptExtensions.none), fs)
        results = svc.run([_repo()])
        assert results == []


# ---------------------------------------------------------------------------
# 7. Winter mode — extension without manifest is skipped
# ---------------------------------------------------------------------------


class TestWinterModeNoManifest:
    def test_extension_skipped_in_winter_mode_without_manifest(self) -> None:
        """In 'winter' mode, an extension with no winter-ext.toml is silently skipped."""
        fs = FakeFilesystem()
        agents_dir = EXT_ROOT / "agents"
        fs.directories.add(EXT_ROOT)
        fs.directories.add(agents_dir)
        fs.files[agents_dir / "reviewer.md"] = _CANONICAL_AGENT

        svc = _probe_svc(_config(adopt_extensions=AdoptExtensions.winter), fs)
        results = svc.run([_repo()])
        # No agents should be expected (extension skipped), so all vendors PASS with 0 agents.
        assert all(r.status == ProbeStatus.pass_ for r in results)


# ---------------------------------------------------------------------------
# 8. Fix A — empty-prefix orphan guard
# ---------------------------------------------------------------------------


class TestEmptyPrefixOrphanGuard:
    def test_no_known_prefixes_first_party_agents_not_flagged(self) -> None:
        """When there are NO known extension prefixes, first-party .claude/agents/*.md
        files (even those with a dash in the name) must NOT be flagged as orphans.

        Regression: when prefix_markers is empty, the old guard
        ``if prefix_markers and not any(...)`` short-circuits to False and
        collects EVERY dashed file, reporting false orphans.  The fix uses
        ``if not prefix_markers or not any(...)`` which skips all entries when
        there are no known prefixes.
        """
        fs = FakeFilesystem()
        # Seed the agents dir with first-party files that have dashes in their names.
        fs.directories.add(CLAUDE_AGENTS)
        fs.files[CLAUDE_AGENTS / "ws-developer.md"] = "# first-party with dash"
        fs.files[CLAUDE_AGENTS / "my-researcher.md"] = "# another first-party with dash"
        fs.files[CLAUDE_AGENTS / "no-prefix-agent.md"] = "# no known prefix"

        # Run with NO standalone repos → known_prefixes is empty.
        svc = _probe_svc(_config(), fs)
        results = svc.run([])

        # All vendor results must be PASS — first-party files must not be flagged.
        vendor_results = [r for r in results if "agent copies:" in r.name]
        assert all(r.status == ProbeStatus.pass_ for r in vendor_results), (
            f"First-party agents falsely flagged with empty prefix set: "
            f"{[(r.name, r.message) for r in vendor_results if r.status != ProbeStatus.pass_]}"
        )

    def test_no_known_prefixes_dashed_files_with_only_first_party_repos(self) -> None:
        """Even when adopt_extensions=all but no extension repos have agents dirs,
        dashed file names in vendor agents dirs are not flagged as orphans."""
        fs = FakeFilesystem()
        # Extension exists (adopt mode=all, no winter-ext.toml), but has no agents dir.
        ext_path = WORKSPACE_ROOT / "my-ext"
        fs.directories.add(ext_path)

        # Dashed first-party file already in the claude agents dir.
        fs.directories.add(CLAUDE_AGENTS)
        fs.files[CLAUDE_AGENTS / "some-agent.md"] = "# first-party"

        repo = StandaloneRepository(name="my-ext", path=ext_path)
        svc = _probe_svc(_config(adopt_extensions=AdoptExtensions.all), fs)
        results = svc.run([repo])

        # The "my-ext" extension has no agents dir → prefix contributes nothing;
        # "some-agent.md" should NOT be flagged as an orphaned "my-ext-*" file.
        vendor_results = [r for r in results if "claude-code" in r.name]
        assert vendor_results
        assert all(r.status == ProbeStatus.pass_ for r in vendor_results), (
            f"Unexpected orphan flag: {[(r.name, r.message) for r in vendor_results]}"
        )


# ---------------------------------------------------------------------------
# 9. Fix F — canonical agent name uniqueness across extensions
# ---------------------------------------------------------------------------


class TestNameUniqueness:
    def test_unique_names_pass(self) -> None:
        """Two extensions each with a DIFFERENT agent name → uniqueness check PASS."""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}

        ext1 = _seed_extension(fs, config_files, agent_files={"reviewer.md": _CANONICAL_AGENT}, name="ext1")
        ext2 = _seed_extension(
            fs,
            config_files,
            agent_files={"planner.md": _CANONICAL_AGENT_2},
            name="ext2",
        )

        svc = _probe_svc(_config(), fs, config_files)
        results = svc.run([ext1, ext2])

        uniqueness = next(r for r in results if "uniqueness" in r.name)
        assert uniqueness.status == ProbeStatus.pass_

    def test_duplicate_name_warns(self) -> None:
        """Two extensions shipping agents with the SAME canonical name → uniqueness WARN."""
        duplicate_agent = """\
---
name: reviewer
description: Also reviews code
model: haiku
---
I also review code.
"""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}

        ext1 = _seed_extension(fs, config_files, agent_files={"reviewer.md": _CANONICAL_AGENT}, name="ext1")
        ext2 = _seed_extension(
            fs,
            config_files,
            agent_files={"reviewer.md": duplicate_agent},
            name="ext2",
        )

        svc = _probe_svc(_config(), fs, config_files)
        results = svc.run([ext1, ext2])

        uniqueness = next(r for r in results if "uniqueness" in r.name)
        assert uniqueness.status == ProbeStatus.warn
        assert "reviewer" in uniqueness.message
        assert uniqueness.remediation is not None

    def test_duplicate_name_message_lists_both_prefixes(self) -> None:
        """The WARN message names both extension prefixes that claim the same agent name."""
        agent_same_name = """\
---
name: reviewer
description: Conflict with ext1
model: sonnet
---
Body.
"""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}

        ext1 = _seed_extension(fs, config_files, agent_files={"reviewer.md": _CANONICAL_AGENT}, name="alpha")
        ext2 = _seed_extension(
            fs,
            config_files,
            agent_files={"reviewer.md": agent_same_name},
            name="beta",
        )

        svc = _probe_svc(_config(), fs, config_files)
        results = svc.run([ext1, ext2])

        uniqueness = next(r for r in results if "uniqueness" in r.name)
        assert "alpha" in uniqueness.message
        assert "beta" in uniqueness.message

    def test_no_extensions_uniqueness_passes(self) -> None:
        """With no extensions the uniqueness result is PASS."""
        fs = FakeFilesystem(directories={WORKSPACE_ROOT})
        svc = _probe_svc(_config(), fs)
        results = svc.run([])

        uniqueness = next(r for r in results if "uniqueness" in r.name)
        assert uniqueness.status == ProbeStatus.pass_

    def test_adopt_none_returns_no_uniqueness_result(self) -> None:
        """When adopt_extensions=none, run() returns [] including no uniqueness result."""
        fs = FakeFilesystem(directories={WORKSPACE_ROOT})
        svc = _probe_svc(_config(adopt_extensions=AdoptExtensions.none), fs)
        results = svc.run([_repo()])
        uniqueness = [r for r in results if "uniqueness" in r.name]
        assert uniqueness == []


# ---------------------------------------------------------------------------
# 10. Misconfigured frontmatter tier: dedicated WARN, not "orphaned copy"
# ---------------------------------------------------------------------------

_BAD_TIER_AGENT = """\
---
name: reviewer
description: Reviews code changes
model: nonexistent-tier
---
You are a code reviewer.
"""


class TestBadFrontmatterTier:
    def test_render_failed_tier_produces_warn_not_orphaned_copy(self) -> None:
        """An agent with an invalid frontmatter tier: a pre-existing on-disk copy is NOT
        classified as 'orphaned copy'; instead a dedicated WARN ProbeResult names
        the tier/vendor (the real cause).

        This guards against the regression where RepoError during render drops the
        agent from `expected`, causing the orphan branch to misclassify the file.
        """
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(fs, config_files, agent_files={"reviewer.md": _BAD_TIER_AGENT})
        cfg = _config()

        # Pre-plant a copy (as if a previous successful install had run before
        # the tier was changed to an invalid value).
        fs.directories.add(CLAUDE_AGENTS)
        fs.files[CLAUDE_AGENTS / "wf-reviewer.md"] = "# stale copy from a prior valid install\n"

        svc = _probe_svc(cfg, fs, config_files)
        results = svc.run([ext])

        # The pre-existing copy must NOT be classified as an orphan.
        assert not any("orphaned copy" in r.message for r in results), (
            f"Unexpected 'orphaned copy' finding: {[(r.name, r.message) for r in results if 'orphaned copy' in r.message]}"
        )

        # A dedicated WARN ProbeResult must name the invalid tier.
        tier_warns = [r for r in results if r.status == ProbeStatus.warn and "nonexistent-tier" in r.message]
        assert tier_warns, (
            "expected a WARN ProbeResult naming the invalid tier 'nonexistent-tier'; "
            f"got: {[(r.name, r.status, r.message) for r in results]}"
        )

    def test_render_failed_tier_warn_result_has_remediation(self) -> None:
        """The dedicated tier-failure WARN ProbeResult includes a remediation hint."""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(fs, config_files, agent_files={"reviewer.md": _BAD_TIER_AGENT})
        cfg = _config()

        svc = _probe_svc(cfg, fs, config_files)
        results = svc.run([ext])

        tier_warns = [r for r in results if r.status == ProbeStatus.warn and "nonexistent-tier" in r.message]
        assert tier_warns
        assert all(r.remediation is not None for r in tier_warns), "expected remediation on tier-failure WARN"

    def test_render_failed_tier_valid_agents_not_affected(self) -> None:
        """A bad-tier agent does not affect probe results for a second valid agent
        in the same extension — the good agent's copy is still checked for staleness."""
        valid_agent = """\
---
name: planner
description: Plans tasks
model: haiku
---
You are a planner.
"""
        fs = FakeFilesystem()
        config_files: dict[Path, dict] = {}
        ext = _seed_extension(
            fs,
            config_files,
            agent_files={
                "reviewer.md": _BAD_TIER_AGENT,
                "planner.md": valid_agent,
            },
        )
        cfg = _config()
        reporter = FakeInitReporter()

        # Install only the valid agent via the installer (the bad-tier agent is skipped).
        _install_svc(cfg, fs, config_files).process(ext, reporter)

        svc = _probe_svc(cfg, fs, config_files)
        results = svc.run([ext])

        # No orphaned or missing copies for the valid agent.
        copy_issues = [
            r
            for r in results
            if "agent copies:" in r.name
            and r.status == ProbeStatus.warn
            and ("orphaned" in r.message or "missing" in r.message)
        ]
        assert not copy_issues, f"Unexpected copy issues for valid agent: {[(r.name, r.message) for r in copy_issues]}"

        # The bad-tier agent generates a tier-failure WARN.
        tier_warns = [r for r in results if r.status == ProbeStatus.warn and "nonexistent-tier" in r.message]
        assert tier_warns
