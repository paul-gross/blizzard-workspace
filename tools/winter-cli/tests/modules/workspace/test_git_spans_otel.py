"""Git spans through the real OpenTelemetry adapter, against real temporary git repos.

`EnvStatusService` fans its per-worktree status reads out over a thread pool; each read's
`git status` span must still be a child of the command span, and a failing git call must export
its error type and nothing the git error said.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from opentelemetry import trace
from opentelemetry.proto.common.v1.common_pb2 import KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status

from tests.modules.workspace.conftest import add_env_worktree, commit, init_repo
from tests.otlp_receiver import OtlpReceiver
from winter_cli.core.internal.otel_command_tracer import OtelCommandTracer
from winter_cli.core.tracing import TracingSettings
from winter_cli.modules.workspace.env_status_service import EnvStatusService
from winter_cli.modules.workspace.internal.git_ops_service import GitOpsService
from winter_cli.modules.workspace.internal.read_workspace_repository import ReadWorkspaceRepository
from winter_cli.modules.workspace.internal.repo_error_factory import RepoErrorFactory
from winter_cli.modules.workspace.internal.write_repo_repository import WriteRepoRepository
from winter_cli.modules.workspace.models import (
    FeatureEnvironment,
    FeatureEnvironmentWorktrees,
    FeatureWorktree,
    ProjectRepository,
    RepoError,
    Workspace,
)


@pytest.fixture(autouse=True)
def isolated_otel_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test with no inherited trace context and no registered global provider."""
    monkeypatch.delenv("TRACEPARENT", raising=False)
    monkeypatch.delenv("TRACESTATE", raising=False)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace._TRACER_PROVIDER_SET_ONCE, "_done", False)


def _attributes(span: Span) -> dict[str, object]:
    def value(attribute: KeyValue) -> object:
        return getattr(attribute.value, str(attribute.value.WhichOneof("value")))

    return {attribute.key: value(attribute) for attribute in span.attributes}


def _env_with_repos(
    tmp_path: Path, repo_names: list[str]
) -> tuple[Workspace, FeatureEnvironment, list[FeatureWorktree]]:
    """One feature env `alpha` holding a real linked worktree of each named project repo."""
    workspace = Workspace(root_path=tmp_path, service_prefix="t", main_branch="main")
    env = FeatureEnvironment(workspace=workspace, name="alpha", index=1, path=tmp_path / "alpha")
    worktrees: list[FeatureWorktree] = []
    for name in repo_names:
        main_path = tmp_path / "projects" / name
        init_repo(main_path)
        commit(main_path, "f.txt", "root\n", "root")
        add_env_worktree(main_path, tmp_path, "alpha", "main", repo_name=name)
        project = ProjectRepository(name=name, main_path=main_path, main_branch="main")
        worktrees.append(FeatureWorktree(workspace=workspace, environment=env, repository=project))
    return workspace, env, worktrees


def test_env_status_over_three_worktrees_yields_a_git_status_span_each_parented_on_the_command_span(
    tmp_path: Path, otlp_receiver: OtlpReceiver
) -> None:
    _workspace, env, worktrees = _env_with_repos(tmp_path, ["api", "web", "worker"])
    tracer = OtelCommandTracer(TracingSettings(otlp_endpoint=otlp_receiver.endpoint))
    error_factory = RepoErrorFactory()
    service = EnvStatusService(
        worktree_repo=ReadWorkspaceRepository(error_factory, tracer),
        repo_repo=WriteRepoRepository(error_factory, GitOpsService(error_factory), tracer),
    )

    tracer.start_command("winter ws status")
    statuses = service.get_worktree_repo_statuses(FeatureEnvironmentWorktrees(environment=env, worktrees=worktrees))
    tracer.end_command(None)
    tracer.export()

    assert len(statuses) == 3
    (request,) = otlp_receiver.requests
    command = next(span for span in request.spans if span.name == "winter ws status")
    git_spans = [span for span in request.spans if span.name == "git status"]
    assert sorted(str(_attributes(span)["winter.repo"]) for span in git_spans) == ["api", "web", "worker"]
    for span in git_spans:
        assert _attributes(span)["winter.env"] == "alpha"
        assert span.trace_id == command.trace_id
        assert span.parent_span_id == command.span_id


def test_failing_git_call_exports_error_status_and_type_but_none_of_the_error_text(
    tmp_path: Path, otlp_receiver: OtlpReceiver
) -> None:
    _workspace, _env, (worktree,) = _env_with_repos(tmp_path, ["demo"])
    tracer = OtelCommandTracer(TracingSettings(otlp_endpoint=otlp_receiver.endpoint))
    error_factory = RepoErrorFactory()
    writer = WriteRepoRepository(error_factory, GitOpsService(error_factory), tracer)
    secret_ref = "no-such-ref-for-stderr"

    tracer.start_command("winter ws checkout")
    with pytest.raises(RepoError) as caught:
        writer.count_commits_not_in(worktree, secret_ref)
    tracer.end_command("RepoError")
    tracer.export()

    # The raised error carries the message and git's stderr; the span must carry neither.
    assert secret_ref in str(caught.value) or secret_ref in (caught.value.stderr or "")
    (request,) = otlp_receiver.requests
    (span,) = [span for span in request.spans if span.name == "git rev-list"]
    assert span.status.code == Status.STATUS_CODE_ERROR
    assert span.status.message == ""
    assert list(span.events) == []
    assert _attributes(span) == {"winter.repo": "demo", "winter.env": "alpha", "error.type": "RepoError"}
    for text in (secret_ref, "count_commits_not_in failed", "fatal", "bad revision"):
        assert text.encode() not in request.body
