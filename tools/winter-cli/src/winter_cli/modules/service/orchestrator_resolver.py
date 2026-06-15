from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from winter_cli.core.filesystem import IFilesystemReader
from winter_cli.modules.workspace.extension_manifest import EXT_MANIFEST, ExtensionManifestLoader
from winter_cli.modules.workspace.models import RepoError, StandaloneRepository
from winter_cli.modules.workspace.repository_factory import IStandaloneRepoProvider


@dataclass(frozen=True)
class ResolvedOrchestrator:
    entrypoint: Path
    ext_dir: Path
    prefix: str


class ServiceOrchestratorResolver:
    """Resolves the registered service orchestrator entrypoint.

    The four resolution failures each raise a distinct `RepoError`:
      1. no `service_orchestrator` registered in `.winter/config.toml`,
      2. the configured name matches no installed extension,
      3. the matched extension declares no `orchestrate_services` entrypoint,
      4. the declared entrypoint file is missing on disk.

    When `override` is supplied (from `--service-orchestrator` or
    `WINTER_SERVICE_ORCHESTRATOR`), it takes precedence over the config value and
    is interpreted as a **local path** when it contains an `os.sep` or resolves to
    an existing directory on disk — skipping failures 1 and 2. A bare name with no
    path separator falls through to the normal registered-extension lookup.

    Shared by `ServiceDispatchService` and `ServiceLogsService` so orchestrator
    resolution logic is not duplicated.
    """

    def __init__(
        self,
        service_orchestrator: str | None,
        repo_factory: IStandaloneRepoProvider,
        manifest_loader: ExtensionManifestLoader,
        fs: IFilesystemReader,
        override: str | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self._service_orchestrator = service_orchestrator
        self._repo_factory = repo_factory
        self._manifest_loader = manifest_loader
        self._fs = fs
        self._override = override
        self._workspace_root = workspace_root

    def resolve(self) -> ResolvedOrchestrator:
        """Return the resolved entrypoint path or raise RepoError."""
        effective = self._override or self._service_orchestrator
        if not effective:
            raise RepoError(
                "no service orchestrator registered — set "
                '`service_orchestrator = "<extension-name>"` in .winter/config.toml '
                "(it must name an installed extension that declares an `orchestrate_services` "
                "entrypoint in its winter-ext.toml)."
            )

        if self._is_path(effective):
            return self._resolve_path(effective)

        return self._resolve_name(effective)

    def _is_path(self, value: str) -> bool:
        """Return True when `value` should be treated as a local extension path.

        A value is a path when it contains a path separator OR when it
        resolves to an existing directory.  A bare registered name (e.g.
        `winter-service-tmux`) never contains a separator and does not resolve
        to a directory in the workspace, so it falls through to name-mode.
        """
        if "/" in value or os.sep in value:
            return True
        return self._fs.is_dir(Path(value))

    def _resolve_path(self, value: str) -> ResolvedOrchestrator:
        """Path mode: treat `value` as a local extension directory, skipping the
        registered-extension lookup (failures 1 and 2).  Failures 3 and 4 still
        apply so a misconfigured local checkout surfaces a clear error.
        """
        ext_dir = Path(value)
        if not ext_dir.is_absolute():
            # workspace_root is always injected from config in production;
            # Path.cwd() is a test/standalone-only fallback.
            base = self._workspace_root if self._workspace_root is not None else Path.cwd()
            ext_dir = base / ext_dir
        ext_dir = ext_dir.resolve()

        if not self._fs.is_dir(ext_dir):
            raise RepoError(f"service orchestrator override {value!r} not found — {ext_dir} is not a directory.")

        manifest_path = ext_dir / EXT_MANIFEST
        if not self._fs.is_file(manifest_path):
            raise RepoError(
                f"service orchestrator override {value!r} has no {EXT_MANIFEST} — expected at {manifest_path}."
            )

        # Build a synthetic StandaloneRepository so the manifest loader's
        # prefix-resolution logic (workspace override → manifest prefix → name → dir)
        # works the same way as for registered extensions.
        synthetic_repo = StandaloneRepository(name=ext_dir.name, path=ext_dir)
        manifest = self._manifest_loader.load(synthetic_repo, manifest_path)

        if not manifest.orchestrate_services:
            raise RepoError(
                f"service orchestrator override {value!r} declares no `orchestrate_services` entrypoint — "
                f'add `orchestrate_services = "<path>"` to {manifest_path}.'
            )

        entrypoint = ext_dir / manifest.orchestrate_services
        if not self._fs.is_file(entrypoint):
            raise RepoError(
                f"service orchestrator override {value!r} entrypoint not found at {entrypoint} "
                f'(declared as `orchestrate_services = "{manifest.orchestrate_services}"` in {manifest_path}).'
            )

        return ResolvedOrchestrator(entrypoint=entrypoint, ext_dir=ext_dir, prefix=manifest.prefix)

    def _resolve_name(self, name: str) -> ResolvedOrchestrator:
        """Name mode: look up a registered installed extension — the original behavior."""
        repo = self._find_extension(name)
        if repo is None:
            raise RepoError(
                f"service orchestrator {name!r} is not an installed extension — "
                "`service_orchestrator` must match the name of a "
                "[[standalone_repository]] in .winter/config.toml."
            )

        manifest = self._manifest_loader.load(repo, repo.path / EXT_MANIFEST)
        if not manifest.orchestrate_services:
            raise RepoError(
                f"service orchestrator {name!r} declares no `orchestrate_services` entrypoint — "
                f'add `orchestrate_services = "<path>"` to {repo.path / EXT_MANIFEST}.'
            )

        entrypoint = repo.path / manifest.orchestrate_services
        if not self._fs.is_file(entrypoint):
            manifest_path = repo.path / EXT_MANIFEST
            raise RepoError(
                f"service orchestrator {name!r} entrypoint not found at {entrypoint} "
                f'(declared as `orchestrate_services = "{manifest.orchestrate_services}"` in {manifest_path}).'
            )
        return ResolvedOrchestrator(entrypoint=entrypoint, ext_dir=repo.path, prefix=manifest.prefix)

    def _find_extension(self, name: str) -> StandaloneRepository | None:
        for repo in self._repo_factory.get_standalone_repos():
            if repo.name == name:
                return repo
        return None
