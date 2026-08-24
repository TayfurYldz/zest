"""Operational checkpoint journal for benchmark ModelPort calls.

This module is execution durability only. It is not Evidence, Finding,
Candidate, PostgreSQL SoR truth, model selection, output repair, or retry logic.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from research_os.benchmark.errors import BenchmarkError
from research_os.benchmark.identity import (
    BenchmarkExperimentConfig,
    ModelConfigurationIdentity,
)
from research_os.benchmark.scenarios import BenchmarkScenario
from research_os.benchmark.suite import build_suite_manifest, scenario_integrity_hash
from research_os.research.model_port import (
    ContentPolicyBlockedError,
    ModelCallRequest,
    ModelCallResult,
    ModelCallTelemetry,
    ModelPort,
    ModelPortError,
    ModelRole,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderRuntimeError,
    ProviderTimeoutError,
    RuntimeProcessError,
    RuntimeUnavailableError,
    StructuredOutputTransportError,
)
from research_os.research.model_runtime import (
    AuthMode,
    ModelRuntimeIdentity,
    RuntimeClass,
    RuntimeKind,
)

CHECKPOINT_VERSION = "gate04b.model-call-journal.v1"
PLAN_FILE = "plan.json"
PAIRED_PLAN_FILE = "paired-plan.json"
PAUSE_FILE = "pause.json"
CALL_DIR = "calls"

_ERROR_CODE_TO_CLASS: dict[str, type[ModelPortError]] = {
    "CONTENT_POLICY_BLOCKED": ContentPolicyBlockedError,
    "PROVIDER_AUTH": ProviderAuthError,
    "PROVIDER_RATE_LIMIT": ProviderRateLimitError,
    "PROVIDER_RUNTIME": ProviderRuntimeError,
    "PROVIDER_TIMEOUT": ProviderTimeoutError,
    "RUNTIME_PROCESS_ERROR": RuntimeProcessError,
    "RUNTIME_UNAVAILABLE": RuntimeUnavailableError,
    "STRUCTURED_OUTPUT_FAILURE": StructuredOutputTransportError,
}

_ERROR_CLASS_TO_CODE: tuple[tuple[type[ModelPortError], str], ...] = (
    (StructuredOutputTransportError, "STRUCTURED_OUTPUT_FAILURE"),
    (ContentPolicyBlockedError, "CONTENT_POLICY_BLOCKED"),
    (ProviderAuthError, "PROVIDER_AUTH"),
    (ProviderRateLimitError, "PROVIDER_RATE_LIMIT"),
    (ProviderTimeoutError, "PROVIDER_TIMEOUT"),
    (RuntimeProcessError, "RUNTIME_PROCESS_ERROR"),
    (RuntimeUnavailableError, "RUNTIME_UNAVAILABLE"),
    (ProviderRuntimeError, "PROVIDER_RUNTIME"),
)

_ERROR_CODE_TO_MESSAGE: dict[str, str] = {
    "CONTENT_POLICY_BLOCKED": "content policy blocked",
    "PROVIDER_AUTH": "provider authentication failed",
    "PROVIDER_RATE_LIMIT": "provider rate limit",
    "PROVIDER_RUNTIME": "provider runtime error",
    "PROVIDER_TIMEOUT": "provider timeout",
    "RUNTIME_PROCESS_ERROR": "runtime process error",
    "RUNTIME_UNAVAILABLE": "runtime unavailable",
    "STRUCTURED_OUTPUT_FAILURE": "structured output failure",
}


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except PermissionError as exc:
        raise BenchmarkError(f"checkpoint directory permissions cannot be restricted: {path}") from exc


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"checkpoint state is corrupt or truncated: {path}") from exc
    if not isinstance(parsed, dict):
        raise BenchmarkError(f"checkpoint state is invalid: {path}")
    return parsed


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _mkdir_private(path.parent)
    encoded = json.dumps(dict(payload), sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


class BenchmarkOperationalPause(BenchmarkError):
    """Planned checkpoint pause before a new ModelPort call boundary."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = dict(payload)
        super().__init__("benchmark paused at ModelPort call boundary")


