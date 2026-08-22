"""Dashboard HTTP client for research-osd Operator API.

Does not own supervisors, run config, scope, or budget. Commands are
run-id only; research-osd reconstructs authoritative configuration from
PostgreSQL.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from research_os.application.autonomous_research_controller import (
    OrchestrationTickResult,
)
from research_os.application.errors import ApplicationError
from research_os.safe_data import redact_secret_keys

RESEARCH_OSD_URL_ENV = "RESEARCH_OSD_URL"


class OperatorApiRunControl:
    """ResearchRunControl-compatible client. Ignores client-supplied command config."""

    def __init__(self, base_url: str, *, opener=None) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("RESEARCH_OSD_URL must be a non-empty local URL")
        self._base = base_url.rstrip("/")
        self._opener = opener or urllib.request.build_opener()

    def start(self, command) -> OrchestrationTickResult:
        return self._action(command.research_run_id, "start")

    def resume(self, command) -> OrchestrationTickResult:
        return self._action(command.research_run_id, "resume")

    def pause(self, research_run_id: str) -> OrchestrationTickResult:
        return self._action(research_run_id, "pause")

    def cancel(self, research_run_id: str) -> OrchestrationTickResult:
        return self._action(research_run_id, "cancel")

    def _action(self, research_run_id: str, action: str) -> OrchestrationTickResult:
        payload = self._request("POST", f"/api/runs/{research_run_id}/{action}", {})
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise ApplicationError("operator API returned an invalid run result")
        return OrchestrationTickResult(
            research_run_id=str(result.get("research_run_id", research_run_id)),
            state=str(result.get("state", "")),
            cycle_number=int(result.get("cycle_number", 0)),
            outcome=str(result.get("outcome", "")),
            stop_reason=result.get("stop_reason"),
            last_phase=str(result.get("last_phase", "")),
            hypothesis_id=result.get("hypothesis_id"),
            experiment_id=result.get("experiment_id"),
        )

    def _request(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self._base + path,
            data=encoded if method != "GET" else None,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with self._opener.open(request, timeout=30) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ApplicationError(f"operator API {exc.code}") from None
        except urllib.error.URLError as exc:
            raise ApplicationError("research-osd is unreachable") from exc
        if not isinstance(raw, dict):
            raise ApplicationError("operator API returned a non-object")
        return redact_secret_keys(raw)
