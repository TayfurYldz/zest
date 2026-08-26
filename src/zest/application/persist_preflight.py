"""Persist operator Preflight reports. Stored PASS is not START authority."""

from __future__ import annotations

import hashlib
from typing import Mapping

from zest.application.autonomous_research_controller import (
    StartAutonomousResearchCommand,
)
from zest.application.identity import new_opaque_id
from zest.application.ports import UnitOfWorkFactory
from zest.application.preflight import PreflightReport
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord, PreflightReportRecord
from zest.safe_data import redact_secret_keys


def configuration_fingerprint_for_command(command: StartAutonomousResearchCommand) -> str:
    payload = {
        "research_run_id": command.research_run_id,
        "budget_id": command.budget_id,
        "target_reference": command.target_reference,
        "research_question": command.research_question,
        "max_cycles": command.bounds.max_cycles,
        "max_experiments": command.bounds.max_experiments,
        "max_model_calls": command.bounds.max_model_calls,
        "max_worker_invocations": command.bounds.max_worker_invocations,
        "side_effect_ceiling": command.bounds.side_effect_ceiling,
    }
    encoded = repr(sorted(payload.items())).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def persist_preflight_report(
    uow_factory: UnitOfWorkFactory,
    report: PreflightReport,
    *,
    runtime_instance_id: str,
    release_version: str,
    configuration_fingerprint: str,
    actor_id: str,
) -> PreflightReportRecord:
    checks: tuple[Mapping[str, object], ...] = tuple(
        redact_secret_keys(
            {
                "name": check.name.value,
                "passed": check.passed,
                "detail": check.detail,
            }
        )
        for check in report.checks
    )
    record = PreflightReportRecord(
        preflight_report_id=new_opaque_id(),
        research_run_id=report.research_run_id,
        runtime_instance_id=runtime_instance_id,
        created_at=report.generated_at,
        release_version=release_version,
        configuration_fingerprint=configuration_fingerprint,
        status=report.status.value,
        checks=checks,
    )
    with uow_factory.open() as uow:
        uow.preflight_reports.insert(record)
        uow.audit_events.insert(
            AuditEventRecord(
                audit_event_id=new_opaque_id(),
                occurred_at=report.generated_at,
                actor_id=actor_id,
                actor_type=ActorType.CONTROL_PLANE.value,
                event_type="PREFLIGHT_COMPLETED",
                subject_type="research_run",
                subject_id=report.research_run_id,
                payload={
                    "preflight_report_id": record.preflight_report_id,
                    "status": record.status,
                    "not_start_authority": True,
                    "not_research_truth": True,
                },
                correlation_id=report.research_run_id,
            )
        )
        uow.commit()
    return record


def preflight_record_to_mapping(record: PreflightReportRecord) -> dict[str, object]:
    return redact_secret_keys(
        {
            "preflight_report_id": record.preflight_report_id,
            "research_run_id": record.research_run_id,
            "runtime_instance_id": record.runtime_instance_id,
            "created_at": record.created_at.isoformat(),
            "release_version": record.release_version,
            "configuration_fingerprint": record.configuration_fingerprint,
            "status": record.status,
            "result": record.status,
            "checks": [dict(item) for item in record.checks],
            "authorizes_start": False,
        }
    )
