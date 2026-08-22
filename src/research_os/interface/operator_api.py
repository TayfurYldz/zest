"""Minimal local Operator API for research-osd. Not a public internet service.

PostgreSQL remains source of truth. This HTTP surface is a command client
boundary: it does not accept scope/budget/config overrides and never
returns secrets.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from research_os.application.errors import ApplicationError
from research_os.application.research_osd import ResearchOsdRuntime
from research_os.safe_data import redact_secret_keys

OPERATOR_API_DEFAULT_HOST = "127.0.0.1"
OPERATOR_API_DEFAULT_PORT = 8766


def _json_bytes(payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> tuple[HTTPStatus, bytes]:
    body = json.dumps(redact_secret_keys(payload), separators=(",", ":"), ensure_ascii=True)
    return status, body.encode("utf-8")


class OperatorApiHandler(BaseHTTPRequestHandler):
    server_version = "ResearchOsdOperator/1.0"

    @property
    def runtime(self) -> ResearchOsdRuntime:
        return self.server.runtime  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send(*_json_bytes(self.runtime.health()))
            return
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "runs" and parts[2]:
            try:
                payload = self.runtime.run_status(unquote(parts[2]))
            except ApplicationError as exc:
                self._send(*_json_bytes({"ok": False, "error": str(exc)}, status=HTTPStatus.NOT_FOUND))
                return
            self._send(*_json_bytes({"ok": True, "result": payload}))
            return
        self._send(HTTPStatus.NOT_FOUND, b"not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")
        if not (
            len(parts) == 4
            and parts[0] == "api"
            and parts[1] == "runs"
            and parts[2]
            and parts[3] in {"start", "pause", "resume", "cancel"}
        ):
            self._send(HTTPStatus.NOT_FOUND, b"not found")
            return
        research_run_id = unquote(parts[2])
        action = parts[3]
        try:
            if action == "start":
                result = self.runtime.start_run(research_run_id)
            elif action == "pause":
                result = self.runtime.pause_run(research_run_id)
            elif action == "resume":
                result = self.runtime.resume_run(research_run_id)
            else:
                result = self.runtime.cancel_run(research_run_id)
        except ApplicationError as exc:
            self._send(
                *_json_bytes({"ok": False, "error": str(exc)}, status=HTTPStatus.CONFLICT)
            )
            return
        payload = {
            "research_run_id": result.research_run_id,
            "state": result.state,
            "cycle_number": result.cycle_number,
            "outcome": result.outcome,
            "stop_reason": result.stop_reason,
            "last_phase": result.last_phase,
            "hypothesis_id": result.hypothesis_id,
            "experiment_id": result.experiment_id,
        }
        self._send(*_json_bytes({"ok": True, "result": payload}))

    def _send(self, status: HTTPStatus | int, payload: bytes, content_type: str = "application/json") -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


class OperatorApiServer:
    def __init__(
        self,
        runtime: ResearchOsdRuntime,
        *,
        host: str = OPERATOR_API_DEFAULT_HOST,
        port: int = OPERATOR_API_DEFAULT_PORT,
    ) -> None:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("operator API must bind locally")
        self._server = ThreadingHTTPServer((host, port), OperatorApiHandler)
        self._server.runtime = runtime  # type: ignore[attr-defined]
        self._thread = None

    @property
    def address(self) -> tuple[str, int]:
        return self._server.server_address[:2]

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
