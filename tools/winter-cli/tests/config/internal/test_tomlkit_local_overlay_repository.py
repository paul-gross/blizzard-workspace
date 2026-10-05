from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from tests.conftest import FakeConfigFileReader, FakeFilesystem
from winter_cli.config.internal.tomlkit_local_overlay_repository import TomlkitLocalOverlayRepository
from winter_cli.core.config_file import ConfigFileReadError

ROOT = Path("/nested")
SHARED = ROOT / ".winter" / "config.toml"
LOCAL = ROOT / ".winter" / "config.local.toml"
VALUES = {"base_port": 4100, "service_prefix": "outer-alpha"}


def _repo(fs: FakeFilesystem, parsed: dict[Path, dict] | None = None) -> TomlkitLocalOverlayRepository:
    return TomlkitLocalOverlayRepository(fs, FakeConfigFileReader(parsed or {}))


def test_upsert_creates_a_missing_overlay() -> None:
    fs = FakeFilesystem()

    changed = _repo(fs).upsert_local(ROOT, VALUES)

    assert changed is True
    assert tomllib.loads(fs.files[LOCAL]) == VALUES


def test_upsert_preserves_other_keys_comments_and_tables() -> None:
    original = '# my machine\nlog_level = "debug"  # keep\n\n# git identity\n[git]\nname = "me"\n'
    fs = FakeFilesystem(files={LOCAL: original})

    _repo(fs).upsert_local(ROOT, VALUES)

    text = fs.files[LOCAL]
    assert tomllib.loads(text) == {"log_level": "debug", "git": {"name": "me"}, **VALUES}
    assert "# my machine" in text
    assert 'log_level = "debug"  # keep' in text
    assert "# git identity\n[git]" in text


def test_upsert_replaces_an_existing_value_in_place() -> None:
    fs = FakeFilesystem(files={LOCAL: "base_port = 1  # stale\nother = true\n"})

    changed = _repo(fs).upsert_local(ROOT, {"base_port": 4100})

    assert changed is True
    assert tomllib.loads(fs.files[LOCAL]) == {"base_port": 4100, "other": True}


@pytest.mark.parametrize(
    "original",
    [
        '[git]\nname = "me"\n',
        '# header\n\n# about git\n[git]\nname = "me"\n',
        "top = 1\n\n[git]\nname = 'me'\n[[tui]]\nx = 1\n",
    ],
)
def test_a_new_top_level_scalar_lands_above_every_table(original: str) -> None:
    fs = FakeFilesystem(files={LOCAL: original})

    _repo(fs).upsert_local(ROOT, {**VALUES, "env_aliases": [], "envs_per_workspace": 4})

    parsed = tomllib.loads(fs.files[LOCAL])
    assert {k: parsed[k] for k in (*VALUES, "env_aliases", "envs_per_workspace")} == {
        **VALUES,
        "env_aliases": [],
        "envs_per_workspace": 4,
    }
    assert parsed["git"] == tomllib.loads(original)["git"]


def test_a_comment_attached_to_a_table_stays_attached() -> None:
    fs = FakeFilesystem(files={LOCAL: '# about git\n[git]\nname = "me"\n'})

    _repo(fs).upsert_local(ROOT, VALUES)

    assert "# about git\n[git]" in fs.files[LOCAL]


GIT = {"user": {"name": "Ada", "email": "ada@example.com"}, "core": {"autocrlf": False}, "sub": {"deep": {"k": 1}}}


def test_a_whole_table_with_nested_sub_tables_round_trips_into_a_missing_overlay() -> None:
    fs = FakeFilesystem()

    _repo(fs).upsert_local(ROOT, {"git": GIT, **VALUES})

    assert tomllib.loads(fs.files[LOCAL]) == {"git": GIT, **VALUES}


def test_a_new_table_lands_after_every_scalar_and_existing_table() -> None:
    fs = FakeFilesystem(files={LOCAL: "top = 1\n\n[other]\nx = 1\n"})

    _repo(fs).upsert_local(ROOT, {"git": GIT, **VALUES})

    text = fs.files[LOCAL]
    assert tomllib.loads(text) == {"top": 1, "other": {"x": 1}, "git": GIT, **VALUES}
    assert text.index("base_port") < text.index("[other]") < text.index("[git")


