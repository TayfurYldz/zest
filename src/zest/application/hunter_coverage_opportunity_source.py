"""Bridge Hunter/Coverage scoring signal into the shared opportunity pool (MR-1).

Slice 3 / campaign Phase D. Converts already-ranked coverage-debt cells
(`ScoredCell`, as produced by `RunHuntScheduler`/`compute_coverage_debt`) into
durable `OpportunitySelectionCandidateRecord` rows. It does not recompute
coverage debt or hunt scoring itself -- that decision logic stays exactly
where it already lives (`research.coverage.debt`, `research.scheduler.score`)
so this producer cannot drift from it or duplicate it.

This source never writes `ResearchOpportunityRecord` directly and never
touches the Hypothesis/Experiment/Core/Worker path. It is purely a durable
proposal: `SelectResearchOpportunities` (unchanged pure admission/dedup/
precedence logic; only its read side was additively extended) is the sole
reader that turns a PENDING candidate into an admitted opportunity or a
recorded non-admission.
"""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.hunt_validation import side_effect_for_family
from zest.application.hunter_coverage_exhaustion import (
    TERMINAL_CONNECTED_DISPOSITIONS,
    hunter_coverage_exhaustion_inventory,
)
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord, OpportunitySelectionCandidateRecord
from zest.research.coverage.types import CoverageState
from zest.research.exploration import (
    OpportunityDimensions,
    OpportunityKind,
    OpportunityMode,
    OrdinalLevel,
    opportunity_structural_identity,
)
from zest.research.scheduler.types import ScoredCell

SOURCE_SYSTEM = "HUNTER_COVERAGE"
STRATEGY_VERSION = "hunter_coverage_opportunity_source.v1"
HUNTER_COVERAGE_CANDIDATES_PROPOSED = "HUNTER_COVERAGE_CANDIDATES_PROPOSED"
DEFAULT_MAX_CANDIDATES = 20

# Cells already hypothesized/tested/queued/covered have their own follow-up
# path (V1/V2/V3 tier progression in RunHuntCycle, or a HYPOTHESIS_FOLLOWUP
# diagnostic opportunity once assessed). Only genuinely untouched coverage
# gaps are proposed here, so this producer can never re-propose work that is
# already in flight and can never count a proposal as coverage itself
# (coverage state is untouched by this producer; it is a pure reader of it).
_ELIGIBLE_COVERAGE_STATES = frozenset(
    {
        CoverageState.UNTESTED,
        CoverageState.HYPOTHESIZED,
        CoverageState.V3_QUEUED,
    }
)


@dataclass(frozen=True)
class HunterCoverageOpportunitySourceCommand:
    research_run_id: str
    scored_cells: tuple[ScoredCell, ...]
    max_candidates: int = DEFAULT_MAX_CANDIDATES


@dataclass(frozen=True)
class HunterCoverageOpportunitySourceResult:
    research_run_id: str
    candidates_created: int
    skipped_ineligible_state: int
    skipped_duplicate: int


