"""Doctor probe for per-vendor agent copy staleness across all extensions.

Mirrors ``SkillProbeService`` but checks rendered file copies rather than
skill symlinks/copy-directories. For each standalone repo and each
``CodeAgentVendor`` the probe re-renders the expected bytes from the canonical
source and compares to the on-disk copy at
``<workspace>/<vendor.agents_subpath>/<prefix>-<name><suffix>``.

Three issue types per vendor:
- **missing copy**: the expected file is absent from the agents dir.
- **stale copy**: the file is present but its bytes differ from the transform
  of its current canonical source.
- **orphaned copy**: a ``<prefix>-*`` file in the agents dir has no live
  canonical source (scoped to known extension prefixes so first-party
  workspace agents are never falsely flagged).

This probe is REPORT-ONLY. It never mutates or re-syncs. Drift is a
WARNING, not a hard failure. Run ``winter ws init`` to repair.

Note on ``.claude/agents`` overlap with ``CoreProbeService._probe_claude_symlinks``:
that probe skips entries that are not symlinks (``if not self._fs.is_symlink(entry):
continue``), so rendered agent copies (plain files) in ``.claude/agents`` are
never incorrectly audited by the symlink probe.
"""

from __future__ import annotations

from pathlib import Path

from winter_cli.config.models import AdoptExtensions, CodeAgentVendor, WorkspaceConfig
from winter_cli.core.filesystem import IFilesystemReader
from winter_cli.modules.doctor.models import ProbeResult, ProbeStatus
from winter_cli.modules.workspace.agent_transform.agent_copy_inspector import AgentCopyInspector, CopyStatus
from winter_cli.modules.workspace.agent_transform.agent_enumerator import KnownAgent
from winter_cli.modules.workspace.agent_transform.model_tiers import EffectiveTierTable, build_effective_tier_table
from winter_cli.modules.workspace.extension_manifest import ExtensionManifestLoader
from winter_cli.modules.workspace.models import StandaloneRepository

AGENT_SOURCE = "agents"


