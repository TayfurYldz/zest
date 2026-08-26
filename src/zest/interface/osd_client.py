"""Dashboard HTTP client for zestd Operator API.

Does not own supervisors, run config, scope, or budget. Commands are
run-id only; zestd reconstructs authoritative configuration from
PostgreSQL.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from zest.application.autonomous_research_controller import (
    OrchestrationTickResult,
)
from zest.application.errors import ApplicationError
from zest.application.operator_errors import OperatorError, OperatorErrorCode
from zest.safe_data import redact_secret_keys

ZEST_URL_ENV = "ZEST_URL"


class OperatorApiRunControl:
    """ResearchRunControl-compatible client. Ignores client-supplied command config."""

    def __init__(self, base_url: str, *, opener=None) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("ZEST_URL must be a non-empty local URL")
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

    def execute_preflight(self, research_run_id: str) -> dict[str, Any]:
        payload = self._request("POST", f"/api/runs/{research_run_id}/preflight", {})
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise ApplicationError("operator API returned an invalid preflight result")
        return result

    def latest_preflight(self, research_run_id: str) -> dict[str, Any] | None:
        payload = self._request("GET", f"/api/runs/{research_run_id}/preflight/latest", {})
        result = payload.get("result") if isinstance(payload, dict) else None
        if result is None:
            return None
        if not isinstance(result, dict):
            raise ApplicationError("operator API returned an invalid preflight result")
        return result

    def get_health(self) -> dict[str, Any]:
        raw = self._request("GET", "/health", {})
        if not isinstance(raw, dict):
            raise ApplicationError("operator API returned invalid health")
        return raw

    def list_runs(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/api/runs", {})
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, list):
            raise ApplicationError("operator API returned invalid run list")
        return result

    def list_programs(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/api/programs", {})
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, list):
            raise ApplicationError("operator API returned invalid program list")
        return result

    def get_run(self, research_run_id: str) -> dict[str, Any]:
        payload = self._request("GET", f"/api/runs/{research_run_id}", {})
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise ApplicationError("operator API returned an invalid run result")
        return result

    def get_run_analysis(self, research_run_id: str) -> dict[str, Any]:
        payload = self._request("GET", f"/api/runs/{research_run_id}/analysis", {})
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise ApplicationError("operator API returned an invalid run analysis")
        return result

    def console_snapshot(self) -> dict[str, Any]:
        payload = self._request("GET", "/api/console", {})
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise ApplicationError("operator API returned an invalid console snapshot")
        return result

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
            raise _operator_http_error(exc.code, detail) from None
        except urllib.error.URLError as exc:
            raise OperatorError(
                OperatorErrorCode.OSD_UNREACHABLE, "zestd is unreachable"
            ) from exc
        if not isinstance(raw, dict):
            raise ApplicationError("operator API returned a non-object")
        return redact_secret_keys(raw)


def _operator_http_error(status: int, body: str) -> ApplicationError:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return OperatorError(OperatorErrorCode.OSD_UNREACHABLE, f"operator API {status}")
    if not isinstance(payload, dict):
        return OperatorError(OperatorErrorCode.OSD_UNREACHABLE, f"operator API {status}")
    code_value = str(payload.get("error") or "")
    detail = str(payload.get("detail") or payload.get("error") or f"operator API {status}")
    for item in OperatorErrorCode:
        if item.value == code_value:
            return OperatorError(item, detail)
    return OperatorError(OperatorErrorCode.INVALID_STATE, detail)
