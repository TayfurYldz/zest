"""Build deterministic executor replay manifests from persisted ledger rows."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from zest.application.errors import ApplicationError
from zest.application.ports import UnitOfWorkFactory
from zest.data.records import (
    ExecutionAttemptRecord,
    ObservationRecord,
    WorkerResultRecord,
)


REPLAY_MANIFEST_VERSION = "executor.replay_manifest.v1"
SENSITIVE_KEY_FRAGMENTS = (
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "csrf",
    "password",
    "secret",
    "session",
    "token",
)


@dataclass(frozen=True)
class BuildExecutorReplayManifestCommand:
    experiment_id: str


@dataclass(frozen=True)
class BuildExecutorReplayManifestResult:
    manifest: Mapping[str, Any]
    manifest_hash: str
    replay_class: str
    reason_codes: tuple[str, ...]


class BuildExecutorReplayManifest:
    """Summarize an executed experiment without redispatching a Worker."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(
        self, command: BuildExecutorReplayManifestCommand
    ) -> BuildExecutorReplayManifestResult:
        with self._uow_factory.open() as uow:
            experiment = uow.experiments.get(command.experiment_id)
            if experiment is None:
                raise ApplicationError("experiment not found")
            attempts = tuple(
                sorted(
                    uow.execution_attempts.list_for_experiment(command.experiment_id),
                    key=lambda item: (item.created_at, item.attempt_id),
                )
            )
            worker_results = {
                item.request_id: item
                for item in uow.worker_results.list_for_experiment(command.experiment_id)
            }
            observations_by_result: dict[str, list[ObservationRecord]] = {}
            for observation in uow.observations.list_for_experiment(command.experiment_id):
                observations_by_result.setdefault(observation.worker_result_id, []).append(
                    observation
                )
            uow.rollback()

        attempt_docs = []
        reason_codes: list[str] = []
        for attempt in attempts:
            result = worker_results.get(attempt.request_id)
            if result is None:
                reason_codes.append("WORKER_RESULT_MISSING")
            attempt_docs.append(
                _attempt_document(
                    attempt,
                    result,
                    tuple(
                        observations_by_result.get(
                            "" if result is None else result.worker_result_id, []
                        )
                    ),
                )
            )

        replay_class, class_reasons = _classify_replay(attempts, worker_results)
        reason_codes.extend(class_reasons)
        if not attempts:
            reason_codes.append("NO_EXECUTION_ATTEMPT")

        manifest = {
            "manifest_version": REPLAY_MANIFEST_VERSION,
            "experiment_id": command.experiment_id,
            "research_run_id": experiment.research_run_id,
            "hypothesis_id": experiment.hypothesis_id,
            "experiment_state": experiment.execution_state,
            "replay_class": replay_class,
            "reason_codes": tuple(dict.fromkeys(reason_codes)),
            "attempts": tuple(attempt_docs),
        }
        manifest_hash = _sha256_json(manifest)
        return BuildExecutorReplayManifestResult(
            manifest=manifest,
            manifest_hash=manifest_hash,
            replay_class=replay_class,
            reason_codes=tuple(manifest["reason_codes"]),
        )


def _attempt_document(
    attempt: ExecutionAttemptRecord,
    result: WorkerResultRecord | None,
    observations: tuple[ObservationRecord, ...],
) -> dict[str, Any]:
    return {
        "attempt_id": attempt.attempt_id,
        "request_id": attempt.request_id,
        "correlation_id": attempt.correlation_id,
        "worker_capability": attempt.worker_capability,
        "action": attempt.action,
        "target_reference": attempt.target_reference,
        "budget_id": attempt.budget_id,
        "side_effect_level": attempt.side_effect_level,
        "authorization_decision_reference": attempt.authorization_decision_reference,
        "attempt_state": attempt.state,
        "created_at": _timestamp(attempt.created_at),
        "authorized_at": _timestamp(attempt.authorized_at),
        "dispatch_started_at": _timestamp(attempt.dispatch_started_at),
        "completed_at": _timestamp(attempt.completed_at),
        "worker_result": None if result is None else _worker_result_document(result),
        "observations": tuple(_observation_document(item) for item in observations),
    }


