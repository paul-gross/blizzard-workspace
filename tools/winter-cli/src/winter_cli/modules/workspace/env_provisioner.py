"""Single source of truth for computing the runtime environment map for a scope.

``EnvProvisionerService.compute(scope)`` returns the complete ``{KEY: VALUE}``
env map that winter injects into every provider subprocess and that
``winter env`` prints as sourceable lines.  All callers — ``winter env``,
``ServiceFanOutService`` (up/down), ``ServiceStatusMatrixService`` (status) —
delegate here so the computation is never duplicated.

Scope semantics
---------------
*scope* is either a feature-env name (e.g. ``"alpha"``) or the reserved literal
``"workspace"``.  The workspace scope uses index 0 (reserved, never allocated to
a feature env); its port base is therefore ``config.port_base_for_index(0)``.

Rendered vars
-------------
The returned map always contains:

    WINTER_ENV                  — scope name
    WINTER_ENV_INDEX            — allocated index as a decimal string
    WINTER_WORKSPACE_PORT_BASE  — port-band start for index 0

For a feature-env scope, additionally:

    WINTER_PORT_BASE            — port-band start for this scope's own band

For the ``"workspace"`` scope, ``WINTER_PORT_BASE`` is deliberately NOT emitted.
The workspace band is exposed ONLY as ``WINTER_WORKSPACE_PORT_BASE`` so the name
carries one meaning everywhere (the per-env band); emitting it under the
workspace value (index-0) would make the name ambiguous across scopes.

Followed by zero or more ``[env.vars]`` entries, rendered in TOML declaration
order.  Each entry may reference any earlier key (including the base vars above)
via ``${NAME}`` or ``${NAME+N}`` tokens.

``_render_env_var_value`` is a module-level helper (previously in
``init_service.py``) that this module re-exports for callers that need it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from winter_cli.modules.service.scope import WORKSPACE_SCOPE
from winter_cli.modules.workspace.env_index import build_env_trio

if TYPE_CHECKING:
    from winter_cli.config.models import WorkspaceConfig
    from winter_cli.modules.workspace.env_index_registry import IEnvIndexRegistry

# Matches ${NAME} or ${NAME+N}: a reference to an in-scope variable, optionally
# plus a non-negative integer offset.  NAME is an env-var-style identifier.
_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:\+(\d+))?\}")
# Matches any ${...} token the reference form did not consume — malformed/unsupported.
_UNKNOWN_TOKEN_RE = re.compile(r"\$\{[^}]*\}")


def _render_env_var_value(key: str, template: str, scope: dict[str, str]) -> str:
    """Resolve ``${NAME}`` / ``${NAME+N}`` references in *template* against *scope*.

    *scope* holds the variables visible to this entry: the managed base vars
    (``WINTER_ENV``, ``WINTER_ENV_INDEX``, ``WINTER_PORT_BASE``,
    ``WINTER_WORKSPACE_PORT_BASE``) plus every earlier ``[env.vars]`` entry
    already rendered, in declaration order.

    - ``${NAME}``   → NAME's resolved string value.
    - ``${NAME+N}`` → ``int(NAME) + N`` (NAME must parse as an int; N ≥ 0).

    Literal values (no ``${...}`` token) pass through unchanged.  A reference to
    an undefined name, a ``+N`` offset applied to a non-integer value, or any
    other ``${...}`` token is a fatal substitution error — raises ``ValueError``
    with a clear message.
    """

    def _replace(m: re.Match[str]) -> str:
        name, offset = m.group(1), m.group(2)
        if name not in scope:
            raise ValueError(
                f"[env.vars] key {key!r}: reference to undefined variable {name!r} "
                f"— reference a managed base var or an earlier [env.vars] entry."
            )
        value = scope[name]
        if offset is None:
            return value
        try:
            return str(int(value) + int(offset))
        except ValueError:
            raise ValueError(
                f"[env.vars] key {key!r}: cannot apply +{offset} to non-integer value of {name!r} ({value!r})."
            ) from None

    rendered = _REF_RE.sub(_replace, template)

    # Any ${...} the reference form left behind is an unsupported token.
    unknown = _UNKNOWN_TOKEN_RE.search(rendered)
    if unknown:
        raise ValueError(
            f"[env.vars] key {key!r}: unsupported substitution token {unknown.group()!r}. "
            f"Use ${{NAME}} or ${{NAME+N}} referencing a managed base var or an earlier entry."
        )
    return rendered


class EnvProvisionerService:
    """Compute the full runtime environment map for any scope.

    The map is the authoritative set of ``WINTER_*`` variables that winter
    injects into provider subprocesses and that ``winter env`` prints.
    Call :meth:`compute` with a feature-env name or ``"workspace"`` to get the
    complete ``{KEY: VALUE}`` dict.

    Construction::

        EnvProvisionerService(config, registry)
    """

    def __init__(
        self,
        config: WorkspaceConfig,
        registry: IEnvIndexRegistry,
    ) -> None:
        self._config = config
        self._registry = registry

    def compute(self, scope: str) -> dict[str, str]:
        """Return the full env map for *scope*.

        For a feature env this is the env trio (``WINTER_ENV``,
        ``WINTER_ENV_INDEX``, ``WINTER_PORT_BASE``) plus
        ``WINTER_WORKSPACE_PORT_BASE`` and any rendered ``[env.vars]`` entries.

        For ``"workspace"``, ``WINTER_ENV``, ``WINTER_ENV_INDEX``, and
        ``WINTER_WORKSPACE_PORT_BASE`` are returned (index 0, the workspace port
        base) plus ``[env.vars]``.  ``WINTER_PORT_BASE`` is deliberately NOT
        included for the workspace scope — the workspace band is exposed only as
        ``WINTER_WORKSPACE_PORT_BASE`` so the name carries one meaning everywhere.

        Raises ``ValueError`` when an ``[env.vars]`` template has an
        unsupported token or a reference to an undefined variable.
        """
        workspace_port_base = str(self._config.port_base_for_index(0))

        if scope == WORKSPACE_SCOPE:
            result: dict[str, str] = {
                "WINTER_ENV": WORKSPACE_SCOPE,
                "WINTER_ENV_INDEX": "0",
                "WINTER_WORKSPACE_PORT_BASE": workspace_port_base,
            }
        else:
            trio = build_env_trio(scope, self._config, self._registry)
            result = {**trio, "WINTER_WORKSPACE_PORT_BASE": workspace_port_base}

        if self._config.env_vars:
            scope_vars = dict(result)
            if scope == WORKSPACE_SCOPE:
                # For template resolution, expose WINTER_PORT_BASE as an alias
                # for WINTER_WORKSPACE_PORT_BASE so that [env.vars] templates
                # written with ${WINTER_PORT_BASE+N} continue to resolve.
                # The alias is NOT propagated to the returned result — the
                # workspace result carries WINTER_WORKSPACE_PORT_BASE only.
                scope_vars["WINTER_PORT_BASE"] = workspace_port_base
            for key, template in self._config.env_vars.items():
                value = _render_env_var_value(key, template, scope_vars)
                scope_vars[key] = value
                result[key] = value

        return result