def test_a_named_table_replaces_the_table_already_in_the_file() -> None:
    original = '# about git\n[git]\n# stale\nname = "old"\nstale = true\n\n[git.gone]\nx = 1\n\n[other]\nx = 1\n'
    fs = FakeFilesystem(files={LOCAL: original})

    changed = _repo(fs).upsert_local(ROOT, {"git": GIT})

    assert changed is True
    text = fs.files[LOCAL]
    assert tomllib.loads(text) == {"git": GIT, "other": {"x": 1}}
    assert "# about git" in text
    assert "stale" not in text
    assert text.index("[git") < text.index("[other]")


@pytest.mark.parametrize(
    "original",
    [
        "git.user.name = 'old'\nbase_port = 1\nservice_prefix = 'mine'\n",
        "top = 1\ngit.user.name = 'old'\ngit.user.email = 'old@example.com'\nbase_port = 1\n\n[other]\nx = 1\n",
        "git = { user = { name = 'old' } }\nbase_port = 1\n",
    ],
    ids=["dotted", "dotted-split-before-a-table", "inline"],
)
def test_a_named_table_replacing_a_key_value_form_leaves_the_keys_after_it_top_level(original: str) -> None:
    fs = FakeFilesystem(files={LOCAL: original})

    _repo(fs).upsert_local(ROOT, {"git": GIT, **VALUES})

    expected = {k: v for k, v in tomllib.loads(original).items() if k != "git"}
    assert tomllib.loads(fs.files[LOCAL]) == {**expected, "git": GIT, **VALUES}


def test_a_named_table_replacing_a_super_table_lands_after_every_table() -> None:
    original = "base_port = 1\n\n[git.user]\nname = 'old'\n\n[other]\nx = 1\n"
    fs = FakeFilesystem(files={LOCAL: original})

    _repo(fs).upsert_local(ROOT, {"git": GIT, **VALUES})

    text = fs.files[LOCAL]
    assert tomllib.loads(text) == {"other": {"x": 1}, "git": GIT, **VALUES}
    assert "'old'" not in text
    assert text.index("[other]") < text.index("[git")


def test_a_scalar_replacing_a_table_header_lands_among_the_top_level_keys() -> None:
    fs = FakeFilesystem(files={LOCAL: "top = 1\n\n[git]\nname = 'old'\n\n[other]\nx = 1\n"})

    _repo(fs).upsert_local(ROOT, {"git": "flat"})

    assert tomllib.loads(fs.files[LOCAL]) == {"top": 1, "git": "flat", "other": {"x": 1}}


def test_a_named_table_replaces_a_scalar_of_the_same_name_and_the_reverse() -> None:
    fs = FakeFilesystem(files={LOCAL: "git = 5\n"})
    repo = _repo(fs)

    repo.upsert_local(ROOT, {"git": GIT})
    assert tomllib.loads(fs.files[LOCAL]) == {"git": GIT}

    repo.upsert_local(ROOT, {"git": "flat"})
    assert tomllib.loads(fs.files[LOCAL]) == {"git": "flat"}


def test_upserting_the_same_table_again_writes_nothing() -> None:
    fs = FakeFilesystem(files={LOCAL: "[other]\nx = 1\n"})
    repo = _repo(fs)
    repo.upsert_local(ROOT, {"git": GIT, **VALUES})
    written = fs.files[LOCAL]

    assert repo.upsert_local(ROOT, {"git": GIT, **VALUES}) is False
    assert fs.files[LOCAL] == written


def test_upsert_writes_nothing_when_the_values_already_hold() -> None:
    fs = FakeFilesystem()
    repo = _repo(fs)
    repo.upsert_local(ROOT, VALUES)
    written = fs.files[LOCAL]

    changed = repo.upsert_local(ROOT, VALUES)

    assert changed is False
    assert fs.files[LOCAL] == written


def test_upsert_does_not_create_the_file_when_there_is_nothing_to_change() -> None:
    fs = FakeFilesystem()

    assert _repo(fs).upsert_local(ROOT, {}) is False
    assert LOCAL not in fs.files


def test_upsert_raises_a_config_file_read_error_naming_a_malformed_overlay_and_writes_nothing() -> None:
    fs = FakeFilesystem(files={LOCAL: "base_port = = 1\n"})

    with pytest.raises(ConfigFileReadError, match=r"config\.local\.toml"):
        _repo(fs).upsert_local(ROOT, VALUES)

    assert fs.files[LOCAL] == "base_port = = 1\n"


def test_read_layers_returns_each_file_and_an_empty_dict_for_a_missing_one() -> None:
    fs = FakeFilesystem(files={SHARED: "x"})
    repo = _repo(fs, {SHARED: {"ports_per_env": 20}})

    assert repo.read_layers(ROOT) == ({"ports_per_env": 20}, {})
