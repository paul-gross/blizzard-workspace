"""Unit tests for winter_cli.config.overlay — the spec-driven TOML overlay engine.

Covers the five merge behaviours required by the acceptance criteria:
  1. Scalar replace
  2. Per-key table merge (TableField)
  3. Array append (ArrayAppendField)
  4. Array keyed override-or-append (ArrayKeyedField)
  5. No mutation of inputs
"""

from __future__ import annotations

from winter_cli.config.overlay import (
    ArrayAppendField,
    ArrayKeyedField,
    MergeSpec,
    ScalarField,
    TableField,
    overlay_merge,
)

# ---------------------------------------------------------------------------
# 1. Scalar replace
# ---------------------------------------------------------------------------


def test_scalar_replace_overlay_wins() -> None:
    spec = MergeSpec(fields={"name": ScalarField()})
    result = overlay_merge({"name": "base"}, {"name": "overlay"}, spec=spec)
    assert result["name"] == "overlay"


def test_scalar_replace_base_kept_when_absent_from_overlay() -> None:
    spec = MergeSpec(fields={"a": ScalarField(), "b": ScalarField()})
    result = overlay_merge({"a": 1, "b": 2}, {"a": 99}, spec=spec)
    assert result["a"] == 99
    assert result["b"] == 2


def test_scalar_replace_overlay_can_set_new_key() -> None:
    spec = MergeSpec(fields={"new": ScalarField()})
    result = overlay_merge({}, {"new": "value"}, spec=spec)
    assert result["new"] == "value"


def test_unspecified_key_defaults_to_scalar_replace() -> None:
    """A key not in the spec defaults to scalar-replace (no KeyError)."""
    spec = MergeSpec(fields={})
    result = overlay_merge({"x": 1}, {"x": 2}, spec=spec)
    assert result["x"] == 2


# ---------------------------------------------------------------------------
# 2. Per-key table merge (TableField)
# ---------------------------------------------------------------------------


def test_table_merge_overlay_key_wins() -> None:
    spec = MergeSpec(fields={"logs": TableField()})
    base = {"logs": {"rotate_size_bytes": 1024, "max_rotations": 3}}
    overlay = {"logs": {"rotate_size_bytes": 2048}}
    result = overlay_merge(base, overlay, spec=spec)
    assert result["logs"]["rotate_size_bytes"] == 2048
    assert result["logs"]["max_rotations"] == 3


def test_table_merge_base_keys_absent_from_overlay_kept() -> None:
    spec = MergeSpec(fields={"cfg": TableField()})
    base = {"cfg": {"a": 1, "b": 2, "c": 3}}
    overlay = {"cfg": {"b": 99}}
    result = overlay_merge(base, overlay, spec=spec)
    assert result["cfg"] == {"a": 1, "b": 99, "c": 3}


def test_table_merge_no_base_table_creates_from_overlay() -> None:
    spec = MergeSpec(fields={"logs": TableField()})
    result = overlay_merge({}, {"logs": {"retention_seconds": 86400}}, spec=spec)
    assert result["logs"] == {"retention_seconds": 86400}


# ---------------------------------------------------------------------------
# 3. Array append (ArrayAppendField)
# ---------------------------------------------------------------------------


def test_array_append_overlay_entries_added_after_base() -> None:
    spec = MergeSpec(fields={"repos": ArrayAppendField()})
    base = {"repos": [{"name": "a"}]}
    overlay = {"repos": [{"name": "b"}]}
    result = overlay_merge(base, overlay, spec=spec)
    assert result["repos"] == [{"name": "a"}, {"name": "b"}]


def test_array_append_no_base_list_starts_from_overlay() -> None:
    spec = MergeSpec(fields={"items": ArrayAppendField()})
    result = overlay_merge({}, {"items": [1, 2]}, spec=spec)
    assert result["items"] == [1, 2]


def test_array_append_empty_overlay_keeps_base() -> None:
    spec = MergeSpec(fields={"items": ArrayAppendField()})
    result = overlay_merge({"items": [1]}, {"items": []}, spec=spec)
    assert result["items"] == [1]


# ---------------------------------------------------------------------------
# 4. Array keyed override-or-append (ArrayKeyedField)
# ---------------------------------------------------------------------------


def test_keyed_merge_overlay_matches_key_overrides_in_place() -> None:
    spec = MergeSpec(fields={"services": ArrayKeyedField(key="name")})
    base = {"services": [{"name": "backend", "command": "old"}]}
    overlay = {"services": [{"name": "backend", "command": "new"}]}
    result = overlay_merge(base, overlay, spec=spec)
    assert len(result["services"]) == 1
    assert result["services"][0]["command"] == "new"


