from __future__ import annotations

import enum
from dataclasses import dataclass, field

from winter_cli.core.config_file import ConfigError

PROVISION_SUBTARGETS = ("dependency", "resource", "data")

_ENTRY_ALLOWED_KEYS = frozenset({"scope", "apply", "destroy", "reset", "required_services"})
_SUBTARGETS_WITH_REQUIRED_SERVICES = frozenset({"resource", "data"})


class ProvisionScope(enum.Enum):
    workspace = "workspace"
    feature_environment = "feature-environment"
    feature_worktree = "feature-worktree"


@dataclass(frozen=True)
class ProvisionHandler:
    subtarget: str
    scope: ProvisionScope
    apply: str
    source: str
    destroy: str | None = None
    reset: str | None = None
    required_services: tuple[str, ...] = field(default_factory=tuple)


class ProvisionManifestParser:
    """Parses a raw ``[provision]`` table into a list of ``ProvisionHandler`` objects.

    Raises ``ConfigError`` on any structural or semantic violation.
    """

    def parse(self, raw: dict | None, source: str) -> list[ProvisionHandler]:
        """Parse raw ``[provision]`` table data.

        ``raw`` is the value of the ``[provision]`` table — a dict mapping
        sub-target names to lists of entry dicts.  Returns ``[]`` for
        ``None`` or empty input.
        """
        if not raw:
            return []

        handlers: list[ProvisionHandler] = []

        for key, entries in raw.items():
            if key not in PROVISION_SUBTARGETS:
                valid = ", ".join(repr(s) for s in PROVISION_SUBTARGETS)
                raise ConfigError(
                    f"Unknown provision sub-target {key!r} in {source!r}. "
                    f"Must be one of: {valid}."
                )

            if not isinstance(entries, list):
                raise ConfigError(
                    f"provision.{key} in {source!r} must be a list of tables, "
                    f"got {type(entries).__name__!r}."
                )

            for i, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    raise ConfigError(
                        f"provision.{key}[{i}] in {source!r} must be a table (dict), "
                        f"got {type(entry).__name__!r}."
                    )
                unknown = set(entry.keys()) - _ENTRY_ALLOWED_KEYS
                if unknown:
                    bad = ", ".join(repr(k) for k in sorted(unknown))
                    allowed = ", ".join(repr(k) for k in sorted(_ENTRY_ALLOWED_KEYS))
                    raise ConfigError(
                        f"Unknown key(s) {bad} in provision.{key}[{i}] in {source!r}. "
                        f"Allowed keys: {allowed}."
                    )

                scope_raw = entry.get("scope")
                if scope_raw is None:
                    raise ConfigError(
                        f"provision.{key}[{i}] in {source!r} is missing required field 'scope'."
                    )
                try:
                    scope = ProvisionScope(scope_raw)
                except ValueError:
                    valid_scopes = ", ".join(repr(s.value) for s in ProvisionScope)
                    raise ConfigError(
                        f"Invalid scope {scope_raw!r} in provision.{key}[{i}] in {source!r}. "
                        f"Must be one of: {valid_scopes}."
                    )

                apply_raw = entry.get("apply")
                if not apply_raw or not isinstance(apply_raw, str):
                    raise ConfigError(
                        f"provision.{key}[{i}] in {source!r} is missing required field 'apply' "
                        f"(must be a non-empty string)."
                    )

                destroy_raw = entry.get("destroy")
                destroy = destroy_raw if isinstance(destroy_raw, str) and destroy_raw else None

                reset_raw = entry.get("reset")
                reset = reset_raw if isinstance(reset_raw, str) and reset_raw else None

                required_services_raw = entry.get("required_services")
                if required_services_raw is not None:
                    if key not in _SUBTARGETS_WITH_REQUIRED_SERVICES:
                        raise ConfigError(
                            f"'required_services' is not allowed on provision.{key}[{i}] in {source!r}. "
                            f"'required_services' may only be declared on 'resource' or 'data' handlers."
                        )
                    if not isinstance(required_services_raw, list) or not all(
                        isinstance(s, str) for s in required_services_raw
                    ):
                        raise ConfigError(
                            f"provision.{key}[{i}].required_services in {source!r} must be a list of strings."
                        )
                    required_services: tuple[str, ...] = tuple(required_services_raw)
                else:
                    required_services = ()

                handlers.append(
                    ProvisionHandler(
                        subtarget=key,
                        scope=scope,
                        apply=apply_raw,
                        source=source,
                        destroy=destroy,
                        reset=reset,
                        required_services=required_services,
                    )
                )

        return handlers
