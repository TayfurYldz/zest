"""Local Operator API for research-osd. Not a public internet service.

PostgreSQL remains source of truth. This HTTP surface is a command client
boundary: it does not accept scope/budget/config overrides and never
returns secrets. SSE is deferred; clients poll REST snapshots.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from research_os.application.errors import ApplicationError
from research_os.application.operator_command_payload import reject_authority_overrides
from research_os.application.operator_errors import OperatorError, OperatorErrorCode
from research_os.application.research_osd import ResearchOsdRuntime
from research_os.safe_data import redact_secret_keys

OPERATOR_API_DEFAULT_HOST = "127.0.0.1"
OPERATOR_API_DEFAULT_PORT = 8766
LOCAL_BIND_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _json_bytes(payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> tuple[HTTPStatus, bytes]:
    body = json.dumps(redact_secret_keys(payload), separators=(",", ":"), ensure_ascii=True)
    return status, body.encode("utf-8")


def _error_bytes(exc: OperatorError) -> tuple[HTTPStatus, bytes]:
    return _json_bytes(exc.to_payload(), status=exc.http_status)


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
        try:
            if parts == ["api", "runs"]:
                self._send(*_json_bytes({"ok": True, "result": self.runtime.list_runs()}))
                return
            if parts == ["api", "programs"]:
                self._send(*_json_bytes({"ok": True, "result": self.runtime.list_programs()}))
                return
            if parts == ["api", "console"]:
                self._send(*_json_bytes({"ok": True, "result": self.runtime.console_snapshot()}))
                return
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "runs" and parts[2]:
                payload = self.runtime.run_detail(unquote(parts[2]))
                self._send(*_json_bytes({"ok": True, "result": payload}))
                return
            if (
                len(parts) == 4
                and parts[0] == "api"
                and parts[1] == "runs"
                and parts[3] == "preflight"
                and parts[2]
            ):
                latest = self.runtime.latest_preflight(unquote(parts[2]))
                self._send(*_json_bytes({"ok": True, "result": latest}))
                return
            if (
                len(parts) == 5
                and parts[0] == "api"
                and parts[1] == "runs"
                and parts[3] == "preflight"
                and parts[4] == "latest"
                and parts[2]
            ):
                latest = self.runtime.latest_preflight(unquote(parts[2]))
                self._send(*_json_bytes({"ok": True, "result": latest}))
                return
        except OperatorError as exc:
            self._send(*_error_bytes(exc))
            return
        except ApplicationError as exc:
            self._send(*_error_bytes(_from_application_error(exc)))
            return
        self._send(*_json_bytes(
            OperatorError(OperatorErrorCode.INVALID_INPUT, "not found").to_payload(),
            status=HTTPStatus.NOT_FOUND,
        ))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")
        if not (
            len(parts) == 4
            and parts[0] == "api"
            and parts[1] == "runs"
            and parts[2]
            and parts[3] in {"start", "pause", "resume", "cancel", "preflight"}
        ):
            self._send(*_json_bytes(
                OperatorError(OperatorErrorCode.INVALID_INPUT, "not found").to_payload(),
                status=HTTPStatus.NOT_FOUND,
            ))
            return
        research_run_id = unquote(parts[2])
        action = parts[3]
        try:
            reject_authority_overrides(self._read_json_body())
            if action == "preflight":
                payload = self.runtime.execute_preflight(research_run_id)
                self._send(*_json_bytes({"ok": True, "result": payload}))
                return
            if action == "start":
                result = self.runtime.start_run(research_run_id)
            elif action == "pause":
                result = self.runtime.pause_run(research_run_id)
            elif action == "resume":
                result = self.runtime.resume_run(research_run_id)
            else:
                result = self.runtime.cancel_run(research_run_id)
        except OperatorError as exc:
            self._send(*_error_bytes(exc))
            return
        except ApplicationError as exc:
            self._send(*_error_bytes(_from_application_error(exc)))
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

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OperatorError(OperatorErrorCode.INVALID_INPUT, "request body must be JSON") from exc
        if parsed is None:
            return {}
        if not isinstance(parsed, dict):
            raise OperatorError(OperatorErrorCode.INVALID_INPUT, "request body must be an object")
        return parsed

    def _send(self, status: HTTPStatus | int, payload: bytes, content_type: str = "application/json") -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


def _from_application_error(exc: ApplicationError) -> OperatorError:
    if isinstance(exc, OperatorError):
        return exc
    message = str(exc)
    lowered = message.lower()
    if "not found" in lowered:
        return OperatorError(OperatorErrorCode.RUN_NOT_FOUND, message)
    if "postgresql unavailable" in lowered or "database" in lowered:
        return OperatorError(OperatorErrorCode.DATABASE_UNAVAILABLE, message)
    return OperatorError(OperatorErrorCode.INVALID_STATE, message)


class OperatorApiServer:
    def __init__(
        self,
        runtime: ResearchOsdRuntime,
        *,
        host: str = OPERATOR_API_DEFAULT_HOST,
        port: int = OPERATOR_API_DEFAULT_PORT,
    ) -> None:
        if host not in LOCAL_BIND_HOSTS:
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
