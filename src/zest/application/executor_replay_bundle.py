"""Assemble privacy-preserving executor replay bundles from persisted rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from zest.application.errors import ApplicationError
from zest.application.executor_replay_manifest import (
    BuildExecutorReplayManifest,
    BuildExecutorReplayManifestCommand,
    _redacted_digest,
    _sha256_json,
    _timestamp,
)
from zest.application.ports import UnitOfWorkFactory
from zest.data.records import ExperimentPlanRecord, WorkerResultRecord


REPLAY_BUNDLE_VERSION = "executor.replay_bundle.v1"
PUBLIC_ARTIFACT_KINDS = frozenset(
    {
        "browser_trace",
        "har",
        "network_trace",
        "response",
        "screenshot",
        "trace",
    }
)


@dataclass(frozen=True)
class BuildExecutorReplayBundleCommand:
    experiment_id: str


@dataclass(frozen=True)
class BuildExecutorReplayBundleResult:
    bundle: Mapping[str, Any]
    bundle_hash: str
    manifest_hash: str
    replay_class: str
    reason_codes: tuple[str, ...]


class BuildExecutorReplayBundle:
    """Create a replay package without redispatching or exposing raw artifacts."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory
        self._manifest_builder = BuildExecutorReplayManifest(uow_factory)

    def execute(
        self, command: BuildExecutorReplayBundleCommand
    ) -> BuildExecutorReplayBundleResult:
        manifest_result = self._manifest_builder.execute(
            BuildExecutorReplayManifestCommand(command.experiment_id)
        )
        with self._uow_factory.open() as uow:
            experiment = uow.experiments.get(command.experiment_id)
            if experiment is None:
                raise ApplicationError("experiment not found")
            plan = uow.experiment_plans.get(command.experiment_id)
            worker_results = tuple(
                sorted(
                    uow.worker_results.list_for_experiment(command.experiment_id),
                    key=lambda item: (item.received_at, item.worker_result_id),
                )
            )
            uow.rollback()

        request_template, request_redactions = _request_template_document(plan)
        response_digests, response_redactions = _response_digest_documents(worker_results)
        artifacts, artifact_redactions = _artifact_descriptor_documents(worker_results)
        bundle = {
            "bundle_version": REPLAY_BUNDLE_VERSION,
            "experiment_id": command.experiment_id,
            "research_run_id": experiment.research_run_id,
            "manifest_hash": manifest_result.manifest_hash,
            "manifest": manifest_result.manifest,
            "request_template": request_template,
            "response_digests": response_digests,
            "artifact_descriptors": artifacts,
            "replay_controls": {
                "auto_redispatch_allowed": False,
                "requires_core_authorization": True,
                "requires_redirect_reauthorization": True,
                "requires_human_review": (
                    manifest_result.replay_class == "HUMAN_REVIEW_REQUIRED"
                ),
                "worker_redispatch": "FORBIDDEN_BY_BUNDLE",
            },
            "redaction_metadata": {
                "request_template_redactions": request_redactions,
                "response_redactions": response_redactions,
                "artifact_descriptor_redactions": artifact_redactions,
            },
        }
        bundle_hash = _sha256_json(bundle)
        return BuildExecutorReplayBundleResult(
            bundle=bundle,
            bundle_hash=bundle_hash,
            manifest_hash=manifest_result.manifest_hash,
            replay_class=manifest_result.replay_class,
            reason_codes=manifest_result.reason_codes,
        )


def _request_template_document(
    plan: ExperimentPlanRecord | None,
) -> tuple[dict[str, Any], int]:
    if plan is None:
        return {"template_state": "PLAN_MISSING"}, 0
    argument_digest, argument_redactions = _redacted_digest(plan.arguments)
    return {
        "template_state": "PLAN_BOUND",
        "required_capability": plan.required_capability,
        "action": plan.action,
        "target_reference": plan.target_reference,
        "side_effect_level": plan.side_effect_level,
        "requested_budget_id": plan.requested_budget_id,
        "capability_version": plan.capability_version,
        "capability_definition_fingerprint": plan.capability_definition_fingerprint,
        "argument_digest": argument_digest,
        "evaluation_strategy": plan.evaluation_strategy,
        "created_at": _timestamp(plan.created_at),
    }, argument_redactions


def _response_digest_documents(
    worker_results: tuple[WorkerResultRecord, ...],
) -> tuple[tuple[dict[str, Any], ...], int]:
    documents = []
    redactions = 0
    for result in worker_results:
        raw_digest, raw_redactions = _redacted_digest(result.raw_result)
        diagnostics_digest, diagnostics_redactions = _redacted_digest(result.diagnostics)
        control_digest, control_redactions = _redacted_digest(result.control_signal)
        documents.append(
            {
                "worker_result_id": result.worker_result_id,
                "request_id": result.request_id,
                "status": result.status,
                "raw_result_digest": raw_digest,
                "diagnostics_digest": diagnostics_digest,
                "control_signal_digest": control_digest,
            }
        )
        redactions += raw_redactions + diagnostics_redactions + control_redactions
    return tuple(documents), redactions


def _artifact_descriptor_documents(
    worker_results: tuple[WorkerResultRecord, ...],
) -> tuple[tuple[dict[str, Any], ...], int]:
    documents = []
    redactions = 0
    for result in worker_results:
        for index, descriptor in enumerate(result.raw_artifact_descriptors or ()):
            descriptor_digest, descriptor_redactions = _redacted_digest(descriptor)
            documents.append(
                {
                    "worker_result_id": result.worker_result_id,
                    "descriptor_index": index,
                    "artifact_kind": _public_artifact_kind(descriptor),
                    "descriptor_digest": descriptor_digest,
                }
            )
            redactions += descriptor_redactions
    return tuple(documents), redactions


def _public_artifact_kind(descriptor: Mapping[str, Any]) -> str:
    kind = descriptor.get("kind")
    if isinstance(kind, str) and kind in PUBLIC_ARTIFACT_KINDS:
        return kind
    return "other"
