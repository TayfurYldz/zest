from __future__ import annotations

import json
import unittest
from io import BytesIO
from unittest import mock

import pathsetup  # noqa: F401

from research_os.application.operator_command_payload import reject_authority_overrides
from research_os.application.operator_errors import OperatorError, OperatorErrorCode
from research_os.application.osd_settings import LINUX_CONFIG_DIR, load_osd_settings
from research_os.interface.dashboard import DashboardHandler, collect_dashboard_payload
from research_os.interface.operator_api import OperatorApiServer


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


class OsdSettingsTests(unittest.TestCase):
    def test_linux_paths_and_no_home_assumption(self) -> None:
        settings = load_osd_settings(
            {
                "RESEARCH_OS_DATABASE_URL": "postgresql+psycopg://research_os@127.0.0.1/research_os",
                "RESEARCH_OSD_LOG_PATH": "/var/log/research-os/research-osd.log",
            }
        )
        public = settings.public_mapping()
        rendered = json.dumps(public)
        self.assertNotIn("password", rendered)
        self.assertNotIn("/home/tayfur", rendered)
        self.assertEqual(public["linux_paths"]["config"], LINUX_CONFIG_DIR)
        self.assertEqual(settings.bind_host, "127.0.0.1")


class DashboardClientOnlyTests(unittest.TestCase):
    def test_collect_payload_marks_client_only(self) -> None:
        payload = collect_dashboard_payload(env={"RESEARCH_OS_DATABASE_URL": ""})
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


if __name__ == "__main__":
    unittest.main()
