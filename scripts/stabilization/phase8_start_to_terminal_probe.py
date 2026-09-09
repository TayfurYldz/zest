#!/usr/bin/env python3
"""Phase8 black-box START-to-terminal stabilization probe.

External driver only. Uses existing dashboard/zestd APIs and the existing
Gate22 loopback lab. It does not write PostgreSQL directly, construct ARC,
bypass preflight/Core, fake Worker/model results, or reinterpret incomplete
work as success.
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
from typing import Any, Iterable, Mapping
from urllib.parse import quote, urlparse

RESULTS = {
    "SUCCESS",
    "COULD_NOT_START",
    "STUCK_OR_CRASHED",
    "FINISHED_WITHOUT_REQUIRED_WORK",
    "CAPABILITY_MISSING",
}
STOPLIKE = {
    "COMPLETED",
    "BUDGET_EXHAUSTED",
    "FAILED_OPERATIONAL",
    "BLOCKED",
    "WAITING_HUMAN",
    "PAUSED",
}
DISCOVERY_ORIGIN = "surface-discovery-v1"
WORK_FABRIC_PREFIX = "research-work-fabric.v1:"
NORMAL_ACCEPTED_STOPS = {"COMPLETED_NO_MORE_OPPORTUNITIES"}
CONDITIONAL_ACCEPTED_STOPS = {
    "MAX_CYCLES_REACHED",
    "COMPLETED_UNDER_CURRENT_AUTHORITY_WITH_BLOCKED_WORK",
}
MODEL_CAPABILITY_GAPS = {
    "UNSUPPORTED_CAPABILITY",
    "PLANNING_INPUT_REJECTED",
    "IDENTITY_ANOMALY_COMPILE_REJECTED",
}


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


def http_json(
    base: str,
    method: str,
    path: str,
    body: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    data = None if method == "GET" else json.dumps(dict(body or {})).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read(16 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        detail = exc.read(65536).decode("utf-8", errors="replace")
        raise ProbeError(f"{method} {path} -> HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise ProbeError(f"{method} {path} -> unreachable: {exc.reason}") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeError(f"{method} {path} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ProbeError(f"{method} {path} returned a non-object")
    return value


def unwrap(payload: Mapping[str, Any]) -> dict[str, Any]:
    nested = payload.get("result")
    return dict(nested) if isinstance(nested, Mapping) else dict(payload)


def bundle_items(root: Mapping[str, Any], *keys: str) -> list[dict[str, Any]]:
    value: Any = root
    for key in keys:
        if not isinstance(value, Mapping):
            return []
        value = value.get(key)
    if isinstance(value, Mapping) and isinstance(value.get("items"), list):
        return [dict(item) for item in value["items"] if isinstance(item, Mapping)]
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


def first_stamp(rows: Iterable[Mapping[str, Any]], *names: str) -> float | None:
    values: list[float] = []
    for row in rows:
        for name in names:
            item = stamp(row.get(name))
            if item is not None:
                values.append(item)
    return min(values) if values else None


def fatal_faults(detail: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = detail.get("run_faults")
    if not isinstance(rows, list):
        return []
    return [
        dict(row)
        for row in rows
        if isinstance(row, Mapping)
        and row.get("fatal") is True
        and not row.get("resolved_at")
    ]


def obligations(detail: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = detail.get("control_obligations")
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def progress(detail: Mapping[str, Any]) -> tuple[Any, ...]:
    operational = detail.get("operational") if isinstance(detail.get("operational"), Mapping) else {}
    return (
        detail.get("state"),
        operational.get("effective_state"),
        detail.get("current_phase"),
        detail.get("cycle_number"),
        detail.get("request_count"),
        detail.get("worker_count"),
        detail.get("model_count"),
        detail.get("hypothesis_count"),
        detail.get("experiment_count"),
        detail.get("observation_count"),
        detail.get("evidence_count"),
        detail.get("candidate_count"),
        detail.get("updated_at"),
    )


def gate22_payload(origin: str, llm_budget: int) -> dict[str, Any]:
    return {
        "program_name": "Zest Phase8 START-to-Terminal Stabilization",
        "program_handle": f"zest-stab-p8-{int(time.time())}",
        "platform": "manual",
        "target_reference": origin.rstrip("/") + "/",
        "authorization_reference": "controlled-local-stabilization-target",
        "operator_id": "stabilization-operator",
        "research_question": (
            "Map the authorized application surface and derive concrete testable "
            "research work from observed target behavior under current authority."
        ),
        "in_scope": [origin.rstrip("/")],
        "out_of_scope": ["http://example.com"],
        "forbidden_actions": [],
        "max_response_bytes": 1048576,
        "timeout_ms": 10000,
        "max_requests_per_window": 90,
        "window_seconds": 60,
        "max_requests": 800,
        "max_tool_calls": 240,
        "max_runtime_ms": 900000,
        "max_concurrency": 1,
        "max_cycles": 36,
        "max_experiments": 60,
        "max_model_calls": 50,
        "max_worker_invocations": 120,
        "max_elapsed_ms": 900000,
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
        raise ProbeError(
            f"cannot import existing Gate22SurfaceLab: {exc.__class__.__name__}: {exc}"
        ) from exc
    lab = Gate22SurfaceLab()
    return lab, lab.start()


def _surface_beyond_seed(facts: list[dict[str, Any]], frontier: list[dict[str, Any]]) -> bool:
    for row in [*facts, *frontier]:
        for name in ("normalized_path", "candidate_path", "path"):
            value = row.get(name)
            if isinstance(value, str) and value not in {"", "/"}:
                return True
    return False


def _audit_events(analysis: Mapping[str, Any]) -> list[dict[str, Any]]:
    return bundle_items(analysis, "audit", "events")


def _reason_codes_from(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, tuple):
        return [str(item) for item in value if item is not None]
    return []


def explicit_capability_gaps(
    admissions: list[dict[str, Any]], audits: list[dict[str, Any]]
) -> list[str]:
    gaps: set[str] = set()
    for row in admissions:
        reason = str(row.get("reason_code") or "")
        if reason in MODEL_CAPABILITY_GAPS:
            gaps.add(reason)
        for item in _reason_codes_from(row.get("reason_codes")):
            if item in MODEL_CAPABILITY_GAPS:
                gaps.add(item)
    for row in audits:
        event_type = str(row.get("event_type") or "")
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        reasons = _reason_codes_from(payload.get("reason_codes"))
        if event_type == "RESEARCH_WORK_DEFERRED_ENGINE_WIRING":
            gaps.add("DEFERRED_ENGINE_WIRING")
        for reason in reasons:
            upper = reason.upper()
            if (
                "UNSUPPORTED" in upper
                or "CAPABILITY_NOT_YET_CONNECTED" in upper
                or "DEFERRED_ENGINE_WIRING" in upper
                or reason in MODEL_CAPABILITY_GAPS
            ):
                gaps.add(reason)
    return sorted(gaps)


def authority_block_completion_proven(audits: list[dict[str, Any]]) -> bool:
    for row in audits:
        if row.get("event_type") != "GLOBAL_RESEARCH_WORK_AUDIT":
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        if payload.get("completion_allowed") is True and payload.get("protocol_authority_blocked") is True:
            return True
    return False


def evaluate(
    detail: Mapping[str, Any],
    analysis: Mapping[str, Any],
    hits: list[str],
    origin: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    facts = bundle_items(analysis, "surface", "facts")
    frontier = bundle_items(analysis, "surface", "frontier_items")
    attempts = bundle_items(analysis, "execution", "attempts")
    worker_results = bundle_items(analysis, "execution", "worker_results")
    observations = bundle_items(analysis, "execution", "observations")
    hypotheses = bundle_items(analysis, "research", "hypotheses")
    reasoning = bundle_items(analysis, "research", "reasoning")
    admissions = bundle_items(analysis, "research", "admissions")
    assessments = bundle_items(analysis, "research", "assessments")
    experiments = bundle_items(analysis, "execution", "experiments")
    plans = bundle_items(analysis, "execution", "plans")
    audits = _audit_events(analysis)

    layer_a_checks = {
        "target_received_seed": "GET /" in hits,
        "discovered_beyond_seed": _surface_beyond_seed(facts, frontier),
        "execution_attempt_present": bool(attempts),
        "worker_result_present": bool(worker_results),
        "observation_present": bool(observations),
        "no_unresolved_fatal_fault": not fatal_faults(detail),
        "no_control_obligation": not obligations(detail),
        "terminal_supervisor_released": detail.get("locally_supervised") is False,
    }
    layer_a = {"passed": all(layer_a_checks.values()), "checks": layer_a_checks}

    hypothesis_by_id = {
        str(row.get("hypothesis_id")): row
        for row in hypotheses
        if row.get("hypothesis_id")
    }
    discovery_hyp_ids = {
        hid
        for hid, row in hypothesis_by_id.items()
        if str(row.get("origin_reference") or "") == DISCOVERY_ORIGIN
    }
    work_fabric_hyp_ids = {
        hid
        for hid, row in hypothesis_by_id.items()
        if str(row.get("origin_reference") or "").startswith(WORK_FABRIC_PREFIX)
    }
    research_hyp_ids = set(hypothesis_by_id) - discovery_hyp_ids

    experiment_by_id = {
        str(row.get("experiment_id")): row
        for row in experiments
        if row.get("experiment_id")
    }
    discovery_exp_ids = {
        eid
        for eid, row in experiment_by_id.items()
        if str(row.get("hypothesis_id") or "") in discovery_hyp_ids
    }
    research_exp_ids = {
        eid
        for eid, row in experiment_by_id.items()
        if str(row.get("hypothesis_id") or "") in research_hyp_ids
    }

    target_plan_by_exp: dict[str, dict[str, Any]] = {}
    for row in plans:
        eid = str(row.get("experiment_id") or "")
        if not eid or eid not in research_exp_ids:
            continue
        capability = str(row.get("required_capability") or "")
        target = str(row.get("target_reference") or "")
        if capability == "diagnostic.echo":
            continue
        if target and not target.startswith(origin.rstrip("/")):
            continue
        target_plan_by_exp[eid] = row
    target_exp_ids = set(target_plan_by_exp)

    attempts_by_exp: dict[str, list[dict[str, Any]]] = {}
    for row in attempts:
        eid = str(row.get("experiment_id") or "")
        attempts_by_exp.setdefault(eid, []).append(row)
    results_by_exp: dict[str, list[dict[str, Any]]] = {}
    result_exp_by_id: dict[str, str] = {}
    for row in worker_results:
        eid = str(row.get("experiment_id") or "")
        results_by_exp.setdefault(eid, []).append(row)
        wid = str(row.get("worker_result_id") or "")
        if wid:
            result_exp_by_id[wid] = eid
    assessments_by_exp: dict[str, list[dict[str, Any]]] = {}
    for row in assessments:
        eid = str(row.get("experiment_id") or "")
        assessments_by_exp.setdefault(eid, []).append(row)

    complete_target_exp_ids = {
        eid
        for eid in target_exp_ids
        if attempts_by_exp.get(eid)
        and results_by_exp.get(eid)
        and assessments_by_exp.get(eid)
    }
    work_fabric_target_ids = {
        eid
        for eid in complete_target_exp_ids
        if str(experiment_by_id[eid].get("hypothesis_id") or "") in work_fabric_hyp_ids
    }
    model_target_ids = complete_target_exp_ids - work_fabric_target_ids

    discovery_worker_result_ids = {
        str(row.get("worker_result_id"))
        for row in worker_results
        if row.get("worker_result_id")
        and str(row.get("experiment_id") or "") in discovery_exp_ids
    }
    discovery_observations = [
        row
        for row in observations
        if str(row.get("worker_result_id") or "") in discovery_worker_result_ids
    ]
    discovery_observation_time = first_stamp(
        discovery_observations, "observed_at", "created_at"
    )
    research_action_rows: list[dict[str, Any]] = []
    for eid in complete_target_exp_ids:
        research_action_rows.append(target_plan_by_exp[eid])
        research_action_rows.extend(attempts_by_exp.get(eid, []))
    research_action_time = first_stamp(
        research_action_rows, "created_at", "started_at", "received_at"
    )
    observation_precedes = (
        discovery_observation_time is not None
        and research_action_time is not None
        and discovery_observation_time <= research_action_time
    )

    roles = {str(row.get("role") or "") for row in reasoning}
    reasoning_times = [
        item
        for item in (stamp(row.get("created_at")) for row in reasoning)
        if item is not None
    ]
    model_reasoning_after_discovery = (
        discovery_observation_time is not None
        and bool(reasoning_times)
        and max(reasoning_times) >= discovery_observation_time
        and {"GENERATOR", "FALSIFIER"}.issubset(roles)
    )

    work_fabric_path = bool(work_fabric_target_ids)
    model_path = bool(model_target_ids) and model_reasoning_after_discovery
    path_kind = "WORK_FABRIC" if work_fabric_path else "MODEL" if model_path else "NONE"

    layer_b_checks = {
        "discovery_observation_precedes_research_action": observation_precedes,
        "non_discovery_research_hypothesis_present": bool(research_hyp_ids),
        "separate_research_experiment_present": bool(research_exp_ids),
        "target_coupled_non_diagnostic_plan_present": bool(target_exp_ids),
        "target_research_attempt_present": any(attempts_by_exp.get(eid) for eid in target_exp_ids),
        "target_research_worker_result_present": any(results_by_exp.get(eid) for eid in target_exp_ids),
        "target_research_assessed": any(assessments_by_exp.get(eid) for eid in target_exp_ids),
        "valid_phase8_research_lane": work_fabric_path or model_path,
        "no_control_obligation": not obligations(detail),
    }
    gaps = explicit_capability_gaps(admissions, audits)
    layer_b = {
        "passed": all(layer_b_checks.values()),
        "checks": layer_b_checks,
        "research_lane": path_kind,
        "work_fabric_complete_experiment_ids": sorted(work_fabric_target_ids),
        "model_complete_experiment_ids": sorted(model_target_ids),
        "model_roles": sorted(role for role in roles if role),
        "capability_missing_reason_codes": gaps,
    }

    diagnostics = {
        "discovery_hypothesis_ids": sorted(discovery_hyp_ids),
        "work_fabric_hypothesis_ids": sorted(work_fabric_hyp_ids),
        "research_hypothesis_ids": sorted(research_hyp_ids),
        "discovery_experiment_ids": sorted(discovery_exp_ids),
        "target_research_experiment_ids": sorted(target_exp_ids),
        "complete_target_research_experiment_ids": sorted(complete_target_exp_ids),
        "authority_block_completion_proven": authority_block_completion_proven(audits),
        "audit_research_work_events": [
            {
                "event_type": row.get("event_type"),
                "occurred_at": row.get("occurred_at"),
                "payload": row.get("payload"),
            }
            for row in audits
            if str(row.get("event_type") or "").startswith("RESEARCH_WORK_")
            or row.get("event_type") == "GLOBAL_RESEARCH_WORK_AUDIT"
        ],
    }
    return layer_a, layer_b, diagnostics


def accepted_stop(
    detail: Mapping[str, Any],
    layer_a: Mapping[str, Any],
    layer_b: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
) -> bool:
    stop = str(detail.get("stop_reason") or "")
    if stop in NORMAL_ACCEPTED_STOPS:
        return True
    if stop == "MAX_CYCLES_REACHED":
        return bool(layer_a.get("passed") and layer_b.get("passed"))
    if stop == "COMPLETED_UNDER_CURRENT_AUTHORITY_WITH_BLOCKED_WORK":
        return bool(
            layer_a.get("passed")
            and layer_b.get("passed")
            and diagnostics.get("authority_block_completion_proven") is True
        )
    return False


def classify(
    detail: Mapping[str, Any],
    analysis: Mapping[str, Any],
    layer_a: Mapping[str, Any],
    layer_b: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    timed_out: bool,
) -> tuple[str, str]:
    if not analysis:
        return "STUCK_OR_CRASHED", "required final HQ analysis unavailable"
    state = str(detail.get("state") or "")
    stop = str(detail.get("stop_reason") or "")
    if timed_out or state == "FAILED_OPERATIONAL" or fatal_faults(detail):
        return "STUCK_OR_CRASHED", "operational failure, fatal fault, or progress timeout"
    if state in {"WAITING_HUMAN", "BLOCKED", "PAUSED"}:
        return "STUCK_OR_CRASHED", f"run cannot continue autonomously from {state}"
    if state in STOPLIKE and detail.get("locally_supervised") is True:
        return "STUCK_OR_CRASHED", "terminal/stop-like state still has a live local supervisor"
    gaps = layer_b.get("capability_missing_reason_codes")
    if isinstance(gaps, list) and gaps and not layer_b.get("passed"):
        return "CAPABILITY_MISSING", "persisted planning/admission evidence identifies an unsupported or not-connected capability"
    if (
        state == "COMPLETED"
        and accepted_stop(detail, layer_a, layer_b, diagnostics)
        and layer_a.get("passed")
        and layer_b.get("passed")
        and not obligations(detail)
        and detail.get("locally_supervised") is False
    ):
        return "SUCCESS", f"Layer A + Layer B passed through {layer_b.get('research_lane')} with truthful stop {stop}"
    if state in STOPLIKE:
        return "FINISHED_WITHOUT_REQUIRED_WORK", f"{state}/{stop or 'NO_REASON'} without the full Phase8 acceptance contract"
    return "STUCK_OR_CRASHED", f"unexpected final state {state or 'UNKNOWN'}"


def cancel_and_verify(
    zest: str,
    run_id: str,
    timeout: float,
    interval: float,
) -> bool:
    rid = quote(run_id, safe="")
    try:
        http_json(zest, "POST", f"/api/runs/{rid}/cancel", {})
    except ProbeError:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            detail = unwrap(http_json(zest, "GET", f"/api/runs/{rid}"))
            if detail.get("locally_supervised") is False and str(detail.get("state") or "") in STOPLIKE:
                return True
        except ProbeError:
            pass
        time.sleep(interval)
    return False


def run(args: argparse.Namespace) -> int:
    zest = local_url(args.zest_url, "--zest-url")
    dashboard = local_url(args.dashboard_url, "--dashboard-url")
    if args.llm_budget_microdollars <= 0:
        raise ProbeError("--llm-budget-microdollars must be positive")

    lab = None
    run_id: str | None = None
    started = False
    timed_out = False
    classification = "COULD_NOT_START"
    reason = "not started"
    record: dict[str, Any] = {
        "schema": "zest.phase8.start-to-terminal.v1",
        "started_at": now(),
        "zest_url": zest,
        "dashboard_url": dashboard,
        "bounds": {
            "total_timeout": args.total_timeout,
            "no_progress_timeout": args.no_progress_timeout,
            "terminal_supervisor_grace": args.terminal_supervisor_grace,
            "cancel_timeout": args.cancel_timeout,
            "poll_interval": args.poll_interval,
            "llm_budget_microdollars": args.llm_budget_microdollars,
        },
        "polls": [],
    }

    try:
        lab, origin = start_gate22()
        record["gate22_origin"] = origin
        print(f"GATE22_ORIGIN={origin}", flush=True)

        health = http_json(zest, "GET", "/health")
        record["health"] = health
        if health.get("ready_for_start") is not True:
            reason = "health.ready_for_start is not true"
            return 2

        bootstrap = unwrap(
            http_json(
                dashboard,
                "POST",
                "/api/programs/bootstrap",
                gate22_payload(origin, args.llm_budget_microdollars),
            )
        )
        record["bootstrap"] = bootstrap
        run_id = str(bootstrap.get("research_run_id") or "")
        if not run_id:
            raise ProbeError("bootstrap did not return research_run_id")
        record["run_id"] = run_id
        print(f"RUN_ID={run_id}", flush=True)
        rid = quote(run_id, safe="")

        preflight = unwrap(http_json(zest, "POST", f"/api/runs/{rid}/preflight", {}))
        record["preflight"] = preflight
        print(f"PREFLIGHT={preflight.get('status')}", flush=True)
        if preflight.get("status") != "READY_TO_START":
            reason = "preflight is not READY_TO_START"
            return 2

        start_response = unwrap(http_json(zest, "POST", f"/api/runs/{rid}/start", {}))
        record["start_response"] = start_response
        started = True
        print(f"START_STATE={start_response.get('state')}", flush=True)

        deadline = time.monotonic() + args.total_timeout
        last_progress = time.monotonic()
        last_fp: tuple[Any, ...] | None = None
        terminal_since: float | None = None
        final_detail: dict[str, Any] = {}

        while True:
            current_time = time.monotonic()
            if current_time >= deadline:
                timed_out = True
                reason = "total runtime timeout"
                break
            try:
                detail = unwrap(http_json(zest, "GET", f"/api/runs/{rid}"))
            except ProbeError as exc:
                if current_time - last_progress >= args.no_progress_timeout:
                    timed_out = True
                    reason = f"run API unavailable without progress: {exc}"
                    break
                time.sleep(args.poll_interval)
                continue

            final_detail = detail
            fp = progress(detail)
            if fp != last_fp:
                last_fp = fp
                last_progress = current_time
                print(
                    "POLL "
                    + " ".join(
                        f"{key}={detail.get(key)}"
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
                            "evidence_count",
                        )
                    ),
                    flush=True,
                )
            if len(record["polls"]) < 600:
                record["polls"].append(
                    {
                        "at": now(),
                        "fingerprint": list(fp),
                    }
                )

            state = str(detail.get("state") or "")
            if state in STOPLIKE:
                terminal_since = terminal_since or current_time
                if detail.get("locally_supervised") is False:
                    break
                if current_time - terminal_since >= args.terminal_supervisor_grace:
                    break
            else:
                terminal_since = None

            if current_time - last_progress >= args.no_progress_timeout:
                timed_out = True
                reason = "no-progress timeout"
                break
            time.sleep(args.poll_interval)

        record["final_detail_before_cleanup"] = final_detail
        try:
            analysis = unwrap(http_json(zest, "GET", f"/api/runs/{rid}/analysis"))
        except ProbeError as exc:
            analysis = {}
            record["analysis_error"] = str(exc)
        record["final_analysis"] = analysis
        hits = list(lab.hits)
        record["target_hits"] = hits

        needs_cleanup = timed_out or (
            str(final_detail.get("state") or "") in STOPLIKE
            and final_detail.get("locally_supervised") is True
        )
        if needs_cleanup:
            record["cleanup_cancel_attempted"] = True
            record["cleanup_cancel_verified"] = cancel_and_verify(
                zest, run_id, args.cancel_timeout, args.poll_interval
            )
            if not record["cleanup_cancel_verified"]:
                timed_out = True
                reason = reason + "; cleanup cancel verification failed"
            try:
                final_detail = unwrap(http_json(zest, "GET", f"/api/runs/{rid}"))
            except ProbeError:
                pass
        record["final_detail"] = final_detail

        layer_a, layer_b, diagnostics = evaluate(final_detail, analysis, hits, origin)
        record["layer_a"] = layer_a
        record["layer_b"] = layer_b
        record["diagnostics"] = diagnostics
        classification, class_reason = classify(
            final_detail,
            analysis,
            layer_a,
            layer_b,
            diagnostics,
            timed_out,
        )
        reason = f"{reason}; {class_reason}" if timed_out else class_reason

        print("\nLAYER_A=" + ("PASS" if layer_a["passed"] else "FAIL"), flush=True)
        for key, passed in layer_a["checks"].items():
            print(f"A.{key}={'PASS' if passed else 'FAIL'}", flush=True)
        print("LAYER_B=" + ("PASS" if layer_b["passed"] else "FAIL"), flush=True)
        print(f"B.research_lane={layer_b.get('research_lane')}", flush=True)
        for key, passed in layer_b["checks"].items():
            print(f"B.{key}={'PASS' if passed else 'FAIL'}", flush=True)
        if layer_b.get("capability_missing_reason_codes"):
            print(
                "B.capability_missing_reason_codes="
                + ",".join(layer_b["capability_missing_reason_codes"]),
                flush=True,
            )
        print(f"S4_CLASSIFICATION={classification}", flush=True)
        print(f"S4_REASON={reason}", flush=True)
        return 0 if classification == "SUCCESS" else 1

    except ProbeError as exc:
        reason = str(exc)
        classification = "COULD_NOT_START" if not started else "STUCK_OR_CRASHED"
        if started and run_id:
            record["exception_cleanup_cancel_attempted"] = True
            record["exception_cleanup_cancel_verified"] = cancel_and_verify(
                zest, run_id, args.cancel_timeout, args.poll_interval
            )
        return 2
    finally:
        if lab is not None:
            record["target_hits_final"] = list(lab.hits)
            try:
                lab.stop()
            except Exception:
                pass
        record["classification"] = classification
        record["reason"] = reason
        record["completed_at"] = now()
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(record, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "classification": classification,
                    "reason": reason,
                    "run_id": run_id,
                    "output": str(output),
                },
                indent=2,
            ),
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Drive one fresh Phase8 Gate22 START-to-terminal acceptance run"
    )
    parser.add_argument("--zest-url", default="http://127.0.0.1:8766")
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:8765")
    parser.add_argument("--llm-budget-microdollars", type=int, required=True)
    parser.add_argument("--total-timeout", type=float, default=900.0)
    parser.add_argument("--no-progress-timeout", type=float, default=360.0)
    parser.add_argument("--terminal-supervisor-grace", type=float, default=15.0)
    parser.add_argument("--cancel-timeout", type=float, default=45.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument(
        "--output",
        default="/tmp/zest-phase8-start-to-terminal-result.json",
    )
    args = parser.parse_args()
    if (
        min(
            args.total_timeout,
            args.no_progress_timeout,
            args.cancel_timeout,
            args.poll_interval,
        )
        <= 0
        or args.terminal_supervisor_grace < 0
    ):
        parser.error("timeouts must be positive; supervisor grace must be non-negative")
    try:
        return run(args)
    except ProbeError as exc:
        print(f"probe configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
