from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import (
    FakeConfigFileReader,
    FakeFilesystem,
    FakeServiceReporter,
    FakeSpecLoader,
    FakeSubprocessRunner,
)
from winter_cli.core.subprocess_runner import SubprocessResult
from winter_cli.modules.capability.capability_registry_service import CapabilityRegistryService
from winter_cli.modules.service.describe_parser import DescribeResultParser
from winter_cli.modules.service.models import LogOptions
from winter_cli.modules.service.orchestrator_resolver import ResolvedOrchestrator, ServiceOrchestratorResolver
from winter_cli.modules.service.service_logs_service import ServiceLogsService
from winter_cli.modules.service.service_provider_index import ServiceDescribeService
from winter_cli.modules.workspace.extension_manifest import EXT_MANIFEST, ExtensionManifestLoader
from winter_cli.modules.workspace.models import StandaloneRepository

WS = Path("/ws")
EXT = WS / "winter-service-tmux"
ENTRYPOINT = EXT / "workflow/logs"
PREFIX = "winter-service-tmux"
SERVICE_PREFIX = "winter"


def _cmd_key(
    patterns: tuple[str, ...],
    *,
    tail: int | str = 200,
    since: str = "",
    until: str = "",
    follow: bool = False,
    timestamps: bool = False,
) -> str:
    """Reconstruct the expected orchestrator command string for FakeSubprocessRunner.

    Mirrors ``ServiceLogsService._stream_single``'s argv order: positional
    patterns, then ``--tail`` (always), ``--since``/``--until`` (when set),
    and the bare ``--follow``/``--timestamps`` flags (when true).
    """
    parts = [str(ENTRYPOINT), "logs", *patterns, "--tail", str(tail)]
    if since:
        parts += ["--since", since]
    if until:
        parts += ["--until", until]
    if follow:
        parts.append("--follow")
    if timestamps:
        parts.append("--timestamps")
    return " ".join(parts)


CMD_KEY = _cmd_key(("alpha",))


CONFIG_DIR = WS / ".winter" / "config" / "winter-service-tmux"


def _resolved() -> ResolvedOrchestrator:
    return ResolvedOrchestrator(entrypoint=ENTRYPOINT, ext_dir=EXT, prefix=PREFIX, config_dir=CONFIG_DIR)


def _opts(**kwargs: Any) -> LogOptions:
    defaults: dict[str, Any] = {
        "patterns": ("alpha",),
        "follow": False,
        "tail": 200,
        "since_rfc3339": "",
        "until_rfc3339": "",
        "timestamps": False,
    }
    defaults.update(kwargs)
    return LogOptions(**defaults)


def _make_single_provider_registry(
    runner: FakeSubprocessRunner,
) -> tuple[CapabilityRegistryService, ServiceOrchestratorResolver]:
    """Build a registry + resolver wired to a single tmux provider."""
    repo = StandaloneRepository(name="winter-service-tmux", path=WS / "winter-service-tmux")
    loader = ExtensionManifestLoader(
        config_file_reader=FakeConfigFileReader({repo.path / EXT_MANIFEST: {"orchestrate_services": "workflow/logs"}})
    )
    fs = FakeFilesystem(files={repo.path / EXT_MANIFEST: "", repo.path / "workflow/logs": ""})
    registry = CapabilityRegistryService(
        repo_factory=_StubRepoFactory([repo]),
        manifest_loader=loader,
        bindings={"service": ["winter-service-tmux"]},
        fs=fs,
        spec_loader=FakeSpecLoader(),
    )
    resolver = ServiceOrchestratorResolver(
        registry=registry,
        repo_factory=_StubRepoFactory([repo]),
        manifest_loader=loader,
        fs=fs,
    )
    return registry, resolver


class _StubRepoFactory:
    def __init__(self, repos: list[StandaloneRepository]) -> None:
        self._repos = repos

    def get_standalone_repos(self) -> list[StandaloneRepository]:
        return self._repos


