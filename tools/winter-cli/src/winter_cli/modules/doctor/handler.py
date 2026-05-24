from __future__ import annotations

import dataclasses
import sys

from winter_cli.modules.doctor.doctor_reporter import IDoctorReporter, JsonDoctorReporter, StreamDoctorReporter
from winter_cli.modules.doctor.doctor_service import DoctorService


@dataclasses.dataclass
class DoctorParams:
    output_json: bool


class DoctorHandler:
    """Dispatches `winter doctor` runs to the service with the right reporter."""

    def __init__(
        self,
        doctor_service: DoctorService,
        stream_reporter: StreamDoctorReporter,
        json_reporter: JsonDoctorReporter,
    ) -> None:
        self._doctor_service = doctor_service
        self._stream_reporter = stream_reporter
        self._json_reporter = json_reporter

    def run(self, params: DoctorParams) -> None:
        reporter: IDoctorReporter = self._json_reporter if params.output_json else self._stream_reporter
        summary = self._doctor_service.run(reporter)
        if summary.exit_code != 0:
            sys.exit(summary.exit_code)
