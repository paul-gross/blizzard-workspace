"""Fixture: `opentelemetry` imported outside the one adapter that owns it."""

from __future__ import annotations

import opentelemetry.trace
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider


class SomeService:
    def current_span(self):
        return trace.get_current_span(), opentelemetry.trace, TracerProvider