def _svc(
    runner: FakeSubprocessRunner | None = None,
) -> ServiceLogsService:
    _runner = runner or FakeSubprocessRunner()
    _registry, res = _make_single_provider_registry(_runner)
    describe_svc = ServiceDescribeService(
        subprocess_runner=_runner,
        describe_parser=DescribeResultParser(),
        workspace_root=WS,
        service_prefix=SERVICE_PREFIX,
    )
    return ServiceLogsService(
        subprocess_runner=_runner,
        orchestrator_resolver=res,
        describe_service=describe_svc,
        workspace_root=WS,
        service_prefix=SERVICE_PREFIX,
    )


def _reporter() -> FakeServiceReporter:
    return FakeServiceReporter()


# ── render flags on argv ──────────────────────────────────────────────────────


def test_stream_appends_render_flags_to_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render options ride on argv (not WINTER_LOG_* env vars) before invoking popen."""
    monkeypatch.setenv("WINTER_TEST_CANARY", "canary")
    multi_cmd_key = _cmd_key(("alpha/api", "alpha/db"), tail=50, timestamps=True)
    runner = FakeSubprocessRunner(
        popen_responses={multi_cmd_key: (['{"ts":"2026-06-13T10:00:01Z","env":"alpha","svc":"api","msg":"up"}'], 0)}
    )
    _svc(runner).stream(_opts(patterns=("alpha/api", "alpha/db"), follow=False, tail=50, timestamps=True), _reporter())

    assert runner.popen_calls[0][0] == [
        str(ENTRYPOINT),
        "logs",
        "alpha/api",
        "alpha/db",
        "--tail",
        "50",
        "--timestamps",
    ]


def test_stream_no_winter_log_env_vars_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The five WINTER_LOG_* dispatch vars are no longer set on the subprocess env."""
    monkeypatch.setenv("WINTER_TEST_CANARY", "canary")
    runner = FakeSubprocessRunner(popen_responses={CMD_KEY: ([], 0)})
    _svc(runner).stream(_opts(), _reporter())

    env = runner.popen_envs[0]
    for key in (
        "WINTER_LOG_FOLLOW",
        "WINTER_LOG_TAIL",
        "WINTER_LOG_SINCE",
        "WINTER_LOG_UNTIL",
        "WINTER_LOG_TIMESTAMPS",
    ):
        assert key not in env


def test_stream_sets_workspace_context_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """WINTER_WORKSPACE_DIR, WINTER_EXT_DIR, WINTER_EXT_PREFIX, and WINTER_SERVICE_PREFIX are always injected.

    ``logs`` is one of the dispatch surfaces that only ever receives the base
    extension vars (never the scope vars) — WINTER_SERVICE_PREFIX must still be
    present here since it is workspace-invariant, not a scope var.
    """
    monkeypatch.setenv("WINTER_TEST_CANARY", "canary")
    runner = FakeSubprocessRunner(popen_responses={CMD_KEY: ([], 0)})
    _svc(runner).stream(_opts(), _reporter())

    env = runner.popen_envs[0]
    assert env["WINTER_WORKSPACE_DIR"] == str(WS)
    assert env["WINTER_EXT_DIR"] == str(EXT)
    assert env["WINTER_EXT_PREFIX"] == PREFIX
    assert env["WINTER_SERVICE_PREFIX"] == SERVICE_PREFIX


