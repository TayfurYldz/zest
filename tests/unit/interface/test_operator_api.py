from __future__ import annotations

import ast
import json
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

import pathsetup  # noqa: F401

from zest.application.operator_command_payload import reject_authority_overrides
from zest.application.operator_errors import OperatorError, OperatorErrorCode
from zest.application.osd_settings import LINUX_CONFIG_DIR, load_osd_settings
from zest.interface.dashboard import DashboardHandler, collect_dashboard_payload
from zest.interface.operator_api import OperatorApiServer


class OperatorCommandPayloadTests(unittest.TestCase):
    def test_empty_body_is_allowed(self) -> None:
        reject_authority_overrides({})
        reject_authority_overrides(None)

    def test_authority_keys_are_rejected(self) -> None:
        with self.assertRaises(OperatorError) as caught:
            reject_authority_overrides(
                {
                    "target": "https://evil.example",
                    "scope": "*",
                    "max_requests": 999999,
                    "side_effect_ceiling": 3,
                    "budget": {"max_requests": 1},
                    "research_question": "changed",
                }
            )
        self.assertEqual(caught.exception.code, OperatorErrorCode.INVALID_INPUT)


class OperatorApiBindTests(unittest.TestCase):
    def test_public_bind_is_rejected(self) -> None:
        runtime = mock.Mock()
        with self.assertRaisesRegex(ValueError, "bind locally"):
            OperatorApiServer(runtime, host="0.0.0.0", port=0)