class HunterCoverageOpportunitySource:
    """Deterministic, LLM-free producer of pre-admission opportunity candidates."""

    def __init__(self, uow_factory: UnitOfWorkFactory, *, clock: Clock | None = None) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(
        self, command: HunterCoverageOpportunitySourceCommand
    ) -> HunterCoverageOpportunitySourceResult:
        if command.max_candidates <= 0:
            raise ApplicationError("max_candidates must be > 0")
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            seen_identities = {
                item.structural_identity
                for item in uow.opportunity_selection_candidates.list_for_research_run(
                    command.research_run_id
                )
            } | {
                item.structural_identity
                for item in uow.research_opportunities.list_for_research_run(
                    command.research_run_id
                )
            }
            families = {item.family_id: item for item in uow.hunter_families.list_enabled()}
            exhaustion = hunter_coverage_exhaustion_inventory(uow, command.research_run_id)
            eligible = []
            skipped_state = 0
            for scored in command.scored_cells:
                cell = scored.cell
                key = (cell.family_id, cell.node_canonical_key, cell.identity_id)
                disposition = exhaustion.dispositions.get(key)
                if cell.state not in _ELIGIBLE_COVERAGE_STATES:
                    skipped_state += 1
                    continue
                if disposition in TERMINAL_CONNECTED_DISPOSITIONS:
                    skipped_state += 1
                    continue
                if disposition == "ENGINE_DEPENDENCY_PENDING":
                    skipped_state += 1
                    continue
                eligible.append(scored)
            created = 0
            skipped_duplicate = 0
            for scored in eligible[: command.max_candidates]:
                family = families.get(scored.cell.family_id)
                family_name = family.name if family is not None else None
                native = None
                if family_name in {"OBJECT_AUTHORIZATION", "WORKFLOW_STATE_TRANSITION"}:
                    from zest.application.identity_auth_workflow_sources import (
                        hunter_native_authz_or_workflow_candidate,
                    )

                    attrs = _fact_attributes_for_node(
                        uow, command.research_run_id, scored.cell.node_canonical_key
                    )
                    native = hunter_native_authz_or_workflow_candidate(
                        research_run_id=command.research_run_id,
                        family_name=family_name,
                        attributes=attrs,
                        now=now,
                        source_system=SOURCE_SYSTEM,
                    )
                elif family_name in {
                    "HTTP_REQUEST_SMUGGLING_DESYNC",
                    "HTTP_CACHE_POISONING_DECEPTION",
                }:
                    from zest.application.mutation_protocol_sources import (
                        hunter_native_protocol_candidate,
                    )

                    native = hunter_native_protocol_candidate(
                        research_run_id=command.research_run_id,
                        family_id=scored.cell.family_id,
                        family_name=family_name,
                        node_key=scored.cell.node_canonical_key,
                        identity_id=scored.cell.identity_id,
                        now=now,
                        source_system=SOURCE_SYSTEM,
                    )
                record = native or _candidate_from_scored_cell(
                    scored,
                    research_run_id=command.research_run_id,
                    now=now,
                    family_name=family_name,
                )
                if record.structural_identity in seen_identities:
                    skipped_duplicate += 1
                    continue
                uow.opportunity_selection_candidates.insert(record)
                seen_identities.add(record.structural_identity)
                created += 1
                from zest.application.oast_source import (
                    OAST_SOURCE_SYSTEM,
                    hunter_native_oast_candidate,
                )
                from zest.core.enums import ScopeClassification
                from zest.research.discovery.types import AttackSurfaceNodeKind

                node = type(
                    "HunterOastNode",
                    (),
                    {
                        "kind": AttackSurfaceNodeKind.HTTP_OPERATION,
                        "attributes": _fact_attributes_for_node(
                            uow, command.research_run_id, scored.cell.node_canonical_key
                        ),
                        "identity_ids": (scored.cell.identity_id,),
                        "canonical_key": scored.cell.node_canonical_key,
                        "scope_classification": ScopeClassification.IN_SCOPE,
                    },
                )()
                oast_record = hunter_native_oast_candidate(
                    research_run_id=command.research_run_id,
                    node=node,
                    now=now,
                    source_system=OAST_SOURCE_SYSTEM,
                )
                if (
                    oast_record is not None
                    and oast_record.structural_identity not in seen_identities
                ):
                    uow.opportunity_selection_candidates.insert(oast_record)
                    seen_identities.add(oast_record.structural_identity)
                    created += 1
            if created or skipped_duplicate or skipped_state:
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=now,
                        actor_id="control-plane:hunter-coverage-opportunity-source",
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type=HUNTER_COVERAGE_CANDIDATES_PROPOSED,
                        subject_type="research_run",
                        subject_id=command.research_run_id,
                        payload={
                            "candidates_created": created,
                            "skipped_ineligible_state": skipped_state,
                            "skipped_duplicate": skipped_duplicate,
                            "not_authorization": True,
                            "not_a_vulnerability": True,
                            "not_coverage_reduction": True,
                        },
                    )
                )
            uow.commit()
        return HunterCoverageOpportunitySourceResult(
            research_run_id=command.research_run_id,
            candidates_created=created,
            skipped_ineligible_state=skipped_state,
            skipped_duplicate=skipped_duplicate,
        )