def test_stream_inherits_parent_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The orchestrator env starts from os.environ so inherited vars are preserved."""
    monkeypatch.setenv("WINTER_SENTINEL", "hello")
    runner = FakeSubprocessRunner(popen_responses={CMD_KEY: ([], 0)})
    _svc(runner).stream(_opts(), _reporter())

    assert runner.popen_envs[0]["WINTER_SENTINEL"] == "hello"


def test_stream_tail_always_emitted_including_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """--tail is emitted unconditionally, carrying the resolved count string (incl. 'all')."""
    monkeypatch.setenv("WINTER_TEST_CANARY", "canary")
    key = _cmd_key(("alpha",), tail="all")
    runner = FakeSubprocessRunner(popen_responses={key: ([], 0)})
    _svc(runner).stream(_opts(tail="all"), _reporter())
    cmd = runner.popen_calls[0][0]
    assert cmd[-2:] == ["--tail", "all"]


def test_stream_sets_follow_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINTER_TEST_CANARY", "canary")
    key = _cmd_key(("alpha",), follow=True)
    runner = FakeSubprocessRunner(popen_responses={key: ([], 0)})
    _svc(runner).stream(_opts(follow=True), _reporter())
    assert "--follow" in runner.popen_calls[0][0]


def test_stream_sets_since_until_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINTER_TEST_CANARY", "canary")
    key = _cmd_key(("alpha",), since="2026-06-13T10:00:00Z", until="2026-06-13T12:00:00Z")
    runner = FakeSubprocessRunner(popen_responses={key: ([], 0)})
    _svc(runner).stream(
        _opts(since_rfc3339="2026-06-13T10:00:00Z", until_rfc3339="2026-06-13T12:00:00Z"),
        _reporter(),
    )
    cmd = runner.popen_calls[0][0]
    assert cmd[cmd.index("--since") + 1] == "2026-06-13T10:00:00Z"
    assert cmd[cmd.index("--until") + 1] == "2026-06-13T12:00:00Z"


# ── rendered output ───────────────────────────────────────────────────────────


def test_stream_renders_ndjson_lines_to_stdout() -> None:
    """Parsed NDJSON lines are passed to reporter.log_line as rendered plain text."""
    runner = FakeSubprocessRunner(
        popen_responses={
            CMD_KEY: (
                [
                    '{"ts":"2026-06-13T10:00:01Z","env":"alpha","svc":"api","msg":"started"}',
                    '{"ts":"2026-06-13T10:00:02Z","env":"alpha","svc":"db","msg":"ready"}',
                ],
                0,
            )
        }
    )
    rep = _reporter()
    _svc(runner).stream(_opts(), rep)
    assert "alpha/api | started" in rep.log_lines
    assert "alpha/db | ready" in rep.log_lines


def test_stream_does_not_echo_to_stderr_when_no_warnings() -> None:
    """No warning events fired when orchestrator lines all carry timestamps."""
    runner = FakeSubprocessRunner(
        popen_responses={
            CMD_KEY: (
                ['{"ts":"2026-06-13T10:00:01Z","env":"alpha","svc":"api","msg":"up"}'],
                0,
            )
        }
    )
    rep = _reporter()
    _svc(runner).stream(_opts(), rep)
    assert rep.timestamps_warning_called == 0
    assert rep.time_filter_warning_called == 0


# ── exit code passthrough ─────────────────────────────────────────────────────


def test_stream_passes_exit_code_through() -> None:
    runner = FakeSubprocessRunner(popen_responses={CMD_KEY: ([], 42)})
    assert _svc(runner).stream(_opts(), _reporter()) == 42


def test_stream_returns_zero_on_clean_exit() -> None:
    runner = FakeSubprocessRunner(popen_responses={CMD_KEY: ([], 0)})
    assert _svc(runner).stream(_opts(), _reporter()) == 0


# ── KeyboardInterrupt paths ───────────────────────────────────────────────────


class _InterruptOnIterRunner:
    """ISubprocessRunner that raises KeyboardInterrupt while iterating stdout_lines."""

    def run(self, cmd: list[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None) -> SubprocessResult:
        raise AssertionError("unexpected run call")

    def call(self, cmd: list[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None) -> int:
        raise AssertionError("unexpected call")

    @contextmanager
    def popen(
        self,
        cmd: list[str] | str,
        *,
        cwd: Path | None = None,
        env: Any = None,
        shell: bool = False,
        merge_stderr: bool = True,
    ) -> Iterator[Any]:
        yield _InterruptOnIterProcess()


class _InterruptOnIterProcess:
    @property
    def stdout_lines(self) -> Iterator[str]:
        raise KeyboardInterrupt
        yield  # make it a generator

    def wait(self) -> int:
        return 0


class _InterruptOnPopenRunner:
    """ISubprocessRunner that raises KeyboardInterrupt before entering the popen context."""

    def run(self, cmd: list[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None) -> SubprocessResult:
        raise AssertionError("unexpected run call")

    def call(self, cmd: list[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None) -> int:
        raise AssertionError("unexpected call")

    @contextmanager
    def popen(
        self,
        cmd: list[str] | str,
        *,
        cwd: Path | None = None,
        env: Any = None,
        shell: bool = False,
        merge_stderr: bool = True,
    ) -> Iterator[Any]:
        raise KeyboardInterrupt
        yield  # type: ignore[misc]  # unreachable — makes this function a generator


def test_stream_returns_130_on_keyboard_interrupt_during_iteration() -> None:
    """KeyboardInterrupt raised while reading stdout_lines returns 130."""
    interrupt_runner = _InterruptOnIterRunner()
    _registry, res = _make_single_provider_registry(FakeSubprocessRunner())
    describe_svc = ServiceDescribeService(
        subprocess_runner=FakeSubprocessRunner(),
        describe_parser=DescribeResultParser(),
        workspace_root=WS,
        service_prefix=SERVICE_PREFIX,
    )
    svc = ServiceLogsService(
        subprocess_runner=interrupt_runner,
        orchestrator_resolver=res,
        describe_service=describe_svc,
        workspace_root=WS,
        service_prefix=SERVICE_PREFIX,
    )
    assert svc.stream(_opts(), _reporter()) == 130


def test_stream_returns_130_on_keyboard_interrupt_at_popen() -> None:
    """KeyboardInterrupt raised at popen entry returns 130."""
    interrupt_runner = _InterruptOnPopenRunner()
    _registry, res = _make_single_provider_registry(FakeSubprocessRunner())
    describe_svc = ServiceDescribeService(
        subprocess_runner=FakeSubprocessRunner(),
        describe_parser=DescribeResultParser(),
        workspace_root=WS,
        service_prefix=SERVICE_PREFIX,
    )
    svc = ServiceLogsService(
        subprocess_runner=interrupt_runner,
        orchestrator_resolver=res,
        describe_service=describe_svc,
        workspace_root=WS,
        service_prefix=SERVICE_PREFIX,
    )
    assert svc.stream(_opts(), _reporter()) == 130


# ── conditional stderr warnings ───────────────────────────────────────────────


def test_stream_emits_timestamps_warning_when_ts_missing_and_timestamps_requested() -> None:
    """`-t` requested but lines carry no ts field → timestamps_warning fired on reporter."""
    key = _cmd_key(("alpha",), timestamps=True)
    runner = FakeSubprocessRunner(popen_responses={key: (['{"env":"alpha","svc":"api","msg":"up"}'], 0)})
    rep = _reporter()
    _svc(runner).stream(_opts(timestamps=True), rep)

    assert rep.timestamps_warning_called == 1


def test_stream_emits_time_filter_warning_when_ts_missing_and_since_set() -> None:
    """--since set but some lines carry no ts → time_filter_warning fired on reporter."""
    key = _cmd_key(("alpha",), since="2026-06-13T10:00:00Z")
    runner = FakeSubprocessRunner(popen_responses={key: (['{"env":"alpha","svc":"api","msg":"up"}'], 0)})
    rep = _reporter()
    _svc(runner).stream(
        _opts(since_rfc3339="2026-06-13T10:00:00Z"),
        rep,
    )

    assert rep.time_filter_warning_called == 1


def test_stream_popen_invoked_with_merge_stderr_false() -> None:
    """popen is always called with merge_stderr=False so orchestrator stderr reaches the terminal."""
    runner = FakeSubprocessRunner(popen_responses={CMD_KEY: ([], 0)})
    _svc(runner).stream(_opts(), _reporter())
    assert runner.popen_merge_stderr == [False]


# ── workspace scope forwarding ────────────────────────────────────────────────


def test_stream_workspace_pattern_forwarded_verbatim_on_argv() -> None:
    """'workspace' pattern is forwarded verbatim as a positional argv token (before render flags)."""
    key = _cmd_key(("workspace",))
    runner = FakeSubprocessRunner(popen_responses={key: ([], 0)})
    _svc(runner).stream(_opts(patterns=("workspace",)), _reporter())
    assert runner.popen_calls[0][0] == [str(ENTRYPOINT), "logs", "workspace", "--tail", "200"]


def test_stream_workspace_service_pattern_forwarded_verbatim_on_argv() -> None:
    """'workspace/<svc>' pattern is forwarded verbatim as a positional argv token (before render flags)."""
    key = _cmd_key(("workspace/nginx",))
    runner = FakeSubprocessRunner(popen_responses={key: ([], 0)})
    _svc(runner).stream(_opts(patterns=("workspace/nginx",)), _reporter())
    assert runner.popen_calls[0][0] == [str(ENTRYPOINT), "logs", "workspace/nginx", "--tail", "200"]