@dataclass
class ModelCallBoundaryBudget:
    """Counts only new provider invocations in this process."""

    max_new_model_calls: int
    new_model_calls: int = 0
    replayed_completed_calls: int = 0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_new_model_calls, int)
            or isinstance(self.max_new_model_calls, bool)
            or self.max_new_model_calls <= 0
        ):
            raise BenchmarkError("--max-new-model-calls must be a positive integer")

    def record_replay(self) -> None:
        self.replayed_completed_calls += 1

    def reserve_new_call_or_pause(
        self,
        *,
        pause_path: Path,
        slot: Mapping[str, Any],
        slot_id: str,
        call_identity: str,
        request_fingerprint: str,
    ) -> None:
        if self.new_model_calls < self.max_new_model_calls:
            self.new_model_calls += 1
            return
        payload = {
            "kind": "BenchmarkOperationalPause",
            "checkpoint_version": CHECKPOINT_VERSION,
            "status": "PAUSED_AT_BOUNDARY",
            "reason": "max_new_model_calls reached before writing next INTENT",
            "created_at": _now(),
            "max_new_model_calls": self.max_new_model_calls,
            "new_model_calls": self.new_model_calls,
            "replayed_completed_calls": self.replayed_completed_calls,
            "next_call": {
                "slot": dict(slot),
                "slot_id": slot_id,
                "call_identity": call_identity,
                "request_fingerprint": request_fingerprint,
            },
            "not_evidence": True,
            "not_finding": True,
            "not_candidate": True,
            "not_sor_truth": True,
            "not_gate_pass": True,
            "not_benchmark_failure": True,
        }
        _atomic_write_json(pause_path, payload)
        raise BenchmarkOperationalPause(payload)


def request_fingerprint(request: ModelCallRequest) -> str:
    return _sha256(
        {
            "role": request.role.value,
            "correlation_id": request.correlation_id,
            "context_fingerprint": request.context_fingerprint,
            "instructions": request.instructions,
            "payload": dict(request.payload),
            "model_id_hint": request.model_id_hint,
            "timeout_ms": request.timeout_ms,
        }
    )


def experiment_plan_material(
    scenarios: tuple[BenchmarkScenario, ...],
    *,
    config: BenchmarkExperimentConfig,
    model_identity: ModelConfigurationIdentity,
    git_commit: str,
    suite_version: str = "1",
    paired_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    suite = build_suite_manifest(
        scenarios, suite_id=config.suite_id, suite_version=suite_version
    )
    material: dict[str, Any] = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "not_evidence": True,
        "not_finding": True,
        "not_candidate": True,
        "not_sor_truth": True,
        "no_retry_or_repair": True,
        "git_commit": git_commit or "unknown",
        "suite": suite.to_mapping(),
        "scenario_order": [
            {
                "identity": scenario.identity,
                "scenario_id": scenario.scenario_id,
                "version": scenario.version,
                "integrity": scenario_integrity_hash(scenario),
            }
            for scenario in scenarios
        ],
        "config": config.to_mapping(),
        "model_configuration": model_identity.to_mapping(),
    }
    if paired_context is not None:
        material["paired_context"] = dict(paired_context)
    return material


def paired_experiment_plan_material(
    scenarios: tuple[BenchmarkScenario, ...],
    *,
    config: BenchmarkExperimentConfig,
    model_identities: tuple[ModelConfigurationIdentity, ModelConfigurationIdentity],
    git_commit: str,
    suite_version: str = "1",
) -> dict[str, Any]:
    suite = build_suite_manifest(
        scenarios, suite_id=config.suite_id, suite_version=suite_version
    )
    return {
        "checkpoint_version": CHECKPOINT_VERSION,
        "kind": "BenchmarkPairedExperimentCheckpointPlan",
        "not_evidence": True,
        "not_finding": True,
        "not_candidate": True,
        "not_sor_truth": True,
        "no_retry_or_repair": True,
        "no_automatic_winner": True,
        "readiness_probes_are_operational_not_results": True,
        "git_commit": git_commit or "unknown",
        "suite": suite.to_mapping(),
        "scenario_order": [
            {
                "identity": scenario.identity,
                "scenario_id": scenario.scenario_id,
                "version": scenario.version,
                "integrity": scenario_integrity_hash(scenario),
            }
            for scenario in scenarios
        ],
        "config": config.to_mapping(),
        "ordered_model_configurations": [
            identity.to_mapping() for identity in model_identities
        ],
    }