class OperatorApiShutdownTests(unittest.TestCase):
    def test_start_then_shutdown_from_other_thread_closes_listener(self) -> None:
        runtime = mock.Mock()
        runtime.health.return_value = {"ok": True, "not_research_truth": True}
        server = OperatorApiServer(runtime, host="127.0.0.1", port=0)
        server.start()
        host, port = server.address
        self.assertGreater(port, 0)
        deadline = time.time() + 2
        while time.time() < deadline:
            try:
                import socket

                with socket.create_connection((host, port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("operator API did not listen")
        server.shutdown()
        server.shutdown()
        with self.assertRaises(OSError):
            import socket

            with socket.create_connection((host, port), timeout=0.2):
                pass

    def test_entrypoint_sigterm_handler_does_not_call_httpserver_shutdown(self) -> None:
        source_path = Path(__file__).resolve().parents[3] / "src/zest/interface/zestd.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        main = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        handler = next(
            node
            for node in ast.walk(main)
            if isinstance(node, ast.FunctionDef) and node.name == "_handle_stop"
        )
        called = [ast.unparse(node.func) for node in ast.walk(handler) if isinstance(node, ast.Call)]
        self.assertTrue(any(name.endswith(".set") or name == "set" for name in called))
        self.assertFalse(any("shutdown" in name for name in called))


class OsdSettingsTests(unittest.TestCase):
    def test_linux_paths_and_no_home_assumption(self) -> None:
        settings = load_osd_settings(
            {
                "ZEST_DATABASE_URL": "postgresql+psycopg://zest@127.0.0.1/zest",
                "ZEST_LOG_PATH": "/var/log/zest/zestd.log",
            }
        )
        public = settings.public_mapping()
        rendered = json.dumps(public)
        self.assertNotIn("password", rendered)
        self.assertNotIn("/home/tayfur", rendered)
        self.assertEqual(public["linux_paths"]["config"], LINUX_CONFIG_DIR)
        self.assertEqual(public["linux_paths"]["env_file"], "/etc/zest/zest.env")
        self.assertEqual(settings.bind_host, "127.0.0.1")


class DashboardClientOnlyTests(unittest.TestCase):
    def test_collect_payload_marks_client_only(self) -> None:
        payload = collect_dashboard_payload(env={"ZEST_DATABASE_URL": ""})
        self.assertTrue(payload["client_only"])
        self.assertNotIn("LocalRunSupervisorRegistry", json.dumps(payload))


class DashboardHandlerNoTracebackTests(unittest.TestCase):
    def test_unknown_route_is_json(self) -> None:
        handler = DashboardHandler.__new__(DashboardHandler)
        handler.path = "/nope"
        handler.headers = {}
        handler.requestline = "GET /nope HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.client_address = ("127.0.0.1", 1)
        sent: list[tuple] = []

        def send_response(code):
            sent.append(("status", code))

        handler.send_response = send_response
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        handler.wfile = BytesIO()
        handler.do_GET()
        self.assertEqual(sent[0][1], 404)
        body = handler.wfile.getvalue().decode("utf-8")
        self.assertNotIn("Traceback", body)


class OperatorApiPostgresOutageHandlerTests(unittest.TestCase):
    def _serve(self, runtime) -> tuple[OperatorApiServer, str, int]:
        server = OperatorApiServer(runtime, host="127.0.0.1", port=0)
        server.start()
        host, port = server.address
        deadline = time.time() + 2
        while time.time() < deadline:
            try:
                import socket

                with socket.create_connection((host, port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            server.shutdown()
            self.fail("operator API did not listen")
        return server, host, port

    def _request(self, host: str, port: int, method: str, path: str) -> tuple[int, dict, bytes]:
        import http.client

        conn = http.client.HTTPConnection(host, port, timeout=3)
        try:
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            body = b"{}" if method == "POST" else None
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                payload = {}
            return response.status, payload, raw
        finally:
            conn.close()

    def test_run_analysis_route_is_read_only_json(self) -> None:
        runtime = mock.Mock()
        runtime.run_analysis.return_value = {
            "schema": "hq.run.analysis.v1",
            "research_run_id": "run-1",
            "projection_only": True,
            "authority": {
                "creates_state": False,
                "authorizes_execution": False,
                "dispatches_worker": False,
                "calls_model": False,
            },
        }
        server, host, port = self._serve(runtime)
        try:
            status, payload, raw = self._request(
                host,
                port,
                "GET",
                "/api/runs/run-1/analysis",
            )
        finally:
            server.shutdown()

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["schema"], "hq.run.analysis.v1")
        self.assertTrue(payload["result"]["projection_only"])
        self.assertFalse(payload["result"]["authority"]["creates_state"])
        self.assertNotIn(b"Traceback", raw)
        runtime.run_analysis.assert_called_once_with("run-1")

    def test_semantic_events_route_is_bounded_redacted_sse(self) -> None:
        runtime = mock.Mock()
        runtime.run_analysis.return_value = {
            "semantic_activity_timeline": {
                "items": [{
                    "activity_id": "activity-1",
                    "timestamp": "2026-08-27T12:00:00+00:00",
                    "plane": "EXECUTION",
                    "event_type": "RUN_FAULT",
                    "importance": "HIGH",
                    "summary": "execution failed password=hidden",
                    "source_type": "run_fault",
                    "source_id": "fault-1",
                }]
            }
        }
        server, host, port = self._serve(runtime)
        import http.client

        conn = http.client.HTTPConnection(host, port, timeout=3)
        try:
            conn.request("GET", "/api/runs/run-1/events")
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "text/event-stream; charset=utf-8")
            self.assertEqual(response.readline(), b"id: activity-1\n")
            self.assertEqual(response.readline(), b"event: semantic_activity\n")
            data = response.readline()
            self.assertIn(b'"schema":"zest.hq.semantic-event.v1"', data)
            self.assertNotIn(b"password=hidden", data)
            self.assertIn(b"[REDACTED]", data)
        finally:
            conn.close()
            server.shutdown()

    def test_semantic_events_resume_after_last_event_id(self) -> None:
        runtime = mock.Mock()
        runtime.run_analysis.return_value = {
            "semantic_activity_timeline": {
                "items": [
                    {"activity_id": "activity-1", "summary": "first"},
                    {"activity_id": "activity-2", "summary": "second"},
                ]
            }
        }
        server, host, port = self._serve(runtime)
        import http.client

        conn = http.client.HTTPConnection(host, port, timeout=3)
        try:
            conn.request("GET", "/api/runs/run-1/events", headers={"Last-Event-ID": "activity-1"})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.readline(), b"id: activity-2\n")
        finally:
            conn.close()
            server.shutdown()

    def test_health_returns_json_when_runtime_health_raises_unavailable(self) -> None:
        from zest.data.errors import DatabaseUnavailableError

        runtime = mock.Mock()
        runtime.health.side_effect = DatabaseUnavailableError("postgresql unavailable")
        runtime.runtime_instance_id = "rt-1"
        runtime._instance = mock.Mock(status="RUNNING")
        server, host, port = self._serve(runtime)
        try:
            status, payload, raw = self._request(host, port, "GET", "/health")
        finally:
            server.shutdown()
        self.assertEqual(status, 200)
        self.assertTrue(payload["pg_unavailable"])
        self.assertFalse(payload["ready_for_start"])
        self.assertFalse(payload["database"]["available_now"])
        self.assertEqual(payload["database"]["health"], "UNAVAILABLE")
        self.assertNotIn(b"Traceback", raw)
        self.assertNotIn(b"password", raw.lower())

    def test_start_fails_closed_with_structured_http_when_db_unavailable(self) -> None:
        from zest.data.errors import DatabaseUnavailableError

        runtime = mock.Mock()
        runtime.start_run.side_effect = DatabaseUnavailableError("postgresql unavailable")
        server, host, port = self._serve(runtime)
        try:
            status, payload, raw = self._request(host, port, "POST", "/api/runs/run-1/start")
        finally:
            server.shutdown()
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "DATABASE_UNAVAILABLE")
        self.assertFalse(payload["ok"])
        self.assertNotIn(b"Traceback", raw)
        self.assertNotIn("postgresql+psycopg://", payload.get("detail", ""))
        runtime.start_run.assert_called_once()

    def test_health_does_not_leak_uncaught_traceback_or_dsn(self) -> None:
        runtime = mock.Mock()
        runtime.health.side_effect = RuntimeError(
            "connection failed with redaction-test-secret"
        )
        runtime.runtime_instance_id = "rt-1"
        runtime._instance = mock.Mock(status="RUNNING")
        server, host, port = self._serve(runtime)
        try:
            status, payload, raw = self._request(host, port, "GET", "/health")
        finally:
            server.shutdown()
        self.assertEqual(status, 200)
        self.assertTrue(payload["pg_unavailable"])
        self.assertNotIn(b"Traceback", raw)
        self.assertNotIn(b"redaction-test-secret", raw)
        self.assertNotIn(b"postgresql+psycopg://", raw)


if __name__ == "__main__":
    unittest.main()
