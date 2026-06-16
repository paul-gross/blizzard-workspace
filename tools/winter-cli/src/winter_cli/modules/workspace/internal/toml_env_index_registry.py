from __future__ import annotations

from pathlib import Path

import tomlkit
from tomlkit.exceptions import ParseError
from tomlkit.items import Table

from winter_cli.core.filesystem import IFilesystemWriter
from winter_cli.modules.workspace.env_index_registry import IEnvIndexRegistry

_STATE_KEY = "env_index"


class TomlEnvIndexRegistry:
    """TOML-backed implementation of ``IEnvIndexRegistry``.

    Reads and writes ``.winter/state.toml`` in the workspace root.  The file
    is machine-local and gitignored — it is *not* a config overlay and must
    not be committed.  A missing file is treated as an empty registry.

    Write operations are load-modify-store, which is safe for the expected
    access pattern (one env allocated at a time during ``winter ws init``).

    Raw file I/O is routed through an injected ``IFilesystemWriter`` seam
    (mirroring ``WriteWinterConfigurationRepository``) so tests run against an
    in-memory fake; tomlkit only touches the string content.

    Layout of ``.winter/state.toml``::

        [env_index]
        alpha = 1
        feature-x = 14
    """

    def __init__(self, state_path: Path, fs: IFilesystemWriter) -> None:
        self._path = state_path
        self._fs = fs

    # ── IEnvIndexRegistry ─────────────────────────────────────────────────

    def get_index(self, name: str) -> int | None:
        table = self._read_table()
        raw = table.get(name)
        return int(raw) if isinstance(raw, int) else None

    def all_assignments(self) -> dict[str, int]:
        table = self._read_table()
        return {k: int(v) for k, v in table.items() if isinstance(v, int)}

    def assign(self, name: str, index: int) -> None:
        doc = self._load_doc()
        table = doc.get(_STATE_KEY)
        if not isinstance(table, Table):
            table = tomlkit.table()
            doc[_STATE_KEY] = table
        table[name] = index
        self._write_doc(doc)

    def remove(self, name: str) -> None:
        doc = self._load_doc()
        table = doc.get(_STATE_KEY)
        if table is None or name not in table:
            return
        del table[name]
        self._write_doc(doc)

    # ── Private helpers ────────────────────────────────────────────────────

    def _read_table(self) -> dict:
        if not self._fs.exists(self._path):
            return {}
        doc = self._load_doc()
        section = doc.get(_STATE_KEY)
        if not isinstance(section, dict):
            return {}
        return dict(section)

    def _load_doc(self) -> tomlkit.TOMLDocument:
        if not self._fs.exists(self._path):
            return tomlkit.document()
        try:
            return tomlkit.parse(self._fs.read_text(self._path))
        except ParseError:
            return tomlkit.document()

    def _write_doc(self, doc: tomlkit.TOMLDocument) -> None:
        self._fs.mkdir(self._path.parent, parents=True, exist_ok=True)
        self._fs.write_text(self._path, tomlkit.dumps(doc))


def _conforms_toml_env_index_registry(x: TomlEnvIndexRegistry) -> IEnvIndexRegistry:
    return x
