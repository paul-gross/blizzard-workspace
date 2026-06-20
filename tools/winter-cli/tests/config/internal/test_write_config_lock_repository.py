from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import FakeFilesystem
from winter_cli.modules.workspace.internal.config_lock_repository import (
    ConfigLockError,
    WriteConfigLockRepository,
)
from winter_cli.modules.workspace.models.domain_model import LockEntry, RefKind

WORKSPACE_ROOT = Path("/ws")
LOCK_PATH = WORKSPACE_ROOT / ".winter" / "config.lock"


@pytest.fixture
def fs() -> FakeFilesystem:
    return FakeFilesystem()


@pytest.fixture
def repo(fs: FakeFilesystem) -> WriteConfigLockRepository:
    return WriteConfigLockRepository(workspace_root=WORKSPACE_ROOT, fs=fs)


# ── write ────────────────────────────────────────────────────────────────────


def test_write_produces_comment_version_and_entries(fs: FakeFilesystem, repo: WriteConfigLockRepository) -> None:
    entries = [
        LockEntry(
            name="winter-backlog",
            ref="v1.4.2",
            kind=RefKind.tag,
            commit="9f3c1ab2e4d5c6f7089a1b2c3d4e5f60718293a4",
        )
    ]
    repo.write(entries)

    content = fs.files[LOCK_PATH]
    assert "# .winter/config.lock — managed by winter; commit this file." in content
    assert "version = 1" in content
    assert "[[standalone]]" in content
    assert 'name = "winter-backlog"' in content
    assert 'ref = "v1.4.2"' in content
    assert 'kind = "tag"' in content
    assert 'commit = "9f3c1ab2e4d5c6f7089a1b2c3d4e5f60718293a4"' in content


def test_write_sorts_entries_by_name(fs: FakeFilesystem, repo: WriteConfigLockRepository) -> None:
    entries = [
        LockEntry(name="zz-repo", ref="main", kind=RefKind.branch, commit="a" * 40),
        LockEntry(name="aa-repo", ref="v1.0", kind=RefKind.tag, commit="b" * 40),
    ]
    repo.write(entries)

    content = fs.files[LOCK_PATH]
    aa_pos = content.index("aa-repo")
    zz_pos = content.index("zz-repo")
    assert aa_pos < zz_pos, "entries must be sorted by name ascending"


def test_write_full_40_char_sha(fs: FakeFilesystem, repo: WriteConfigLockRepository) -> None:
    sha = "deadbeef" * 5  # 40 chars
    repo.write([LockEntry(name="repo", ref="abc123", kind=RefKind.commit, commit=sha)])
    assert sha in fs.files[LOCK_PATH]


# ── round-trip ───────────────────────────────────────────────────────────────


def test_round_trip_write_then_read(fs: FakeFilesystem, repo: WriteConfigLockRepository) -> None:
    entries = [
        LockEntry(
            name="winter-backlog",
            ref="v1.4.2",
            kind=RefKind.tag,
            commit="9f3c1ab2e4d5c6f7089a1b2c3d4e5f60718293a4",
        ),
        LockEntry(
            name="alpha-ext",
            ref="main",
            kind=RefKind.branch,
            commit="cafebabe" * 5,
        ),
    ]
    repo.write(entries)
    result = repo.read()

    assert set(result.keys()) == {"winter-backlog", "alpha-ext"}
    wb = result["winter-backlog"]
    assert wb.ref == "v1.4.2"
    assert wb.kind == RefKind.tag
    assert wb.commit == "9f3c1ab2e4d5c6f7089a1b2c3d4e5f60718293a4"

    ae = result["alpha-ext"]
    assert ae.ref == "main"
    assert ae.kind == RefKind.branch
    assert ae.commit == "cafebabe" * 5


# ── read: absent file ────────────────────────────────────────────────────────


def test_read_returns_empty_dict_when_file_absent(repo: WriteConfigLockRepository) -> None:
    result = repo.read()
    assert result == {}


# ── read: bad version ────────────────────────────────────────────────────────


def test_read_raises_on_version_2(fs: FakeFilesystem, repo: WriteConfigLockRepository) -> None:
    fs.write_text(LOCK_PATH, "version = 2\n")
    with pytest.raises(ConfigLockError, match="version"):
        repo.read()


def test_read_raises_on_missing_version(fs: FakeFilesystem, repo: WriteConfigLockRepository) -> None:
    fs.write_text(LOCK_PATH, "[[standalone]]\nname = 'x'\n")
    with pytest.raises(ConfigLockError, match="version"):
        repo.read()


# ── read: malformed TOML ─────────────────────────────────────────────────────


def test_read_raises_on_malformed_toml(fs: FakeFilesystem, repo: WriteConfigLockRepository) -> None:
    fs.write_text(LOCK_PATH, "version = 1\n[[standalone\n")
    with pytest.raises(ConfigLockError):
        repo.read()


# ── write empty ──────────────────────────────────────────────────────────────


def test_write_empty_entries_produces_no_standalone_section(
    fs: FakeFilesystem, repo: WriteConfigLockRepository
) -> None:
    repo.write([])
    content = fs.files[LOCK_PATH]
    assert "version = 1" in content
    assert "[[standalone]]" not in content


# ── upsert ───────────────────────────────────────────────────────────────────


def test_upsert_adds_entry_preserving_others(fs: FakeFilesystem, repo: WriteConfigLockRepository) -> None:
    repo.write([LockEntry(name="aa", ref="v1", kind=RefKind.tag, commit="a" * 40)])
    repo.upsert(LockEntry(name="bb", ref="main", kind=RefKind.branch, commit="b" * 40))

    result = repo.read()
    assert set(result.keys()) == {"aa", "bb"}, "upsert must preserve the existing entry"


def test_upsert_replaces_existing_entry(fs: FakeFilesystem, repo: WriteConfigLockRepository) -> None:
    repo.write([LockEntry(name="aa", ref="v1", kind=RefKind.tag, commit="a" * 40)])
    repo.upsert(LockEntry(name="aa", ref="v2", kind=RefKind.tag, commit="c" * 40))

    result = repo.read()
    assert result["aa"].ref == "v2"
    assert result["aa"].commit == "c" * 40


def test_upsert_is_concurrency_safe_no_lost_entries() -> None:
    """Regression for the fan-out race: concurrent per-repo upserts must all survive.

    ``ws init``/``pull``/``update`` reconcile standalone repos across a thread
    pool, each upserting its own lock entry. An unguarded read-modify-write
    drops entries (last-writer-wins). A filesystem that sleeps between read and
    write forces the interleaving so this test reliably fails without the
    instance lock and passes with it.
    """
    import threading
    import time

    class SlowReadFilesystem(FakeFilesystem):
        def read_text(self, path: Path) -> str:
            content = super().read_text(path)
            time.sleep(0.005)  # widen the read→write window to force a race
            return content

    fs = SlowReadFilesystem()
    repo = WriteConfigLockRepository(workspace_root=WORKSPACE_ROOT, fs=fs)

    n = 12
    entries = [LockEntry(name=f"repo-{i:02d}", ref="main", kind=RefKind.branch, commit=f"{i:040d}") for i in range(n)]

    def worker(entry: LockEntry) -> None:
        repo.upsert(entry)

    threads = [threading.Thread(target=worker, args=(e,)) for e in entries]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    result = repo.read()
    assert set(result.keys()) == {e.name for e in entries}, (
        f"lost entries under concurrency: expected {n}, got {len(result)}"
    )
