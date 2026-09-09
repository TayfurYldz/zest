#!/usr/bin/env python3
"""S2 deployed control-path probe for the recovered Phase8 baseline.

This is an external driver only. It uses the existing dashboard/zestd HTTP
surfaces, starts the existing Gate22 loopback lab, waits for one real target
contact + WorkerResult/Observation, then exercises the existing cancel path.
It never writes PostgreSQL directly and never bypasses preflight, scope,
authorization, Worker, or model boundaries.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote


class ProbeError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def request_json(base: str, method: str, path: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
    encoded = None if method == "GET" else json.dumps(dict(body or {})).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=encoded,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        detail = exc.read(65536).decode("utf-8", errors="replace")
        raise ProbeError(f"{method} {path} -> HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise ProbeError(f"{method} {path} -> unreachable: {exc.reason}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeError(f"{method} {path} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ProbeError(f"{method} {path} returned non-object JSON")
    return payload


def unwrap(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    return dict(result) if isinstance(result, Mapping) else dict(payload)


def gate22_payload(origin: str, llm_budget_microdollars: int) -> dict[str, Any]:
    return {
        "program_name": "Zest Stabilization S2 Control Path",
        "program_handle": f"zest-stab-s2-{int(time.time())}",
        "platform": "manual",
        "target_reference": origin.rstrip("/") + "/",
        "authorization_reference": "controlled-local-stabilization-target",
        "operator_id": "stabilization-operator",
        "research_question": "Map the authorized local application surface before controlled operator cancellation.",
        "in_scope": [origin.rstrip("/")],
        "out_of_scope": ["http://example.com"],
        "forbidden_actions": [],
        "max_response_bytes": 1048576,
        "timeout_ms": 10000,
        "max_requests_per_window": 60,
        "window_seconds": 60,
        "max_requests": 500,
        "max_tool_calls": 200,
        "max_runtime_ms": 600000,
        "max_concurrency": 1,
        "max_cycles": 20,
        "max_experiments": 50,
        "max_model_calls": 50,
        "max_worker_invocations": 100,
        "max_elapsed_ms": 600000,
        "max_selected_opportunities": 4,
        "max_runtime_fallback": 1,
        "daily_llm_budget_microdollars": llm_budget_microdollars,
        "side_effect_ceiling": 1,
    }


def start_gate22():
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "tests"))
    try:
        from e2e.lab.surface_discovery_lab import Gate22SurfaceLab
    except Exception as exc:
        raise ProbeError(f"cannot import Gate22SurfaceLab: {exc.__class__.__name__}: {exc}") from exc
    lab = Gate22SurfaceLab()
    return lab, lab.start()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify deployed create→preflight→start→observe→cancel")
    parser.add_argument("--zest-url", default="http://127.0.0.1:8766")
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:8765")
    parser.add_argument("--llm-budget-microdollars", type=int, required=True)
    parser.add_argument("--observe-timeout", type=float, default=180.0)
    parser.add_argument("--cancel-timeout", type=float, default=45.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--output", default="var/stabilization/s2_control_path_result.json")
    args = parser.parse_args()
    if args.llm_budget_microdollars <= 0:
        parser.error("--llm-budget-microdollars must be positive")
    if min(args.observe_timeout, args.cancel_timeout, args.poll_interval) <= 0:
        parser.error("timeouts must be positive")

    zest = args.zest_url.rstrip("/")
    dashboard = args.dashboard_url.rstrip("/")
    lab = None
    run_id: str | None = None
    started = False
    record: dict[str, Any] = {"started_at": now(), "polls": []}
    exit_code = 2

    try:
        lab, origin = start_gate22()
        record["gate22_origin"] = origin
        print(f"GATE22_ORIGIN={origin}", flush=True)

        health = request_json(zest, "GET", "/health")
        record["health"] = health
        if health.get("ready_for_start") is not True:
            raise ProbeError("zestd health is not ready_for_start")

        bootstrap = unwrap(request_json(dashboard, "POST", "/api/programs/bootstrap", gate22_payload(origin, args.llm_budget_microdollars)))
        record["bootstrap"] = bootstrap
        run_id = str(bootstrap.get("research_run_id") or "")
        if not run_id:
            raise ProbeError("bootstrap did not return research_run_id")
        print(f"RUN_ID={run_id}", flush=True)
        rid = quote(run_id, safe="")

        preflight = unwrap(request_json(zest, "POST", f"/api/runs/{rid}/preflight", {}))
        record["preflight"] = preflight
        print(f"PREFLIGHT={preflight.get('status')}", flush=True)
        if preflight.get("status") != "READY_TO_START":
            raise ProbeError("preflight is not READY_TO_START")

        start_response = unwrap(request_json(zest, "POST", f"/api/runs/{rid}/start", {}))
        record["start_response"] = start_response
        started = True
        print(f"START_STATE={start_response.get('state')}", flush=True)

        deadline = time.monotonic() + args.observe_timeout
        observed = False
        final_before_cancel: dict[str, Any] = {}
        while time.monotonic() < deadline:
            detail = unwrap(request_json(zest, "GET", f"/api/runs/{rid}"))
            final_before_cancel = detail
            row = {
                key: detail.get(key)
                for key in (
                    "state",
                    "current_phase",
                    "cycle_number",
                    "stop_reason",
                    "locally_supervised",
                    "request_count",
                    "worker_count",
                    "model_count",
                    "hypothesis_count",
                    "experiment_count",
                    "observation_count",
                )
            }
            row["at"] = now()
            record["polls"].append(row)
            print("POLL " + " ".join(f"{k}={v}" for k, v in row.items() if k != "at"), flush=True)

            worker_count = int(detail.get("worker_count") or 0)
            observation_count = int(detail.get("observation_count") or 0)
            seed_hit = "GET /" in list(lab.hits)
            if worker_count >= 1 and observation_count >= 1 and seed_hit:
                observed = True
                break

            state = str(detail.get("state") or "")
            if state in {"COMPLETED", "BUDGET_EXHAUSTED", "FAILED_OPERATIONAL", "WAITING_HUMAN", "BLOCKED", "PAUSED"}:
                break
            time.sleep(args.poll_interval)

        record["before_cancel"] = final_before_cancel
        record["target_hits_before_cancel"] = list(lab.hits)
        record["real_work_observed"] = observed
        if not observed:
            raise ProbeError("run did not produce seed target contact + WorkerResult/Observation before stop/timeout")

        cancel_response = unwrap(request_json(zest, "POST", f"/api/runs/{rid}/cancel", {}))
        record["cancel_response"] = cancel_response
        print(f"CANCEL_STATE={cancel_response.get('state')} CANCEL_REASON={cancel_response.get('stop_reason')}", flush=True)

        deadline = time.monotonic() + args.cancel_timeout
        cancel_verified = False
        final_detail: dict[str, Any] = {}
        while time.monotonic() < deadline:
            final_detail = unwrap(request_json(zest, "GET", f"/api/runs/{rid}"))
            if final_detail.get("stop_reason") == "OPERATOR_CANCELLED" and final_detail.get("locally_supervised") is False:
                cancel_verified = True
                break
            time.sleep(args.poll_interval)

        record["final_detail"] = final_detail
        record["cancel_verified"] = cancel_verified
        try:
            record["final_analysis"] = unwrap(request_json(zest, "GET", f"/api/runs/{rid}/analysis"))
        except ProbeError as exc:
            record["analysis_error"] = str(exc)

        checks = {
            "bootstrap_startable": bootstrap.get("state") == "STARTABLE",
            "preflight_ready": preflight.get("status") == "READY_TO_START",
            "start_returned_run": start_response.get("research_run_id") == run_id,
            "target_received_seed": "GET /" in list(lab.hits),
            "worker_and_observation_seen": observed,
            "cancel_verified": cancel_verified,
        }
        record["checks"] = checks
        passed = all(checks.values())
        record["result"] = "PASS" if passed else "FAIL"
        print("\n" + "\n".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in checks.items()), flush=True)
        print(f"S2_2_RESULT={'PASS' if passed else 'FAIL'}", flush=True)
        exit_code = 0 if passed else 1

    except ProbeError as exc:
        record["error"] = str(exc)
        record["result"] = "FAIL"
        print(f"S2_2_RESULT=FAIL error={exc}", flush=True)
        if started and run_id:
            try:
                rid = quote(run_id, safe="")
                request_json(zest, "POST", f"/api/runs/{rid}/cancel", {})
                record["cleanup_cancel_attempted"] = True
            except Exception as cleanup_exc:
                record["cleanup_cancel_error"] = cleanup_exc.__class__.__name__
        exit_code = 2
    finally:
        if lab is not None:
            record["target_hits_final"] = list(lab.hits)
            try:
                lab.stop()
            except Exception:
                pass
        record["completed_at"] = now()
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(record, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        print(f"OUTPUT={output}", flush=True)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
