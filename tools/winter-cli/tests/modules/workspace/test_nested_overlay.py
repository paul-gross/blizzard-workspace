from __future__ import annotations

from winter_cli.modules.workspace.nested_overlay import delegated_keys, footprint, inherited_keys


def test_delegated_keys_without_envs_set_only_the_port_base_and_prefix() -> None:
    assert delegated_keys(4100, "outer-alpha", None) == {"base_port": 4100, "service_prefix": "outer-alpha"}


def test_delegated_keys_with_envs_clear_the_aliases_and_reserve_the_buffer_slot() -> None:
    keys = delegated_keys(4100, "outer-alpha", 3)

    assert keys == {"base_port": 4100, "service_prefix": "outer-alpha", "env_aliases": [], "envs_per_workspace": 4}


def test_footprint_is_one_slot_per_index_including_zero() -> None:
    assert footprint({"ports_per_env": 20, "envs_per_workspace": 4}, {}) == 100


def test_footprint_uses_winters_defaults_when_nothing_sets_the_keys() -> None:
    assert footprint({}, {}) == (48 + 1) * 20


def test_the_local_overlay_wins_over_the_committed_config() -> None:
    committed = {"ports_per_env": 20, "envs_per_workspace": 48}

    assert footprint(committed, {"envs_per_workspace": 4, "ports_per_env": 10}) == 50


def test_the_delegated_envs_count_shrinks_the_footprint_below_the_committed_one() -> None:
    committed = {"ports_per_env": 20, "envs_per_workspace": 48}
    after = {**delegated_keys(4000, "p", 3)}

    assert footprint(committed, after) == (3 + 2) * 20


def test_a_non_integer_value_falls_back_to_the_default() -> None:
    assert footprint({"ports_per_env": True, "envs_per_workspace": "x"}, {}) == (48 + 1) * 20


OUTER = {"git": {"user": {"name": "Ada"}}, "secret": "x", "project_repository": [{"name": "p"}]}


def test_inherited_keys_copies_exactly_the_named_keys() -> None:
    assert inherited_keys(OUTER, ["git"]) == {"git": {"user": {"name": "Ada"}}}
    assert inherited_keys(OUTER, ["git", "secret"]) == {"git": {"user": {"name": "Ada"}}, "secret": "x"}


def test_inherited_keys_with_no_names_copies_nothing() -> None:
    assert inherited_keys(OUTER, []) == {}


def test_inherited_keys_skips_a_name_the_overlay_does_not_hold() -> None:
    assert inherited_keys(OUTER, ["missing", "git"]) == {"git": {"user": {"name": "Ada"}}}


def test_inherited_keys_never_copies_the_repository_arrays() -> None:
    outer = {**OUTER, "standalone_repository": [{"name": "s"}]}

    assert inherited_keys(outer, ["project_repository", "standalone_repository"]) == {}


def test_inherited_keys_does_not_alias_the_outer_overlay() -> None:
    inherited = inherited_keys(OUTER, ["git"])
    inherited["git"]["user"]["name"] = "changed"

    assert OUTER["git"]["user"]["name"] == "Ada"
