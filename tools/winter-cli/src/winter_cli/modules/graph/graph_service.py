from __future__ import annotations

import logging

from winter_cli.core.filesystem import IFilesystemReader
from winter_cli.modules.graph.models import ModuleNode
from winter_cli.modules.workspace.extension_manifest import EXT_MANIFEST, ExtensionManifestLoader
from winter_cli.modules.workspace.models import RepoError
from winter_cli.modules.workspace.repository_factory import IStandaloneRepoProvider

logger = logging.getLogger(__name__)


class GraphService:
    """Builds the module dependency graph from each module's `requires`.

    Enumerates every standalone repo (the installed extension modules) that
    ships a `winter-ext.toml` and records its declared `requires`. Pure data
    aggregation: it reports the edges and applies no rules — `winter lint`
    checks (e.g. the extractability linter) consume this graph and decide what
    is allowed. A module whose manifest can't be read is skipped with a log
    line rather than aborting the whole graph.
    """

    def __init__(
        self,
        fs: IFilesystemReader,
        manifest_loader: ExtensionManifestLoader,
        repo_factory: IStandaloneRepoProvider,
    ) -> None:
        self._fs = fs
        self._manifest_loader = manifest_loader
        self._repo_factory = repo_factory

    def build(self) -> list[ModuleNode]:
        nodes: list[ModuleNode] = []
        for repo in self._repo_factory.get_standalone_repos():
            manifest_path = repo.path / EXT_MANIFEST
            if not self._fs.is_file(manifest_path):
                continue
            try:
                manifest = self._manifest_loader.load(repo, manifest_path)
            except RepoError as exc:
                logger.warning("skipping %s in dependency graph — %s", repo.name, exc)
                continue
            nodes.append(ModuleNode(name=repo.name, requires=manifest.requires))
        return nodes
