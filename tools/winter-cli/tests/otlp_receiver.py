"""Localhost endpoints for tracing tests: a receiver that decodes OTLP, one that hangs, one that refuses.

All three speak real TCP, so the exporter's actual transport is exercised rather than a mock.
"""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.trace.v1.trace_pb2 import Span


@dataclass
class ReceivedRequest:
    path: str
    headers: dict[str, str]
    body: bytes
    request: ExportTraceServiceRequest

    @property
    def spans(self) -> list[Span]:
        return [
            span
            for resource_spans in self.request.resource_spans
            for scope_spans in resource_spans.scope_spans
            for span in scope_spans.spans
        ]

    @property
    def resource_attributes(self) -> dict[str, str]:
        return {
            attribute.key: attribute.value.string_value
            for resource_spans in self.request.resource_spans
            for attribute in resource_spans.resource.attributes
        }


@dataclass
class OtlpReceiver:
    """An HTTP server that records and decodes every OTLP trace export it receives."""

    requests: list[ReceivedRequest] = field(default_factory=list)
    _server: ThreadingHTTPServer | None = None

    @property
    def endpoint(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> None:
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                request = ExportTraceServiceRequest()
                request.ParseFromString(body)
                headers = {key.lower(): value for key, value in self.headers.items()}
                receiver.requests.append(ReceivedRequest(self.path, headers, body, request))
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


class HangingEndpoint:
    """A TCP listener that accepts connections, reads them, and never answers."""

    def __init__(self) -> None:
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen()
        self._held: list[socket.socket] = []
        self._thread = threading.Thread(target=self._accept_forever, daemon=True)
        self._thread.start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self._listener.getsockname()[1]}"

    def _accept_forever(self) -> None:
        while True:
            try:
                connection, _ = self._listener.accept()
            except OSError:
                return
            self._held.append(connection)

    def stop(self) -> None:
        self._listener.close()
        for connection in self._held:
            connection.close()


def refused_endpoint() -> str:
    """An endpoint on a localhost port nothing listens on."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"
