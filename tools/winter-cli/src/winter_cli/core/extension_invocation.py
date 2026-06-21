"""Shared builder for the extension subprocess environment.

``build_extension_env`` is the single point that sets the four base winter
extension context variables on every dispatch — service, doctor, lint, hooks,
and ext-verify all call it.  Callers merge additional action-specific vars on
top of the returned dict.
"""

from __future__ import annotations

import os
from pathlib import Path


def build_extension_env(
    *,
    workspace_root: Path,
    ext_dir: Path,
    prefix: str,
    config_dir: Path,
    base: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return a new env dict with the four winter base extension context vars set.

    Starts from ``base`` (defaults to ``os.environ``) and overlays the four
    always-present variables.  Callers receive a fresh copy — the original is
    never mutated.

    ``workspace_root``  absolute path to the workspace root.
    ``ext_dir``         absolute path to the extension's on-disk root (the dir
                        containing ``winter-ext.toml``).
    ``prefix``          resolved symlink prefix for this extension.
    ``config_dir``      absolute path to this extension's writable config/asset
                        directory (default ``<ws>/.winter/config/<name>/``).
    ``base``            optional starting dict; when None, ``os.environ`` is used.
    """
    merged = dict(base if base is not None else os.environ)
    merged["WINTER_WORKSPACE_DIR"] = str(workspace_root)
    merged["WINTER_EXT_DIR"] = str(ext_dir)
    merged["WINTER_EXT_PREFIX"] = prefix
    merged["WINTER_EXT_CONFIG_DIR"] = str(config_dir)
    return merged
