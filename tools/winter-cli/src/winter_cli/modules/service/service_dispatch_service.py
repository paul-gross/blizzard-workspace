from __future__ import annotations

from pathlib import Path

from winter_cli.core.subprocess_runner import ISubprocessRunner
from winter_cli.modules.capability.models import ResolvedCapability
from winter_cli.modules.service.orchestrator_resolver import ServiceOrchestratorResolver
from winter_cli.modules.service.provider_invocation import (
    build_provider_env,
    restart_pattern_env_known,
    service_matches_pattern,
    up_down_positional,
)
from winter_cli.modules.service.service_fan_out_service import FanOutCell, ServiceFanOutService
from winter_cli.modules.service.service_provider_index import ServiceDescribeService
from winter_cli.modules.service.service_readiness_service import DEFAULT_WAIT_TIMEOUT_S
from winter_cli.modules.service.service_reporter import IServiceReporter
from winter_cli.modules.service.service_status_matrix_service import (
    ServiceStatusMatrixService,
    cell_service_patterns,
)


class ServiceDispatchService:
    """Dispatches up/down/restart to the registered service orchestrator(s).

    For ``up`` and ``down``, reuses ``ServiceStatusMatrixService.build_matrix`` to
    enumerate the matched (provider, scope) cells for the user's ``<env>/<service>``
    glob PATTERNS — the same registry-driven enumeration `status` uses — and fans
    them out via ``ServiceFanOutService`` with no readiness gate or ordering
    semantics beyond the matrix's own deterministic cell order.

    For ``restart``, every pattern is first validated against the known env/service
    catalog (winter#149) — a pattern matching neither a configured env nor (when
    ownership is known) a known service is a hard error, not a silently-dropped
    no-op. With multiple providers, builds the service-to-provider ownership
    index via ``ServiceDescribeService``, groups matched services by owning provider,
    and dispatches each provider only the services it owns.  With a single provider,
    ``restart`` still makes no ``describe`` call (only the env segment is
    validated, against the configured-env registry) and forwards patterns
    verbatim once validated.

    For other actions (``describe``, etc.), dispatches to the single resolved provider
    via the orchestrator resolver, as before.

    The orchestrator is invoked as `<entrypoint> <action> [positional...]` (argv),
    with `cwd` at the workspace root. Every dispatch exports `WINTER_WORKSPACE_DIR`,
    `WINTER_EXT_DIR`, `WINTER_EXT_PREFIX`, and `WINTER_SERVICE_PREFIX` (matching the
    doctor/lint/hook dispatches).

    For up/down the positional per cell is the bare scope (no service-segment filter
    matched), the scope-qualified pattern (exactly one real filter matched — see
    ``up_down_positional``), or, when 2+ distinct service-segment filters target the
    same (provider, scope) cell, one FanOutCell per service pattern (``cell_service_patterns``)
    so the provider starts/stops exactly the requested services rather than the whole
    scope (winter#139 MUST-FIX — up/down has no post-dispatch backstop filter like
    `status` does). For restart the positionals are the verbatim `<env>/<service>`
    selection PATTERNS forwarded unchanged on argv. No per-action selection env vars
    are set here (status is handled separately by ServiceStatusService; logs is
    handled separately by ServiceLogsService).

    The entrypoint's exit code is returned unmodified; stdout/stderr are
    inherited from the parent process (no capture).
    """

    def __init__(
        self,
        subprocess_runner: ISubprocessRunner,
        orchestrator_resolver: ServiceOrchestratorResolver,
        fan_out_service: ServiceFanOutService,
        describe_service: ServiceDescribeService,
        matrix_service: ServiceStatusMatrixService,
        workspace_root: Path,
        service_prefix: str,
        reporter: IServiceReporter | None = None,
    ) -> None:
        self._subprocess_runner = subprocess_runner
        self._orchestrator_resolver = orchestrator_resolver
        self._fan_out_service = fan_out_service
        self._describe_service = describe_service
        self._matrix_service = matrix_service
        self._workspace_root = workspace_root
        self._service_prefix = service_prefix
        self._reporter = reporter

    def dispatch(self, action: str, positionals: list[str], timeout_s: float = DEFAULT_WAIT_TIMEOUT_S) -> int:
        """Run the orchestrator's entrypoint and return its exit code unmodified.

        ``timeout_s`` is only consulted for ``up`` — it is injected into every
        matched cell's provider subprocess env as ``WINTER_SERVICE_TIMEOUT`` (see
        ``ServiceFanOutService.up``), regardless of whether the caller passed
        ``--wait``. It defaults to ``DEFAULT_WAIT_TIMEOUT_S`` so callers that have
        no ``--timeout`` concept of their own (e.g. provision's service check)
        still inject the effective default.
        """
        if action == "up":
            return self._dispatch_up_down("up", tuple(positionals), timeout_s)

        if action == "down":
            return self._dispatch_up_down("down", tuple(positionals))

        if action == "restart":
            return self._dispatch_restart(positionals)

        # For all other actions (describe, …), fall through to the
        # single-provider path via the orchestrator resolver.
        resolved = self._orchestrator_resolver.resolve()
        cmd = [str(resolved.entrypoint), action, *positionals]
        merged = build_provider_env(resolved, self._workspace_root, self._service_prefix)
        return self._subprocess_runner.call(cmd, cwd=self._workspace_root, env=merged)

    def _dispatch_up_down(
        self, action: str, patterns: tuple[str, ...], timeout_s: float = DEFAULT_WAIT_TIMEOUT_S
    ) -> int:
        """Fan ``up``/``down`` out across every matched (provider, scope) cell.

        Reuses ``ServiceStatusMatrixService.build_matrix`` for enumeration — the
        same registry-driven ``IEnvIndexRegistry.all_assignments()`` + (multi-provider)
        ``describe`` ownership rows/columns `status` builds — instead of duplicating
        that logic here. Each cell's dispatch positional is the bare scope when the
        cell carries no service-segment filter, or the scope-qualified pattern when
        it does (``up_down_positional``). No cells matched (e.g. an env name/glob
        that matches no configured env or owning provider) surfaces a diagnostic and
        returns 1, mirroring `status`'s ``no_service_matched`` behaviour.

        Unlike `status`, up/down has no post-dispatch backstop filter — so a scope
        matched by 2+ distinct service-segment patterns (``down alpha/db alpha/api``)
        must NOT collapse to the matrix's whole-scope ``"<scope>/*"`` cell pattern
        (that would dispatch a bare ``down alpha``, stopping the entire scope instead
        of just the named services — winter#139 MUST-FIX). ``cell_service_patterns``
        detects this case and each matched cell is expanded into one FanOutCell per
        service pattern, each carrying its own ``<scope>/<svc>`` positional.

        ``timeout_s`` is forwarded to ``ServiceFanOutService.up`` (ignored for
        ``down``), which injects it as ``WINTER_SERVICE_TIMEOUT`` into every
        cell's provider subprocess env.
        """
        providers = self._orchestrator_resolver.resolve_all()

        def _on_describe_error(name: str, detail: str) -> None:
            if self._reporter is not None:
                self._reporter.describe_parse_error(name, detail)

        cells = self._matrix_service.build_matrix(providers, patterns, on_describe_error=_on_describe_error)

        if not cells:
            if patterns and self._reporter is not None:
                self._reporter.no_service_matched(", ".join(repr(p) for p in patterns))
            return 1

        fan_cells: list[FanOutCell] = []
        for cell in cells:
            svc_patterns = cell_service_patterns(cell.scope, patterns)
            if svc_patterns is not None and len(svc_patterns) >= 2:
                for svc in svc_patterns:
                    fan_cells.append(
                        FanOutCell(
                            provider=cell.provider,
                            scope=cell.scope,
                            positional=f"{cell.scope}/{svc}",
                        )
                    )
            else:
                fan_cells.append(
                    FanOutCell(
                        provider=cell.provider,
                        scope=cell.scope,
                        positional=up_down_positional(cell.scope, cell.cell_pattern),
                    )
                )

        if action == "up":
            return self._fan_out_service.up(fan_cells, timeout_s)
        return self._fan_out_service.down(fan_cells)

    def _dispatch_restart(self, patterns: list[str]) -> int:
        """Route restart to the owning provider(s) based on the service ownership index.

        Every pattern is first validated against the known env/service catalog
        (winter#149): a pattern whose env segment names neither a configured env,
        the reserved ``workspace`` scope, nor the cross-env wildcard ``*`` is a
        hard error — this catches a bare token that was intended as a qualified
        ``<env>/<service>`` selector (e.g. ``restart alpha repo-name``, where
        ``repo-name`` is silently parsed as the unrelated env query
        ``repo-name/*``) before any provider is invoked, rather than dropping it
        while the valid ``alpha`` token restarts the whole env as a side effect.
        A qualified pattern (``<env>/<svc>``) whose service segment matches no
        known service is likewise a hard error once ownership is known
        (multi-provider only — see below). A bare env-only pattern is not held
        to that stricter check: it selects "every service in that env", valid
        regardless of whether the env currently has any concretely-known
        service.

        D1 short-circuit: with a single provider, no describe call is made (this
        validation only consults the configured-env registry, not the provider's
        service catalog); the provider receives all patterns verbatim once the
        env-segment check passes.

        With multiple providers, the ownership index is built, each user pattern
        is matched against the known service names, and each provider receives
        only the original pattern tokens that match services it owns (in the
        user-supplied order, deduplicated per provider).
        """
        providers = self._orchestrator_resolver.resolve_all()
        known_envs = self._matrix_service.known_envs()
        env_invalid = [pat for pat in patterns if not restart_pattern_env_known(pat, known_envs)]

        # D1: single-provider short-circuit — no describe, forward verbatim
        # once every pattern's env segment is known.
        if len(providers) == 1:
            if env_invalid:
                self._report_invalid_restart_patterns(env_invalid, known_envs)
                return 1
            provider = providers[0]
            return self._call_provider(provider, "restart", patterns)

        # Multi-provider: build the ownership index.
        index = self._describe_service.build(providers)

        # Collect all known service names from the index.
        known_services = list(index.known_service_names())

        # For each provider, collect the original pattern tokens that match its
        # owned services.  Each pattern is forwarded at most once per provider
        # (deduplicated while preserving first-match order).
        provider_patterns: dict[str, list[str]] = {p.extension_name: [] for p in providers}

        matched_patterns: set[str] = set()
        for pat in patterns:
            for svc_name in known_services:
                owner = index.owner_for(svc_name)
                if owner is None:
                    continue
                if service_matches_pattern(svc_name, pat):
                    matched_patterns.add(pat)
                    owned = provider_patterns[owner.extension_name]
                    if pat not in owned:
                        owned.append(pat)

        # A qualified pattern that matches no known service is a hard error
        # (winter#149); a bare env-only pattern is exempt from this check (see
        # docstring above) as long as its env segment already passed.
        svc_invalid = [pat for pat in patterns if "/" in pat and pat not in matched_patterns and pat not in env_invalid]
        invalid = [pat for pat in patterns if pat in env_invalid or pat in svc_invalid]
        if invalid:
            self._report_invalid_restart_patterns(invalid, known_envs, svc_invalid=frozenset(svc_invalid))
            return 1

        # Dispatch each provider that owns matched patterns.
        exit_code = 0
        for provider in providers:
            owned = provider_patterns.get(provider.extension_name, [])
            if not owned:
                continue
            code = self._call_provider(provider, "restart", owned)
            if code != 0 and exit_code == 0:
                exit_code = code

        return exit_code

    def _report_invalid_restart_patterns(
        self,
        invalid: list[str],
        known_envs: frozenset[str],
        svc_invalid: frozenset[str] = frozenset(),
    ) -> None:
        """Emit one hard-error diagnostic naming every invalid restart pattern.

        Two distinct causes land here and are worded differently (winter#149):

        - A pattern in *svc_invalid* is already qualified as ``<env>/<svc>``
          against a real, known env — its service segment is the only thing
          wrong. The diagnostic names that env (the pattern's own, not some
          other configured env) and the missing service directly, and does
          not suggest re-running the identical qualified form.
        - Every other pattern here (a bare token, or one whose env segment
          matches no configured env) is diagnosed as an unknown environment,
          with a suggestion to use it as the service half of a qualified
          pattern under an actual configured env (or the generic ``<env>``
          placeholder when none are configured).
        """
        if self._reporter is None:
            return
        example_env = sorted(known_envs)[0] if known_envs else "<env>"
        parts: list[str] = []
        for pat in invalid:
            if pat in svc_invalid:
                env_seg, _, svc_seg = pat.partition("/")
                parts.append(f"{pat!r}: no service {svc_seg!r} in environment {env_seg!r}")
            else:
                suggestion = f"{example_env}/{pat}"
                parts.append(f"{pat!r} is not a valid environment — did you mean {suggestion!r}?")
        self._reporter.invalid_restart_pattern("; ".join(parts))

    def _call_provider(self, provider: ResolvedCapability, action: str, positionals: list[str]) -> int:
        cmd = [str(provider.entrypoint), action, *positionals]
        merged = build_provider_env(provider, self._workspace_root, self._service_prefix)
        return self._subprocess_runner.call(cmd, cwd=self._workspace_root, env=merged)