@dataclass(frozen=True)
class ExperimentPlanFingerprint:
    material: dict[str, Any]
    fingerprint: str


def build_experiment_plan_fingerprint(
    scenarios: tuple[BenchmarkScenario, ...],
    *,
    config: BenchmarkExperimentConfig,
    model_identity: ModelConfigurationIdentity,
    git_commit: str,
    suite_version: str = "1",
    paired_context: Mapping[str, Any] | None = None,
) -> ExperimentPlanFingerprint:
    material = experiment_plan_material(
        scenarios,
        config=config,
        model_identity=model_identity,
        git_commit=git_commit,
        suite_version=suite_version,
        paired_context=paired_context,
    )
    return ExperimentPlanFingerprint(material=material, fingerprint=_sha256(material))


def build_paired_experiment_plan_fingerprint(
    scenarios: tuple[BenchmarkScenario, ...],
    *,
    config: BenchmarkExperimentConfig,
    model_identities: tuple[ModelConfigurationIdentity, ModelConfigurationIdentity],
    git_commit: str,
    suite_version: str = "1",
) -> ExperimentPlanFingerprint:
    material = paired_experiment_plan_material(
        scenarios,
        config=config,
        model_identities=model_identities,
        git_commit=git_commit,
        suite_version=suite_version,
    )
    return ExperimentPlanFingerprint(material=material, fingerprint=_sha256(material))


