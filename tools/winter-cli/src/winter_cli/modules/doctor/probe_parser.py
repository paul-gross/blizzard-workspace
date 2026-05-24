from __future__ import annotations

import json

from winter_cli.modules.doctor.models import ProbeResult, ProbeStatus

_VALID_STATUSES = {s.value for s in ProbeStatus}


def parse_probe_output(source: str, stdout: str, stderr: str, returncode: int) -> list[ProbeResult]:
    """Convert a probe script's stdout/stderr/exit into a list of ProbeResults.

    One NDJSON object per stdout line → one ProbeResult. A non-zero exit
    appends a synthetic `fail` result with stderr as the message — surfaced
    even when no NDJSON was emitted. Used by both extension and workspace
    probes since the contract is identical.
    """
    results: list[ProbeResult] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        results.append(_parse_probe_line(source, stripped))

    if returncode != 0:
        message = stderr.strip() or f"probe exited with code {returncode}"
        results.append(
            ProbeResult(
                source=source,
                name="doctor",
                status=ProbeStatus.fail,
                message=message,
            )
        )
    return results


def _parse_probe_line(source: str, line: str) -> ProbeResult:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return ProbeResult(
            source=source,
            name="doctor",
            status=ProbeStatus.warn,
            message=f"unparseable line: {line[:80]}",
        )
    if not isinstance(payload, dict):
        return ProbeResult(
            source=source,
            name="doctor",
            status=ProbeStatus.warn,
            message=f"expected JSON object, got {type(payload).__name__}",
        )

    name = payload.get("name")
    status = payload.get("status")
    if not isinstance(name, str) or not name:
        return ProbeResult(
            source=source,
            name="doctor",
            status=ProbeStatus.warn,
            message="probe missing `name`",
        )
    if status not in _VALID_STATUSES:
        return ProbeResult(
            source=source,
            name=name,
            status=ProbeStatus.warn,
            message=f"unknown status `{status}` (expected pass/warn/fail)",
        )

    message = payload.get("message")
    remediation = payload.get("remediation")
    return ProbeResult(
        source=source,
        name=name,
        status=ProbeStatus(status),
        message=str(message) if message is not None else "",
        remediation=str(remediation) if remediation else None,
    )
