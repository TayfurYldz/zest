"""Human-gated permanent HunterFamily admission for a validated exploratory draft.

Genuinely new use case. Does not execute Workers. Does not create Findings.
Does not reuse FindingProposal / HumanReview / Approval rows — those are
finding-acceptance records (approval.proposal_id is a FK to finding_proposal).
The durable promotion trail is an append-only audit event plus the hunter_family
insert that only a HUMAN_OPERATOR APPROVE may trigger.
"""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.exploratory_binding import load_exploratory_binding
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType, ApprovalDecision
from zest.data.errors import PersistenceConflictError
from zest.data.records import AuditEventRecord, HunterFamilyRecord
from zest.research.exploratory import EXPLORATORY_SUBJECT_TYPE, ExploratoryHypothesisDraft
from zest.research.exploratory_compile import EXPLORATORY_COMPILER_ADAPTER_VERSION

CONTROL_PLANE_ACTOR_ID = "control-plane:exploratory-family-promotion"
EXPLORATORY_FAMILY_PROMOTED_EVENT = "EXPLORATORY_FAMILY_PROMOTED"
EXPLORATORY_FAMILY_PROMOTION_REJECTED_EVENT = "EXPLORATORY_FAMILY_PROMOTION_REJECTED"
ALLOWED_VALIDATION_TIERS = frozenset({"V1", "V2", "V3"})


@dataclass(frozen=True)
class PromoteExploratoryFamilyCommand:
    research_run_id: str
    hypothesis_id: str
    reviewer_id: str
    actor_type: ActorType
    decision: ApprovalDecision
    validation_tier: str = "V3"
    note: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class PromoteExploratoryFamilyResult:
    hypothesis_id: str
    draft_id: str
    promoted: bool
    reason_code: str
    family_id: str | None = None
    family_version: int | None = None
    reviewer_id: str | None = None


