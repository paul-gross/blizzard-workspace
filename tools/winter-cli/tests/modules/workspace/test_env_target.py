"""`EnvTargetService`: which env tokens resolve to the one env `winter.env` reports."""

from __future__ import annotations

import pytest

from winter_cli.modules.workspace.env_target import EnvNameDiscovery, EnvTargetService


class _FakeAnnotator:
    def __init__(self) -> None:
        self.envs: list[str] = []

    def annotate_env(self, env_name: str) -> None:
        self.envs.append(env_name)


class _FakeDiscovery:
    """Stands in for `EnvNameDiscovery`; counts discovery runs and can fail."""

    def __init__(self, names: list[str], error: Exception | None = None) -> None:
        self._names = names
        self._error = error
        self.runs = 0

    def names(self) -> list[str]:
        self.runs += 1
        if self._error is not None:
            raise self._error
        return list(self._names)


def _service(discovery: _FakeDiscovery, *, enabled: bool = True) -> tuple[EnvTargetService, _FakeAnnotator]:
    annotator = _FakeAnnotator()
    service = EnvTargetService(
        annotator=annotator,
        tracing_enabled=enabled,
        discovery_factory=lambda: discovery,  # type: ignore[arg-type,return-value]
    )
    return service, annotator


_ENVS = ["alpha", "beta", "gamma"]


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (["alpha"], ["alpha"]),
        (["alpha/winter"], ["alpha"]),
        (["alpha/winter", "alpha/winter-docs"], ["alpha"]),
        (["alpha", "alpha/winter"], ["alpha"]),
        (["alpha", "beta"], []),
        (["alpha/winter", "beta/winter"], []),
        ([], []),
        ([""], []),
        (["workspace"], []),
        (["workspace/nginx"], []),
        (["alpha", "workspace"], ["alpha"]),
        (["workspace/nginx", "alpha/api"], ["alpha"]),
        # A literal segment counts as written, whether or not the env exists yet.
        (["brand-new"], ["brand-new"]),
        # A glob segment counts through discovery.
        (["al*"], ["alpha"]),
        (["*/winter"], []),
        (["*"], []),
        (["zzz*"], []),
        (["al*/winter"], ["alpha"]),
        (["alpha", "al*"], ["alpha"]),
        (["alpha", "b*"], []),
        (["workspace", "al*"], ["alpha"]),
    ],
)
def test_tokens_report_the_one_env_they_resolve_to(tokens: list[str], expected: list[str]) -> None:
    service, annotator = _service(_FakeDiscovery(_ENVS))

    service.report(tokens)

    assert annotator.envs == expected


def test_trailing_tokens_that_are_not_targets_are_left_out() -> None:
    service, annotator = _service(_FakeDiscovery(_ENVS))

    service.report(["alpha", "feature/x"], trailing_non_targets=1)
    service.report(["alpha", "beta", "main"], trailing_non_targets=1)
    service.report(["alpha"], trailing_non_targets=1)
    service.report([], trailing_non_targets=1)

    assert annotator.envs == ["alpha"]


def test_literal_targets_run_no_discovery() -> None:
    discovery = _FakeDiscovery(_ENVS)
    service, _ = _service(discovery)

    service.report(["alpha", "beta/winter"])

    assert discovery.runs == 0


def test_with_tracing_off_nothing_is_reported_and_no_discovery_runs() -> None:
    discovery = _FakeDiscovery(_ENVS)
    service, annotator = _service(discovery, enabled=False)

    service.report(["alpha"])
    service.report(["al*"])

    assert annotator.envs == []
    assert discovery.runs == 0


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (["alpha"], ["alpha"]),
        (["winter"], []),
        (["alpha", "winter"], ["alpha"]),
        (["alpha", "beta"], []),
        (["al*"], ["alpha"]),
        (["winter*"], []),
        (["brand-new"], []),
        (["workspace"], []),
    ],
)
def test_discovered_only_counts_only_names_that_are_discovered_envs(tokens: list[str], expected: list[str]) -> None:
    service, annotator = _service(_FakeDiscovery(_ENVS))

    service.report(tokens, discovered_only=True)

    assert annotator.envs == expected


def test_a_discovery_failure_is_swallowed_and_reports_nothing() -> None:
    service, annotator = _service(_FakeDiscovery(_ENVS, error=RuntimeError("workspace unreadable")))

    service.report(["al*"])
    service.report(["alpha"], discovered_only=True)

    assert annotator.envs == []


def test_an_annotator_failure_is_swallowed() -> None:
    class _FailingAnnotator:
        def annotate_env(self, env_name: str) -> None:
            raise RuntimeError("span is gone")

    service = EnvTargetService(
        annotator=_FailingAnnotator(),
        tracing_enabled=True,
        discovery_factory=lambda: _FakeDiscovery(_ENVS),  # type: ignore[arg-type,return-value]
    )

    service.report(["alpha"])


def test_env_name_discovery_lists_the_discovered_env_names() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    workspace_repo = MagicMock()
    workspace_repo.get_environments.return_value = [SimpleNamespace(name="alpha"), SimpleNamespace(name="beta")]
    repo_factory = MagicMock()
    repo_factory.get_project_repos.return_value = ["repo"]
    workspace = MagicMock()

    names = EnvNameDiscovery(workspace_repo, repo_factory, workspace).names()

    assert names == ["alpha", "beta"]
    workspace_repo.get_environments.assert_called_once_with(workspace, ["repo"])