class BenchmarkCheckpointSession:
    """Root checkpoint directory. One subdirectory is created per plan fingerprint."""

    def __init__(
        self,
        root: Path,
        *,
        resume: bool,
        max_new_model_calls: int | None = None,
    ) -> None:
        self.root = root
        self.resume = resume
        self.boundary_budget = (
            None
            if max_new_model_calls is None
            else ModelCallBoundaryBudget(max_new_model_calls=max_new_model_calls)
        )
        if resume:
            if not root.is_dir():
                raise BenchmarkError(f"checkpoint resume directory does not exist: {root}")
            _mkdir_private(root)
        else:
            if root.exists() and any(root.iterdir()):
                raise BenchmarkError(f"refusing to overwrite unrelated checkpoint directory: {root}")
            _mkdir_private(root)

    def open_plan(
        self,
        scenarios: tuple[BenchmarkScenario, ...],
        *,
        config: BenchmarkExperimentConfig,
        model_identity: ModelConfigurationIdentity,
        git_commit: str,
        suite_version: str = "1",
    ) -> "BenchmarkPlanCheckpoint":
        plan = build_experiment_plan_fingerprint(
            scenarios,
            config=config,
            model_identity=model_identity,
            git_commit=git_commit,
            suite_version=suite_version,
        )
        plan_dir = self.root / "plans" / plan.fingerprint
        calls_dir = plan_dir / CALL_DIR
        plan_file = plan_dir / PLAN_FILE
        if plan_file.exists():
            stored = _read_json(plan_file)
            if (
                stored.get("fingerprint") != plan.fingerprint
                or stored.get("material") != plan.material
            ):
                raise BenchmarkError("checkpoint experiment plan fingerprint mismatch")
        else:
            if self.resume:
                raise BenchmarkError("checkpoint experiment plan not found for resume")
            _atomic_write_json(
                plan_file,
                {
                    "kind": "BenchmarkExperimentCheckpointPlan",
                    "fingerprint": plan.fingerprint,
                    "created_at": _now(),
                    "material": plan.material,
                },
            )
        _mkdir_private(calls_dir)
        return BenchmarkPlanCheckpoint(
            plan_dir=plan_dir,
            plan=plan,
            boundary_budget=self.boundary_budget,
            pause_path=self.root / PAUSE_FILE,
        )

    def open_paired_plan(
        self,
        scenarios: tuple[BenchmarkScenario, ...],
        *,
        config: BenchmarkExperimentConfig,
        model_identities: tuple[ModelConfigurationIdentity, ModelConfigurationIdentity],
        git_commit: str,
        suite_version: str = "1",
    ) -> "BenchmarkPairedCheckpoint":
        plan = build_paired_experiment_plan_fingerprint(
            scenarios,
            config=config,
            model_identities=model_identities,
            git_commit=git_commit,
            suite_version=suite_version,
        )
        paired_dir = self.root / "paired-plans" / plan.fingerprint
        plan_file = paired_dir / PAIRED_PLAN_FILE
        allow_create_missing_resume_stage = False
        if plan_file.exists():
            stored = _read_json(plan_file)
            if (
                stored.get("fingerprint") != plan.fingerprint
                or stored.get("material") != plan.material
            ):
                raise BenchmarkError("checkpoint paired experiment plan fingerprint mismatch")
        else:
            if self.resume:
                allow_create_missing_resume_stage = self._resume_may_create_missing_paired_stage(
                    plan.material
                )
                if not allow_create_missing_resume_stage:
                    raise BenchmarkError("checkpoint paired experiment plan not found for resume")
            _atomic_write_json(
                plan_file,
                {
                    "kind": "BenchmarkPairedExperimentCheckpointPlan",
                    "fingerprint": plan.fingerprint,
                    "created_at": _now(),
                    "material": plan.material,
                },
            )
        _mkdir_private(paired_dir / "models")
        return BenchmarkPairedCheckpoint(
            paired_dir=paired_dir,
            plan=plan,
            scenarios=scenarios,
            config=config,
            model_identities=model_identities,
            git_commit=git_commit,
            suite_version=suite_version,
            resume=self.resume,
            boundary_budget=self.boundary_budget,
            pause_path=self.root / PAUSE_FILE,
        )

    def _resume_may_create_missing_paired_stage(self, material: Mapping[str, Any]) -> bool:
        paired_root = self.root / "paired-plans"
        if not paired_root.is_dir():
            return False
        current_config = material.get("config")
        if not isinstance(current_config, Mapping):
            return False
        current_stage = (
            current_config.get("suite_id"),
            current_config.get("harness_version"),
        )
        saw_existing = False
        for path in paired_root.glob(f"*/{PAIRED_PLAN_FILE}"):
            stored = _read_json(path)
            stored_material = stored.get("material")
            if not isinstance(stored_material, Mapping):
                raise BenchmarkError("checkpoint paired experiment plan material is invalid")
            stored_config = stored_material.get("config")
            if not isinstance(stored_config, Mapping):
                raise BenchmarkError("checkpoint paired experiment plan config is invalid")
            saw_existing = True
            stored_stage = (
                stored_config.get("suite_id"),
                stored_config.get("harness_version"),
            )
            if stored_stage == current_stage:
                raise BenchmarkError("checkpoint paired experiment plan fingerprint mismatch")
        return saw_existing


@dataclass(frozen=True)
class BenchmarkPairedCheckpoint:
    paired_dir: Path
    plan: ExperimentPlanFingerprint
    scenarios: tuple[BenchmarkScenario, ...]
    config: BenchmarkExperimentConfig
    model_identities: tuple[ModelConfigurationIdentity, ModelConfigurationIdentity]
    git_commit: str
    suite_version: str = "1"
    resume: bool = False
    boundary_budget: ModelCallBoundaryBudget | None = None
    pause_path: Path | None = None

    def open_model_plan(
        self,
        *,
        model_index: int,
    ) -> "BenchmarkPlanCheckpoint":
        if model_index not in (0, 1):
            raise BenchmarkError("paired checkpoint model_index must be 0 or 1")
        identity = self.model_identities[model_index]
        paired_context = {
            "paired_plan_fingerprint": self.plan.fingerprint,
            "ordered_model_index": model_index,
            "ordered_model_configurations": [
                item.to_mapping() for item in self.model_identities
            ],
        }
        plan = build_experiment_plan_fingerprint(
            self.scenarios,
            config=self.config,
            model_identity=identity,
            git_commit=self.git_commit,
            suite_version=self.suite_version,
            paired_context=paired_context,
        )
        plan_dir = self.paired_dir / "models" / str(model_index) / plan.fingerprint
        calls_dir = plan_dir / CALL_DIR
        plan_file = plan_dir / PLAN_FILE
        if plan_file.exists():
            stored = _read_json(plan_file)
            if (
                stored.get("fingerprint") != plan.fingerprint
                or stored.get("material") != plan.material
            ):
                raise BenchmarkError("checkpoint paired model plan fingerprint mismatch")
        else:
            _atomic_write_json(
                plan_file,
                {
                    "kind": "BenchmarkExperimentCheckpointPlan",
                    "fingerprint": plan.fingerprint,
                    "created_at": _now(),
                    "material": plan.material,
                },
            )
        _mkdir_private(calls_dir)
        return BenchmarkPlanCheckpoint(
            plan_dir=plan_dir,
            plan=plan,
            boundary_budget=self.boundary_budget,
            pause_path=self.pause_path,
        )


