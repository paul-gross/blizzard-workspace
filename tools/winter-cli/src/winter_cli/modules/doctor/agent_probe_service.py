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

import logging
from pathlib import Path

from winter_cli.config.models import AdoptExtensions, CodeAgentVendor, WorkspaceConfig
from winter_cli.core.filesystem import IFilesystemReader
from winter_cli.modules.doctor.models import ProbeResult, ProbeStatus
from winter_cli.modules.workspace.agent_transform.registry import PARSER, RENDERERS
from winter_cli.modules.workspace.extension_manifest import (
    EXT_MANIFEST,
    ExtensionManifestLoader,
)
from winter_cli.modules.workspace.models import RepoError, StandaloneRepository

logger = logging.getLogger(__name__)

AGENT_SOURCE = "agents"


def _noop_warn(field: str, agent_name: str, vendor_label: str) -> None:
    """No-op warn callback used in the probe's render path.

    The probe never needs to surface lossy-field warnings — it only compares
    output bytes to detect staleness, and the renderer's output is independent
    of whether a warning was emitted.
    """


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

    Renderers and the parser are taken from ``agent_transform.RENDERERS`` and
    ``agent_transform.PARSER`` — the same instances used by
    ``ExtensionAgentService`` — so "stale" here is defined identically to what
    the installer would write.

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
    ) -> None:
        self._config = config
        self._fs = fs
        self._manifest_loader = manifest_loader

    def run(self, standalone_repos: list[StandaloneRepository]) -> list[ProbeResult]:
        if self._config.adopt_extensions == AdoptExtensions.none:
            return []

        results: list[ProbeResult] = []
        for vendor in CodeAgentVendor:
            results.extend(self._probe_vendor(vendor, standalone_repos))
        results.extend(self._probe_name_uniqueness(standalone_repos))
        return results

    # ── Per-vendor probe ──────────────────────────────────────────────────

    def _probe_vendor(self, vendor: CodeAgentVendor, standalone_repos: list[StandaloneRepository]) -> list[ProbeResult]:
        """Check all extensions for one vendor and emit one probe result."""
        agents_dir = self._config.workspace_root / vendor.agents_subpath

        # Build the full expected set: filename → expected bytes.
        expected: dict[str, bytes] = {}
        known_prefixes: set[str] = set()
        for repo in standalone_repos:
            ext_expected, prefix = self._expected_agents_with_prefix(repo, vendor)
            expected.update(ext_expected)
            if prefix is not None:
                known_prefixes.add(prefix)

        # Collect the actual set of <prefix>-* files scoped to known extension prefixes.
        actual: dict[str, Path] = self._actual_agents(agents_dir, known_prefixes)

        issues: list[str] = []

        # Check for orphans (in actual but not in expected) and stale copies.
        for name, actual_path in sorted(actual.items()):
            if name not in expected:
                issues.append(f"orphaned copy: {name} (no live canonical source)")
                continue
            issue = self._check_copy(name, actual_path, expected[name])
            if issue:
                issues.append(issue)

        # Check for missing copies (in expected but not in actual).
        for name in sorted(expected):
            if name not in actual:
                issues.append(f"missing copy: {name} (canonical source exists, copy absent)")

        label = f"agent copies: {vendor.value}"
        if issues:
            return [
                ProbeResult(
                    source=AGENT_SOURCE,
                    name=label,
                    status=ProbeStatus.warn,
                    message="; ".join(issues),
                    remediation="Run `winter ws init` to sync agent copies.",
                )
            ]
        n_agents = len(expected)
        return [
            ProbeResult(
                source=AGENT_SOURCE,
                name=label,
                status=ProbeStatus.pass_,
                message=f"{n_agents} agent(s) in sync",
            )
        ]

    # ── Expected agents from extensions ──────────────────────────────────

    def _expected_agents_with_prefix(
        self, repo: StandaloneRepository, vendor: CodeAgentVendor
    ) -> tuple[dict[str, bytes], str | None]:
        """Return ``({filename: expected_bytes}, prefix)`` for one extension + vendor.

        Returns ``({}, None)`` when the extension doesn't qualify (no manifest
        in winter mode, no agents dir, manifest load error). Mirrors the
        permissive approach of the install path.
        """
        mode = self._config.adopt_extensions
        manifest_path = repo.path / EXT_MANIFEST
        manifest_present = self._fs.is_file(manifest_path)

        if mode == AdoptExtensions.winter and not manifest_present:
            return {}, None

        try:
            manifest = self._manifest_loader.load(repo, manifest_path if manifest_present else None)
        except RepoError:
            return {}, None

        agents_root = self._resolve_existing_dir(repo.path, manifest.agents_dirs)
        if agents_root is None:
            return {}, manifest.prefix

        prefix = manifest.prefix
        renderer = RENDERERS[vendor.agent_format]
        result: dict[str, bytes] = {}

        try:
            entries = self._fs.iterdir(agents_root)
        except OSError:
            return {}, prefix

        for entry in sorted(entries):
            if not self._fs.is_file(entry):
                continue
            if not entry.name.endswith(".md"):
                continue
            if entry.name == "README.md":
                continue

            try:
                text = self._fs.read_text(entry)
                agent = PARSER.parse(text)
            except RepoError as exc:
                logger.warning(
                    "agent probe: %s — parse error for %s: %s",
                    repo.name,
                    entry.name,
                    exc,
                )
                continue

            rendered = renderer.render(agent, warn=_noop_warn)
            filename = f"{prefix}-{rendered.filename_stem}{rendered.suffix}"
            result[filename] = rendered.text.encode("utf-8")

        return result, prefix

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

    # ── Per-copy health check ─────────────────────────────────────────────

    def _check_copy(self, name: str, actual_path: Path, expected_bytes: bytes) -> str | None:
        """Return an issue description for one copy, or None if healthy.

        The byte comparison uses the same logic as ``ExtensionAgentService._sync_file``
        so "stale" in the probe means exactly "would be overwritten by the next
        ``winter ws init`` run".
        """
        if not self._fs.is_file(actual_path):
            return f"missing copy: {name}"
        try:
            actual_bytes = self._fs.read_bytes(actual_path)
        except OSError:
            return f"missing copy: {name} (read error)"
        if actual_bytes != expected_bytes:
            return f"stale copy: {name} (transform mismatch)"
        return None

    # ── Name uniqueness guard ─────────────────────────────────────────────

    def _probe_name_uniqueness(self, standalone_repos: list[StandaloneRepository]) -> list[ProbeResult]:
        """Check that canonical agent ``name`` values are unique across all extensions.

        Claude Code resolves agents by the ``name`` frontmatter field, not by
        filename.  When two extensions each ship an agent named ``explorer``,
        the second installed copy silently shadows the first.  This check
        reports a WARN finding listing every duplicate name and the extensions
        that claim it so the author can rename one agent to avoid the collision.
        """
        mode = self._config.adopt_extensions
        name_to_prefixes: dict[str, list[str]] = {}

        for repo in standalone_repos:
            manifest_path = repo.path / EXT_MANIFEST
            manifest_present = self._fs.is_file(manifest_path)
            if mode == AdoptExtensions.winter and not manifest_present:
                continue
            try:
                manifest = self._manifest_loader.load(repo, manifest_path if manifest_present else None)
            except RepoError:
                continue

            agents_root = self._resolve_existing_dir(repo.path, manifest.agents_dirs)
            if agents_root is None:
                continue

            try:
                entries = self._fs.iterdir(agents_root)
            except OSError:
                continue

            for entry in sorted(entries):
                if not self._fs.is_file(entry):
                    continue
                if not entry.name.endswith(".md"):
                    continue
                if entry.name == "README.md":
                    continue
                try:
                    text = self._fs.read_text(entry)
                    agent = PARSER.parse(text)
                except RepoError:
                    continue
                name_to_prefixes.setdefault(agent.name, []).append(manifest.prefix)

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

    # ── Helpers ───────────────────────────────────────────────────────────

    def _resolve_existing_dir(self, base: Path, candidates: tuple[str, ...]) -> Path | None:
        """Return the first candidate path under ``base`` that exists as a directory."""
        for candidate in candidates:
            path = base / candidate
            if self._fs.is_dir(path):
                return path
        return None


__all__ = ["AGENT_SOURCE", "AgentProbeService"]
