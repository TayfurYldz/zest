"""Loopback HTTP lab for Phase 8 authorization-differential Worker traffic."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse


class Phase8AuthzLab:
    """Serves /vulnerable and /secure account objects for native authz Worker."""

    def __init__(self) -> None:
        handler = _handler_for(self)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="phase8-authz-lab"
        )
        self.hits: list[str] = []
        self.mode = "vulnerable"

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

    def __enter__(self) -> Phase8AuthzLab:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        self.stop()


def _handler_for(lab: Phase8AuthzLab):
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            del format, args

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            lab.hits.append("GET " + parsed.path)
            actor = self.headers.get("X-Lab-Actor")
            path = parsed.path
            if path in {"/", "/index.html"}:
                body = b"<!doctype html><html><body>phase8</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/fetch") or path.startswith("/oast-sink"):
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            payload, status = _account_payload(path, actor, lab.mode)
            if payload is None:
                self.send_response(404)
                self.end_headers()
                return
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            lab.hits.append("POST " + parsed.path)
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self.send_response(404)
            self.end_headers()

    return _Handler


def _account_payload(path: str, actor: str | None, mode: str) -> tuple[dict[str, Any] | None, int]:
    parts = [item for item in path.split("/") if item]
    if len(parts) != 3 or parts[1] != "accounts":
        return None, 404
    lane, account = parts[0], parts[2]
    if lane not in {"vulnerable", "secure"}:
        return None, 404
    owner = account
    body = {
        "owner": owner,
        "visibility": "PRIVATE",
        "resource_kind": "ACCOUNT",
        "authorized_readers": [owner],
    }
    if actor is None:
        return {"error": "unauthenticated"}, 401
    if lane == "secure" or mode == "secure_only":
        if actor != owner:
            return {"error": "forbidden"}, 403
        return body, 200
    return body, 200
