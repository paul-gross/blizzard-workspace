from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


class ILocalOverlayRepository(Protocol):
    """Reads and writes the top-level keys of another workspace's config files by its root.

    Unlike `IWriteWinterConfigurationRepository`, which is bound to the current
    workspace and edits repository blocks, this addresses any workspace root —
    the shape a nested workspace needs, since winter writes into a root it is not
    running in.
    """

    def read_layers(self, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        """The parsed `.winter/config.toml` and `.winter/config.local.toml` of *root*; a missing file reads as `{}`.

        Raises `ConfigFileReadError` naming the file when one is not valid TOML.
        """
        ...

    def upsert_local(self, root: Path, values: Mapping[str, Any]) -> bool:
        """Set each top-level key of *values* in *root*'s `.winter/config.local.toml`; return whether the file changed.

        Only the given keys are touched: every other key, comment, and table is
        preserved. The file is created when missing, and written only when its
        text would change. Raises `ConfigFileReadError` naming the file when it
        is not valid TOML, writing nothing.
        """
        ...
