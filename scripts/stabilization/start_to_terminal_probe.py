#!/usr/bin/env python3
"""Black-box Zest START-to-terminal stabilization probe.

Uses only existing dashboard/zestd APIs. No direct DB writes, no lifecycle
shortcuts, no fake model/Worker path.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlparse

RESULTS = {
    "SUCCESS",
    "COULD_NOT_START",
    "STUCK_OR_CRASHED",
    "FINISHED_WITHOUT_REQUIRED_WORK",
    "CAPABILITY_MISSING",
}
STOPLIKE = {"COMPLETED", "BUDGET_EXHAUSTED", "FAILED_OPERATIONAL", "BLOCKED", "WAITING_HUMAN", "PAUSED"}
ACCEPTED_STOPS = {"COMPLETED_NO_MORE_OPPORTUNITIES", "MAX_CYCLES_REACHED"}
CAPABILITY_GAPS = {"UNSUPPORTED_CAPABILITY", "PLANNING_INPUT_REJECTED", "IDENTITY_ANOMALY_COMPILE_REJECTED"}
DISCOVERY_ORIGIN = "surface-discovery-v1"


class ProbeError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_url(value: str, label: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProbeError(f"{label} must be an http(s) URL")
    if parsed.hostname != "localhost":
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as exc:
            raise ProbeError(f"{label} must use localhost or a loopback literal") from exc
        if not address.is_loopback:
            raise ProbeError(f"{label} must be loopback-only")
    return value.rstrip("/")


def http_json(base: str, method: str, path: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
    data = None if method == "GET" else json.dumps(dict(body or {})).encode()
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        detail = exc.read(65536).decode(errors="replace")
        raise ProbeError(f"{method} {path} -> HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise ProbeError(f"{method} {path} -> unreachable: {exc.reason}") from exc
    try:
        value = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeError(f"{method} {path} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ProbeError(f"{method} {path} returned a non-object")
    return value


def result(payload: Mapping[str, Any]) -> dict[str, Any]:
    nested = payload.get("result")
    return dict(nested) if isinstance(nested, Mapping) else dict(payload)


def items(root: Mapping[str, Any], *keys: str) -> list[dict[str, Any]]:
    value: Any = root
    for key in keys:
        if not isinstance(value, Mapping):
            return []
        value = value.get(key)
    if isinstance(value, Mapping) and isinstance(value.get("items"), list):
        return [dict(row) for row in value["items"] if isinstance(row, Mapping)]
    return []


def stamp(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def progress(detail: Mapping[str, Any]) -> tuple[Any, ...]:
    operational = detail.get("operational") if isinstance(detail.get("operational"), Mapping) else {}
    return (
        detail.get("state"), operational.get("effective_state"), detail.get("current_phase"),
        detail.get("cycle_number"), detail.get("request_count"), detail.get("worker_count"),
        detail.get("model_count"), detail.get("hypothesis_count"), detail.get("experiment_count"),
        detail.get("observation_count"), detail.get("updated_at"),
    )


def fatal_faults(detail: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = detail.get("run_faults") if isinstance(detail.get("run_faults"), list) else []
    return [dict(x) for x in rows if isinstance(x, Mapping) and x.get("fatal") is True and not x.get("resolved_at")]


def obligations(detail: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = detail.get("control_obligations") if isinstance(detail.get("control_obligations"), list) else []
    return [dict(x) for x in rows if isinstance(x, Mapping)]


def gate22_payload(origin: str, llm_budget: int) -> dict[str, Any]:
    return {
        "program_name": "Zest Stabilization Gate22",
        "program_handle": f"zest-stab-g22-{int(time.time())}",
        "platform": "manual",
        "target_reference": origin.rstrip("/") + "/",
        "authorization_reference": "controlled-local-stabilization-target",
        "operator_id": "stabilization-operator",
        "research_question": "Map the authorized application surface and derive one concrete testable research step from observed target behavior.",
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
        "daily_llm_budget_microdollars": llm_budget,
        "side_effect_ceiling": 1,
    }


def start_gate22():
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "tests"))
    try:
        from e2e.lab.surface_discovery_lab import Gate22SurfaceLab
    except Exception as exc:
        raise ProbeError(f"cannot import existing Gate22SurfaceLab: {exc.__class__.__name__}") from exc
    lab = Gate22SurfaceLab()
    return lab, lab.start()


def evaluate(detail: Mapping[str, Any], analysis: Mapping[str, Any], hits: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    facts = items(analysis, "surface", "facts")
    frontier = items(analysis, "surface", "frontier_items")
    attempts = items(analysis, "execution", "attempts")
    results = items(analysis, "execution", "worker_results")
    obs = items(analysis, "execution", "observations")
    hypotheses = items(analysis, "research", "hypotheses")
    reasoning = items(analysis, "research", "reasoning")
    admissions = items(analysis, "research", "admissions")
    assessments = items(analysis, "research", "assessments")
    experiments = items(analysis, "execution", "experiments")
    plans = items(analysis, "execution", "plans")

    layer_a_checks = {
        "target_received_seed": "GET /" in hits,
        "discovered_beyond_seed": any(str(x.get("normalized_path") or x.get("candidate_path") or "") not in {"", "/"} for x in [*facts, *frontier]),
        "execution_attempt_present": bool(attempts),
        "worker_result_present": bool(results),
        "observation_present": bool(obs),
        "no_unresolved_fatal_fault": not fatal_faults(detail),
        "no_control_obligation": not obligations(detail),
    }
    layer_a = {"passed": all(layer_a_checks.values()), "checks": layer_a_checks}

    discovery_ids = {str(x.get("hypothesis_id")) for x in hypotheses if x.get("origin_reference") == DISCOVERY_ORIGIN and x.get("hypothesis_id")}
    research_hyp = [x for x in hypotheses if x.get("hypothesis_id") and str(x.get("hypothesis_id")) not in discovery_ids]
    research_hyp_ids = {str(x["hypothesis_id"]) for x in research_hyp}
    research_exp = [x for x in experiments if str(x.get("hypothesis_id") or "") in research_hyp_ids]
    research_exp_ids = {str(x["experiment_id"]) for x in research_exp if x.get("experiment_id")}
    target_plans = [x for x in plans if str(x.get("experiment_id") or "") in research_exp_ids and x.get("required_capability") != "diagnostic.echo"]
    target_ids = {str(x["experiment_id"]) for x in target_plans if x.get("experiment_id")}
    target_attempts = [x for x in attempts if str(x.get("experiment_id") or "") in target_ids]
    target_results = [x for x in results if str(x.get("experiment_id") or "") in target_ids]
    target_assessments = [x for x in assessments if str(x.get("experiment_id") or "") in target_ids]

    obs_times = [t for row in obs for t in (stamp(row.get("observed_at")), stamp(row.get("created_at"))) if t is not None]
    reason_times = [t for row in reasoning if (t := stamp(row.get("created_at"))) is not None]
    roles = {str(x.get("role") or "") for x in reasoning}
    gap_codes = sorted({str(x.get("reason_code")) for x in admissions if str(x.get("reason_code") or "") in CAPABILITY_GAPS})

    layer_b_checks = {
        "observation_precedes_model_reasoning": bool(obs_times and reason_times and min(reason_times) >= min(obs_times)),
        "generator_and_falsifier_reasoning_present": {"GENERATOR", "FALSIFIER"}.issubset(roles),
        "non_discovery_hypothesis_admitted": bool(research_hyp),
        "separate_research_experiment_present": bool(research_exp),
        "target_coupled_non_diagnostic_plan_present": bool(target_plans),
        "target_experiment_attempted": bool(target_attempts),
        "target_experiment_worker_result_present": bool(target_results),
        "target_experiment_assessed": bool(target_assessments),
    }
    layer_b = {
        "passed": all(layer_b_checks.values()),
        "checks": layer_b_checks,
        "capability_missing_reason_codes": gap_codes,
    }
    return layer_a, layer_b


def classify(detail: Mapping[str, Any], analysis: Mapping[str, Any], a: Mapping[str, Any], b: Mapping[str, Any], timed_out: bool) -> tuple[str, str]:
    if not analysis:
        return "STUCK_OR_CRASHED", "required final run analysis unavailable"
    state = str(detail.get("state") or "")
    stop = str(detail.get("stop_reason") or "")
    if timed_out or state == "FAILED_OPERATIONAL" or fatal_faults(detail):
        return "STUCK_OR_CRASHED", "operational failure, fatal fault, or progress timeout"
    if state in {"WAITING_HUMAN", "BLOCKED", "PAUSED"}:
        return "STUCK_OR_CRASHED", f"run cannot continue autonomously from {state}"
    gaps = b.get("capability_missing_reason_codes")
    if isinstance(gaps, list) and gaps and not b.get("passed"):
        return "CAPABILITY_MISSING", "persisted admission/planning record identifies a missing executable capability"
    if state == "COMPLETED" and stop in ACCEPTED_STOPS and a.get("passed") and b.get("passed") and not obligations(detail) and detail.get("locally_supervised") is False:
        return "SUCCESS", "Layer A + Layer B passed with an accepted truthful stop"
    if state in STOPLIKE:
        return "FINISHED_WITHOUT_REQUIRED_WORK", f"{state}/{stop or 'NO_REASON'} without the full acceptance contract"
    return "STUCK_OR_CRASHED", f"unexpected final state {state or 'UNKNOWN'}"


def cancel(zest: str, run_id: str, timeout: float, interval: float) -> bool:
    rid = quote(run_id, safe="")
    try:
        http_json(zest, "POST", f"/api/runs/{rid}/cancel", {})
    except ProbeError:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            detail = result(http_json(zest, "GET", f"/api/runs/{rid}"))
            if detail.get("stop_reason") == "OPERATOR_CANCELLED" and detail.get("locally_supervised") is False:
                return True
        except ProbeError:
            pass
        time.sleep(interval)
    return False


def run(args: argparse.Namespace) -> int:
    zest = local_url(args.zest_url, "--zest-url")
    dashboard = local_url(args.dashboard_url, "--dashboard-url")
    lab = None
    record: dict[str, Any] = {"started_at": now(), "zest_url": zest, "dashboard_url": dashboard, "polls": []}
    classification = "COULD_NOT_START"
    reason = "not started"
    run_id = None
    timed_out = False

    try:
        if args.gate22:
            if args.llm_budget_microdollars <= 0:
                raise ProbeError("--gate22 requires positive explicit --llm-budget-microdollars")
            lab, origin = start_gate22()
            record["gate22_origin"] = origin
            print(f"GATE22_ORIGIN={origin}", flush=True)

        health = http_json(zest, "GET", "/health")
        record["health"] = health
        if health.get("ready_for_start") is not True:
            reason = "health.ready_for_start is not true"
            return 2

        if args.gate22:
            bootstrap = result(http_json(dashboard, "POST", "/api/programs/bootstrap", gate22_payload(origin, args.llm_budget_microdollars)))
            record["bootstrap"] = bootstrap
            run_id = str(bootstrap.get("research_run_id") or "")
        else:
            run_id = args.run_id
        if not run_id:
            raise ProbeError("research_run_id is missing")
        record["run_id"] = run_id
        rid = quote(run_id, safe="")

        preflight = result(http_json(zest, "POST", f"/api/runs/{rid}/preflight", {}))
        record["preflight"] = preflight
        if preflight.get("status") != "READY_TO_START":
            reason = "preflight is not READY_TO_START"
            return 2

        record["start_response"] = result(http_json(zest, "POST", f"/api/runs/{rid}/start", {}))
        deadline = time.monotonic() + args.total_timeout
        last_progress = time.monotonic()
        last_fp = None
        stoplike_since = None
        final_detail: dict[str, Any] = {}

        while True:
            if time.monotonic() >= deadline:
                timed_out, reason = True, "total runtime timeout"
                break
            try:
                detail = result(http_json(zest, "GET", f"/api/runs/{rid}"))
            except ProbeError as exc:
                if time.monotonic() - last_progress >= args.no_progress_timeout:
                    timed_out, reason = True, f"run API unavailable without progress: {exc}"
                    break
                time.sleep(args.poll_interval)
                continue
            final_detail = detail
            fp = progress(detail)
            if fp != last_fp:
                last_fp, last_progress = fp, time.monotonic()
            row = {k: detail.get(k) for k in ("state", "current_phase", "cycle_number", "stop_reason", "locally_supervised", "worker_count", "model_count", "hypothesis_count", "experiment_count", "observation_count")}
            row["at"] = now()
            record["polls"].append(row)
            print("POLL", " ".join(f"{k}={v}" for k, v in row.items() if k != "at"), flush=True)

            state = str(detail.get("state") or "")
            if state in STOPLIKE:
                stoplike_since = stoplike_since or time.monotonic()
                if detail.get("locally_supervised") is False:
                    break
                if time.monotonic() - stoplike_since >= args.terminal_supervisor_grace:
                    break
            else:
                stoplike_since = None
            if time.monotonic() - last_progress >= args.no_progress_timeout:
                timed_out, reason = True, "no-progress timeout"
                break
            time.sleep(args.poll_interval)

        record["final_detail"] = final_detail
        try:
            analysis = result(http_json(zest, "GET", f"/api/runs/{rid}/analysis"))
        except ProbeError as exc:
            analysis = {}
            record["analysis_error"] = str(exc)
        record["final_analysis"] = analysis
        hits = list(lab.hits) if lab is not None else []
        record["target_hits"] = hits

        needs_cleanup = timed_out or (final_detail.get("locally_supervised") is True and str(final_detail.get("state") or "") in STOPLIKE)
        if needs_cleanup:
            record["cancel_attempted"] = True
            record["cancel_verified"] = cancel(zest, run_id, args.cancel_timeout, args.poll_interval)
            if not record["cancel_verified"]:
                timed_out = True
                reason += "; cancel verification failed"

        layer_a, layer_b = evaluate(final_detail, analysis, hits)
        record["layer_a"] = layer_a
        record["layer_b"] = layer_b
        classification, class_reason = classify(final_detail, analysis, layer_a, layer_b, timed_out)
        reason = f"{reason}; {class_reason}" if timed_out else class_reason
        return 0 if classification == "SUCCESS" else 1

    except ProbeError as exc:
        reason = str(exc)
        classification = "COULD_NOT_START" if run_id is None else "STUCK_OR_CRASHED"
        return 2
    finally:
        if lab is not None:
            record["target_hits"] = list(lab.hits)
            try:
                lab.stop()
            except Exception:
                pass
        record["classification"] = classification
        record["reason"] = reason
        record["completed_at"] = now()
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(record, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        print(json.dumps({"classification": classification, "reason": reason, "run_id": run_id, "output": str(output)}, indent=2), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Drive one real Zest START-to-terminal stabilization run")
    parser.add_argument("--zest-url", default="http://127.0.0.1:8766")
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:8765")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--gate22", action="store_true", help="launch the existing Gate22 lab and create a fresh run")
    mode.add_argument("--run-id", help="diagnose an already-created run; cannot prove target-side hit by itself")
    parser.add_argument("--llm-budget-microdollars", type=int, default=0)
    parser.add_argument("--total-timeout", type=float, default=600.0)
    parser.add_argument("--no-progress-timeout", type=float, default=240.0)
    parser.add_argument("--terminal-supervisor-grace", type=float, default=10.0)
    parser.add_argument("--cancel-timeout", type=float, default=30.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--output", default="var/stabilization/start_to_terminal_result.json")
    args = parser.parse_args()
    if min(args.total_timeout, args.no_progress_timeout, args.cancel_timeout, args.poll_interval) <= 0 or args.terminal_supervisor_grace < 0:
        parser.error("timeouts must be positive; supervisor grace must be non-negative")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
