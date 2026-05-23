from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ConfigFileReadError(Exception):
    """Raised when a config file cannot be parsed (decode error or read failure).

    Distinguished from "file not present" so callers can decide: a missing file
    is often a no-op, a malformed file is a configuration bug.
    """


class IConfigFileReader(Protocol):
    """Parses a config file from disk into a plain dict.

    Today's only implementation is TOML (via stdlib `tomllib`). The Protocol
    deliberately doesn't mention TOML in its name — callers shouldn't care
    what format the file is in, only that they get back a `dict`.
    """

    def load(self, path: Path) -> dict: ...