def _candidate_from_scored_cell(
    scored: ScoredCell,
    *,
    research_run_id: str,
    now,
    family_name: str | None = None,
) -> OpportunitySelectionCandidateRecord:
    cell = scored.cell
    native_se = side_effect_for_family(family_name) if family_name else 0
    proposed_direction = (
        f"Investigate HunterFamily {cell.family_id} against node {cell.node_canonical_key} "
        f"for identity {cell.identity_id} (coverage state {cell.state.value})."
    )
    context_signature = (
        f"hunter_coverage:{cell.family_id}:{cell.node_canonical_key}:{cell.identity_id}"
    )
    identity = opportunity_structural_identity(
        kind=OpportunityKind.HUNTER_COVERAGE_GAP,
        source_refs=(cell.family_id, cell.node_canonical_key, cell.identity_id),
        context_signature=context_signature,
        proposed_direction=proposed_direction,
    )
    information_value = (
        OrdinalLevel.HIGH if cell.state is CoverageState.UNTESTED else OrdinalLevel.MEDIUM
    )
    uncertainty = (
        OrdinalLevel.HIGH if cell.state is CoverageState.UNTESTED else OrdinalLevel.MEDIUM
    )
    dimensions = OpportunityDimensions(
        expected_information_value=information_value,
        security_relevance_potential=OrdinalLevel.LOW,
        novelty_composition=OrdinalLevel.LOW,
        unresolved_uncertainty=uncertainty,
        chain_potential=OrdinalLevel.LOW,
        evidence_coverage=OrdinalLevel.LOW,
        execution_cost=OrdinalLevel.LOW,
        side_effect_requirement=native_se,
        duplicate_risk=OrdinalLevel.LOW,
        previous_failed_attempts=0,
    )
    unresolved_question = (
        f"Does HunterFamily {cell.family_id} reveal new information for this node/identity?"
    )
    information_description = (
        "; ".join(cell.missing_evidence) if cell.missing_evidence else "no missing evidence recorded"
    )
    return OpportunitySelectionCandidateRecord(
        candidate_id=new_opaque_id(),
        research_run_id=research_run_id,
        source_system=SOURCE_SYSTEM,
        opportunity_kind=OpportunityKind.HUNTER_COVERAGE_GAP.value,
        mode=OpportunityMode.EXPLORATION.value,
        source_refs=(cell.family_id, cell.node_canonical_key, cell.identity_id),
        proposed_direction=proposed_direction,
        unresolved_question=unresolved_question,
        expected_information_value_description=information_description,
        assumptions=(
            "hunter_coverage_gap is plumbing, not authorization",
            "not_a_vulnerability",
            f"side_effect_class:{native_se}",
            f"hunter_score:{scored.score.total_score}",
        ),
        dimensions=dimensions.to_mapping(),
        context_signature=context_signature,
        structural_identity=identity,
        strategy_version=STRATEGY_VERSION,
        created_at=now,
    )


def _fact_attributes_for_node(uow, research_run_id: str, node_canonical_key: str) -> dict:
    attrs: dict = {}
    for fact in uow.discovery_facts.list_for_research_run(research_run_id):
        if fact.canonical_key != node_canonical_key:
            continue
        attrs.update(dict(fact.attributes or {}))
        if fact.normalized_origin:
            attrs.setdefault("authorized_origin", fact.normalized_origin)
            attrs.setdefault("origin", fact.normalized_origin)
        if fact.normalized_path:
            attrs.setdefault("path", fact.normalized_path)
    return attrs

