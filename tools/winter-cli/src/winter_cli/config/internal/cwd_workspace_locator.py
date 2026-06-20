from __future__ import annotations

from pathlib import Path

from winter_cli.config.workspace import CONFIG_FILE, WINTER_DIR
from winter_cli.config.workspace_locator import IWorkspaceLocator
from winter_cli.core.config_file import ConfigError

# The workspace root is the directory holding `.winter/config.toml`. WINTER_DIR
# and CONFIG_FILE are imported from config.workspace so this rule is defined once
# (the `winter` wrapper script also encodes it independently, in bash).
ROOT_MARKER = f"{WINTER_DIR}/{CONFIG_FILE}"


class CwdWorkspaceLocator:
    """Finds the workspace root by walking up from `Path.cwd()` for a `.winter/config.toml`.

    The only IWorkspaceLocator adapter in production. Confines `Path.cwd()`
    to this file so service code never reaches the filesystem implicitly.

    The marker is `.winter/config.toml`, not a bare `.winter/` directory: per-env
    service logs live at `<env>/.winter/logs/`, so a bare-directory check would
    mistake any env that has run services for the workspace root.
    """

    def find_workspace_root(self) -> Path:
        current = Path.cwd()
        for directory in [current, *current.parents]:
            if (directory / WINTER_DIR / CONFIG_FILE).is_file():
                return directory
        raise ConfigError(
            f"Could not find workspace root from {current}. Expected to find a {ROOT_MARKER} in a parent."
        )


def _conforms_cwd_workspace_locator(x: CwdWorkspaceLocator) -> IWorkspaceLocator:
    return x
