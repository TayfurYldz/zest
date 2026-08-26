"""Local Operator API for research-osd. Not a public internet service.

PostgreSQL remains source of truth. This HTTP surface is a command client
boundary: it does not accept scope/budget/config overrides and never
returns secrets. SSE is deferred; clients poll REST snapshots.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from research_os.application.errors import ApplicationError
from research_os.application.operator_command_payload import reject_authority_overrides
from research_os.application.operator_errors import OperatorError, OperatorErrorCode
from research_os.application.research_osd import ResearchOsdRuntime
from research_os.data.errors import DatabaseUnavailableError, PersistenceError
from research_os.safe_data import redact_secret_keys

OPERATOR_API_DEFAULT_HOST = "127.0.0.1"
OPERATOR_API_DEFAULT_PORT = 8766
LOCAL_BIND_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
LOGGER = logging.getLogger("research_os.operator_api")

_DEGRADED_HEALTH = {
    "ok": False,
    "pg_unavailable": True,
    "ready_for_start": False,
    "not_research_truth": True,
    "database": {
        "installed": True,
        "configured": True,
        "available_now": False,
        "schema_at_expected_head": False,
        "health": "UNAVAILABLE",
        "detail": "unavailable",
    },
}


def _json_bytes(payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> tuple[HTTPStatus, bytes]:
    body = json.dumps(redact_secret_keys(payload), separators=(",", ":"), ensure_ascii=True)
    return status, body.encode("utf-8")


def _error_bytes(exc: OperatorError) -> tuple[HTTPStatus, bytes]:
    return _json_bytes(exc.to_payload(), status=exc.http_status)


def _database_unavailable_error() -> OperatorError:
    return OperatorError(
        OperatorErrorCode.DATABASE_UNAVAILABLE,
        "postgresql unavailable; refusing new authoritative work",
    )


def _runtime_health_ids(runtime: ResearchOsdRuntime) -> dict[str, object]:
    payload: dict[str, object] = {}
    try:
        payload["runtime_instance_id"] = runtime.runtime_instance_id
    except ApplicationError:
        return payload
    instance = getattr(runtime, "_instance", None)
    if instance is not None:
        payload["status"] = instance.status
    return payload


def _from_application_error(exc: ApplicationError) -> OperatorError:
    if isinstance(exc, OperatorError):
        return exc
    message = str(exc)
    lowered = message.lower()
    if "not found" in lowered:
        return OperatorError(OperatorErrorCode.RUN_NOT_FOUND, message)
    if "postgresql unavailable" in lowered or "database" in lowered:
        return _database_unavailable_error()
    return OperatorError(OperatorErrorCode.INVALID_STATE, message)


class OperatorApiHandler(BaseHTTPRequestHandler):
    server_version = "ResearchOsdOperator/1.0"

    @property
    def runtime(self) -> ResearchOsdRuntime:
        return self.server.runtime  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        return

    def handle_error(self, request, client_address) -> None:
        exc_type = sys.exc_info()[0]
        LOGGER.error(
            "operator_api.unhandled type=%s",
            exc_type.__name__ if exc_type is not None else "unknown",
        )

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/health":
                self._send(*_json_bytes(self.runtime.health()))
                return
            parts = path.strip("/").split("/")
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
                and parts[2]
                and parts[3] == "analysis"
            ):
                payload = self.runtime.run_analysis(unquote(parts[2]))
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
        except DatabaseUnavailableError:
            self._send_database_unavailable(path)
            return
        except PersistenceError:
            if path == "/health":
                self._send_database_unavailable(path)
                return
            self._send(*_json_bytes(
                OperatorError(OperatorErrorCode.INVALID_STATE, "persistence error").to_payload(),
                status=HTTPStatus.CONFLICT,
            ))
            return
        except ApplicationError as exc:
            self._send(*_error_bytes(_from_application_error(exc)))
            return
        except Exception:
            self._send_unhandled(path)
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
        except DatabaseUnavailableError:
            self._send(*_error_bytes(_database_unavailable_error()))
            return
        except PersistenceError:
            self._send(*_json_bytes(
                OperatorError(OperatorErrorCode.INVALID_STATE, "persistence error").to_payload(),
                status=HTTPStatus.CONFLICT,
            ))
            return
        except ApplicationError as exc:
            self._send(*_error_bytes(_from_application_error(exc)))
            return
        except Exception:
            self._send_unhandled(path)
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

    def _send_database_unavailable(self, path: str) -> None:
        if path == "/health":
            self._send(*_json_bytes({**_DEGRADED_HEALTH, **_runtime_health_ids(self.runtime)}))
            return
        self._send(*_error_bytes(_database_unavailable_error()))

    def _send_unhandled(self, path: str) -> None:
        exc_type = sys.exc_info()[0]
        LOGGER.error(
            "operator_api.request_error type=%s",
            exc_type.__name__ if exc_type is not None else "unknown",
        )
        if path == "/health":
            self._send(*_json_bytes({**_DEGRADED_HEALTH, **_runtime_health_ids(self.runtime)}))
            return
        self._send(*_json_bytes(
            OperatorError(OperatorErrorCode.INVALID_STATE, "operator request failed").to_payload(),
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        ))

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
        self._serve_thread: threading.Thread | None = None
        self._shutdown_lock = threading.Lock()
        self._closed = False

    @property
    def address(self) -> tuple[str, int]:
        return self._server.server_address[:2]

    def serve_forever(self) -> None:
        """Blocking accept loop.

        ``shutdown()`` must be called from another thread. Calling it from
        this thread (including a SIGTERM handler on the serve thread)
        deadlocks: HTTPServer.shutdown() waits for serve_forever() to
        finish, and serve_forever cannot resume until the handler returns.
        """

        self._server.serve_forever()

    def start(self) -> None:
        """Accept connections on a dedicated thread. Main calls shutdown()."""

        if self._serve_thread is not None and self._serve_thread.is_alive():
            return
        self._closed = False
        self._serve_thread = threading.Thread(
            target=self._server.serve_forever,
            name="research-osd-operator-api",
            daemon=False,
        )
        self._serve_thread.start()

    def shutdown(self) -> None:
        """Stop the accept loop and close the socket. Idempotent.

        Must not run on the serve_forever thread.
        """

        with self._shutdown_lock:
            if self._closed:
                return
            self._closed = True
        self._server.shutdown()
        self._server.server_close()
        thread = self._serve_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
            self._serve_thread = None
