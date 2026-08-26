"""Build a report package from an approved Finding. Does not submit externally."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord
from zest.research.finding_proposal import FindingProposalState
from zest.research.report_package import (
    ExternalDuplicateSignal,
    FindingReportInput,
    FindingReportPackage,
    build_finding_report_package,
)

REPORT_PACKAGE_BUILT = "REPORT_PACKAGE_BUILT"


@dataclass(frozen=True)
class PackageFindingReportCommand:
    finding_id: str
    external_duplicate_signals: tuple[Mapping[str, str] | ExternalDuplicateSignal, ...] = ()


@dataclass(frozen=True)
class PackageFindingReportResult:
    finding_id: str
    package: FindingReportPackage


class PackageFindingReport:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
        actor_id: str = "control-plane:report-package",
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._actor_id = actor_id

    def execute(self, command: PackageFindingReportCommand) -> PackageFindingReportResult:
        with self._uow_factory.open() as uow:
            finding = uow.findings.get(command.finding_id)
            if finding is None:
                raise ApplicationError("finding not found")
            proposal = uow.finding_proposals.get(finding.finding_proposal_id)
            if proposal is None:
                raise ApplicationError("finding proposal not found")
            if proposal.state != FindingProposalState.APPROVED.value:
                raise ApplicationError("finding proposal is not approved")
            report_input = FindingReportInput(
                finding_id=finding.finding_id,
                finding_proposal_id=finding.finding_proposal_id,
                candidate_id=finding.candidate_id,
                research_run_id=finding.research_run_id,
                approval_id=finding.approval_id,
                human_review_id=finding.human_review_id,
                title=finding.title,
                claim=finding.claim,
                classification=finding.classification,
                evidence_ids=finding.evidence_ids,
                verification_ids=finding.verification_ids,
                impact_chain_ids=proposal.impact_chain_ids,
            )
            signals = tuple(
                signal
                if isinstance(signal, ExternalDuplicateSignal)
                else ExternalDuplicateSignal(**dict(signal))
                for signal in command.external_duplicate_signals
            )
            package = build_finding_report_package(
                report_input,
                package_id=new_opaque_id(),
                external_duplicate_signals=signals,
            )
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=self._clock.now(),
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type=REPORT_PACKAGE_BUILT,
                    subject_type="finding",
                    subject_id=finding.finding_id,
                    correlation_id=finding.research_run_id,
                    payload={
                        "finding_id": finding.finding_id,
                        "package_id": package.package_id,
                        "package_hash": package.package_hash,
                        "internal_duplicate_fingerprint": package.internal_duplicate_fingerprint,
                        "external_signal_count": len(package.external_duplicate_signals),
                        "not_auto_submitted": True,
                    },
                )
            )
            uow.commit()
        return PackageFindingReportResult(finding_id=finding.finding_id, package=package)
