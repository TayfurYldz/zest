"""Bounded sanitized analytical projection for Zest HQ.

Projection only. PostgreSQL remains authoritative. This module creates no
research state, grants no authority, dispatches no Worker, and calls no Model.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from inspect import Parameter, signature
from typing import Any

from zest.application.operator_errors import OperatorError, OperatorErrorCode
from zest.application.observability import activity_from_fault, project_effective_run_state
from zest.application.observer import build_fallback_brief, build_observer_context
from zest.application.ports import UnitOfWorkFactory
from zest.application.retry_policy import RetryClassification, classify_retry_semantics
from zest.safe_data import redact_secret_keys


HQ_SCHEMA_VERSION = "hq.run.analysis.v1"
HQ_READ_MODEL_SCHEMA = "hq.run.read-model.v1"
MAX_ITEMS_PER_COLLECTION = 250
MAX_STRING_LENGTH = 8192
MAX_SERIALIZATION_DEPTH = 8
_LIMITED_READ_METHODS = frozenset(
    {
        "list_for_research_run",
        "list_for_correlation",
        "list_for_subject",
        "get_nodes",
        "get_edges",
    }
)


class _BoundedRepositoryProxy:
    def __init__(self, repository: Any) -> None:
        self._repository = repository

    def __getattr__(self, name: str) -> Any:
        method = getattr(self._repository, name)
        if name not in _LIMITED_READ_METHODS:
            return method

        def bounded_call(*args: Any, **kwargs: Any) -> Any:
            parameters = signature(method).parameters.values()
            accepts_limit = any(
                parameter.name == "limit"
                or parameter.kind is Parameter.VAR_KEYWORD
                for parameter in parameters
            )
            if accepts_limit:
                kwargs["limit"] = MAX_ITEMS_PER_COLLECTION
                return method(*args, **kwargs)
            # Keep lightweight test doubles and older read-only adapters bounded
            # without requiring them to implement the optional limit keyword.
            return list(method(*args, **kwargs))[:MAX_ITEMS_PER_COLLECTION]

        return bounded_call


class _BoundedUnitOfWorkProxy:
    def __init__(self, uow: Any) -> None:
        self._uow = uow

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._uow, name)
        if name in {"rollback", "commit"}:
            return value
        return _BoundedRepositoryProxy(value)


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_SERIALIZATION_DEPTH:
        return {"truncated": "max_depth"}

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    if isinstance(value, Enum):
        return _safe_value(value.value, depth=depth + 1)

    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "length": len(value),
            "raw_exposed": False,
        }

    if isinstance(value, str):
        if len(value) <= MAX_STRING_LENGTH:
            return value
        return value[:MAX_STRING_LENGTH] + "...[truncated]"

    if is_dataclass(value):
        return {
            field.name: _safe_value(
                getattr(value, field.name),
                depth=depth + 1,
            )
            for field in fields(value)
        }

    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda item: str(item[0]))
        return {
            str(key): _safe_value(item, depth=depth + 1)
            for key, item in items[:MAX_ITEMS_PER_COLLECTION]
        }

    if isinstance(value, (list, tuple, set, frozenset)):
        if isinstance(value, (set, frozenset)):
            value = sorted(
                value,
                key=lambda item: (type(item).__name__, repr(item)),
            )
        return [
            _safe_value(item, depth=depth + 1)
            for item in list(value)[:MAX_ITEMS_PER_COLLECTION]
        ]

    return {
        "type": type(value).__name__,
        "raw_exposed": False,
    }


def _bundle(records: Sequence[Any]) -> dict[str, Any]:
    total = len(records)
    visible = list(records[:MAX_ITEMS_PER_COLLECTION])
    return {
        "count": total,
        "shown": len(visible),
        "truncated": total > len(visible),
        "items": [_safe_value(item) for item in visible],
    }


def _record_id(record: Any) -> str | None:
    for name in (
        "fault_id",
        "attempt_id",
        "worker_result_id",
        "observation_id",
        "evidence_id",
        "assessment_id",
        "experiment_id",
        "hypothesis_id",
        "opportunity_id",
        "program_id",
        "selection_id",
        "cycle_id",
        "audit_event_id",
        "research_run_id",
    ):
        value = getattr(record, name, None)
        if value is not None:
            return str(value)
    return None


def _record_timestamp(record: Any) -> datetime | None:
    values = [
        getattr(record, name, None)
        for name in (
            "occurred_at",
            "received_at",
            "observed_at",
            "completed_at",
            "updated_at",
            "created_at",
            "started_at",
        )
    ]
    timestamps = [value for value in values if isinstance(value, datetime)]
    if not timestamps:
        return None
    return max(timestamps)


def _summary(record: Any, names: Sequence[str]) -> dict[str, Any]:
    return {
        name: _safe_value(getattr(record, name, None))
        for name in names
        if hasattr(record, name)
    }


def _reasoning_summary(record: Any) -> dict[str, Any]:
    return _summary(
        record,
        (
            "reasoning_record_id",
            "research_run_id",
            "hypothesis_id",
            "role",
            "adapter_identity",
            "provider_adapter_identity",
            "correlation_id",
            "context_fingerprint",
            "model_id",
            "model_version",
            "created_at",
        ),
    )


def _worker_result_summary(record: Any) -> dict[str, Any]:
    # Raw result and diagnostics are deliberately not dashboard data.
    return _summary(
        record,
        (
            "worker_result_id",
            "experiment_id",
            "research_run_id",
            "request_id",
            "correlation_id",
            "worker_capability",
            "action",
            "contract_version",
            "worker_id",
            "status",
            "received_at",
            "started_at",
            "completed_at",
            "parent_request_id",
            "control_signal",
        ),
    )


def _observation_summary(record: Any) -> dict[str, Any]:
    # Observation payloads may contain target data; the read model exposes only
    # provenance and kind, never the raw payload.
    return _summary(
        record,
        (
            "observation_id",
            "worker_result_id",
            "observation_kind",
            "normalization_version",
            "observed_at",
            "created_at",
        ),
    )


def _assessment_summary(record: Any) -> dict[str, Any]:
    # Rationale is a deterministic evaluator input, not operator-facing prose.
    return _summary(
        record,
        (
            "assessment_id",
            "hypothesis_id",
            "experiment_id",
            "research_run_id",
            "assessment_outcome",
            "observation_ids",
            "evaluator_kind",
            "evaluator_version",
            "evaluation_strategy",
            "created_at",
        ),
    )


def _evidence_summary(record: Any) -> dict[str, Any]:
    return _summary(
        record,
        (
            "evidence_id",
            "research_run_id",
            "hypothesis_id",
            "experiment_id",
            "admission_record_id",
            "polarity",
            "claim_scope",
            "observation_ids",
            "assessment_ids",
            "created_at",
        ),
    )


def _fault_summary(record: Any) -> dict[str, Any]:
    return _summary(
        record,
        (
            "fault_id",
            "research_run_id",
            "hypothesis_id",
            "experiment_id",
            "attempt_id",
            "request_id",
            "runtime_instance_id",
            "correlation_id",
            "component",
            "phase",
            "fault_class",
            "fault_code",
            "fatal",
            "occurred_at",
            "resolved_at",
            "diagnostic_summary",
        ),
    )


def _lineage_stage(
    kind: str,
    records: Sequence[Any],
    *,
    names: Sequence[str],
) -> dict[str, Any]:
    items = [_summary(record, names) for record in records[:MAX_ITEMS_PER_COLLECTION]]
    return {
        "kind": kind,
        "status": "PRESENT" if items else "UNKNOWN",
        "count": len(records),
        "items": items,
    }


def _lineage_target(target_reference: str | None) -> dict[str, Any]:
    if not target_reference:
        return {"kind": "target", "status": "UNKNOWN", "id": None}
    return {
        "kind": "target",
        "status": "PRESENT",
        "id": target_reference,
        "target_reference": target_reference,
    }


def _latest_record(records: Sequence[Any]) -> Any | None:
    return max(
        records,
        key=lambda item: (
            _record_timestamp(item) or datetime.min.replace(tzinfo=timezone.utc),
            _record_id(item) or "",
        ),
        default=None,
    )


def _research_intent_projection(
    *,
    hypotheses: Sequence[Any],
    experiments: Sequence[Any],
    plans: Sequence[Any],
    assessments: Sequence[Any],
    selections: Sequence[Any],
    opportunities: Sequence[Any],
    verifications: Sequence[Any],
) -> dict[str, Any]:
    """Expose persisted intent without inventing a rationale or next step."""

    hypothesis = _latest_record(hypotheses)
    selection = _latest_record(selections)
    opportunity_id = None if selection is None else getattr(selection, "opportunity_id", None)
    opportunity = next(
        (item for item in opportunities if getattr(item, "opportunity_id", None) == opportunity_id),
        None,
    )
    experiment = _latest_record(experiments)
    plan = next(
        (
            item for item in plans
            if experiment is not None
            and getattr(item, "experiment_id", None) == getattr(experiment, "experiment_id", None)
        ),
        None,
    )
    assessment = _latest_record(assessments)
    verification = _latest_record(verifications)
    source_refs = []
    if opportunity is not None:
        source_refs.extend(str(item) for item in getattr(opportunity, "source_refs", ())[:MAX_ITEMS_PER_COLLECTION])
    if not source_refs and hypothesis is not None and getattr(hypothesis, "origin_reference", None):
        source_refs.append(str(hypothesis.origin_reference))
    return {
        "status": "PRESENT" if any(item is not None for item in (hypothesis, experiment, plan, assessment)) else "UNKNOWN",
        "hypothesis": None if hypothesis is None else _summary(hypothesis, ("hypothesis_id", "research_run_id", "claim", "identity_id", "origin_reference", "created_at")),
        "hypothesis_state": "RECORDED" if hypothesis is not None else "UNKNOWN",
        "selection": None if selection is None else _summary(selection, ("selection_id", "opportunity_id", "outcome", "created_at")),
        "selection_reason_codes": [] if selection is None else _safe_value(getattr(selection, "reason_codes", ())),
        "source_references": source_refs,
        "experiment": None if experiment is None else _summary(experiment, ("experiment_id", "hypothesis_id", "execution_state", "created_at")),
        "expected_observation": "UNKNOWN" if plan is None else _safe_value(getattr(plan, "expected_observation", "UNKNOWN")),
        "disconfirming_observation": "UNKNOWN" if plan is None else _safe_value(getattr(plan, "disconfirming_observation", "UNKNOWN")),
        "experiment_objective": "UNKNOWN" if plan is None else _summary(plan, ("required_capability", "action", "evaluation_strategy")),
        "assessment": None if assessment is None else _assessment_summary(assessment),
        "verification": None if verification is None else _summary(verification, ("verification_id", "candidate_id", "outcome", "proposed_candidate_state", "created_at")),
        "next_direction": "UNKNOWN" if opportunity is None else _safe_value(getattr(opportunity, "proposed_direction", "UNKNOWN")),
    }


def _verification_projection(
    *,
    opportunities: Sequence[Any],
    hypotheses: Sequence[Any],
    experiments: Sequence[Any],
    observations: Sequence[Any],
    evidence: Sequence[Any],
    candidates: Sequence[Any],
    verifications: Sequence[Any],
    findings: Sequence[Any],
) -> dict[str, Any]:
    """Persisted chain only; Observer output never changes these states."""

    def stage(name: str, records: Sequence[Any], field_names: Sequence[str]) -> dict[str, Any]:
        latest = _latest_record(records)
        return {
            "stage": name,
            "status": "PRESENT" if latest is not None else "UNKNOWN",
            "record_id": _record_id(latest) if latest is not None else None,
            "record": None if latest is None else _summary(latest, field_names),
        }

    latest_candidate = _latest_record(candidates)
    latest_verification = _latest_record(verifications)
    latest_finding = _latest_record(findings)
    final_state = "UNKNOWN"
    final_record_id = None
    if latest_finding is not None:
        final_state, final_record_id = "CONFIRMED", _record_id(latest_finding)
    elif latest_candidate is not None and getattr(latest_candidate, "state", None) == "REJECTED":
        final_state, final_record_id = "FALSE_POSITIVE", _record_id(latest_candidate)
    elif latest_candidate is not None and getattr(latest_candidate, "state", None) == "INCONCLUSIVE":
        final_state, final_record_id = "INCONCLUSIVE", _record_id(latest_candidate)
    elif latest_verification is not None and getattr(latest_verification, "outcome", None) == "INCONCLUSIVE":
        final_state, final_record_id = "INCONCLUSIVE", _record_id(latest_verification)
    return {
        "stages": [
            stage("SIGNAL", opportunities, ("opportunity_id", "opportunity_kind", "source_refs", "created_at")),
            stage("HYPOTHESIS", hypotheses, ("hypothesis_id", "claim", "created_at")),
            stage("EXPERIMENT", experiments, ("experiment_id", "hypothesis_id", "execution_state", "created_at")),
            stage("OBSERVATION", observations, ("observation_id", "worker_result_id", "observation_kind", "observed_at")),
            stage("EVIDENCE", evidence, ("evidence_id", "hypothesis_id", "experiment_id", "polarity", "created_at")),
            stage("CANDIDATE", candidates, ("candidate_id", "hypothesis_id", "state", "created_at")),
            stage("VERIFICATION", verifications, ("verification_id", "candidate_id", "outcome", "created_at")),
            {"stage": "FINAL", "status": final_state, "record_id": final_record_id},
        ],
        "final_state": final_state,
        "authoritative": True,
        "observer_can_finalize": False,
    }


def _lineage_projection(
    *,
    program: Any,
    orchestration: Any,
    target_reference: str | None,
    opportunities: Sequence[Any],
    hypotheses: Sequence[Any],
    experiments: Sequence[Any],
    plans: Sequence[Any],
    attempts: Sequence[Any],
    worker_results: Sequence[Any],
    observations: Sequence[Any],
    evidence: Sequence[Any],
    assessments: Sequence[Any],
    target_inferences: Sequence[Any],
    attack_surface_snapshots: Sequence[Any],
) -> dict[str, Any]:
    surface_records = list(target_inferences) + list(attack_surface_snapshots)
    stages = {
        "program": _lineage_stage(
            "program", [program] if program is not None else [], names=("program_id", "name", "handle", "platform")
        ),
        "target": _lineage_target(target_reference),
        "surface": _lineage_stage(
            "surface",
            surface_records,
            names=("inference_id", "snapshot_id", "research_run_id", "kind", "epistemic_status", "opaque_ref", "target_identity", "captured_at"),
        ),
        "opportunity": _lineage_stage(
            "opportunity", opportunities, names=("opportunity_id", "research_run_id", "opportunity_kind", "mode", "structural_identity", "created_at")
        ),
        "hypothesis": _lineage_stage(
            "hypothesis", hypotheses, names=("hypothesis_id", "research_run_id", "claim", "identity_id", "created_at")
        ),
        "experiment": _lineage_stage(
            "experiment", experiments, names=("experiment_id", "research_run_id", "hypothesis_id", "budget_id", "execution_state", "created_at")
        ),
        "capability_action": _lineage_stage(
            "capability_action", plans, names=("experiment_id", "research_run_id", "required_capability", "capability_version", "action", "target_reference", "side_effect_level", "created_at")
        ),
        "execution_attempt": _lineage_stage(
            "execution_attempt", attempts, names=("attempt_id", "request_id", "experiment_id", "research_run_id", "correlation_id", "worker_capability", "action", "target_reference", "state", "target_contact_status", "created_at", "completed_at")
        ),
        "worker_result": _lineage_stage(
            "worker_result", worker_results, names=("worker_result_id", "request_id", "experiment_id", "research_run_id", "correlation_id", "status", "received_at", "completed_at")
        ),
        "observation": _lineage_stage(
            "observation", observations, names=("observation_id", "worker_result_id", "observation_kind", "observed_at", "created_at")
        ),
        "evidence": _lineage_stage(
            "evidence", evidence, names=("evidence_id", "research_run_id", "hypothesis_id", "experiment_id", "polarity", "claim_scope", "created_at")
        ),
        "assessment": _lineage_stage(
            "assessment", assessments, names=("assessment_id", "hypothesis_id", "experiment_id", "research_run_id", "assessment_outcome", "observation_ids", "created_at")
        ),
    }
    links: list[dict[str, Any]] = []
    for source_kind, source_records, target_kind, target_field in (
        ("experiment", experiments, "hypothesis", "hypothesis_id"),
        ("execution_attempt", attempts, "experiment", "experiment_id"),
        ("worker_result", worker_results, "experiment", "experiment_id"),
        ("observation", observations, "worker_result", "worker_result_id"),
        ("evidence", evidence, "hypothesis", "hypothesis_id"),
        ("assessment", assessments, "experiment", "experiment_id"),
    ):
        for record in source_records[:MAX_ITEMS_PER_COLLECTION]:
            source_id = _record_id(record)
            target_id = getattr(record, target_field, None)
            if source_id and target_id:
                links.append(
                    {
                        "source_kind": source_kind,
                        "source_id": source_id,
                        "target_kind": target_kind,
                        "target_id": str(target_id),
                        "relationship": "caused_by",
                    }
                )
    return {"stages": stages, "links": links[:MAX_ITEMS_PER_COLLECTION]}


def _activity(
    *,
    activity_id: str,
    occurred_at: datetime | None,
    plane: str,
    kind: str,
    summary: str,
    source_type: str,
    source_id: str | None,
    record: Any,
    importance: str = "INFO",
) -> dict[str, Any]:
    return {
        "activity_id": activity_id,
        "timestamp": _safe_value(occurred_at),
        "plane": plane,
        "event_type": kind,
        "kind": kind,
        "summary": summary,
        "importance": importance,
        "source_type": source_type,
        "source_id": source_id,
        "run_id": getattr(record, "research_run_id", None),
        "hypothesis_id": getattr(record, "hypothesis_id", None),
        "experiment_id": getattr(record, "experiment_id", None),
        "attempt_id": getattr(record, "attempt_id", None),
        "request_id": getattr(record, "request_id", None),
        "component": getattr(record, "component", None),
        "capability": getattr(record, "worker_capability", None) or getattr(record, "capability", None),
        "action": getattr(record, "action", None),
    }


def _semantic_timeline(
    *,
    faults: Sequence[Any],
    audits: Sequence[Any],
    attempts: Sequence[Any],
    worker_results: Sequence[Any],
    observations: Sequence[Any],
    assessments: Sequence[Any],
    evidence: Sequence[Any],
) -> list[dict[str, Any]]:
    activities: list[dict[str, Any]] = []
    for fault in faults:
        typed = activity_from_fault(fault)
        item = _activity(
            activity_id=typed.activity_id,
            occurred_at=typed.occurred_at,
            plane=typed.plane,
            kind=typed.kind,
            summary=typed.summary,
            source_type=typed.source_type,
            source_id=typed.source_id,
            record=fault,
            importance="HIGH" if fault.fatal else "MEDIUM",
        )
        activities.append(item)
    known = (
        (audits, "CONTROL", "AUDIT_EVENT", "audit event"),
        (attempts, "EXECUTION", "EXECUTION_ATTEMPT", "execution attempt"),
        (worker_results, "EXECUTION", "WORKER_RESULT", "worker result"),
        (observations, "EPISTEMIC", "OBSERVATION", "observation"),
        (assessments, "EPISTEMIC", "ASSESSMENT", "assessment"),
        (evidence, "EPISTEMIC", "EVIDENCE", "evidence"),
    )
    for records, plane, kind, label in known:
        for record in records:
            record_id = _record_id(record)
            timestamp = _record_timestamp(record)
            if record_id is None or timestamp is None:
                continue
            activities.append(
                _activity(
                    activity_id=f"activity:{kind.lower()}:{record_id}",
                    occurred_at=timestamp,
                    plane=plane,
                    kind=kind,
                    summary=f"{label} recorded",
                    source_type=kind.lower(),
                    source_id=record_id,
                    record=record,
                )
            )
    activities.sort(key=lambda item: (str(item.get("timestamp") or ""), str(item["activity_id"])))
    return activities[:MAX_ITEMS_PER_COLLECTION]


def _motor(
    name: str,
    *,
    availability: str = "UNKNOWN",
    activity: str = "IDLE",
    outcome: str = "NOT_REACHED",
    evidence_count: int = 0,
    reason: str | None = None,
) -> dict[str, Any]:
    result = {
        "name": name,
        "availability": availability,
        "activity": activity,
        "last_outcome": outcome,
        "evidence_count": evidence_count,
    }
    if reason is not None:
        result["reason"] = reason
    return result


def _lifecycle_activity(state: str | None) -> str:
    if state in {"RUNNING", "READY"}:
        return "ACTIVE"
    if state in {"WAITING_HUMAN", "BLOCKED", "BUDGET_EXHAUSTED"}:
        return "BLOCKED"
    if state in {"COMPLETED", "FAILED_OPERATIONAL", "CANCELLED"}:
        return "STOPPED"
    return "IDLE"


def _motor_projection(
    *,
    run: Any,
    orchestration: Any,
    runtime: Any,
    operational: Any,
    locally_supervised: bool,
    faults: Sequence[Any],
    reasoning: Sequence[Any],
    attempts: Sequence[Any],
    worker_results: Sequence[Any],
    observations: Sequence[Any],
    evidence: Sequence[Any],
    assessments: Sequence[Any],
    surface_records: Sequence[Any],
    oast_records: Sequence[Any],
    cycles: Sequence[Any],
) -> dict[str, dict[str, Any]]:
    state = None if orchestration is None else orchestration.state
    fatal_faults = [fault for fault in faults if fault.fatal and fault.resolved_at is None]
    failed = bool(fatal_faults)
    runtime_bad = operational.runtime_liveness in {"STALE", "STOPPED", "MISSING"}
    orchestrator_availability = "UNKNOWN"
    if orchestration is not None:
        orchestrator_availability = "DEAD" if runtime_bad and operational.runtime_liveness in {"STOPPED", "MISSING"} else "DEGRADED" if runtime_bad else "READY"
    outcome = "FAILED" if failed else "SUCCEEDED" if state in {"COMPLETED", "BUDGET_EXHAUSTED"} else "UNKNOWN"
    motors = {
        "core": _motor("Core", availability="READY" if run is not None else "UNKNOWN", activity=_lifecycle_activity(state), outcome=outcome, evidence_count=1 if run is not None else 0),
        "orchestrator_supervisor": _motor("Orchestrator / Supervisor", availability=orchestrator_availability, activity=_lifecycle_activity(state), outcome=outcome, evidence_count=int(orchestration is not None), reason="local supervision is not persisted" if not locally_supervised else None),
        "model_runtime": _motor("Model Runtime", activity="ACTIVE" if reasoning else "IDLE", outcome="SUCCEEDED" if reasoning else "NOT_REACHED", evidence_count=len(reasoning), reason="runtime readiness is not represented by run records"),
        "http_worker": _worker_motor("HTTP Worker", attempts, worker_results, faults, "http"),
        "browser_worker": _worker_motor("Browser Worker", attempts, worker_results, faults, "browser"),
        "surface_engine": _motor("Surface Engine", activity="ACTIVE" if surface_records else "IDLE", outcome="SUCCEEDED" if surface_records else "NOT_REACHED", evidence_count=len(surface_records)),
        "oast": _motor("OAST", activity="ACTIVE" if oast_records else "IDLE", outcome="SUCCEEDED" if oast_records else "NOT_REACHED", evidence_count=len(oast_records)),
        "evidence_pipeline": _motor("Evidence Pipeline", activity="ACTIVE" if assessments or evidence else "IDLE", outcome="SUCCEEDED" if evidence else "UNKNOWN" if assessments else "NOT_REACHED", evidence_count=len(assessments) + len(evidence)),
        "memory_learning": _motor("Memory / Learning", activity="ACTIVE" if cycles else "IDLE", outcome="SUCCEEDED" if cycles else "NOT_REACHED", evidence_count=len(cycles)),
        "observer": _motor("Observer", availability="READY", outcome="DETERMINISTIC", reason="read-only bounded narrator; no execution authority"),
    }
    return motors


def _worker_motor(name: str, attempts: Sequence[Any], results: Sequence[Any], faults: Sequence[Any], marker: str) -> dict[str, Any]:
    selected_attempts = [item for item in attempts if marker in str(getattr(item, "worker_capability", "")).lower()]
    selected_results = [item for item in results if marker in str(getattr(item, "worker_capability", "")).lower()]
    selected_faults = [item for item in faults if marker in str(getattr(item, "capability", "") or "").lower()]
    active = any(getattr(item, "state", None) in {"AUTHORIZED", "DISPATCHING"} for item in selected_attempts)
    outcome = "FAILED" if selected_faults else "SUCCEEDED" if selected_results else "NOT_REACHED" if not selected_attempts else "UNKNOWN"
    return _motor(name, activity="ACTIVE" if active else "STOPPED" if selected_attempts else "IDLE", outcome=outcome, evidence_count=len(selected_attempts) + len(selected_results), reason="worker readiness is not represented by run records")


def _failure_inspector(
    *,
    faults: Sequence[Any],
    attempts: Sequence[Any],
    worker_results: Sequence[Any],
    observations: Sequence[Any],
    evidence: Sequence[Any],
    assessments: Sequence[Any],
    audits: Sequence[Any],
    operational: Any,
    orchestration: Any,
) -> dict[str, Any] | None:
    relevant = [fault for fault in faults if fault.resolved_at is None]
    if not relevant:
        return None
    fault = sorted(relevant, key=lambda item: (_record_timestamp(item) or datetime.min.replace(tzinfo=timezone.utc), str(item.fault_id)))[-1]
    attempt = next((item for item in attempts if item.attempt_id == fault.attempt_id), None)
    result = next((item for item in worker_results if item.request_id == fault.request_id or item.correlation_id == fault.correlation_id), None)
    result_id = None if result is None else result.worker_result_id
    linked_observations = [item for item in observations if item.worker_result_id == result_id] if result_id else []
    linked_observation_ids = {item.observation_id for item in linked_observations}
    linked_evidence = [item for item in evidence if linked_observation_ids.intersection(set(getattr(item, "observation_ids", ())))]
    linked_assessments = [item for item in assessments if linked_observation_ids.intersection(set(getattr(item, "observation_ids", ()) ))]
    decision = next((item for item in reversed(audits) if getattr(item, "event_type", "") in {"EXECUTION_DECISION", "ATTEMPT_AUTHORIZED"}), None)
    retry = (
        classify_retry_semantics(attempt).to_mapping()
        if attempt is not None
        else {
            "classification": RetryClassification.HUMAN_DECISION_REQUIRED.value,
            "reason_code": "ATTEMPT_NOT_FOUND",
            "reason": "the related execution attempt is unavailable; require explicit human decision",
        }
    )
    return {
        "fault": _fault_summary(fault),
        "boundary": fault.component,
        "core_decision": None if decision is None else _summary(decision, ("audit_event_id", "event_type", "occurred_at", "subject_type", "subject_id")),
        "dispatch_committed": None if attempt is None else attempt.dispatch_started_at is not None,
        "invocation_started": None if result is None else result.started_at is not None,
        "worker_result_present": result is not None,
        "target_contact_status": None if attempt is None else attempt.target_contact_status,
        "response_present": None,
        "response_recorded": bool(linked_observations),
        "observation_present": bool(linked_observations),
        "evidence_present": bool(linked_evidence),
        "assessment_present": bool(linked_assessments),
        "persisted_state": None if orchestration is None else orchestration.state,
        "effective_state": operational.effective_state,
        "reason_codes": list(operational.reason_codes),
        "resolved": fault.resolved_at is not None,
        "unresolved": fault.resolved_at is None,
        "retry_classification": retry,
    }


def _state_consistency_warnings(
    *,
    orchestration: Any,
    runtime: Any,
    operational: Any,
    locally_supervised: bool,
    faults: Sequence[Any],
    attempts: Sequence[Any],
    worker_results: Sequence[Any],
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []

    def add(code: str, summary: str, detail: str) -> None:
        warnings.append({"code": code, "summary": summary, "detail": detail})

    if orchestration is not None and orchestration.state == "RUNNING" and operational.effective_state == "RUNTIME_FAULT":
        add("PERSISTED_RUNNING_EFFECTIVE_RUNTIME_FAULT", "Persisted RUNNING conflicts with effective runtime fault", "The persisted lifecycle row remains RUNNING while liveness or runtime fault evidence makes the effective state RUNTIME_FAULT.")
    if runtime is not None and getattr(runtime, "status", None) == "RUNNING" and not locally_supervised:
        add("RUNTIME_LIVE_SUPERVISION_UNKNOWN", "Runtime is alive but local supervision is not proven", "The runtime record is RUNNING, but this process has no authoritative local supervision claim for the selected run.")
    unknown_contact_failures = [item for item in attempts if getattr(item, "state", None) in {"FAILED", "TIMED_OUT", "UNKNOWN_OUTCOME"} and getattr(item, "target_contact_status", "UNKNOWN") == "UNKNOWN"]
    if unknown_contact_failures:
        add("FAILED_ATTEMPT_CONTACT_UNKNOWN", "Attempt failed before target contact was proven", f"{len(unknown_contact_failures)} failed or uncertain attempt(s) retain UNKNOWN target-contact status.")
    if any(getattr(item, "fatal", False) and getattr(item, "resolved_at", None) is None for item in faults):
        add("UNRESOLVED_FATAL_RUN_FAULT", "Unresolved fatal operational fault", "A fatal RunFault remains unresolved; no research conclusion is implied.")
    if operational.runtime_liveness == "STALE":
        add("STALE_RUNTIME_HEARTBEAT", "Runtime heartbeat is stale", "The persisted runtime heartbeat exceeded the liveness bound; operational state is not assumed healthy.")
    attempts_without_results = [item for item in attempts if getattr(item, "state", None) in {"FAILED", "TIMED_OUT", "UNKNOWN_OUTCOME"} and not any(getattr(result, "request_id", None) == getattr(item, "request_id", None) or getattr(result, "correlation_id", None) == getattr(item, "correlation_id", None) for result in worker_results)]
    if attempts_without_results:
        add("NO_WORKER_RESULT", "No WorkerResult is persisted for a failed attempt", "The missing WorkerResult remains UNKNOWN and is not converted into a negative execution result.")
    if orchestration is not None and operational.effective_state not in {"HEALTHY", "RUNNING"}:
        add("API_LIVE_RESEARCH_NOT_OPERATIONAL", "Operator API is live while research is not operational", f"The read model is available, but effective operational state is {operational.effective_state}.")
    return warnings


def _counter_projection(
    *,
    issued: Any,
    orchestration: Any,
    consumptions: Sequence[Any],
    attempts: Sequence[Any],
    experiments: Sequence[Any],
    cycles: Sequence[Any],
    observations: Sequence[Any],
    evidence: Sequence[Any],
    selections: Sequence[Any],
    run: Any,
    now: datetime,
) -> dict[str, Any]:
    amounts = {
        resource_type: sum(int(getattr(item, "amount", 0)) for item in consumptions if getattr(item, "resource_type", None) == resource_type)
        for resource_type in ("REQUEST", "WORKER_INVOCATION", "MODEL_CALL", "MODEL_TOKENS_IN", "MODEL_TOKENS_OUT", "MODEL_ESCALATION_DECISION", "EXECUTION_TIME")
    }
    confirmed = sum(1 for item in attempts if getattr(item, "target_contact_status", None) == "CONFIRMED")
    response_count = None if not observations else len(observations)
    elapsed = None
    if run is not None and isinstance(run.started_at, datetime):
        elapsed = max(int((now - run.started_at).total_seconds() * 1000), 0)
    return {
        "request_budget_reserved": None if issued is None else issued.max_requests,
        "network_request_attempted": amounts["REQUEST"],
        "target_contact_confirmed": confirmed,
        "response_received": response_count,
        "worker_invocations": amounts["WORKER_INVOCATION"],
        "model_calls": amounts["MODEL_CALL"],
        "model_tokens_in": amounts["MODEL_TOKENS_IN"],
        "model_tokens_out": amounts["MODEL_TOKENS_OUT"],
        "model_escalation_decisions": amounts["MODEL_ESCALATION_DECISION"],
        "experiments": len(experiments),
        "cycles": len(cycles),
        "observations": len(observations),
        "evidence_admitted": len(evidence),
        "selected_opportunities": sum(1 for item in selections if getattr(item, "outcome", None) == "SELECT"),
        "elapsed_ms": elapsed,
        "max_elapsed_ms": None if orchestration is None else orchestration.max_elapsed_ms,
        "remaining": {
            "requests": None if issued is None else max(issued.max_requests - amounts["REQUEST"], 0),
            "worker_invocations": None if issued is None else max(issued.max_tool_calls - amounts["WORKER_INVOCATION"], 0),
            "model_calls": None if orchestration is None else max(orchestration.max_model_calls - amounts["MODEL_CALL"], 0),
            "execution_time_ms": None if orchestration is None else max(orchestration.max_elapsed_ms - amounts["EXECUTION_TIME"], 0),
        },
        "unknown_semantics": ["response_received is UNKNOWN when no authoritative observation exists"],
    }


def _active_pipeline(
    *,
    hypotheses: Sequence[Any],
    experiments: Sequence[Any],
    attempts: Sequence[Any],
    worker_results: Sequence[Any],
    observations: Sequence[Any],
    assessments: Sequence[Any],
    faults: Sequence[Any],
    orchestration: Any,
) -> list[dict[str, Any]]:
    def stage(name: str, records: Sequence[Any], *, status: str | None = None) -> dict[str, Any]:
        record = records[-1] if records else None
        return {"stage": name, "status": status or ("PRESENT" if record is not None else "UNKNOWN"), "record_id": _record_id(record) if record is not None else None}

    return [
        stage("hypothesis", hypotheses),
        stage("experiment", experiments),
        stage("core_decision", [], status="PRESENT" if orchestration is not None else "UNKNOWN"),
        stage("dispatch", attempts),
        stage("worker_or_network_outcome", worker_results or faults),
        stage("observation", observations),
        stage("assessment", assessments),
        stage("next_state", [], status="PRESENT" if orchestration is not None else "UNKNOWN") | {"state": None if orchestration is None else orchestration.state},
    ]


def _truth_projection(
    *,
    run: Any,
    orchestration: Any,
    runtime: Any,
    operational: Any,
    faults: Sequence[Any],
    locally_supervised: bool,
    records: Sequence[Any],
) -> dict[str, Any]:
    unresolved_fatal = [
        fault for fault in faults if fault.fatal and fault.resolved_at is None
    ]
    latest_activity = max(
        (timestamp for timestamp in (_record_timestamp(record) for record in records) if timestamp is not None),
        default=None,
    )
    persisted_state = None if orchestration is None else orchestration.state
    supervisor_liveness = "ACTIVE" if locally_supervised else "UNKNOWN"
    if not locally_supervised and persisted_state in {"COMPLETED", "FAILED_OPERATIONAL", "CANCELLED"}:
        supervisor_liveness = "STOPPED"
    return {
        "research_run_id": run.research_run_id,
        "persisted_lifecycle_state": persisted_state or "UNKNOWN",
        "effective_operational_state": operational.effective_state,
        "effective_state_reason_codes": list(operational.reason_codes),
        "runtime_liveness": operational.runtime_liveness,
        "supervisor_liveness": supervisor_liveness,
        "runtime": None if runtime is None else _summary(runtime, ("runtime_instance_id", "status", "last_seen_at", "stopped_at")),
        "active_unresolved_fatal_fault": _fault_summary(unresolved_fatal[-1]) if unresolved_fatal else None,
        "current_phase": None if orchestration is None else orchestration.current_phase,
        "last_authoritative_activity_at": _safe_value(latest_activity),
        "human_attention_required": bool(unresolved_fatal or operational.effective_state == "RUNTIME_FAULT" or persisted_state == "WAITING_HUMAN"),
    }


def build_hq_run_analysis(
    uow_factory: UnitOfWorkFactory,
    research_run_id: str,
    *,
    locally_supervised: bool = False,
) -> dict[str, Any]:
    """Build one bounded, read-only analytical snapshot for an authorized run."""

    with uow_factory.open() as uow:
        uow = _BoundedUnitOfWorkProxy(uow)
        run = uow.research_runs.get(research_run_id)
        if run is None:
            uow.rollback()
            raise OperatorError(
                OperatorErrorCode.RUN_NOT_FOUND,
                "research run not found",
            )
        program = uow.programs.get(run.program_id)
        orchestration = uow.research_orchestrations.get(research_run_id)
        runtime = (
            None
            if orchestration is None or orchestration.owner_runtime_instance_id is None
            else uow.runtime_instances.get(orchestration.owner_runtime_instance_id)
        )
        run_faults = uow.run_faults.list_for_research_run(research_run_id)

        hypotheses = uow.hypotheses.list_for_research_run(research_run_id)
        experiments = uow.experiments.list_for_research_run(research_run_id)
        attempts = uow.execution_attempts.list_for_research_run(research_run_id)
        worker_results = uow.worker_results.list_for_research_run(research_run_id)
        observations = uow.observations.list_for_research_run(research_run_id)

        reasoning = uow.research_reasoning.list_for_research_run(research_run_id)
        research_admissions = uow.research_admissions.list_for_research_run(
            research_run_id
        )
        assessments = uow.hypothesis_assessments.list_for_research_run(
            research_run_id
        )

        evidence = uow.evidence.list_for_research_run(research_run_id)
        evidence_admissions = uow.evidence_admissions.list_for_research_run(
            research_run_id
        )
        candidates = uow.candidates.list_for_research_run(research_run_id)
        candidate_admissions = uow.candidate_admissions.list_for_research_run(
            research_run_id
        )
        promotion_runs = uow.promotion_runs.list_for_research_run(research_run_id)
        verifications = uow.verifications.list_for_research_run(research_run_id)
        proposals = uow.finding_proposals.list_for_research_run(research_run_id)
        findings = uow.findings.list_for_research_run(research_run_id)

        opportunities = uow.research_opportunities.list_for_research_run(
            research_run_id
        )
        selections = uow.research_selections.list_for_research_run(
            research_run_id
        )
        opportunity_candidates = (
            uow.opportunity_selection_candidates.list_for_research_run(
                research_run_id
            )
        )
        hunt_v3 = uow.hunt_v3_queue.list_for_research_run(research_run_id)

        sensor_observations = uow.sensor_observations.list_for_research_run(
            research_run_id
        )
        control_events = uow.control_events.list_for_research_run(research_run_id)
        discovery_facts = uow.discovery_facts.list_for_research_run(research_run_id)
        discovery_inferences = uow.discovery_inferences.list_for_research_run(
            research_run_id
        )
        frontier_items = uow.frontier_items.list_for_research_run(research_run_id)
        frontier_events = uow.frontier_events.list_for_research_run(research_run_id)
        snapshots = uow.snapshots.list_for_research_run(research_run_id)
        change_events = uow.change_events.list_for_research_run(research_run_id)
        attack_surface_snapshots = (
            uow.attack_surface_snapshots.list_for_research_run(research_run_id)
        )
        coverage = uow.coverage_debt_snapshots.list_for_research_run(
            research_run_id
        )
        target_inferences = uow.target_inferences.list_for_research_run(
            research_run_id
        )
        differentials = uow.differential_observations.list_for_research_run(
            research_run_id
        )
        invariants = uow.invariant_hypotheses.list_for_research_run(research_run_id)
        chain_hypotheses = uow.chain_hypotheses.list_for_research_run(
            research_run_id
        )
        impact_chains = uow.impact_chains.list_for_research_run(research_run_id)

        research_cycles = uow.research_cycles.list_for_research_run(research_run_id)
        budgets = uow.issued_budgets.list_for_research_run(research_run_id)
        budget_consumptions = uow.budget_consumptions.list_for_research_run(
            research_run_id
        )
        preflights = uow.preflight_reports.list_for_research_run(research_run_id)
        audits = uow.audit_events.list_for_subject("research_run", research_run_id)

        oast_correlations = uow.oast_correlations.list_for_research_run(
            research_run_id
        )
        oast_admissions = uow.oast_admissions.list_for_research_run(
            research_run_id
        )

        experiment_plans = []
        for experiment in experiments:
            plan = uow.experiment_plans.get(experiment.experiment_id)
            if plan is not None:
                experiment_plans.append(plan)

        oast_deliveries = []
        for correlation in oast_correlations:
            oast_deliveries.extend(
                uow.oast_callback_deliveries.list_for_correlation(
                    correlation.correlation_id
                )
            )

        reviews = []
        approvals = []
        for proposal in proposals:
            review = uow.human_reviews.get_for_proposal(proposal.proposal_id)
            if review is not None:
                reviews.append(review)
            approval = uow.approvals.get_by_subject(proposal.proposal_id)
            if approval is not None:
                approvals.append(approval)

        impact_rows = []
        for chain in impact_chains[:MAX_ITEMS_PER_COLLECTION]:
            nodes = uow.impact_chains.get_nodes(
                chain.chain_id,
                limit=MAX_ITEMS_PER_COLLECTION,
            )
            edges = uow.impact_chains.get_edges(
                chain.chain_id,
                limit=MAX_ITEMS_PER_COLLECTION,
            )
            impact_rows.append(
                {
                    "chain": _safe_value(chain),
                    "nodes": _safe_value(nodes),
                    "edges": _safe_value(edges),
                    "nodes_bounded": len(nodes) >= MAX_ITEMS_PER_COLLECTION,
                    "edges_bounded": len(edges) >= MAX_ITEMS_PER_COLLECTION,
                }
            )

        uow.rollback()

    browser_attempts = [
        item
        for item in attempts
        if "browser" in str(getattr(item, "worker_capability", "")).lower()
    ]
    operational = project_effective_run_state(
        orchestration,
        runtime,
        run_faults,
        now=datetime.now(timezone.utc),
    )
    issued = budgets[0] if budgets else None
    timeline = _semantic_timeline(
        faults=run_faults,
        audits=audits,
        attempts=attempts,
        worker_results=worker_results,
        observations=observations,
        assessments=assessments,
        evidence=evidence,
    )
    truth_records = [
        run,
        orchestration,
        runtime,
        *run_faults,
        *audits,
        *attempts,
        *worker_results,
        *observations,
        *assessments,
        *evidence,
        *research_cycles,
    ]
    target_reference = next(
        (
            getattr(record, "target_reference", None)
            for record in (orchestration, *experiment_plans, *attempts)
            if record is not None and getattr(record, "target_reference", None)
        ),
        None,
    )
    surface_records = [*target_inferences, *attack_surface_snapshots]
    oast_records = [*oast_correlations, *oast_admissions, *oast_deliveries]
    read_model_truth = _truth_projection(
        run=run,
        orchestration=orchestration,
        runtime=runtime,
        operational=operational,
        faults=run_faults,
        locally_supervised=locally_supervised,
        records=truth_records,
    )
    lineage = _lineage_projection(
        program=program,
        orchestration=orchestration,
        target_reference=target_reference,
        opportunities=opportunities,
        hypotheses=hypotheses,
        experiments=experiments,
        plans=experiment_plans,
        attempts=attempts,
        worker_results=worker_results,
        observations=observations,
        evidence=evidence,
        assessments=assessments,
        target_inferences=target_inferences,
        attack_surface_snapshots=attack_surface_snapshots,
    )
    motors = _motor_projection(
        run=run,
        orchestration=orchestration,
        runtime=runtime,
        operational=operational,
        locally_supervised=locally_supervised,
        faults=run_faults,
        reasoning=reasoning,
        attempts=attempts,
        worker_results=worker_results,
        observations=observations,
        evidence=evidence,
        assessments=assessments,
        surface_records=surface_records,
        oast_records=oast_records,
        cycles=research_cycles,
    )
    failure = _failure_inspector(
        faults=run_faults,
        attempts=attempts,
        worker_results=worker_results,
        observations=observations,
        evidence=evidence,
        assessments=assessments,
        audits=audits,
        operational=operational,
        orchestration=orchestration,
    )
    consistency_warnings = _state_consistency_warnings(
        orchestration=orchestration,
        runtime=runtime,
        operational=operational,
        locally_supervised=locally_supervised,
        faults=run_faults,
        attempts=attempts,
        worker_results=worker_results,
    )
    counters = _counter_projection(
        issued=issued,
        orchestration=orchestration,
        consumptions=budget_consumptions,
        attempts=attempts,
        experiments=experiments,
        cycles=research_cycles,
        observations=observations,
        evidence=evidence,
        selections=selections,
        run=run,
        now=datetime.now(timezone.utc),
    )

    payload = {
        "schema": HQ_SCHEMA_VERSION,
        "read_model_schema": HQ_READ_MODEL_SCHEMA,
        "research_run_id": research_run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "projection_only": True,
        "not_research_truth": True,
        "authority": {
            "postgresql_source_of_truth": True,
            "creates_state": False,
            "authorizes_execution": False,
            "dispatches_worker": False,
            "calls_model": False,
        },
        "operational": _safe_value(operational),
        "run_faults": _bundle(run_faults),
        "semantic_activities": _bundle(
            [activity_from_fault(fault) for fault in run_faults]
        ),
        "limits": {
            "max_items_per_collection": MAX_ITEMS_PER_COLLECTION,
            "max_nested_items": MAX_ITEMS_PER_COLLECTION,
            "max_string_length": MAX_STRING_LENGTH,
            "raw_bytes_exposed": False,
        },
        "truth": read_model_truth,
        "motors": motors,
        "current_research_lineage": lineage,
        "active_pipeline": _active_pipeline(
            hypotheses=hypotheses,
            experiments=experiments,
            attempts=attempts,
            worker_results=worker_results,
            observations=observations,
            assessments=assessments,
            faults=run_faults,
            orchestration=orchestration,
        ),
        "semantic_activity_timeline": {
            "count": len(timeline),
            "shown": len(timeline),
            "truncated": False,
            "items": timeline,
        },
        "failure_inspector": failure,
        "state_consistency_warnings": consistency_warnings,
        "research_intent": _research_intent_projection(
            hypotheses=hypotheses,
            experiments=experiments,
            plans=experiment_plans,
            assessments=assessments,
            selections=selections,
            opportunities=opportunities,
            verifications=verifications,
        ),
        "verification_chain": _verification_projection(
            opportunities=opportunities,
            hypotheses=hypotheses,
            experiments=experiments,
            observations=observations,
            evidence=evidence,
            candidates=candidates,
            verifications=verifications,
            findings=findings,
        ),
        "counters": counters,
        "engine_summary": {
            "hunter_opportunities": len(opportunities),
            "hunter_selections": len(selections),
            "model_reasoning_records": len(reasoning),
            "hypotheses": len(hypotheses),
            "experiments": len(experiments),
            "execution_attempts": len(attempts),
            "run_faults": len(run_faults),
            "browser_attempts": len(browser_attempts),
            "observations": len(observations),
            "evidence": len(evidence),
            "candidates": len(candidates),
            "verifications": len(verifications),
            "finding_proposals": len(proposals),
            "findings": len(findings),
            "surface_facts": len(discovery_facts),
            "surface_inferences": len(discovery_inferences),
            "oast_correlations": len(oast_correlations),
            "oast_deliveries": len(oast_deliveries),
        },
        "hunter": {
            "opportunities": _bundle(opportunities),
            "selections": _bundle(selections),
            "selection_candidates": _bundle(opportunity_candidates),
            "v3_queue": _bundle(hunt_v3),
        },
        "research": {
            "reasoning": _bundle([_reasoning_summary(item) for item in reasoning]),
            "admissions": _bundle(research_admissions),
            "hypotheses": _bundle(hypotheses),
            "assessments": _bundle([_assessment_summary(item) for item in assessments]),
            "cycles": _bundle(research_cycles),
        },
        "execution": {
            "experiments": _bundle(experiments),
            "plans": _bundle(experiment_plans),
            "attempts": _bundle(attempts),
            "worker_results": _bundle([_worker_result_summary(item) for item in worker_results]),
            "observations": _bundle([_observation_summary(item) for item in observations]),
            "budget_consumptions": _bundle(budget_consumptions),
            "preflights": _bundle(preflights),
        },
        "browser": {
            "attempts": _bundle(browser_attempts),
        },
        "oast": {
            "correlations": _bundle(oast_correlations),
            "deliveries": _bundle(oast_deliveries),
            "admissions": _bundle(oast_admissions),
        },
        "surface": {
            "sensor_observations": _bundle(sensor_observations),
            "control_events": _bundle(control_events),
            "facts": _bundle(discovery_facts),
            "inferences": _bundle(discovery_inferences),
            "frontier_items": _bundle(frontier_items),
            "frontier_events": _bundle(frontier_events),
            "snapshots": _bundle(snapshots),
            "change_events": _bundle(change_events),
            "attack_surface_snapshots": _bundle(attack_surface_snapshots),
            "coverage_debt": _bundle(coverage),
            "target_inferences": _bundle(target_inferences),
            "differentials": _bundle(differentials),
            "invariants": _bundle(invariants),
            "chain_hypotheses": _bundle(chain_hypotheses),
            "impact_chains": {
                "count": len(impact_chains),
                "shown": len(impact_rows),
                "truncated": len(impact_chains) > len(impact_rows),
                "items": impact_rows,
            },
        },
        "evidence_chain": {
            "evidence": _bundle([_evidence_summary(item) for item in evidence]),
            "evidence_admissions": _bundle(evidence_admissions),
            "candidates": _bundle(candidates),
            "candidate_admissions": _bundle(candidate_admissions),
            "promotion_runs": _bundle(promotion_runs),
            "verifications": _bundle(verifications),
            "finding_proposals": _bundle(proposals),
            "human_reviews": _bundle(reviews),
            "approvals": _bundle(approvals),
            "findings": _bundle(findings),
        },
        "audit": {
            "events": _bundle(audits),
        },
    }
    observer_context = build_observer_context(payload)
    payload["observer"] = {
        "mode": "DETERMINISTIC",
        "enabled": False,
        "provider": "none",
        "model": None,
        "fallback_reason": "DISABLED",
        "brief": build_fallback_brief(observer_context, reason="DISABLED"),
    }
    return redact_secret_keys(payload)
