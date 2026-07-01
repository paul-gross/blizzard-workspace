"""Shared canonical-agent file discovery for the agent transform layer.

Both ``ExtensionAgentService`` (the installer) and ``AgentProbeService`` (the
staleness probe) need to walk "every candidate canonical agent file across
qualifying extension repos" — manifest load, agents-dir resolution, flat
``*.md`` listing minus ``README.md``. Three call sites used to hand-copy this
walk; copies drift. ``CanonicalAgentEnumerator`` is the single source for the
mechanical listing (``resolve_agents_dir`` + ``iter_candidate_agent_files``)
and, for the two call sites with identical silent-skip-on-error semantics,
the full parse-and-yield walk (``iter_known_agents``).

``ExtensionAgentService.process`` does NOT use ``iter_known_agents``: a
manifest load failure there is a hard per-repo failure (reported via
``reporter.repo_error`` and a ``False`` return), not a silent skip, so it
keeps its own manifest-loading and parse-error handling and uses only the
mechanical listing methods below.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from winter_cli.config.models import AdoptExtensions
from winter_cli.core.filesystem import IFilesystemReader
from winter_cli.modules.workspace.agent_transform.canonical_parser import CanonicalAgentParser
from winter_cli.modules.workspace.agent_transform.models import CanonicalAgent
from winter_cli.modules.workspace.agent_transform.registry import PARSER
from winter_cli.modules.workspace.extension_manifest import EXT_MANIFEST, ExtensionManifestLoader
from winter_cli.modules.workspace.models import RepoError, StandaloneRepository


class CanonicalAgentEnumerator:
    """Discovers canonical agent files across qualifying extension repos.

    Collaborators (``fs``, ``manifest_loader``) are injected at construction.
    ``parser`` defaults to the shared ``agent_transform.PARSER`` singleton —
    the same instance ``ExtensionAgentService`` and ``AgentProbeService``
    already use directly for rendering, so a parsed ``CanonicalAgent`` means
    the same thing everywhere in this pipeline.
    """

    def __init__(
        self,
        fs: IFilesystemReader,
        manifest_loader: ExtensionManifestLoader,
        parser: CanonicalAgentParser = PARSER,
    ) -> None:
        self._fs = fs
        self._manifest_loader = manifest_loader
        self._parser = parser

    def resolve_agents_dir(self, base: Path, candidates: tuple[str, ...]) -> Path | None:
        """Return the first ``candidates`` entry under ``base`` that exists as a directory."""
        for candidate in candidates:
            path = base / candidate
            if self._fs.is_dir(path):
                return path
        return None

    def iter_candidate_agent_files(self, agents_root: Path) -> Iterator[Path]:
        """Yield candidate canonical-agent file paths in ``agents_root``.

        Flat (non-recursive) ``*.md`` files, excluding ``README.md``, in
        sorted order. An unreadable directory yields nothing rather than
        raising.
        """
        try:
            entries = self._fs.iterdir(agents_root)
        except OSError:
            return
        for entry in sorted(entries):
            if not self._fs.is_file(entry):
                continue
            if not entry.name.endswith(".md"):
                continue
            if entry.name == "README.md":
                continue
            yield entry

    def iter_known_agents(
        self,
        repos: list[StandaloneRepository],
        *,
        mode: AdoptExtensions,
    ) -> Iterator[tuple[str, CanonicalAgent]]:
        """Yield ``(prefix, agent)`` for every qualifying, successfully-parsed canonical agent.

        Silent-skip on any failure — no manifest (in ``winter`` mode), no
        agents dir, a manifest that fails to load, or a file that fails to
        parse. This is the "which agents does this workspace consider known
        and installed" definition shared by the doctor's
        name-uniqueness/override-target probes and the installer's
        unknown-override warning, so the two can never disagree about the
        answer.
        """
        for repo in repos:
            manifest_path = repo.path / EXT_MANIFEST
            manifest_present = self._fs.is_file(manifest_path)
            if mode == AdoptExtensions.winter and not manifest_present:
                continue
            try:
                manifest = self._manifest_loader.load(repo, manifest_path if manifest_present else None)
            except RepoError:
                continue
            agents_root = self.resolve_agents_dir(repo.path, manifest.agents_dirs)
            if agents_root is None:
                continue
            for entry in self.iter_candidate_agent_files(agents_root):
                try:
                    text = self._fs.read_text(entry)
                    agent = self._parser.parse(text, default_name=entry.stem)
                except RepoError:
                    continue
                yield manifest.prefix, agent
