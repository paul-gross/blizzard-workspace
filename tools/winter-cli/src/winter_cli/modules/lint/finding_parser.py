from __future__ import annotations

import json

from winter_cli.modules.lint.models import LintFinding, LintStatus

_VALID_STATUSES = {s.value for s in LintStatus}

# Default check name for synthetic findings (parse failures, non-zero exit)
# that aren't attributable to a named check the script emitted.
_SYNTHETIC_CHECK = "lint"


def parse_lint_output(source: str, stdout: str, stderr: str, returncode: int) -> list[LintFinding]:
    """Convert a lint script's stdout/stderr/exit into a list of LintFindings.

    One NDJSON object per stdout line → one LintFinding. Mirrors `doctor`'s
    `parse_probe_output` contract, adding optional `file`/`line` location
    fields. A non-zero exit appends a synthetic `fail` with stderr as the
    message — surfaced even when no NDJSON was emitted. Lines that don't parse,
    or that lack required fields, become a single `warn` so the contract
    violation is visible without aborting the run.
    """
    findings: list[LintFinding] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        findings.append(_parse_finding_line(source, stripped))

    if returncode != 0:
        message = stderr.strip() or f"lint check exited with code {returncode}"
        findings.append(
            LintFinding(
                source=source,
                check=_SYNTHETIC_CHECK,
                status=LintStatus.fail,
                message=message,
            )
        )
    return findings


def _parse_finding_line(source: str, line: str) -> LintFinding:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return LintFinding(
            source=source,
            check=_SYNTHETIC_CHECK,
            status=LintStatus.warn,
            message=f"unparseable line: {line[:80]}",
        )
    if not isinstance(payload, dict):
        return LintFinding(
            source=source,
            check=_SYNTHETIC_CHECK,
            status=LintStatus.warn,
            message=f"expected JSON object, got {type(payload).__name__}",
        )

    check = payload.get("check") or payload.get("name")
    status = payload.get("status")
    if not isinstance(check, str) or not check:
        return LintFinding(
            source=source,
            check=_SYNTHETIC_CHECK,
            status=LintStatus.warn,
            message="finding missing `check`",
        )
    if status not in _VALID_STATUSES:
        return LintFinding(
            source=source,
            check=check,
            status=LintStatus.warn,
            message=f"unknown status `{status}` (expected pass/warn/fail)",
        )

    message = payload.get("message")
    remediation = payload.get("remediation")
    return LintFinding(
        source=source,
        check=check,
        status=LintStatus(status),
        message=str(message) if message is not None else "",
        file=_coerce_file(payload.get("file")),
        line=_coerce_line(payload.get("line")),
        remediation=str(remediation) if remediation else None,
    )


def _coerce_file(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _coerce_line(value: object) -> int | None:
    """Accept an int or an int-like string; ignore anything else (e.g. a bool)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None
