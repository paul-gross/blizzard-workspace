from __future__ import annotations

from pathlib import Path
from typing import Protocol


class IWorkspaceLocator(Protocol):
    """Discovers the workspace root.

    The default implementation walks up from the current working directory
    looking for a `.winter/` marker, but the seam exists so tests can supply
    an explicit path without chdir-ing.
    """

    def find_workspace_root(self) -> Path: ...
