from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from winter_cli.config.models import WorkspaceConfig
from winter_cli.core.extension_invocation import build_extension_env
from winter_cli.core.filesystem import IFilesystemWriter
from winter_cli.core.subprocess_runner import ISubprocessRunner
from winter_cli.modules.provision.manifest import ProvisionHandler, ProvisionScope
from winter_cli.modules.workspace.env_index import build_env_trio
from winter_cli.modules.workspace.env_index_registry import IEnvIndexRegistry
from winter_cli.modules.workspace.extension_manifest import EXT_MANIFEST, ExtensionManifestLoader
from winter_cli.modules.workspace.models import RepoError, StandaloneRepository
from winter_cli.modules.workspace.repository_factory import RepositoryFactory

logger = logging.getLogger(__name__)

# The source label used for workspace-config (project) handlers.
# Mirrors WORKSPACE_SOURCE used in lint/doctor services.
PROJECT_SOURCE = "project"


class IProvisionOutputSink(Protocol):
    """Minimal output sink for provision script execution.

    Phase 4 wraps this with its own Stream/Json reporter pair.  Services that
    only need execution (no JSON/stream distinction) inject any object that
    satisfies this Protocol.
    """

    def execution_started(self, label: str, action: str, cwd: Path) -> None:
        """Called immediately before a script subprocess is launched."""
        ...

    def execution_output_line(self, label: str, line: str) -> None:
        """Called for each line of stdout/stderr emitted by the script."""
        ...

    def execution_completed(self, label: str, action: str, exit_code: int) -> None:
        """Called after the subprocess exits."""
        ...

    def execution_error(self, label: str, error: str) -> None:
        """Called when the script cannot be launched (path-escape, missing, not-executable)."""
        ...


@dataclass(frozen=True)
class SingleRunResult:
    """Result for one concrete script invocation (one cwd, one process)."""

    cwd: Path
    exit_code: int


@dataclass(frozen=True)
class HandlerExecutionResult:
    """Aggregated result for running one ``ProvisionHandler`` action.

    For ``workspace`` and ``feature-environment`` scope a single run is
    produced.  For ``feature-worktree`` scope one ``SingleRunResult`` per
    project worktree appears in ``runs``, in the order the worktrees were
    visited.

    ``ok`` is True when every run exited 0 (or when ``runs`` is empty).
    """

    handler: ProvisionHandler
    action: str
    runs: tuple[SingleRunResult, ...] = field(default_factory=tuple)
    error: str | None = None

    @property
    def ok(self) -> bool:
        if self.error:
            return False
        return all(r.exit_code == 0 for r in self.runs)