@dataclass(frozen=True)
class BenchmarkPlanCheckpoint:
    plan_dir: Path
    plan: ExperimentPlanFingerprint
    boundary_budget: ModelCallBoundaryBudget | None = None
    pause_path: Path | None = None

    def wrap_model(
        self,
        model: ModelPort,
        *,
        scenario_identity: str,
        run_index: int,
    ) -> ModelPort:
        return CheckpointedModelPort(
            inner=model,
            checkpoint=self,
            scenario_identity=scenario_identity,
            run_index=run_index,
        )

    def complete_or_call(
        self,
        request: ModelCallRequest,
        *,
        scenario_identity: str,
        run_index: int,
        invoke,
    ) -> ModelCallResult:
        slot = {
            "plan_fingerprint": self.plan.fingerprint,
            "scenario_identity": scenario_identity,
            "run_index": run_index,
            "role": request.role.value,
        }
        slot_id = _sha256(slot)
        req_fp = request_fingerprint(request)
        call_identity = _sha256({**slot, "request_fingerprint": req_fp})
        path = self.plan_dir / CALL_DIR / f"{slot_id}.json"
        if path.exists():
            record = _read_json(path)
            _validate_record(
                record,
                slot=slot,
                request_fingerprint=req_fp,
                call_identity=call_identity,
            )
            status = record.get("status")
            if status in {"COMPLETED", "COMPLETED_SUCCESS"}:
                if self.boundary_budget is not None:
                    self.boundary_budget.record_replay()
                return _result_from_record(record)
            if status == "COMPLETED_ERROR":
                if self.boundary_budget is not None:
                    self.boundary_budget.record_replay()
                raise _error_from_record(record)
            if status == "INTENT":
                raise BenchmarkError(
                    "checkpoint call is UNKNOWN_OUTCOME; automatic retry is refused"
                )
            raise BenchmarkError(f"checkpoint call has invalid status: {status!r}")
        if self.boundary_budget is not None:
            if self.pause_path is None:
                raise BenchmarkError("checkpoint pause path is not configured")
            self.boundary_budget.reserve_new_call_or_pause(
                pause_path=self.pause_path,
                slot=slot,
                slot_id=slot_id,
                call_identity=call_identity,
                request_fingerprint=req_fp,
            )
        _atomic_write_json(
            path,
            {
                "kind": "BenchmarkModelCallCheckpoint",
                "checkpoint_version": CHECKPOINT_VERSION,
                "status": "INTENT",
                "created_at": _now(),
                "updated_at": _now(),
                "slot": slot,
                "slot_id": slot_id,
                "call_identity": call_identity,
                "request_fingerprint": req_fp,
                "not_evidence": True,
                "not_finding": True,
                "not_candidate": True,
                "not_sor_truth": True,
            },
        )
        try:
            result = invoke(request)
        except ModelPortError as exc:
            completed_error = _completed_error_record(
                exc,
                slot=slot,
                slot_id=slot_id,
                request_fingerprint=req_fp,
                call_identity=call_identity,
            )
            existing = _read_json(path)
            if existing.get("status") != "INTENT":
                raise BenchmarkError("checkpoint error record would overwrite immutable state")
            _validate_record(
                existing,
                slot=slot,
                request_fingerprint=req_fp,
                call_identity=call_identity,
            )
            _atomic_write_json(path, completed_error)
            raise _error_from_record(completed_error) from None
        completed = _completed_success_record(
            result,
            slot=slot,
            slot_id=slot_id,
            request_fingerprint=req_fp,
            call_identity=call_identity,
        )
        existing = _read_json(path)
        if existing.get("status") != "INTENT":
            raise BenchmarkError("checkpoint completed record would overwrite immutable state")
        _validate_record(
            existing,
            slot=slot,
            request_fingerprint=req_fp,
            call_identity=call_identity,
        )
        _atomic_write_json(path, completed)
        return result


