"""Tests for ``service_matches_pattern`` — segment-wise describe/pattern matching.

Providers emit scope-qualified describe identifiers: ``<env>/<svc>`` for a concrete
env, ``*/<svc>`` for a project-scoped (env-agnostic) service, and ``workspace/<svc>``
for a workspace-scoped singleton. A user selection pattern matches such an identifier
segment-by-segment, with wildcards honoured on either side, and a bare pattern is an
environment query that expands to ``<env>/*``.
"""

from __future__ import annotations

import pytest

from winter_cli.modules.service.provider_invocation import service_matches_pattern


@pytest.mark.parametrize(
    ("svc_name", "pattern"),
    [
        # Exact <env>/<svc> match.
        ("beta/api", "beta/api"),
        # Workspace-scope exact match.
        ("workspace/db", "workspace/db"),
        # Project-scoped describe (env-agnostic) matched by a concrete env query.
        ("*/api", "beta/api"),
        # Wildcard on the query env segment.
        ("beta/api", "*/api"),
        # Wildcard on the query svc segment.
        ("beta/api", "beta/*"),
        # Bare env query expands to <env>/* and matches every project service in it.
        ("*/api", "beta"),
        ("*/db", "beta"),
        # Bare env query matches a concrete-env describe identifier.
        ("beta/worker", "beta"),
        # Bare 'workspace' query matches workspace-scoped services.
        ("workspace/db", "workspace"),
        ("workspace/rabbitmq", "workspace"),
    ],
)
def test_matches(svc_name: str, pattern: str) -> None:
    assert service_matches_pattern(svc_name, pattern) is True


@pytest.mark.parametrize(
    ("svc_name", "pattern"),
    [
        # Different svc segment.
        ("*/api", "beta/db"),
        ("beta/api", "beta/db"),
        # Workspace-scoped service excluded by a concrete feature-env query.
        ("workspace/db", "beta/db"),
        # Bare env query does not reach a different env's concrete service.
        ("beta/api", "gamma"),
        # Bare env query does not match a workspace-scoped service.
        ("workspace/db", "beta"),
        # Wildcard env, wrong svc.
        ("*/api", "*/db"),
        # Reserved 'workspace' scope is distinct from the '*' any-feature-env wildcard:
        # a workspace query never pulls in a project-scoped service...
        ("*/api", "workspace"),
        ("*/api", "workspace/api"),
        # ...and a '*' query never pulls in a workspace-scoped singleton.
        ("workspace/db", "*/db"),
    ],
)
def test_non_matches(svc_name: str, pattern: str) -> None:
    assert service_matches_pattern(svc_name, pattern) is False
