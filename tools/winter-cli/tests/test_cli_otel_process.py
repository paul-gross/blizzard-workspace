"""Process-level tracing behavior: real `python -m winter_cli.cli` runs against localhost endpoints.

These cover what only a whole process can show: the import cost of leaving tracing off, the
exit code and stderr a command keeps when the endpoint misbehaves, and which switches surface
`opentelemetry` diagnostics.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.otlp_receiver import HangingEndpoint, OtlpReceiver

_TRACING_ENV_PREFIXES = ("OTEL_", "WINTER_OTEL_", "WINTER_LOG_LEVEL", "WINTER_LAUNCH_TIME", "TRACEPARENT")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """The smallest workspace a read-only command (`capabilities`) runs in."""
    (tmp_path / ".winter").mkdir()
    (tmp_path / ".winter" / "config.toml").write_text('main_branch = "master"\n')
    return tmp_path


def _winter(
    workspace: Path, *argv: str, python_flags: tuple[str, ...] = (), **env: str
) -> subprocess.CompletedProcess[str]:
    base = {key: value for key, value in os.environ.items() if not key.startswith(_TRACING_ENV_PREFIXES)}
    return subprocess.run(
        [sys.executable, *python_flags, "-m", "winter_cli.cli", *argv],
        cwd=workspace,
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def _imported_opentelemetry(importtime_stderr: str) -> bool:
    return any(
        line.rstrip().endswith("| opentelemetry") or "| opentelemetry." in line
        for line in importtime_stderr.splitlines()
    )


# ── Disabled cost ────────────────────────────────────────────────────────────


def test_no_endpoint_imports_no_opentelemetry_even_with_the_generic_endpoint_set(workspace: Path) -> None:
    result = _winter(
        workspace,
        "ws",
        "status",
        python_flags=("-X", "importtime"),
        OTEL_EXPORTER_OTLP_ENDPOINT="http://127.0.0.1:4318",
    )

    assert "import time:" in result.stderr
    assert not _imported_opentelemetry(result.stderr)


def test_the_kill_switch_imports_no_opentelemetry_even_with_the_endpoint_set(
    workspace: Path, otlp_receiver: OtlpReceiver
) -> None:
    result = _winter(
        workspace,
        "ws",
        "status",
        python_flags=("-X", "importtime"),
        WINTER_OTEL_EXPORTER_OTLP_ENDPOINT=otlp_receiver.endpoint,
        OTEL_SDK_DISABLED="true",
    )

    assert "import time:" in result.stderr
    assert not _imported_opentelemetry(result.stderr)
    assert otlp_receiver.requests == []


def test_the_endpoint_loads_opentelemetry(workspace: Path, otlp_receiver: OtlpReceiver) -> None:
    """Positive control: the import check above can fail only if tracing really is the importer."""
    result = _winter(
        workspace,
        "ws",
        "status",
        python_flags=("-X", "importtime"),
        WINTER_OTEL_EXPORTER_OTLP_ENDPOINT=otlp_receiver.endpoint,
    )

    assert _imported_opentelemetry(result.stderr)


# ── A command end to end ─────────────────────────────────────────────────────


def test_a_command_exports_one_root_span_nested_under_the_caller(workspace: Path, otlp_receiver: OtlpReceiver) -> None:
    launched = f"{time.time() - 2:.6f}"
    result = _winter(
        workspace,
        "capabilities",
        WINTER_OTEL_EXPORTER_OTLP_ENDPOINT=otlp_receiver.endpoint,
        WINTER_LAUNCH_TIME=launched.replace(".", ","),
        TRACEPARENT="00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert len(otlp_receiver.requests) == 1
    [span] = otlp_receiver.requests[0].spans
    assert span.name == "winter capabilities"
    assert span.trace_id.hex() == "0af7651916cd43dd8448eb211c80319c"
    assert span.parent_span_id.hex() == "b7ad6b7169203331"
    assert span.start_time_unix_nano == int(launched.replace(".", "")) * 1_000
    assert otlp_receiver.requests[0].resource_attributes["service.name"] == "winter"


def test_a_failing_command_exports_error_status_and_type_only(workspace: Path, otlp_receiver: OtlpReceiver) -> None:
    result = _winter(workspace, "capabilities", "--bogus", WINTER_OTEL_EXPORTER_OTLP_ENDPOINT=otlp_receiver.endpoint)

    assert result.returncode == 2
    [span] = otlp_receiver.requests[0].spans
    attributes = {attribute.key: attribute.value.string_value for attribute in span.attributes}
    assert span.name == "winter capabilities"
    assert attributes == {"winter.command": "winter capabilities", "error.type": "NoSuchOption"}
    assert span.status.message == ""


# ── A misbehaving endpoint never disturbs the command ────────────────────────


def test_a_hanging_endpoint_keeps_the_exit_code_and_leaves_stderr_empty(
    workspace: Path, hanging_endpoint: HangingEndpoint
) -> None:
    result = _winter(workspace, "capabilities", WINTER_OTEL_EXPORTER_OTLP_ENDPOINT=hanging_endpoint.endpoint)

    assert result.returncode == 0
    assert result.stderr == ""


def test_a_refused_endpoint_keeps_the_exit_code_and_leaves_stderr_empty(
    workspace: Path, refused_otlp_endpoint: str
) -> None:
    result = _winter(workspace, "capabilities", WINTER_OTEL_EXPORTER_OTLP_ENDPOINT=refused_otlp_endpoint)

    assert result.returncode == 0
    assert result.stderr == ""


# ── A tracer that cannot be built never disturbs the command ─────────────────


def test_a_malformed_generic_sdk_variable_keeps_the_exit_code_and_stderr_of_tracing_off(
    workspace: Path, otlp_receiver: OtlpReceiver
) -> None:
    for argv in (("capabilities",), ("capabilities", "--bogus")):
        untraced = _winter(workspace, *argv, OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT="abc")
        traced = _winter(
            workspace,
            *argv,
            WINTER_OTEL_EXPORTER_OTLP_ENDPOINT=otlp_receiver.endpoint,
            OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT="abc",
        )

        assert traced.returncode == untraced.returncode
        assert traced.stderr == untraced.stderr
        assert "Traceback" not in traced.stderr
    assert otlp_receiver.requests == []


def test_verbose_surfaces_the_tracer_setup_failure_on_stderr(workspace: Path, otlp_receiver: OtlpReceiver) -> None:
    result = _winter(
        workspace,
        "--verbose",
        "capabilities",
        WINTER_OTEL_EXPORTER_OTLP_ENDPOINT=otlp_receiver.endpoint,
        OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT="abc",
    )

    assert result.returncode == 0
    assert "tracing unavailable" in result.stderr


# ── Diagnostics surface only on request ──────────────────────────────────────


def _opentelemetry_records(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if " opentelemetry" in line]


def test_verbose_surfaces_opentelemetry_diagnostics_on_stderr(workspace: Path, refused_otlp_endpoint: str) -> None:
    result = _winter(workspace, "--verbose", "capabilities", WINTER_OTEL_EXPORTER_OTLP_ENDPOINT=refused_otlp_endpoint)

    assert result.returncode == 0
    assert _opentelemetry_records(result.stderr)


def test_winter_log_level_surfaces_opentelemetry_diagnostics_on_stderr(
    workspace: Path, refused_otlp_endpoint: str
) -> None:
    result = _winter(
        workspace,
        "capabilities",
        WINTER_OTEL_EXPORTER_OTLP_ENDPOINT=refused_otlp_endpoint,
        WINTER_LOG_LEVEL="ERROR",
    )

    assert result.returncode == 0
    assert _opentelemetry_records(result.stderr)
