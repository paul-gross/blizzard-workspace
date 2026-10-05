from __future__ import annotations

from typing import TYPE_CHECKING, Any

import tomlkit
from tomlkit.exceptions import ParseError
from tomlkit.items import AoT, Comment, Table, Whitespace

from winter_cli.config.local_overlay_repository import ILocalOverlayRepository
from winter_cli.config.workspace import CONFIG_FILE, LOCAL_CONFIG_FILE, WINTER_DIR
from winter_cli.core.config_file import ConfigFileReadError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from winter_cli.core.config_file import IConfigFileReader
    from winter_cli.core.filesystem import IFilesystemWriter

_BLANK_AFTER = "\n\n"
"""Trail of the last key placed ahead of a table, so a blank line separates it from what follows."""


class TomlkitLocalOverlayRepository:
    """`ILocalOverlayRepository` over tomlkit, preserving the comments and structure of the overlay.

    Raw file I/O goes through an injected `IFilesystemWriter` and parsing of
    the read layers through `IConfigFileReader`; tomlkit only edits the text the
    seam returns.
    """

    def __init__(self, fs: IFilesystemWriter, config_file_reader: IConfigFileReader) -> None:
        self._fs = fs
        self._config_file_reader = config_file_reader

    def read_layers(self, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        return (
            self._read(root / WINTER_DIR / CONFIG_FILE),
            self._read(root / WINTER_DIR / LOCAL_CONFIG_FILE),
        )

    def upsert_local(self, root: Path, values: Mapping[str, Any]) -> bool:
        path = root / WINTER_DIR / LOCAL_CONFIG_FILE
        before = self._fs.read_text(path) if self._fs.exists(path) else ""
        try:
            doc = tomlkit.parse(before)
        except ParseError as exc:
            raise ConfigFileReadError(f"reading {path} — {exc}") from exc
        for key, value in values.items():
            self._set(doc, key, value)
        after = tomlkit.dumps(doc)
        if after == before:
            return False
        self._fs.mkdir(path.parent, parents=True, exist_ok=True)
        self._fs.write_text(path, after)
        return True

    def _read(self, path: Path) -> dict[str, Any]:
        return self._config_file_reader.load(path) if self._fs.exists(path) else {}

    @staticmethod
    def _set(doc: tomlkit.TOMLDocument, key: str, value: Any) -> None:
        """Set *key*: in place when present, else added above the first table.

        A key appended after a `[table]` header would parse as that table's
        member, so a new top-level scalar goes ahead of the first table or
        array-of-tables: after the last top-level scalar already there, else
        before the table and the comment lines attached to it. The last key
        placed ahead of a table carries a blank line after it. tomlkit exposes
        no public positional insert, hence `_insert_at`.
        """
        if key in doc:
            if doc[key] != value:
                doc[key] = value
            return
        body = doc.body
        first_table = next((i for i, (_, item) in enumerate(body) if isinstance(item, (Table, AoT))), None)
        if first_table is None:
            doc[key] = value
            return
        item = tomlkit.item(value)
        last_scalar = max((i for i in range(first_table) if body[i][0] is not None), default=None)
        if last_scalar is not None:
            at = last_scalar + 1
            previous = body[last_scalar][1]
            if previous.trivia.trail == _BLANK_AFTER:
                previous.trivia.trail = "\n"
                item.trivia.trail = _BLANK_AFTER
        else:
            at = first_table
            while at > 0 and isinstance(body[at - 1][1], Comment):
                at -= 1
            if not isinstance(body[at][1], Whitespace):
                item.trivia.trail = _BLANK_AFTER
        doc._insert_at(at, key, item)


def _conforms_tomlkit_local_overlay_repository(x: TomlkitLocalOverlayRepository) -> ILocalOverlayRepository:
    return x
