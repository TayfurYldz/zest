"""Deterministic two-origin loopback lab for full-lifecycle field regressions.

Not a product server. Main origin is in-scope. Third-party origin is a
separate port used as a network-egress canary.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse


class OriginServer:
    def __init__(self, *, name: str) -> None:
        handler = _handler_for(self)
        self.name = name
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name=f"lab-{name}"
        )
        self.hits: list[str] = []
        self.pages: dict[str, tuple[str, bytes]] = {}
        self.redirects: dict[str, str] = {}

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


class FullLifecycleLab:
    """One in-scope app origin plus one out-of-scope third-party origin."""

    def __init__(self, *, include_passive_third_party: bool) -> None:
        self.include_passive_third_party = include_passive_third_party
        self.main = OriginServer(name="main")
        self.third = OriginServer(name="third")

    def start(self) -> str:
        third_origin = self.third.start()
        main_origin = self.main.start()
        self.third.pages = {
            "/beacon.js": ("application/javascript", b"window.__third=true;"),
            "/pixel.gif": ("image/gif", b"GIF89a"),
            "/font.css": ("text/css", b"@font-face{font-family:x;src:url(/pixel.gif)}"),
            "/arrive": ("text/html; charset=utf-8", _html("third", "<p>third</p>").encode("utf-8")),
        }
        self.main.pages = _main_pages(
            main_origin,
            third_origin,
            include_passive_third_party=self.include_passive_third_party,
        )
        self.main.redirects = {"/leave-redirect": f"{third_origin}/arrive"}
        return main_origin

    def stop(self) -> None:
        self.main.stop()
        self.third.stop()

    def __enter__(self) -> FullLifecycleLab:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        self.stop()


def _handler_for(server: OriginServer):
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            del format, args

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            server.hits.append("GET " + parsed.path)
            location = server.redirects.get(parsed.path)
            if location is not None:
                payload = b""
                self.send_response(302)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()
                if payload:
                    self.wfile.write(payload)
                return
            item = server.pages.get(parsed.path)
            if item is None:
                self.send_response(404)
                self.end_headers()
                return
            content_type, body = item
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            server.hits.append("POST " + parsed.path)
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self.send_response(404)
            self.end_headers()

    return _Handler


def _html(title: str, body: str) -> str:
    return (
        "<!doctype html><html><head><title>"
        + title
        + "</title></head><body>"
        + body
        + "</body></html>"
    )


def _main_pages(
    main_origin: str,
    third_origin: str,
    *,
    include_passive_third_party: bool,
) -> dict[str, tuple[str, bytes]]:
    del main_origin
    passive = ""
    if include_passive_third_party:
        passive = (
            f'<script src="{third_origin}/beacon.js"></script>'
            f'<img src="{third_origin}/pixel.gif" alt="">'
            f'<link rel="stylesheet" href="{third_origin}/font.css">'
        )
    home = _html(
        "home",
        "<link rel='stylesheet' href='/assets/app.css'>"
        "<script src='/assets/app.js'></script>"
        + passive
        + "<p id='home' name='home'>in-scope-home</p>"
        "<a href='/leave' name='leave'>leave</a>"
        "<a href='/leave-redirect' name='leave-redirect'>leave-redirect</a>"
        "<script>"
        "fetch('/api/session.js');"
        "fetch('/api/health');"
        "fetch('/api/config');"
        "</script>",
    )
    leave = _html(
        "leave",
        "<p name='leaving'>explicit-off-origin</p>"
        f"<script>window.location.href={json.dumps(third_origin + '/arrive')};</script>",
    )
    return {
        "/": ("text/html; charset=utf-8", home.encode("utf-8")),
        "/leave": ("text/html; charset=utf-8", leave.encode("utf-8")),
        "/assets/app.js": ("application/javascript", b"window.__app=true;"),
        "/assets/app.css": ("text/css", b"body{color:#111}"),
        "/api/session.js": (
            "application/javascript",
            b"window.__session={ready:true,endpoint:'/api/session.js'};",
        ),
        "/api/health": ("application/json", json.dumps({"ok": True}).encode("utf-8")),
        "/api/config": ("application/json", json.dumps({"locale": "en"}).encode("utf-8")),
    }