@dataclass
class CheckpointedModelPort:
    inner: ModelPort
    checkpoint: BenchmarkPlanCheckpoint
    scenario_identity: str
    run_index: int

    def complete(self, request: ModelCallRequest) -> ModelCallResult:
        return self.checkpoint.complete_or_call(
            request,
            scenario_identity=self.scenario_identity,
            run_index=self.run_index,
            invoke=self.inner.complete,
        )


def _validate_record(
    record: Mapping[str, Any],
    *,
    slot: Mapping[str, Any],
    request_fingerprint: str,
    call_identity: str,
) -> None:
    if record.get("kind") != "BenchmarkModelCallCheckpoint":
        raise BenchmarkError("checkpoint call record has invalid kind")
    if record.get("checkpoint_version") != CHECKPOINT_VERSION:
        raise BenchmarkError("checkpoint call record version mismatch")
    if record.get("slot") != dict(slot):
        raise BenchmarkError("checkpoint call slot mismatch")
    if record.get("request_fingerprint") != request_fingerprint:
        raise BenchmarkError("checkpoint request fingerprint mismatch")
    if record.get("call_identity") != call_identity:
        raise BenchmarkError("checkpoint call identity mismatch")


def _telemetry_mapping(telemetry: ModelCallTelemetry | None) -> dict[str, Any] | None:
    if telemetry is None:
        return None
    return {
        "latency_ms": telemetry.latency_ms,
        "input_tokens": telemetry.input_tokens,
        "output_tokens": telemetry.output_tokens,
        "retries": telemetry.retries,
        "provider_reported_cost": telemetry.provider_reported_cost,
        "provider_cost_provenance": telemetry.provider_cost_provenance,
    }


def _runtime_identity_mapping(identity: ModelRuntimeIdentity | None) -> dict[str, Any] | None:
    if identity is None:
        return None
    return identity.to_mapping()


def _completed_success_record(
    result: ModelCallResult,
    *,
    slot: Mapping[str, Any],
    slot_id: str,
    request_fingerprint: str,
    call_identity: str,
) -> dict[str, Any]:
    return {
        "kind": "BenchmarkModelCallCheckpoint",
        "checkpoint_version": CHECKPOINT_VERSION,
        "status": "COMPLETED_SUCCESS",
        "created_at": _now(),
        "updated_at": _now(),
        "slot": dict(slot),
        "slot_id": slot_id,
        "call_identity": call_identity,
        "request_fingerprint": request_fingerprint,
        "not_evidence": True,
        "not_finding": True,
        "not_candidate": True,
        "not_sor_truth": True,
        "result": {
            "role": result.role.value,
            "adapter_identity": result.adapter_identity,
            "provider_adapter_identity": result.provider_adapter_identity,
            "structured_output": dict(result.structured_output),
            "model_id": result.model_id,
            "model_version": result.model_version,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "telemetry": _telemetry_mapping(result.telemetry),
            "runtime_identity": _runtime_identity_mapping(result.runtime_identity),
        },
    }


def _completed_error_record(
    exc: ModelPortError,
    *,
    slot: Mapping[str, Any],
    slot_id: str,
    request_fingerprint: str,
    call_identity: str,
) -> dict[str, Any]:
    code = _allowlisted_error_code(exc)
    if code is None:
        raise exc
    return {
        "kind": "BenchmarkModelCallCheckpoint",
        "checkpoint_version": CHECKPOINT_VERSION,
        "status": "COMPLETED_ERROR",
        "created_at": _now(),
        "updated_at": _now(),
        "slot": dict(slot),
        "slot_id": slot_id,
        "call_identity": call_identity,
        "request_fingerprint": request_fingerprint,
        "not_evidence": True,
        "not_finding": True,
        "not_candidate": True,
        "not_sor_truth": True,
        "error": {
            "code": code,
            "classification": code,
            "message": _ERROR_CODE_TO_MESSAGE[code],
            "sanitized": True,
        },
    }


