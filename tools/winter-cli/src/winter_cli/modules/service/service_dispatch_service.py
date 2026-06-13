from __future__ import annotations

import os
from pathlib import Path

from winter_cli.core.subprocess_runner import ISubprocessRunner
from winter_cli.modules.service.orchestrator_resolver import ServiceOrchestratorResolver


class ServiceDispatchService:
    """Dispatches up/down/status/restart to the registered service orchestrator.

    Each action is invoked as exactly `<entrypoint> <action> <env>` (argv),
    with `cwd` at the workspace root. Every dispatch exports `WINTER_WORKSPACE_DIR`,
    `WINTER_EXT_DIR`, and `WINTER_EXT_PREFIX` (matching the doctor/lint/hook
    dispatches). Action-specific context is conveyed via further env vars:
      - restart: `WINTER_SERVICE_NAME=<service>` (required; the service to bounce)

    The entrypoint's exit code is returned unmodified; stdout/stderr are
    inherited from the parent process (no capture).
    """

    def __init__(
        self,
        subprocess_runner: ISubprocessRunner,
        orchestrator_resolver: ServiceOrchestratorResolver,
        workspace_root: Path,
    ) -> None:
        self._subprocess_runner = subprocess_runner
        self._orchestrator_resolver = orchestrator_resolver
        self._workspace_root = workspace_root

    def dispatch(self, action: str, env: str, extra_env: dict[str, str] | None = None) -> int:
        """Run the orchestrator's entrypoint and return its exit code unmodified."""
        resolved = self._orchestrator_resolver.resolve()
        cmd = [str(resolved.entrypoint), action, env]
        merged = os.environ.copy()
        if extra_env:
            merged.update(extra_env)
        merged["WINTER_WORKSPACE_DIR"] = str(self._workspace_root)
        merged["WINTER_EXT_DIR"] = str(resolved.ext_dir)
        merged["WINTER_EXT_PREFIX"] = resolved.prefix
        return self._subprocess_runner.call(cmd, cwd=self._workspace_root, env=merged)
