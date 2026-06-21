from __future__ import annotations

from winter_cli.config.overlay import ArrayAppendField, MergeSpec, TableField, overlay_merge

# Merge spec for .winter/config.toml + config.local.toml overlay.
#
# - project_repository and standalone_repository are TOML array-of-tables:
#   the overlay appends entries without wiping the shared set declared in
#   config.toml (ArrayAppendField).
# - [git], [keybindings], [tui], [capabilities]: nested tables merge per-key so
#   a config.local.toml can override individual sub-keys without wiping the
#   entire table (TableField).
# - All other top-level keys default to scalar-replace (handled by MergeSpec's
#   unspecified-key fallback), allowing config.local.toml to trim or rewrite
#   them entirely.
#
# Merges the four nested-table keys (git, keybindings, tui, capabilities) one
# level deep via TableField; a new nested-table key needing per-key overlay must
# be added to this spec explicitly.
_WORKSPACE_CONFIG_SPEC = MergeSpec(
    fields={
        "project_repository": ArrayAppendField(),
        "standalone_repository": ArrayAppendField(),
        "git": TableField(),
        "keybindings": TableField(),
        "tui": TableField(),
        "capabilities": TableField(),
    }
)


def deep_merge(base: dict, overlay: dict) -> dict:
    """Thin shim: delegate to the spec-driven overlay engine.

    Preserved for callers that already import this symbol; new code should
    call ``overlay_merge`` from ``winter_cli.config.overlay`` directly with
    an explicit spec.
    """
    return overlay_merge(base, overlay, spec=_WORKSPACE_CONFIG_SPEC)
