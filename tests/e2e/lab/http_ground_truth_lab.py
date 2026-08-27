"""Local synthetic HTTP fixtures for GATE 15. Bind 127.0.0.1 only.

Fixture kind is harness-internal. HTTP responses never include expected
security classification, scenario ids, or ground-truth labels.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

ACTOR_HEADER = "X-Lab-Actor"
EXTERNAL_REDIRECT = "http://example.com/out-of-lab"
TIMEOUT_SLEEP_SECONDS = 6.0
PROBE_REQUESTS_PER_CYCLE = 4

TRUE_BOLA = "TRUE_BOLA"
SECURE_OBJECT = "SECURE_OBJECT"
PUBLIC_OBJECT = "PUBLIC_OBJECT"
DELEGATED_ACCESS = "DELEGATED_ACCESS"
DECEPTIVE_200 = "DECEPTIVE_200"
SHARED_RESOURCE = "SHARED_RESOURCE"
CONTRADICTORY = "CONTRADICTORY"
OPERATIONAL_TIMEOUT = "OPERATIONAL_TIMEOUT"
REDIRECT_BOUNDARY = "REDIRECT_BOUNDARY"

OWNED_ACCOUNTS = {
    "alice": {"account_id": "alice", "owner": "alice", "marker": "lab-alice-marker"},
    "bob": {"account_id": "bob", "owner": "bob", "marker": "lab-bob-marker"},
}


@dataclass(frozen=True)
class LabRequestRecord:
    method: str
    path: str
    actor: str | None
    client_host: str
    status: int


class GroundTruthLab:
    """Loopback fixture with a request ledger. Not a product server."""

    def __init__(self, fixture_kind: str) -> None:
        self.fixture_kind = fixture_kind
        self.ledger: list[LabRequestRecord] = []
        self.get_count = 0
        self._ledger_condition = threading.Condition()
        self._server = _LabServer(("127.0.0.1", 0), _GroundTruthHandler, lab=self)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def origin(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> str:
        self._thread.start()
        return self.origin

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def __enter__(self) -> GroundTruthLab:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        self.stop()

    def http_request_count(self) -> int:
        with self._ledger_condition:
            return len(self.ledger)

    def wait_for_request_count(self, expected: int, timeout: float = 1.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._ledger_condition:
            while len(self.ledger) < expected:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._ledger_condition.wait(remaining)
            return True

    def followed_external(self) -> bool:
        return any("example.com" in item.path for item in self.ledger)

    def record(self, record: LabRequestRecord) -> None:
        with self._ledger_condition:
            self.ledger.append(record)
            self._ledger_condition.notify_all()


class _LabServer(ThreadingHTTPServer):
    def __init__(self, address, handler, *, lab: GroundTruthLab) -> None:
        self.lab = lab
        super().__init__(address, handler)


class _GroundTruthHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        del format, args

    def do_GET(self) -> None:
        lab: GroundTruthLab = self.server.lab  # type: ignore[attr-defined]
        parsed = urlparse(self.path)
        actor = self.headers.get(ACTOR_HEADER)
        client_host = self.client_address[0]
        lab.get_count += 1
        try:
            status = self._respond(lab, parsed.path, actor)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            status = 0
        lab.record(
            LabRequestRecord(
                method="GET",
                path=parsed.path,
                actor=actor,
                client_host=client_host,
                status=status,
            )
        )

    def do_POST(self) -> None:
        self._send(405, {"error": "GET only"})

    def do_PUT(self) -> None:
        self._send(405, {"error": "GET only"})

    def do_PATCH(self) -> None:
        self._send(405, {"error": "GET only"})

    def do_DELETE(self) -> None:
        self._send(405, {"error": "GET only"})

    def _respond(self, lab: GroundTruthLab, path: str, actor: str | None) -> int:
        if lab.fixture_kind == REDIRECT_BOUNDARY:
            return self._redirect()
        if path == "/redirect":
            return self._redirect()
        parts = [item for item in path.split("/") if item]
        if len(parts) != 3 or parts[1] != "accounts" or parts[0] not in {"vulnerable", "secure"}:
            return self._send(404, {"error": "not found"})
        area, _, account_id = parts
        if lab.fixture_kind == OPERATIONAL_TIMEOUT and lab.get_count > PROBE_REQUESTS_PER_CYCLE:
            time.sleep(TIMEOUT_SLEEP_SECONDS)
        if not actor or actor not in {"alice", "bob"}:
            return self._send(401, {"error": "unauthenticated"})
        if area == "secure":
            if account_id == "shared":
                return self._send(403, {"error": "forbidden"})
            account = OWNED_ACCOUNTS.get(account_id)
            if account is None:
                return self._send(404, {"error": "unknown account"})
            if actor != account["owner"]:
                return self._send(403, {"error": "forbidden"})
            return self._send(200, dict(account))
        return self._vulnerable(lab, actor, account_id)

    def _vulnerable(self, lab: GroundTruthLab, actor: str, account_id: str) -> int:
        kind = lab.fixture_kind
        later_cycle = lab.get_count > PROBE_REQUESTS_PER_CYCLE
        if kind == SECURE_OBJECT:
            account = OWNED_ACCOUNTS.get(account_id)
            if account is None:
                return self._send(404, {"error": "unknown account"})
            if actor != account["owner"]:
                return self._send(403, {"error": "forbidden"})
            return self._send(200, dict(account))
        if kind == PUBLIC_OBJECT:
            account = OWNED_ACCOUNTS.get(account_id)
            if account is None:
                return self._send(404, {"error": "unknown account"})
            payload = dict(account)
            if actor != account["owner"]:
                payload["visibility"] = "PUBLIC"
            return self._send(200, payload)
        if kind == DELEGATED_ACCESS:
            account = OWNED_ACCOUNTS.get(account_id)
            if account is None:
                return self._send(404, {"error": "unknown account"})
            payload = dict(account)
            if actor != account["owner"]:
                payload["authorized_readers"] = [actor]
            return self._send(200, payload)
        if kind == DECEPTIVE_200:
            if account_id == "alice":
                return self._send(200, dict(OWNED_ACCOUNTS["alice"]))
            if account_id == "bob":
                return self._send(
                    200, {"account_id": "account-2", "display_name": "bob-like"}
                )
            return self._send(404, {"error": "unknown account"})
        if kind == SHARED_RESOURCE:
            if account_id == "alice":
                return self._send(200, dict(OWNED_ACCOUNTS["alice"]))
            if account_id == "shared":
                return self._send(
                    200,
                    {
                        "account_id": "shared",
                        "owner": "shared",
                        "resource_kind": "SHARED",
                        "marker": "lab-shared-marker",
                    },
                )
            return self._send(404, {"error": "unknown account"})
        if kind == CONTRADICTORY and later_cycle:
            account = OWNED_ACCOUNTS.get(account_id)
            if account is None:
                return self._send(404, {"error": "unknown account"})
            if actor != account["owner"]:
                return self._send(403, {"error": "forbidden"})
            return self._send(200, dict(account))
        account = OWNED_ACCOUNTS.get(account_id)
        if account is None:
            return self._send(404, {"error": "unknown account"})
        return self._send(200, dict(account))

    def _redirect(self) -> int:
        self.send_response(302)
        self.send_header("Location", EXTERNAL_REDIRECT)
        self.end_headers()
        return 302

    def _send(self, status: int, payload: dict[str, Any]) -> int:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return status
