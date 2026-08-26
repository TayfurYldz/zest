"""Assess production executor fabric invariants from persisted ledger rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from zest.application.errors import ApplicationError
from zest.application.executor_replay_bundle import (
    BuildExecutorReplayBundle,
    BuildExecutorReplayBundleCommand,
)
from zest.application.executor_replay_manifest import (
    BuildExecutorReplayManifest,
    BuildExecutorReplayManifestCommand,
    _sha256_json,
)
from zest.application.ports import UnitOfWorkFactory
from zest.data.records import WorkerResultRecord


EXECUTOR_FABRIC_ASSESSMENT_VERSION = "executor.fabric_assessment.v1"


@dataclass(frozen=True)
class AssessExecutorFabricExperimentCommand:
    experiment_id: str


@dataclass(frozen=True)
class AssessExecutorFabricExperimentResult:
    assessment: Mapping[str, Any]
    assessment_hash: str
    assessment_status: str
    reason_codes: tuple[str, ...]
    manifest_hash: str
    bundle_hash: str


class AssessExecutorFabricExperiment:
    """Check executor-fabric safety invariants without redispatching Workers."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory
        self._manifest_builder = BuildExecutorReplayManifest(uow_factory)
        self._bundle_builder = BuildExecutorReplayBundle(uow_factory)

    def execute(
        self, command: AssessExecutorFabricExperimentCommand
    ) -> AssessExecutorFabricExperimentResult:
        manifest_result = self._manifest_builder.execute(
            BuildExecutorReplayManifestCommand(command.experiment_id)
        )
        bundle_result = self._bundle_builder.execute(
            BuildExecutorReplayBundleCommand(command.experiment_id)
        )
        with self._uow_factory.open() as uow:
            experiment = uow.experiments.get(command.experiment_id)
            if experiment is None:
                raise ApplicationError("experiment not found")
            worker_results = tuple(
                sorted(
                    uow.worker_results.list_for_experiment(command.experiment_id),
                    key=lambda item: (item.received_at, item.worker_result_id),
                )
            )
            uow.rollback()

        reason_codes, invariants = _evaluate_invariants(
            bundle_result.bundle,
            worker_results,
        )
        assessment_status = (
            "PASS"
            if not any(item.startswith("VIOLATION_") for item in reason_codes)
            else "FAIL"
        )
        if not worker_results:
            assessment_status = "FAIL"
        if assessment_status == "PASS":
            reason_codes = tuple(dict.fromkeys((*reason_codes, "EXECUTOR_FABRIC_PASS")))
        assessment = {
            "assessment_version": EXECUTOR_FABRIC_ASSESSMENT_VERSION,
            "experiment_id": command.experiment_id,
            "research_run_id": experiment.research_run_id,
            "manifest_hash": manifest_result.manifest_hash,
            "bundle_hash": bundle_result.bundle_hash,
            "replay_class": manifest_result.replay_class,
            "capability_surface": tuple(
                item["worker_capability"] for item in manifest_result.manifest["attempts"]
            ),
            "worker_result_statuses": tuple(item.status for item in worker_results),
            "assessment_status": assessment_status,
            "reason_codes": reason_codes,
            "invariants": invariants,
        }
        return AssessExecutorFabricExperimentResult(
            assessment=assessment,
            assessment_hash=_sha256_json(assessment),
            assessment_status=assessment_status,
            reason_codes=reason_codes,
            manifest_hash=manifest_result.manifest_hash,
            bundle_hash=bundle_result.bundle_hash,
        )


def _evaluate_invariants(
    bundle: Mapping[str, Any],
    worker_results: tuple[WorkerResultRecord, ...],
) -> tuple[tuple[str, ...], dict[str, Any]]:
    reason_codes: list[str] = []
    controls = bundle.get("replay_controls")
    if (
        not isinstance(controls, Mapping)
        or controls.get("auto_redispatch_allowed") is not False
    ):
        reason_codes.append("VIOLATION_REPLAY_REDISPATCH_ALLOWED")
    if (
        not isinstance(controls, Mapping)
        or controls.get("requires_core_authorization") is not True
    ):
        reason_codes.append("VIOLATION_CORE_AUTHORIZATION_NOT_REQUIRED")
    if (
        not isinstance(controls, Mapping)
        or controls.get("requires_redirect_reauthorization") is not True
    ):
        reason_codes.append("VIOLATION_REDIRECT_REAUTHORIZATION_NOT_REQUIRED")
    if not worker_results:
        reason_codes.append("VIOLATION_NO_WORKER_RESULT")

    redirect_count = 0
    scope_escape_block_count = 0
    self_authorized_count = 0
    contacted_outside_count = 0
    for result in worker_results:
        raw = result.raw_result if isinstance(result.raw_result, Mapping) else {}
        diagnostics = result.diagnostics if isinstance(result.diagnostics, Mapping) else {}
        if _truthy(raw.get("self_authorized")) or _truthy(diagnostics.get("self_authorized")):
            self_authorized_count += 1
            reason_codes.append("VIOLATION_WORKER_SELF_AUTHORIZED")
        if _truthy(diagnostics.get("redirect")) or result.status == "REAUTHORIZATION_REQUIRED":
            redirect_count += 1
            if result.status != "REAUTHORIZATION_REQUIRED":
                reason_codes.append("VIOLATION_REDIRECT_DID_NOT_STOP")
            if diagnostics.get("followed") is not False:
                reason_codes.append("VIOLATION_REDIRECT_FOLLOWED")
            if diagnostics.get("requires_core_re_evaluation") is not True:
                reason_codes.append("VIOLATION_REDIRECT_NOT_REEVALUATED_BY_CORE")
        if _outside_envelope(diagnostics):
            if diagnostics.get("contacted") is False:
                scope_escape_block_count += 1
                reason_codes.append("SCOPE_ESCAPE_BLOCKED_BY_ENVELOPE")
            else:
                contacted_outside_count += 1
                reason_codes.append("VIOLATION_SCOPE_ESCAPE_CONTACTED")

    if redirect_count:
        reason_codes.append("REDIRECT_REAUTHORIZATION_REQUIRED")
    if self_authorized_count == 0:
        reason_codes.append("WORKER_SELF_AUTHORIZATION_ABSENT")
    invariants = {
        "worker_result_count": len(worker_results),
        "redirect_reauthorization_count": redirect_count,
        "scope_escape_block_count": scope_escape_block_count,
        "contacted_outside_envelope_count": contacted_outside_count,
        "self_authorized_count": self_authorized_count,
        "auto_redispatch_allowed": (
            controls.get("auto_redispatch_allowed") if isinstance(controls, Mapping) else None
        ),
        "requires_core_authorization": (
            controls.get("requires_core_authorization") if isinstance(controls, Mapping) else None
        ),
        "requires_redirect_reauthorization": (
            controls.get("requires_redirect_reauthorization") if isinstance(controls, Mapping) else None
        ),
    }
    return tuple(dict.fromkeys(reason_codes)), invariants


def _outside_envelope(diagnostics: Mapping[str, Any]) -> bool:
    error = diagnostics.get("error")
    return isinstance(error, str) and "outside authorized network envelope" in error


def _truthy(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")
