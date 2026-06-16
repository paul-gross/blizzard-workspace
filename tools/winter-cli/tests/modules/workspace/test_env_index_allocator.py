"""Unit tests for the Phase 2 env-index registry seam and allocator.

Covers:
  (a) alias-driven fixed indices — alpha→1 etc., respecting a custom env_aliases list
  (b) empty-env_aliases ⇒ pure hash for every name
  (c) probe-on-collision — two ad-hoc names whose suggested slots collide get distinct
      adjacent indices via upward probing, recorded in the registry
  (d) persisted read-back — assign via the real .winter/state.toml adapter against a
      temp dir, then a fresh adapter instance reads the same index back
  (e) idempotent re-allocation returns the recorded index
"""
from __future__ import annotations

from pathlib import Path

import pytest

from winter_cli.modules.workspace.env_index import (
    GREEK_LETTERS,
    EnvIndexAllocator,
    _resolve_with_params,
    resolve_env_index,
)
from winter_cli.modules.workspace.env_index_registry import IEnvIndexRegistry
from winter_cli.modules.workspace.internal.toml_env_index_registry import (
    TomlEnvIndexRegistry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _allocate(name: str, env_aliases: list[str], envs_per_workspace: int, registry: IEnvIndexRegistry) -> int:
    """Allocate via EnvIndexAllocator — thin wrapper keeping the test call sites compact."""
    return EnvIndexAllocator(registry).allocate(name, env_aliases, envs_per_workspace)


class _InMemoryRegistry:
    """Minimal in-memory IEnvIndexRegistry for allocator tests that don't need persistence."""

    def __init__(self) -> None:
        self._data: dict[str, int] = {}

    def get_index(self, name: str) -> int | None:
        return self._data.get(name)

    def all_assignments(self) -> dict[str, int]:
        return dict(self._data)

    def assign(self, name: str, index: int) -> None:
        self._data[name] = index

    def remove(self, name: str) -> None:
        self._data.pop(name, None)


def _registry(tmp: Path) -> TomlEnvIndexRegistry:
    from winter_cli.core.internal.local_filesystem import LocalFilesystem

    return TomlEnvIndexRegistry(tmp / ".winter" / "state.toml", LocalFilesystem())


# ---------------------------------------------------------------------------
# (a) Alias-driven fixed indices
# ---------------------------------------------------------------------------

class TestAliasFixedIndices:
    def test_default_first_alias_is_index_1(self) -> None:
        """alpha (first in default 10-letter alias list) → 1."""
        idx = resolve_env_index("alpha")
        assert idx == 1

    def test_default_second_alias_is_index_2(self) -> None:
        idx = resolve_env_index("beta")
        assert idx == 2

    def test_default_tenth_alias_is_index_10(self) -> None:
        """kappa is the 10th default alias → 10."""
        idx = resolve_env_index("kappa")
        assert idx == 10

    def test_custom_alias_list_first_is_index_1(self) -> None:
        """With a custom aliases list [zeta, eta], zeta→1."""
        idx = resolve_env_index("zeta", env_aliases=["zeta", "eta"], envs_per_workspace=10)
        assert idx == 1

    def test_custom_alias_list_second_is_index_2(self) -> None:
        idx = resolve_env_index("eta", env_aliases=["zeta", "eta"], envs_per_workspace=10)
        assert idx == 2

    def test_full_greek_letters_alias_list_omega_is_24(self) -> None:
        """With all 24 Greek letters as aliases, omega→24."""
        idx = resolve_env_index("omega", env_aliases=GREEK_LETTERS, envs_per_workspace=48)
        assert idx == len(GREEK_LETTERS)  # 24

    def test_allocate_alias_returns_fixed_slot(self) -> None:
        """allocate for an alias name returns the fixed slot."""
        registry = _InMemoryRegistry()
        result = _allocate("alpha", ["alpha", "beta"], 10, registry)
        assert result == 1

    def test_allocate_alias_writes_to_registry(self) -> None:
        registry = _InMemoryRegistry()
        _allocate("beta", ["alpha", "beta"], 10, registry)
        assert registry.get_index("beta") == 2

    def test_allocate_alias_idempotent_on_second_call(self) -> None:
        registry = _InMemoryRegistry()
        r1 = _allocate("alpha", ["alpha", "beta"], 10, registry)
        r2 = _allocate("alpha", ["alpha", "beta"], 10, registry)
        assert r1 == r2 == 1


# ---------------------------------------------------------------------------
# (b) Empty env_aliases ⇒ pure hash for every name
# ---------------------------------------------------------------------------

class TestEmptyAliases:
    def test_name_hashes_into_band_with_empty_aliases(self) -> None:
        """With 0 aliases and 10 envs-per-workspace, band is 2..10 (9 slots)."""
        idx = resolve_env_index("anything", env_aliases=[], envs_per_workspace=10)
        assert 2 <= idx <= 10

    def test_hash_is_deterministic_with_empty_aliases(self) -> None:
        first = resolve_env_index("feature-x", env_aliases=[], envs_per_workspace=20)
        second = resolve_env_index("feature-x", env_aliases=[], envs_per_workspace=20)
        assert first == second

    def test_all_names_hash_with_empty_aliases(self) -> None:
        """No name gets a fixed slot when aliases list is empty."""
        for name in ["alpha", "omega", "feature-y", "foo"]:
            idx = resolve_env_index(name, env_aliases=[], envs_per_workspace=20)
            # band is 2..20 with 0 aliases
            assert 2 <= idx <= 20

    def test_allocate_with_empty_aliases_hashes(self) -> None:
        registry = _InMemoryRegistry()
        idx = _allocate("feature-z", [], 20, registry)
        assert 2 <= idx <= 20
        assert registry.get_index("feature-z") == idx


# ---------------------------------------------------------------------------
# (c) Probe-on-collision
# ---------------------------------------------------------------------------

class TestProbeOnCollision:
    def _find_collision_pair(self, aliases: list[str], envs: int) -> tuple[str, str, int]:
        """Brute-force two names that share the same suggested slot.

        Returns (name1, name2, shared_slot). Tries sequential candidate names until a
        collision is found within a reasonable search space.
        """
        slot_to_name: dict[int, str] = {}
        for i in range(1000):
            candidate = f"cand-{i}"
            slot = _resolve_with_params(candidate, aliases, envs)
            if slot in slot_to_name:
                return (slot_to_name[slot], candidate, slot)
            slot_to_name[slot] = candidate
        raise RuntimeError("Could not find a collision pair in 1000 candidates")

    def test_second_name_probes_to_next_free_slot(self) -> None:
        """When two names hash to the same slot, the second gets the next free slot."""
        aliases: list[str] = []  # no fixed aliases; hash band is 2..10
        envs = 10
        name1, name2, shared_slot = self._find_collision_pair(aliases, envs)

        registry = _InMemoryRegistry()
        idx1 = _allocate(name1, aliases, envs, registry)
        assert idx1 == shared_slot

        idx2 = _allocate(name2, aliases, envs, registry)
        assert idx2 != idx1
        assert 2 <= idx2 <= envs  # still within the valid band

    def test_probed_indices_are_distinct(self) -> None:
        """Two colliding names end up with different indices in the registry."""
        aliases: list[str] = []
        envs = 10
        name1, name2, _ = self._find_collision_pair(aliases, envs)

        registry = _InMemoryRegistry()
        idx1 = _allocate(name1, aliases, envs, registry)
        idx2 = _allocate(name2, aliases, envs, registry)
        assert idx1 != idx2

    def test_probe_wraps_within_band(self) -> None:
        """When probing wraps inside the band, a slot before the original is acceptable."""
        # Force a specific scenario: aliases=[], envs=4 gives band 2..4 (3 slots).
        # Pre-fill slots 3 and 4; a name that hashes to 3 should wrap to 2.
        aliases: list[str] = []
        envs = 4
        registry = _InMemoryRegistry()
        registry.assign("other-a", 3)
        registry.assign("other-b", 4)

        # Find a name that hashes to slot 3.
        target_name: str | None = None
        for i in range(1000):
            c = f"wrap-{i}"
            if _resolve_with_params(c, aliases, envs) == 3:
                target_name = c
                break
        assert target_name is not None, "expected to find a name hashing to slot 3"

        idx = _allocate(target_name, aliases, envs, registry)
        assert idx == 2  # only free slot; wraps around

    def test_full_band_raises_index_error(self) -> None:
        """When every slot in the hash band is taken, IndexError is raised."""
        aliases: list[str] = []
        envs = 4  # band is 2..4 (3 slots)
        registry = _InMemoryRegistry()
        registry.assign("held-a", 2)
        registry.assign("held-b", 3)
        registry.assign("held-c", 4)

        # Find any ad-hoc name; its slot is taken, probing will exhaust the band.
        with pytest.raises(IndexError, match="slots"):
            _allocate("new-name", aliases, envs, registry)


# ---------------------------------------------------------------------------
# (d) Persisted read-back via real TomlEnvIndexRegistry
# ---------------------------------------------------------------------------

class TestPersistedReadBack:
    def test_assign_then_read_back_with_fresh_instance(self, tmp_path: Path) -> None:
        """Assign via one registry instance; a new instance reading the same file returns it."""
        reg1 = _registry(tmp_path)
        reg1.assign("gamma", 3)

        reg2 = _registry(tmp_path)
        assert reg2.get_index("gamma") == 3

    def test_all_assignments_returns_persisted_entries(self, tmp_path: Path) -> None:
        reg1 = _registry(tmp_path)
        reg1.assign("alpha", 1)
        reg1.assign("feature-a", 14)

        reg2 = _registry(tmp_path)
        assert reg2.all_assignments() == {"alpha": 1, "feature-a": 14}

    def test_missing_file_is_empty_registry(self, tmp_path: Path) -> None:
        """A fresh (never-written) registry behaves as empty — no file needed."""
        reg = _registry(tmp_path)
        assert reg.get_index("any") is None
        assert reg.all_assignments() == {}

    def test_remove_deletes_from_persisted_state(self, tmp_path: Path) -> None:
        reg1 = _registry(tmp_path)
        reg1.assign("delta", 4)
        reg1.remove("delta")

        reg2 = _registry(tmp_path)
        assert reg2.get_index("delta") is None

    def test_assign_preserves_existing_entries(self, tmp_path: Path) -> None:
        reg1 = _registry(tmp_path)
        reg1.assign("alpha", 1)
        reg1.assign("beta", 2)

        reg2 = _registry(tmp_path)
        assert reg2.get_index("alpha") == 1
        assert reg2.get_index("beta") == 2

    def test_state_file_is_created_in_dot_winter(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        reg.assign("epsilon", 5)
        assert (tmp_path / ".winter" / "state.toml").exists()

    def test_full_allocation_with_real_registry(self, tmp_path: Path) -> None:
        """End-to-end: allocate via EnvIndexAllocator; fresh registry reads it back."""
        reg1 = _registry(tmp_path)
        idx = _allocate("feature-x", ["alpha", "beta"], 20, reg1)

        reg2 = _registry(tmp_path)
        assert reg2.get_index("feature-x") == idx


# ---------------------------------------------------------------------------
# (e) Idempotent re-allocation
# ---------------------------------------------------------------------------

class TestIdempotentReallocation:
    def test_second_allocate_returns_same_index_as_first(self) -> None:
        """Re-allocating a registered name returns the originally assigned index."""
        registry = _InMemoryRegistry()
        idx1 = _allocate("feature-q", [], 20, registry)
        idx2 = _allocate("feature-q", [], 20, registry)
        assert idx1 == idx2

    def test_idempotent_does_not_change_registry(self) -> None:
        registry = _InMemoryRegistry()
        _allocate("feature-r", [], 20, registry)
        before = registry.all_assignments()
        _allocate("feature-r", [], 20, registry)
        after = registry.all_assignments()
        assert before == after

    def test_alias_idempotent_with_persistent_registry(self, tmp_path: Path) -> None:
        """Alias allocation persisted across instances stays idempotent."""
        reg1 = _registry(tmp_path)
        idx1 = _allocate("alpha", ["alpha"], 10, reg1)

        reg2 = _registry(tmp_path)
        idx2 = _allocate("alpha", ["alpha"], 10, reg2)
        assert idx1 == idx2 == 1