def _result_from_record(record: Mapping[str, Any]) -> ModelCallResult:
    result = record.get("result")
    if not isinstance(result, Mapping):
        raise BenchmarkError("checkpoint completed record is missing result")
    try:
        role = ModelRole(str(result["role"]))
        telemetry = _telemetry_from_mapping(result.get("telemetry"))
        runtime_identity = _runtime_identity_from_mapping(result.get("runtime_identity"))
        structured = result["structured_output"]
    except (KeyError, TypeError, ValueError) as exc:
        raise BenchmarkError("checkpoint completed result is invalid") from exc
    if not isinstance(structured, Mapping):
        raise BenchmarkError("checkpoint completed structured output is invalid")
    return ModelCallResult(
        role=role,
        adapter_identity=str(result["adapter_identity"]),
        provider_adapter_identity=str(result["provider_adapter_identity"]),
        structured_output=dict(structured),
        model_id=_optional_str(result.get("model_id")),
        model_version=_optional_str(result.get("model_version")),
        prompt_tokens=_optional_int(result.get("prompt_tokens")),
        completion_tokens=_optional_int(result.get("completion_tokens")),
        telemetry=telemetry,
        runtime_identity=runtime_identity,
    )


def _error_from_record(record: Mapping[str, Any]) -> ModelPortError:
    error = record.get("error")
    if not isinstance(error, Mapping):
        raise BenchmarkError("checkpoint completed error record is missing error")
    if set(error) != {"code", "classification", "message", "sanitized"}:
        raise BenchmarkError("checkpoint completed error record is invalid")
    try:
        code = str(error["code"])
        classification = str(error["classification"])
        message = str(error["message"])
        sanitized = error["sanitized"]
    except (KeyError, TypeError, ValueError) as exc:
        raise BenchmarkError("checkpoint completed error record is invalid") from exc
    if code != classification or code not in _ERROR_CODE_TO_CLASS:
        raise BenchmarkError("checkpoint completed error classification is invalid")
    if message != _ERROR_CODE_TO_MESSAGE[code] or sanitized is not True:
        raise BenchmarkError("checkpoint completed error replay details are invalid")
    return _ERROR_CODE_TO_CLASS[code](message)


def _allowlisted_error_code(exc: ModelPortError) -> str | None:
    for cls, code in _ERROR_CLASS_TO_CODE:
        if isinstance(exc, cls):
            return code
    return None


def _telemetry_from_mapping(value: object) -> ModelCallTelemetry | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise BenchmarkError("checkpoint telemetry is invalid")
    return ModelCallTelemetry(
        latency_ms=_optional_int(value.get("latency_ms")),
        input_tokens=_optional_int(value.get("input_tokens")),
        output_tokens=_optional_int(value.get("output_tokens")),
        retries=_optional_int(value.get("retries")),
        provider_reported_cost=_optional_float(value.get("provider_reported_cost")),
        provider_cost_provenance=_optional_str(value.get("provider_cost_provenance")),
    )


def _runtime_identity_from_mapping(value: object) -> ModelRuntimeIdentity | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise BenchmarkError("checkpoint runtime identity is invalid")
    return ModelRuntimeIdentity(
        runtime_kind=RuntimeKind(str(value["runtime_kind"])),
        runtime_class=RuntimeClass(str(value["runtime_class"])),
        adapter_id=str(value["adapter_id"]),
        runtime_id=str(value["runtime_id"]),
        auth_mode=AuthMode(str(value["auth_mode"])),
        configuration_fingerprint=str(value["configuration_fingerprint"]),
        runtime_version=_optional_str(value.get("runtime_version")),
        session_reference=_optional_str(value.get("session_reference")),
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BenchmarkError("checkpoint string field is invalid")
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise BenchmarkError("checkpoint integer field is invalid")
    return value


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BenchmarkError("checkpoint float field is invalid")
    return float(value)
