from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from winter_cli.modules.workspace.models.domain_model import LockEntry


class IConfigLockRepository(Protocol):
    """Reads and writes ``.winter/config.lock`` — the resolved-pin manifest.

    ``read`` returns a dict keyed by repo name; an absent file yields ``{}``.
    ``write`` performs a full rewrite of every entry, sorted by name.
    ``upsert`` atomically replaces a single repo's entry, preserving the rest —
    the concurrency-safe mutation callers must use during a parallel fan-out.
    ``ws init``/``pull``/``update`` reconcile standalone repos concurrently, so
    an unguarded read-modify-write drops entries via a last-writer-wins race;
    ``upsert`` serializes the read-merge-write so every pinned repo survives.

    Callers never mutate the dict returned by ``read`` — treat it as immutable.
    """

    def read(self) -> dict[str, LockEntry]: ...

    def write(self, entries: Iterable[LockEntry]) -> None: ...

    def upsert(self, entry: LockEntry) -> None: ...
