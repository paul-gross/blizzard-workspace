from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import Path

import tomlkit

from winter_cli.core.config_file import ConfigError
from winter_cli.core.filesystem import IFilesystemWriter
from winter_cli.modules.workspace.config_lock_repository import IConfigLockRepository
from winter_cli.modules.workspace.models.domain_model import LockEntry, RefKind

WINTER_DIR = ".winter"
LOCK_FILE = "config.lock"
LOCK_COMMENT = "# .winter/config.lock — managed by winter; commit this file."
LOCK_VERSION = 1


class ConfigLockError(ConfigError):
    """Raised when ``.winter/config.lock`` cannot be parsed or has an unsupported version.

    Distinguished from "file not present" (which is valid and returns ``{}``) so
    callers can surface a clear configuration bug rather than silently ignoring
    a corrupt or forward-version lock file.
    """


class WriteConfigLockRepository:
    """Reads and writes ``.winter/config.lock`` via tomlkit.

    All file I/O goes through an injected ``IFilesystemWriter`` so tests can
    run against an in-memory fake; tomlkit is only invoked on the string
    content returned or written by the seam.

    ``read`` returns ``{}`` when the file is absent; raises ``ConfigLockError``
    for a present-but-malformed file or an unknown version.

    ``write`` performs a full rewrite: entries sorted by name, full 40-char
    SHAs, ``version = 1`` header, and a leading managed-by comment.
    """

    def __init__(self, workspace_root: Path, fs: IFilesystemWriter) -> None:
        self._lock_path = workspace_root / WINTER_DIR / LOCK_FILE
        self._fs = fs
        # Serializes upsert's read-merge-write so concurrent per-repo pin writes
        # (init/pull/update fan standalone repos out across a thread pool) can't
        # clobber each other. One instance is shared across a run's threads.
        self._mutate_lock = threading.Lock()

    def read(self) -> dict[str, LockEntry]:
        if not self._fs.exists(self._lock_path):
            return {}
        try:
            raw = self._fs.read_text(self._lock_path)
            doc = tomlkit.parse(raw)
        except Exception as exc:
            raise ConfigLockError(f"Cannot parse {self._lock_path}: {exc}") from exc

        version = doc.get("version")
        if version != LOCK_VERSION:
            raise ConfigLockError(
                f"{self._lock_path}: unsupported lock file version {version!r} (expected {LOCK_VERSION})"
            )

        entries: dict[str, LockEntry] = {}
        for item in doc.get("standalone", []):
            try:
                name = str(item["name"])
                ref = str(item["ref"])
                kind = RefKind(str(item["kind"]))
                commit = str(item["commit"])
            except (KeyError, ValueError) as exc:
                raise ConfigLockError(f"{self._lock_path}: malformed [[standalone]] entry: {exc}") from exc
            entries[name] = LockEntry(name=name, ref=ref, kind=kind, commit=commit)
        return entries

    def write(self, entries: Iterable[LockEntry]) -> None:
        sorted_entries = sorted(entries, key=lambda e: e.name)

        doc = tomlkit.document()
        doc.add(tomlkit.comment(LOCK_COMMENT.lstrip("# ")))
        doc["version"] = LOCK_VERSION

        aot = tomlkit.aot()
        for entry in sorted_entries:
            block = tomlkit.table()
            block.add("name", entry.name)
            block.add("ref", entry.ref)
            block.add("kind", entry.kind.value)
            block.add("commit", entry.commit)
            aot.append(block)

        if sorted_entries:
            doc["standalone"] = aot

        self._fs.write_text(self._lock_path, tomlkit.dumps(doc))

    def upsert(self, entry: LockEntry) -> None:
        """Atomically replace ``entry``'s repo in the lock, preserving all others.

        Holds an instance-level mutex across the read → merge → write so that
        concurrent fan-out writers (one per standalone repo) serialize rather
        than each overwriting the file from a stale read.
        """
        with self._mutate_lock:
            entries = self.read()
            entries[entry.name] = entry
            self.write(entries.values())


def _conforms_write_config_lock_repository(
    x: WriteConfigLockRepository,
) -> IConfigLockRepository:
    return x
