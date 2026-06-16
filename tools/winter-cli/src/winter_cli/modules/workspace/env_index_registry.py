from __future__ import annotations

from typing import Protocol


class IEnvIndexRegistry(Protocol):
    """Read/write registry mapping env name → assigned index.

    Backed by `.winter/state.toml` (machine-local, gitignored). A missing
    file is treated as an empty registry — callers do not distinguish
    "never written" from "empty".
    """

    def get_index(self, name: str) -> int | None:
        """Return the recorded index for *name*, or ``None`` if not assigned."""
        ...

    def all_assignments(self) -> dict[str, int]:
        """Return a snapshot of every name → index assignment in the registry."""
        ...

    def assign(self, name: str, index: int) -> None:
        """Record *index* for *name*, overwriting any prior value."""
        ...

    def remove(self, name: str) -> None:
        """Remove the assignment for *name*. No-op when *name* is not present."""
        ...
