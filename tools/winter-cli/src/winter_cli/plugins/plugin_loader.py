from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Protocol


class IPluginLoader(Protocol):
    """Imports a winter plugin module from an arbitrary path on disk.

    The default implementation uses `importlib.util.spec_from_file_location`
    so plugins can live outside the package's import path. The seam exists
    so the registry's test can substitute a fake module without exercising
    real importlib.
    """

    def load(self, name: str, entry_point: Path) -> ModuleType: ...
