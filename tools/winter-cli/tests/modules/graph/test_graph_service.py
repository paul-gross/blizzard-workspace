from __future__ import annotations

from pathlib import Path

from tests.conftest import FakeConfigFileReader, FakeFilesystem
from winter_cli.modules.graph.graph_service import GraphService
from winter_cli.modules.workspace.extension_manifest import EXT_MANIFEST, ExtensionManifestLoader
from winter_cli.modules.workspace.models import StandaloneRepository

WS = Path("/ws")


class _StubRepoFactory:
    def __init__(self, repos: list[StandaloneRepository]) -> None:
        self._repos = repos

    def get_standalone_repos(self) -> list[StandaloneRepository]:
        return self._repos


def _service(
    repos: list[StandaloneRepository],
    files: dict[Path, str],
    config_files: dict[Path, dict],
    broken: set[Path] | None = None,
) -> GraphService:
    fs = FakeFilesystem(files=files, directories=set())
    loader = ExtensionManifestLoader(config_file_reader=FakeConfigFileReader(config_files, broken=broken))
    return GraphService(fs=fs, manifest_loader=loader, repo_factory=_StubRepoFactory(repos))


def test_builds_nodes_with_requires_edges() -> None:
    wf = StandaloneRepository(name="winter-workflow", path=WS / "winter-workflow")
    wh = StandaloneRepository(name="winter-harness", path=WS / "winter-harness")
    files = {wf.path / EXT_MANIFEST: "", wh.path / EXT_MANIFEST: ""}
    config_files = {
        wf.path / EXT_MANIFEST: {"requires": ["winter-product"]},
        wh.path / EXT_MANIFEST: {},
    }
    nodes = {n.name: n.requires for n in _service([wf, wh], files, config_files).build()}
    assert nodes == {"winter-workflow": ("winter-product",), "winter-harness": ()}


def test_skips_repo_without_manifest() -> None:
    repo = StandaloneRepository(name="winter-x", path=WS / "winter-x")
    assert _service([repo], files={}, config_files={}).build() == []


def test_skips_broken_manifest_rather_than_aborting() -> None:
    good = StandaloneRepository(name="winter-good", path=WS / "winter-good")
    bad = StandaloneRepository(name="winter-bad", path=WS / "winter-bad")
    files = {good.path / EXT_MANIFEST: "", bad.path / EXT_MANIFEST: ""}
    config_files = {good.path / EXT_MANIFEST: {"requires": ["winter-x"]}}
    svc = _service([good, bad], files, config_files, broken={bad.path / EXT_MANIFEST})
    nodes = {n.name: n.requires for n in svc.build()}
    assert nodes == {"winter-good": ("winter-x",)}