class PromoteExploratoryFamily:
    """Write hunter_family only after an explicit HUMAN_OPERATOR APPROVE command."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
        actor_id: str = CONTROL_PLANE_ACTOR_ID,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._actor_id = actor_id

    def execute(
        self, command: PromoteExploratoryFamilyCommand
    ) -> PromoteExploratoryFamilyResult:
        if command.validation_tier not in ALLOWED_VALIDATION_TIERS:
            raise ApplicationError("validation_tier must be V1, V2, or V3")
        if not isinstance(command.actor_type, ActorType):
            raise ApplicationError("actor_type must be ActorType")
        if not isinstance(command.decision, ApprovalDecision):
            raise ApplicationError("decision must be ApprovalDecision")
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            _hypothesis, draft, _audit = load_exploratory_binding(
                uow,
                research_run_id=command.research_run_id,
                hypothesis_id=command.hypothesis_id,
            )
            if draft.may_write_hunter_registry:
                raise ApplicationError("exploratory draft cannot authorize hunter registry write")

            prior = _existing_promotion(uow, draft.draft_id)
            if prior is not None:
                family_id = str(prior.payload.get("family_id") or "")
                version = prior.payload.get("family_version")
                uow.commit()
                return PromoteExploratoryFamilyResult(
                    hypothesis_id=command.hypothesis_id,
                    draft_id=draft.draft_id,
                    promoted=True,
                    reason_code="ALREADY_PROMOTED",
                    family_id=family_id or None,
                    family_version=version if isinstance(version, int) else None,
                    reviewer_id=str(prior.payload.get("reviewer_id") or "") or None,
                )

            reason_code = _authorization_reason(command)
            if reason_code is not None:
                _audit_promotion(
                    uow,
                    now,
                    draft,
                    command,
                    promoted=False,
                    reason_code=reason_code,
                    family_id=None,
                    family_version=None,
                )
                uow.commit()
                return PromoteExploratoryFamilyResult(
                    hypothesis_id=command.hypothesis_id,
                    draft_id=draft.draft_id,
                    promoted=False,
                    reason_code=reason_code,
                    reviewer_id=command.reviewer_id,
                )

            overlap = _enabled_name_collision(uow, draft.proposed_family_name)
            if overlap is not None:
                _audit_promotion(
                    uow,
                    now,
                    draft,
                    command,
                    promoted=False,
                    reason_code="FAMILY_NAME_ALREADY_REGISTERED",
                    family_id=overlap.family_id,
                    family_version=overlap.version,
                )
                uow.commit()
                return PromoteExploratoryFamilyResult(
                    hypothesis_id=command.hypothesis_id,
                    draft_id=draft.draft_id,
                    promoted=False,
                    reason_code="FAMILY_NAME_ALREADY_REGISTERED",
                    family_id=overlap.family_id,
                    family_version=overlap.version,
                    reviewer_id=command.reviewer_id,
                )

            family_id = new_opaque_id()
            record = HunterFamilyRecord(
                family_id=family_id,
                name=draft.proposed_family_name,
                target_node_kinds=draft.target_node_kinds,
                preconditions={
                    "scope_classification": "IN_SCOPE",
                    "exploratory_draft_id": draft.draft_id,
                    "research_run_id": command.research_run_id,
                    "compiler_adapter": EXPLORATORY_COMPILER_ADAPTER_VERSION,
                },
                claim_template=draft.hypothesis_claim,
                evidence_requirements={
                    "source_refs": list(draft.source_refs),
                    "validation_gates": list(draft.validation_gates),
                },
                validation_tier=command.validation_tier,
                enabled=True,
                version=1,
                created_at=now,
            )
            try:
                uow.hunter_families.insert(record)
            except PersistenceConflictError as exc:
                raise ApplicationError("hunter family already recorded") from exc
            _audit_promotion(
                uow,
                now,
                draft,
                command,
                promoted=True,
                reason_code="ALLOWED",
                family_id=family_id,
                family_version=1,
            )
            uow.commit()
            return PromoteExploratoryFamilyResult(
                hypothesis_id=command.hypothesis_id,
                draft_id=draft.draft_id,
                promoted=True,
                reason_code="ALLOWED",
                family_id=family_id,
                family_version=1,
                reviewer_id=command.reviewer_id,
            )


def approval_subject_for_exploratory_family(draft_id: str) -> str:
    """Stable subject id for audits. Not a FindingProposal approval subject."""

    return f"exploratory-family:{draft_id}"


def _authorization_reason(command: PromoteExploratoryFamilyCommand) -> str | None:
    if command.actor_type is not ActorType.HUMAN_OPERATOR:
        return "APPROVAL_INVALID_ACTOR"
    if command.decision is ApprovalDecision.REJECT:
        return "APPROVAL_REJECTED"
    if command.decision is not ApprovalDecision.APPROVE:
        return "APPROVAL_REQUIRED"
    return None


def _existing_promotion(uow, draft_id: str) -> AuditEventRecord | None:
    matches = [
        event
        for event in uow.audit_events.list_for_subject_type(EXPLORATORY_SUBJECT_TYPE)
        if event.event_type == EXPLORATORY_FAMILY_PROMOTED_EVENT
        and event.subject_id == draft_id
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda item: item.occurred_at)[0]


def _enabled_name_collision(uow, proposed_family_name: str) -> HunterFamilyRecord | None:
    proposed = proposed_family_name.strip().lower()
    for record in uow.hunter_families.list_enabled():
        if record.name.strip().lower() == proposed or record.family_id.strip().lower() == proposed:
            return record
    return None


def _audit_promotion(
    uow,
    now,
    draft: ExploratoryHypothesisDraft,
    command: PromoteExploratoryFamilyCommand,
    *,
    promoted: bool,
    reason_code: str,
    family_id: str | None,
    family_version: int | None,
) -> None:
    uow.audit_events.insert(
        AuditEventRecord(
            audit_event_id=new_opaque_id(),
            occurred_at=now,
            actor_id=command.reviewer_id,
            actor_type=command.actor_type.value,
            event_type=(
                EXPLORATORY_FAMILY_PROMOTED_EVENT
                if promoted
                else EXPLORATORY_FAMILY_PROMOTION_REJECTED_EVENT
            ),
            subject_type=EXPLORATORY_SUBJECT_TYPE,
            subject_id=draft.draft_id,
            payload={
                "hypothesis_id": command.hypothesis_id,
                "research_run_id": command.research_run_id,
                "proposed_family_name": draft.proposed_family_name,
                "may_write_hunter_registry": False,
                "requires_human_family_approval": True,
                "reason_code": reason_code,
                "decision": command.decision.value,
                "reviewer_id": command.reviewer_id,
                "family_id": family_id,
                "family_version": family_version,
                "note": command.note,
                "subject_reference": approval_subject_for_exploratory_family(draft.draft_id),
            },
            correlation_id=command.correlation_id,
        )
    )