class AgentProbeService:
    """Doctor probe for per-vendor agent copy staleness across all extensions.

    For each installed extension and each ``CodeAgentVendor``, re-renders the
    expected bytes from the canonical source (using the SAME renderers as
    ``ExtensionAgentService``) and compares to the on-disk copy:

    - **missing copy** — expected copy absent from the agents dir.
    - **stale copy (transform mismatch)** — copy present but bytes differ from
      the renderer's current output for that source.
    - **orphaned copy** — a ``<prefix>-*`` file whose prefix belongs to a known
      extension but has no corresponding canonical source.
    - **name collision** — two or more extensions ship canonical agents with the
      same ``name`` field; Claude resolves agents by ``name``, so collisions
      cause unpredictable agent selection.

    The walk itself — manifest load, agents-dir resolution, parse, resolve,
    render, and the byte comparison — is delegated to the injected
    ``AgentCopyInspector``, which uses the same renderers and resolver as
    ``ExtensionAgentService`` (the installer), so "stale" here is defined
    identically to what the installer would write.

    This is REPORT-ONLY: the probe never mutates or re-syncs. Drift is a
    WARNING, not a hard failure. Run ``winter ws init`` to repair.

    Agent discovery is **flat ``.md``-only**: subdirectories inside an
    extension's agents directory are ignored (see ``ExtensionAgentService``).
    """

    def __init__(
        self,
        config: WorkspaceConfig,
        fs: IFilesystemReader,
        manifest_loader: ExtensionManifestLoader,
        agent_copy_inspector: AgentCopyInspector,
    ) -> None:
        self._config = config
        self._fs = fs
        self._manifest_loader = manifest_loader
        self._agent_copy_inspector = agent_copy_inspector

    def run(self, standalone_repos: list[StandaloneRepository]) -> list[ProbeResult]:
        if self._config.adopt_extensions == AdoptExtensions.none:
            return []

        tier_table = build_effective_tier_table(self._config.model_tiers.tiers, self._config.model_tiers.tier_sources)
        # One walk for the whole probe run: every check below reads this list,
        # so an unreadable agent file is logged once, not once per check.
        known = self._agent_copy_inspector.known_agents(standalone_repos, mode=self._config.adopt_extensions)
        results: list[ProbeResult] = []
        for vendor in CodeAgentVendor:
            results.extend(self._probe_vendor(vendor, standalone_repos, known, tier_table))
        results.extend(self._probe_name_uniqueness(known))
        results.extend(self._probe_override_targets(known))
        return results

    # ── Per-vendor probe ──────────────────────────────────────────────────

    def _probe_vendor(
        self,
        vendor: CodeAgentVendor,
        standalone_repos: list[StandaloneRepository],
        known: list[KnownAgent],
        tier_table: EffectiveTierTable,
    ) -> list[ProbeResult]:
        """Check all extensions for one vendor and emit probe results."""
        agents_dir = self._config.workspace_root / vendor.agents_subpath

        # Installed filename → its expected copy. A later canonical file that
        # renders to the same filename replaces the earlier one, as the
        # installer's later write overwrites the earlier copy on disk.
        expected: dict[str, CopyStatus | None] = {}
        render_failures: list[tuple[str, str]] = []
        for copy in self._agent_copy_inspector.inspect(
            known,
            vendor,
            tier_table=tier_table,
            agent_model_overrides=self._config.agent_model_overrides,
            agents_dir=agents_dir,
        ):
            name = copy.installed_path.name
            if copy.error is not None:
                render_failures.append((name, str(copy.error)))
                continue
            expected[name] = copy.copy_status

        # Names whose render failed (unknown or incomplete tier) — excluded from
        # the orphan check so a pre-existing on-disk copy is not mislabelled
        # "orphaned copy" when the real cause is a tier-resolution error.
        render_failed_names = {name for name, _ in render_failures}

        # The <prefix>-* files on disk, scoped to known extension prefixes.
        known_prefixes = self._agent_copy_inspector.known_prefixes(standalone_repos, mode=self._config.adopt_extensions)
        actual = self._actual_agents(agents_dir, known_prefixes)

        issues: list[str] = []

        # On-disk copies first, in filename order: orphans and stale copies.
        for name in sorted(actual):
            if name in render_failed_names:
                continue
            if name not in expected:
                issues.append(f"orphaned copy: {name} (no live canonical source)")
                continue
            status = expected[name]
            if status is CopyStatus.missing:
                # Listed on disk, so a missing status means its bytes could not be read.
                issues.append(f"missing copy: {name} (read error)")
            elif status is CopyStatus.stale:
                issues.append(f"stale copy: {name} (transform mismatch)")

        # Then expected copies absent from disk.
        for name in sorted(expected):
            if name not in actual:
                issues.append(f"missing copy: {name} (canonical source exists, copy absent)")

        label = f"agent copies: {vendor.value}"
        results: list[ProbeResult] = []
        if issues:
            results.append(
                ProbeResult(
                    source=AGENT_SOURCE,
                    name=label,
                    status=ProbeStatus.warn,
                    message="; ".join(issues),
                    remediation="Run `winter ws init` to sync agent copies.",
                )
            )
        else:
            results.append(
                ProbeResult(
                    source=AGENT_SOURCE,
                    name=label,
                    status=ProbeStatus.pass_,
                    message=f"{len(expected)} agent(s) in sync",
                )
            )

        # Emit a dedicated WARN ProbeResult for each render failure so the real
        # cause (tier label + vendor) reaches the structured output rather than
        # being buried in a log line or mislabelled as an orphaned copy.
        for _filename, error_msg in sorted(render_failures):
            results.append(
                ProbeResult(
                    source=AGENT_SOURCE,
                    name=f"agent tier: {vendor.value}",
                    status=ProbeStatus.warn,
                    message=error_msg,
                    remediation=(
                        "Fix the model tier in the agent's frontmatter or add the "
                        "missing tier/vendor mapping in [model_tiers]."
                    ),
                )
            )
        return results

    # ── Actual agents in target dir ───────────────────────────────────────

    def _actual_agents(self, agents_dir: Path, known_prefixes: set[str]) -> dict[str, Path]:
        """Return ``{filename: full_path}`` for extension-owned copies in agents_dir.

        Only files whose name starts with ``<known_prefix>-`` are included.
        Entries that don't match any known extension prefix are outside this
        probe's jurisdiction and silently skipped (e.g. first-party workspace
        agents that have no ``-`` prefix).
        """
        if not self._fs.is_dir(agents_dir):
            return {}

        prefix_markers = {f"{p}-" for p in known_prefixes}
        result: dict[str, Path] = {}

        try:
            entries = self._fs.iterdir(agents_dir)
        except OSError:
            return result

        for entry in entries:
            if not self._fs.is_file(entry):
                continue
            if "-" not in entry.name:
                continue
            # Skip when no known extension prefixes exist (empty prefix_markers means
            # "nothing is extension-owned → skip all") or when the entry's name does
            # not start with any known extension prefix marker.
            if not prefix_markers or not any(entry.name.startswith(m) for m in prefix_markers):
                continue
            result[entry.name] = entry

        return result

    # ── Name uniqueness guard ─────────────────────────────────────────────

    def _probe_name_uniqueness(self, known_agents: list[KnownAgent]) -> list[ProbeResult]:
        """Check that canonical agent ``name`` values are unique across all extensions.

        Claude Code resolves agents by the ``name`` frontmatter field, not by
        filename.  When two extensions each ship an agent named ``explorer``,
        the second installed copy silently shadows the first.  This check
        reports a WARN finding listing every duplicate name and the extensions
        that claim it so the author can rename one agent to avoid the collision.
        """
        name_to_prefixes: dict[str, list[str]] = {}
        for known in known_agents:
            name_to_prefixes.setdefault(known.agent.name, []).append(known.prefix)

        collisions = {name: prefixes for name, prefixes in name_to_prefixes.items() if len(prefixes) > 1}
        if not collisions:
            return [
                ProbeResult(
                    source=AGENT_SOURCE,
                    name="agent names: uniqueness",
                    status=ProbeStatus.pass_,
                    message="all canonical agent names are unique across extensions",
                )
            ]

        issues = [
            f"name {name!r} claimed by: {', '.join(sorted(set(prefixes)))}"
            for name, prefixes in sorted(collisions.items())
        ]
        return [
            ProbeResult(
                source=AGENT_SOURCE,
                name="agent names: uniqueness",
                status=ProbeStatus.warn,
                message="; ".join(issues),
                remediation=("Rename the agent in one of the conflicting extensions to avoid Claude name collision."),
            )
        ]

    # ── Override target validation ────────────────────────────────────────

    def _probe_override_targets(self, known_agents: list[KnownAgent]) -> list[ProbeResult]:
        """Check that all ``[agent_model_overrides]`` entries target known agent names.

        Collects every canonical agent name across all qualifying extensions
        and reports a WARN for each override entry whose key does not match
        any known agent.  An override for an unknown name is almost always a
        typo and will silently have no effect — surfacing it here keeps the
        config honest.

        Returns an empty list when no overrides are configured so the probe
        output is clean for workspaces that don't use the feature.
        """
        overrides = self._config.agent_model_overrides.overrides
        if not overrides:
            return []

        known_names = {known.agent.name for known in known_agents}
        unknown = sorted(name for name in overrides if name not in known_names)
        if not unknown:
            return [
                ProbeResult(
                    source=AGENT_SOURCE,
                    name="agent model overrides: targets",
                    status=ProbeStatus.pass_,
                    message=f"all {len(overrides)} override(s) target known agents",
                )
            ]

        issues = [f"unknown agent {name!r}" for name in unknown]
        return [
            ProbeResult(
                source=AGENT_SOURCE,
                name="agent model overrides: targets",
                status=ProbeStatus.warn,
                message="; ".join(issues),
                remediation=(
                    "Remove or correct the [agent_model_overrides] entries in "
                    ".winter/config.toml or config.local.toml that reference "
                    "unknown agent names."
                ),
            )
        ]


__all__ = ["AGENT_SOURCE", "AgentProbeService"]
