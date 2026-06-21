"""Shared helpers for provider invocation: env-dict construction and pattern matching.

``build_provider_env`` builds the WINTER_* environment dict for any provider
subprocess call, merging the current process environment with the four
base extension context variables (including ``WINTER_EXT_CONFIG_DIR``).

``service_matches_pattern`` is the segment-aware fnmatch check used by
``restart`` and ``logs`` routing to decide whether a known service name
matches a user-supplied selection pattern.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from winter_cli.core.extension_invocation import build_extension_env


def build_provider_env(provider: Any, workspace_root: Path) -> dict[str, str]:
    """Return a copy of os.environ with WINTER_WORKSPACE_DIR/EXT_DIR/EXT_PREFIX/EXT_CONFIG_DIR set.

    ``provider`` must expose ``ext_dir: Path``, ``prefix: str``, and
    ``config_dir: Path``; compatible with both ``ResolvedCapability`` and
    ``ResolvedOrchestrator``.
    """
    return build_extension_env(
        workspace_root=workspace_root,
        ext_dir=provider.ext_dir,
        prefix=provider.prefix,
        config_dir=provider.config_dir,
    )


def service_matches_pattern(svc_name: str, pattern: str) -> bool:
    """Return True when ``svc_name`` matches ``pattern``.

    Handles two forms:
    - Two-segment ``<env>/<svc>`` pattern: only the svc segment is matched
      against ``svc_name`` (the env segment is used for env-scoping at the
      provider level — see dispatch routing).
    - Bare pattern (no ``/``): matched directly against ``svc_name`` via fnmatch.
    """
    if "/" in pattern:
        _env_seg, svc_seg = pattern.split("/", 1)
        return fnmatch.fnmatchcase(svc_name, svc_seg)
    return fnmatch.fnmatchcase(svc_name, pattern)