def _worker_result_document(result: WorkerResultRecord) -> dict[str, Any]:
    raw_digest, raw_redactions = _redacted_digest(result.raw_result)
    diagnostics_digest, diagnostics_redactions = _redacted_digest(result.diagnostics)
    control_digest, control_redactions = _redacted_digest(result.control_signal)
    artifacts_digest, artifact_redactions = _redacted_digest(result.raw_artifact_descriptors)
    return {
        "worker_result_id": result.worker_result_id,
        "status": result.status,
        "contract_version": result.contract_version,
        "worker_id": result.worker_id,
        "received_at": _timestamp(result.received_at),
        "started_at": _timestamp(result.started_at),
        "completed_at": _timestamp(result.completed_at),
        "parent_request_id": result.parent_request_id,
        "raw_result_digest": raw_digest,
        "diagnostics_digest": diagnostics_digest,
        "control_signal_digest": control_digest,
        "artifact_descriptor_digest": artifacts_digest,
        "artifact_descriptor_count": (
            0
            if result.raw_artifact_descriptors is None
            else len(result.raw_artifact_descriptors)
        ),
        "redaction_count": (
            raw_redactions
            + diagnostics_redactions
            + control_redactions
            + artifact_redactions
        ),
    }


def _observation_document(observation: ObservationRecord) -> dict[str, Any]:
    payload_digest, redactions = _redacted_digest(observation.payload)
    return {
        "observation_id": observation.observation_id,
        "observation_kind": observation.observation_kind,
        "normalization_version": observation.normalization_version,
        "observed_at": _timestamp(observation.observed_at),
        "payload_digest": payload_digest,
        "redaction_count": redactions,
    }


def _classify_replay(
    attempts: tuple[ExecutionAttemptRecord, ...],
    worker_results: Mapping[str, WorkerResultRecord],
) -> tuple[str, tuple[str, ...]]:
    if not attempts:
        return "NOT_REPLAYABLE", ("NO_EXECUTION_ATTEMPT",)
    if any(attempt.side_effect_level > 1 for attempt in attempts):
        return "HUMAN_REVIEW_REQUIRED", ("SIDE_EFFECT_LEVEL_REQUIRES_APPROVAL",)
    if any(attempt.request_id not in worker_results for attempt in attempts):
        return "NOT_REPLAYABLE", ("WORKER_RESULT_MISSING",)
    if any(worker_results[attempt.request_id].status != "SUCCEEDED" for attempt in attempts):
        return "ENVIRONMENT_SENSITIVE", ("WORKER_RESULT_NOT_SUCCEEDED",)
    if any(attempt.worker_capability == "browser.page" for attempt in attempts):
        return "ENVIRONMENT_SENSITIVE", ("BROWSER_STATE_ENVIRONMENT_SENSITIVE",)
    if any(attempt.side_effect_level > 0 for attempt in attempts):
        return "ENVIRONMENT_SENSITIVE", ("STATEFUL_SIDE_EFFECT_ENVIRONMENT_SENSITIVE",)
    return "DETERMINISTIC_REPLAY", ("REPLAY_MANIFEST_READY",)


def _redacted_digest(value: Any) -> tuple[str | None, int]:
    if value is None:
        return None, 0
    redacted, count = _redact(value)
    return _sha256_json(redacted), count


def _redact(value: Any) -> tuple[Any, int]:
    if isinstance(value, Mapping):
        redactions = 0
        output: dict[str, Any] = {}
        for key in sorted(value.keys(), key=str):
            key_text = str(key)
            if _is_sensitive_key(key_text):
                output[key_text] = "<redacted>"
                redactions += 1
                continue
            child, child_redactions = _redact(value[key])
            output[key_text] = child
            redactions += child_redactions
        return output, redactions
    if isinstance(value, (list, tuple)):
        items = []
        redactions = 0
        for item in value:
            child, child_redactions = _redact(item)
            items.append(child)
            redactions += child_redactions
        return tuple(items), redactions
    return value, 0


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return _timestamp(value)
    return value


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