def test_keyed_merge_overlay_new_key_appended() -> None:
    spec = MergeSpec(fields={"services": ArrayKeyedField(key="name")})
    base = {"services": [{"name": "backend"}]}
    overlay = {"services": [{"name": "worker"}]}
    result = overlay_merge(base, overlay, spec=spec)
    assert [s["name"] for s in result["services"]] == ["backend", "worker"]


def test_keyed_merge_position_preserved_for_override() -> None:
    spec = MergeSpec(fields={"services": ArrayKeyedField(key="name")})
    base = {"services": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}
    overlay = {"services": [{"name": "b", "command": "b-new"}]}
    result = overlay_merge(base, overlay, spec=spec)
    names = [s["name"] for s in result["services"]]
    assert names == ["a", "b", "c"]
    assert result["services"][1]["command"] == "b-new"


def test_keyed_merge_partial_override_keeps_existing_fields() -> None:
    spec = MergeSpec(fields={"services": ArrayKeyedField(key="name")})
    base = {"services": [{"name": "svc", "command": "old", "log": "pane"}]}
    overlay = {"services": [{"name": "svc", "command": "new"}]}
    result = overlay_merge(base, overlay, spec=spec)
    assert result["services"][0]["command"] == "new"
    assert result["services"][0]["log"] == "pane"


def test_keyed_merge_override_and_append_combined() -> None:
    spec = MergeSpec(fields={"services": ArrayKeyedField(key="name")})
    base = {"services": [{"name": "a"}, {"name": "b"}]}
    overlay = {"services": [{"name": "b", "x": 1}, {"name": "c"}]}
    result = overlay_merge(base, overlay, spec=spec)
    names = [s["name"] for s in result["services"]]
    assert names == ["a", "b", "c"]
    assert result["services"][1]["x"] == 1


def test_keyed_merge_entry_without_key_always_appended() -> None:
    spec = MergeSpec(fields={"items": ArrayKeyedField(key="id")})
    base = {"items": [{"id": 1}]}
    overlay = {"items": [{"value": "no-id"}]}
    result = overlay_merge(base, overlay, spec=spec)
    assert len(result["items"]) == 2
    assert result["items"][1] == {"value": "no-id"}


# ---------------------------------------------------------------------------
# 5. No mutation of inputs
# ---------------------------------------------------------------------------


def test_no_mutation_of_base_dict() -> None:
    spec = MergeSpec(fields={"a": ScalarField()})
    base = {"a": 1}
    overlay_merge(base, {"a": 2}, spec=spec)
    assert base["a"] == 1


def test_no_mutation_of_overlay_dict() -> None:
    spec = MergeSpec(fields={"a": ScalarField()})
    overlay = {"a": 2}
    overlay_merge({"a": 1}, overlay, spec=spec)
    assert overlay["a"] == 2


def test_no_mutation_of_base_keyed_list() -> None:
    spec = MergeSpec(fields={"items": ArrayKeyedField(key="name")})
    base_items = [{"name": "x", "val": 1}]
    base = {"items": base_items}
    overlay_merge(base, {"items": [{"name": "x", "val": 99}]}, spec=spec)
    assert base_items[0]["val"] == 1


def test_no_mutation_of_base_table() -> None:
    spec = MergeSpec(fields={"logs": TableField()})
    base_logs = {"a": 1, "b": 2}
    base = {"logs": base_logs}
    overlay_merge(base, {"logs": {"a": 99}}, spec=spec)
    assert base_logs["a"] == 1


def test_no_mutation_of_nested_status_dict() -> None:
    """Simulates the status.url alias-mutation bug from ManifestReader history."""
    spec = MergeSpec(fields={"status_urls": ArrayKeyedField(key="label")})
    base_urls = [{"label": "Backend", "url": "http://localhost:3000"}]
    base = {"status_urls": base_urls}
    overlay_merge(base, {"status_urls": [{"label": "Backend", "url": "http://localhost:4000"}]}, spec=spec)
    assert base_urls[0]["url"] == "http://localhost:3000"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_overlay_returns_copy_of_base() -> None:
    spec = MergeSpec(fields={"a": ScalarField()})
    base = {"a": 1, "b": 2}
    result = overlay_merge(base, {}, spec=spec)
    assert result == base
    assert result is not base


def test_empty_base_returns_overlay_keys() -> None:
    spec = MergeSpec(fields={"a": ScalarField()})
    result = overlay_merge({}, {"a": 42}, spec=spec)
    assert result == {"a": 42}
