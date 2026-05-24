from __future__ import annotations

import enum
from dataclasses import dataclass


class ProbeStatus(enum.Enum):
    """Outcome of a single doctor probe."""

    pass_ = "pass"
    warn = "warn"
    fail = "fail"


@dataclass(frozen=True)
class ProbeResult:
    """One check result emitted by a probe.

    `source` identifies which group the probe belongs to — `"core"` for the
    built-in winter-cli probes, the extension's symlink prefix otherwise.
    `remediation` is an optional one-line hint shown under failures.
    """

    source: str
    name: str
    status: ProbeStatus
    message: str = ""
    remediation: str | None = None