class ProvisionExecutionService:
    """Runs a single ``ProvisionHandler`` action at the correct cwd with the
    correct env vars.

    Execution only — no ordering, no chain composition, no service check.
    Those live in Phase 4's ``ProvisionService``.

    Source-root / cwd rules:
    - ``project`` source → source root = workspace root; for workspace scope
      there is no ``WINTER_EXT_DIR`` / ``WINTER_EXT_PREFIX`` (workspace-config
      handlers are not extensions), so we pass ``ext_dir=workspace_root`` and
      ``prefix=PROJECT_SOURCE`` as neutral values that still satisfy
      ``build_extension_env``'s signature.
    - Extension source → source root = the extension repo's on-disk path;
      ``WINTER_EXT_DIR`` / ``WINTER_EXT_PREFIX`` / ``WINTER_EXT_CONFIG_DIR``
      are set from that repo's manifest, mirroring the hook service.

    cwd is set by scope, independently of the source root:
    - ``workspace``          → workspace root
    - ``feature-environment`` → ``<workspace_root>/<env>``
    - ``feature-worktree``   → ``<workspace_root>/<env>/<repo.name>`` per project repo
    """

    def __init__(
        self,
        config: WorkspaceConfig,
        fs: IFilesystemWriter,
        subprocess_runner: ISubprocessRunner,
        manifest_loader: ExtensionManifestLoader,
        repo_factory: RepositoryFactory,
        registry: IEnvIndexRegistry | None = None,
    ) -> None:
        self._config = config
        self._fs = fs
        self._subprocess = subprocess_runner
        self._manifest_loader = manifest_loader
        self._repo_factory = repo_factory
        self._registry = registry

    def run_handler(
        self,
        handler: ProvisionHandler,
        action: str,
        env_name: str,
        sink: IProvisionOutputSink,
    ) -> HandlerExecutionResult:
        """Run one handler's named action and return a structured result.

        ``action`` must be one of ``"apply"``, ``"destroy"``, or ``"reset"``.
        The caller is responsible for ensuring the action's script is non-None
        on the handler before calling here (Phase 4 owns the
        decompose/warn/degrade decisions).

        Returns ``HandlerExecutionResult`` with all per-run outcomes.  A
        path-escape or missing/non-executable script is captured in
        ``HandlerExecutionResult.error`` and ``ok=False``; no exception is
        raised.
        """
        script_rel = self._resolve_script(handler, action)
        if script_rel is None:
            error = f"handler for {handler.subtarget!r} has no script for action {action!r}"
            logger.warning("%s", error)
            return HandlerExecutionResult(handler=handler, action=action, error=error)

        try:
            source_root, base_env = self._resolve_source(handler)
        except RepoError as exc:
            error = str(exc)
            sink.execution_error(_handler_label(handler), error)
            return HandlerExecutionResult(handler=handler, action=action, error=error)

        try:
            script_path = self._validate_script(script_rel, source_root)
        except RepoError as exc:
            error = str(exc)
            sink.execution_error(_handler_label(handler), error)
            return HandlerExecutionResult(handler=handler, action=action, error=error)

        cwds = self._resolve_cwds(handler.scope, env_name)
        runs: list[SingleRunResult] = []

        for cwd in cwds:
            env = dict(base_env)
            if handler.scope in (
                ProvisionScope.feature_environment,
                ProvisionScope.feature_worktree,
            ):
                env.update(build_env_trio(env_name, self._config, self._registry))

            label = _handler_label(handler)
            sink.execution_started(label, action, cwd)
            try:
                with self._subprocess.popen([str(script_path)], cwd=cwd, env=env) as proc:
                    for line in proc.stdout_lines:
                        sink.execution_output_line(label, line)
                    exit_code = proc.wait()
            except OSError as exc:
                error = f"provision script {script_rel!r} — {exc}"
                sink.execution_error(label, error)
                return HandlerExecutionResult(handler=handler, action=action, runs=tuple(runs), error=error)
            sink.execution_completed(label, action, exit_code)
            runs.append(SingleRunResult(cwd=cwd, exit_code=exit_code))

        return HandlerExecutionResult(handler=handler, action=action, runs=tuple(runs))

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _resolve_script(handler: ProvisionHandler, action: str) -> str | None:
        """Return the script path string for the named action, or None."""
        if action == "apply":
            return handler.apply
        if action == "destroy":
            return handler.destroy
        if action == "reset":
            return handler.reset
        raise ValueError(f"unknown action {action!r}; must be 'apply', 'destroy', or 'reset'")

    def _resolve_source(
        self,
        handler: ProvisionHandler,
    ) -> tuple[Path, dict[str, str]]:
        """Return ``(source_root, base_env)`` for this handler.

        For project-source handlers the source root is the workspace root.
        For extension handlers the source root is the extension repo directory.
        ``base_env`` always contains the four ``build_extension_env`` vars.
        """
        workspace_root = self._config.workspace_root

        if handler.source == PROJECT_SOURCE:
            # Workspace-config handler: script lives in the workspace root.
            # No real WINTER_EXT_DIR / WINTER_EXT_PREFIX semantics, but we
            # still call build_extension_env so downstream scripts see the
            # four standard vars (WINTER_WORKSPACE_DIR is the useful one here).
            config_dir = workspace_root / ".winter" / "config"
            base_env = build_extension_env(
                workspace_root=workspace_root,
                ext_dir=workspace_root,
                prefix=PROJECT_SOURCE,
                config_dir=config_dir,
            )
            return workspace_root, base_env

        # Extension handler: resolve the standalone repo by its source label (prefix).
        ext_repo = self._find_extension(handler.source)
        if ext_repo is None:
            raise RepoError(
                f"provision handler declares source={handler.source!r} but no installed "
                f"extension with that prefix was found"
            )

        manifest_path = ext_repo.path / EXT_MANIFEST
        if not self._fs.is_file(manifest_path):
            raise RepoError(
                f"extension {handler.source!r}: {EXT_MANIFEST} not found at {manifest_path}"
            )
        manifest = self._manifest_loader.load(ext_repo, manifest_path)

        config_dir = (
            ext_repo.config_dir
            if ext_repo.config_dir is not None
            else (workspace_root / ".winter" / "config" / ext_repo.name)
        )
        base_env = build_extension_env(
            workspace_root=workspace_root,
            ext_dir=ext_repo.path,
            prefix=manifest.prefix,
            config_dir=config_dir,
        )
        return ext_repo.path, base_env

    def _find_extension(self, source_label: str) -> StandaloneRepository | None:
        """Find a standalone repo whose resolved prefix matches *source_label*."""
        for repo in self._repo_factory.get_standalone_repos():
            manifest_path = repo.path / EXT_MANIFEST
            if not self._fs.is_file(manifest_path):
                continue
            try:
                manifest = self._manifest_loader.load(repo, manifest_path)
                if manifest.prefix == source_label:
                    return repo
            except RepoError:
                continue
        return None

    def _validate_script(self, script_rel: str, source_root: Path) -> Path:
        """Resolve and validate a script path relative to *source_root*.

        Raises ``RepoError`` if the path escapes the source root, is missing,
        or is not executable — mirroring the hook service's guard.
        """
        script_path = (source_root / script_rel).resolve()
        try:
            script_path.relative_to(source_root.resolve())
        except ValueError as exc:
            raise RepoError(
                f"provision script path {script_rel!r} escapes the source directory; refusing to run"
            ) from exc
        if not self._fs.is_file(script_path):
            raise RepoError(f"provision script {script_rel!r} not found at {script_path}")
        if not self._fs.access_x_ok(script_path):
            raise RepoError(f"provision script {script_rel!r} is not executable")
        return script_path

    def _resolve_cwds(self, scope: ProvisionScope, env_name: str) -> list[Path]:
        """Return the list of cwds to run the script in for the given scope."""
        workspace_root = self._config.workspace_root
        if scope is ProvisionScope.workspace:
            return [workspace_root]
        if scope is ProvisionScope.feature_environment:
            return [workspace_root / env_name]
        # feature-worktree: one cwd per project repo in the env
        return [
            workspace_root / env_name / repo.name
            for repo in self._repo_factory.get_project_repos()
        ]


def _handler_label(handler: ProvisionHandler) -> str:
    """Human-readable label for a handler, used in reporter calls."""
    return f"{handler.source}/{handler.subtarget}[{handler.scope.value}]"
