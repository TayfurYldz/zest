"""Compose surface.discovery.v1 with existing G12 execute/prepare primitives.

Does not own Core authority. Does not auto-retry UNKNOWN_OUTCOME.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from zest.application.discovery.claim import claim_frontier_selected
from zest.application.discovery.compile_plan import (
    LivePageSnapshot,
    ReobserveRequired,
    UnsupportedDiscoveryCapability,
    compile_frontier_plan,
)
from zest.application.discovery.config import (
    assert_runtime_matches_persisted,
    config_from_record,
    record_from_config,
)
from zest.application.discovery.control_events import ingest_control_event_from_worker_result
from zest.application.discovery.reauthorization_followup import (
    compile_allowed_same_origin_redirect_frontier,
)
from zest.application.discovery.project import (
    reconcile_missing_projections,
)
from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
)
from zest.application.http_transaction_authorization import authorize_http_transaction_plan
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.program_research_context import ProgramPolicyView
from zest.application.scope_reauthorization import evaluate_reauthorization_request
from zest.application.prepare_planned_experiment import (
    PreparePlannedExperiment,
    PreparePlannedExperimentCommand,
)
from zest.core.approval import ApprovalView
from zest.core.enums import ScopeClassification, ScopeDecision
from zest.core.scope import ScopeEvaluationInput
from zest.core.scope_compiler import CompiledScope, evaluate_scope_candidate
from zest.data.records import (
    ExperimentExecutionState,
    FrontierEventRecord,
    FrontierItemRecord,
    FrontierSourceRecord,
    HypothesisRecord,
    ObservationRecord,
)
from zest.platform.secrets import CompositeSecretPort
from zest.platform.url_normalize import normalize_url
from zest.platform.worker import InvocationStatus, WorkerPort
from zest.research.discovery.canonical import canonical_key
from zest.research.discovery.config import DiscoveryRunConfig
from zest.research.discovery.control_resolve import LiveControlView
from zest.research.discovery.frontier import (
    SURFACE_DISCOVERY_MAX_SIDE_EFFECT,
    FrontierEvent,
    FrontierItem,
    runnable_discovery_frontier_items,
    select_eligible_frontier,
)
from zest.research.discovery.projection import seed_inspect_path_frontier
from zest.research.discovery.types import (
    ANONYMOUS_IDENTITY_ID,
    DiscoveryGoalKind,
    FrontierEventKind,
)
from zest.research.identity_session import Identity
from zest.tools.capabilities import (
    BROWSER_PAGE_CAPABILITY,
    BROWSER_PAGE_OBSERVE_ACTION,
    HTTP_TRANSACTION_CAPABILITY,
)


DISCOVERY_HYPOTHESIS_CLAIM = (
    "Observe authorized in-scope target surface under configured identities."
)


@dataclass(frozen=True)
class SurfaceDiscoveryStart:
    config: DiscoveryRunConfig
    compiled_scope: CompiledScope | None = None
    identities: tuple[str, ...] = (ANONYMOUS_IDENTITY_ID,)
    session_context_by_identity: Mapping[str, str] | None = None
    identity_by_id: Mapping[str, Identity] | None = None


@dataclass(frozen=True)
class SurfaceDiscoveryCycleResult:
    research_run_id: str
    stop_reason: str | None
    frontier_id: str | None
    experiment_id: str | None
    worker_invoked: bool
    eligible_before: int = 0
    eligible_after: int = 0
    selected_goal_kind: str | None = None
    selected_path: str | None = None
    compiled_capability: str | None = None
    worker_status: str | None = None
    observation_id: str | None = None
    discovery_exhausted: bool = False


class SurfaceDiscoveryRunner:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        worker: WorkerPort,
        *,
        clock: Clock | None = None,
        secret_port: CompositeSecretPort | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._prepare = PreparePlannedExperiment(uow_factory, clock=self._clock)
        self._execute = ExecutePlannedExperiment(
            uow_factory, worker, clock=self._clock, secret_port=secret_port
        )
        self._live_pages: dict[tuple[str, str, str, str], LivePageSnapshot] = {}

    def clear_live_pages(self) -> None:
        """Drop ephemeral el-N mappings. Restart must re-observe."""

        self._live_pages.clear()

    def _drop_live_pages(self, research_run_id: str, *, origin: str, identity_id: str) -> None:
        self._live_pages = {
            key: value
            for key, value in self._live_pages.items()
            if not (key[0] == research_run_id and key[1] == origin and key[3] == identity_id)
        }

    def ensure_started(self, start: SurfaceDiscoveryStart) -> None:
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            existing = uow.discovery_run_configs.get(start.config.research_run_id)
            if existing is None:
                uow.discovery_run_configs.insert(record_from_config(start.config, created_at=now))
                self._seed(uow, start, created_at=now)
                self._ensure_hypothesis(uow, start.config.research_run_id, created_at=now)
                uow.commit()
                return
            persisted = config_from_record(existing)
            assert_runtime_matches_persisted(persisted, start.config)
            uow.rollback()

    def run_cycle(
        self,
        start: SurfaceDiscoveryStart,
        *,
        budget_id: str,
        target_reference: str,
        scope: ScopeEvaluationInput,
        approval: ApprovalView | None = None,
        program_policy: ProgramPolicyView | None = None,
    ) -> SurfaceDiscoveryCycleResult:
        self.ensure_started(start)
        now = self._clock.now()
        research_run_id = start.config.research_run_id
        with self._uow_factory.open() as uow:
            for result in uow.worker_results.list_for_research_run(research_run_id):
                ingest_control_event_from_worker_result(
                    uow, result, created_at=now, target_reference=target_reference
                )
            reconcile_missing_projections(
                uow, research_run_id, created_at=now, target_reference=target_reference
            )
            from zest.application.discovery.lifecycle import (
                apply_discovery_exit_dispositions,
                discovery_exit_audit,
            )

            apply_discovery_exit_dispositions(uow, research_run_id, created_at=now)
            bound_stop = _bound_stop_reason(uow, start.config)
            if bound_stop is not None:
                uow.commit()
                return SurfaceDiscoveryCycleResult(
                    research_run_id, bound_stop, None, None, False
                )
            items = uow.frontier_items.list_for_research_run(research_run_id)
            events_by = {
                item.frontier_id: tuple(
                    _domain_events(uow.frontier_events.list_for_frontier(item.frontier_id))
                )
                for item in items
            }
            domain_items = tuple(_domain_item(item) for item in items)
            eligible_before = _eligible_count(domain_items, events_by)
            chosen = select_eligible_frontier(
                domain_items, events_by, max_side_effect=SURFACE_DISCOVERY_MAX_SIDE_EFFECT
            )
            if chosen is None:
                apply_discovery_exit_dispositions(uow, research_run_id, created_at=now)
                audit = discovery_exit_audit(uow, research_run_id, considered=True)
                eligible_after = _snapshot_eligible_count(uow, research_run_id)
                uow.commit()
                return SurfaceDiscoveryCycleResult(
                    research_run_id,
                    "NO_ELIGIBLE_FRONTIER" if audit.discovery_exit_allowed else "DISCOVERY_EXIT_BLOCKED",
                    None,
                    None,
                    False,
                    eligible_before=eligible_before,
                    eligible_after=eligible_after,
                    discovery_exhausted=audit.discovery_exit_allowed,
                )
            if chosen.candidate_origin != start.config.normalized_origin:
                self._block(uow, chosen.frontier_id, "BLOCKED_SCOPE", now)
                eligible_after = _snapshot_eligible_count(uow, research_run_id)
                uow.commit()
                return SurfaceDiscoveryCycleResult(
                    research_run_id,
                    "BLOCKED_SCOPE",
                    chosen.frontier_id,
                    None,
                    False,
                    eligible_before=eligible_before,
                    eligible_after=eligible_after,
                    selected_goal_kind=chosen.goal_kind.value,
                    selected_path=chosen.candidate_path,
                    compiled_capability=chosen.proposed_capability,
                )
            chosen_record = uow.frontier_items.get(chosen.frontier_id)
            if chosen_record is not None and _known_control_destination_is_explicit_oos(
                chosen_record, start.compiled_scope
            ):
                self._append_event(
                    uow,
                    chosen.frontier_id,
                    "BLOCKED_SCOPE",
                    now,
                    reason_code="KNOWN_CONTROL_DESTINATION_OUT_OF_SCOPE",
                )
                eligible_after = _snapshot_eligible_count(uow, research_run_id)
                uow.commit()
                return SurfaceDiscoveryCycleResult(
                    research_run_id,
                    "BLOCKED_SCOPE",
                    chosen.frontier_id,
                    None,
                    False,
                    eligible_before=eligible_before,
                    eligible_after=eligible_after,
                    selected_goal_kind=chosen.goal_kind.value,
                    selected_path=chosen.candidate_path,
                    compiled_capability=chosen.proposed_capability,
                )
            claim_frontier_selected(uow, chosen.frontier_id, created_at=now)
            hypothesis_id = uow.hypotheses.list_for_research_run(research_run_id)[0].hypothesis_id
            record = uow.frontier_items.get(chosen.frontier_id)
            uow.commit()
        assert record is not None
        live_page = self._live_pages.get(
            (
                research_run_id,
                record.candidate_origin.rstrip("/"),
                record.candidate_path or "/",
                record.identity_id,
            )
        )
        try:
            plan = compile_frontier_plan(
                record,
                hypothesis_id=hypothesis_id,
                budget_id=budget_id,
                target_reference=target_reference,
                live_page=live_page,
            )
        except ReobserveRequired:
            with self._uow_factory.open() as uow:
                self._reobserve(uow, record, created_at=now)
                eligible_after = _snapshot_eligible_count(uow, research_run_id)
                uow.commit()
            return SurfaceDiscoveryCycleResult(
                research_run_id,
                None,
                record.frontier_id,
                None,
                False,
                eligible_before=eligible_before,
                eligible_after=eligible_after,
                selected_goal_kind=record.goal_kind,
                selected_path=record.candidate_path,
            )
        except UnsupportedDiscoveryCapability:
            with self._uow_factory.open() as uow:
                self._append_event(
                    uow, record.frontier_id, "UNSUPPORTED", now, reason_code="UNSUPPORTED_CAPABILITY"
                )
                eligible_after = _snapshot_eligible_count(uow, research_run_id)
                uow.commit()
            return SurfaceDiscoveryCycleResult(
                research_run_id,
                "UNSUPPORTED_CAPABILITY",
                record.frontier_id,
                None,
                False,
                eligible_before=eligible_before,
                eligible_after=eligible_after,
                selected_goal_kind=record.goal_kind,
                selected_path=record.candidate_path,
                compiled_capability=record.proposed_capability,
            )
        scope_decision = authorize_http_transaction_plan(
            plan,
            start.compiled_scope,
            program_policy=program_policy,
        )
        if not scope_decision.accepted:
            with self._uow_factory.open() as uow:
                self._block(uow, record.frontier_id, "BLOCKED_SCOPE", now)
                eligible_after = _snapshot_eligible_count(uow, research_run_id)
                uow.commit()
            return SurfaceDiscoveryCycleResult(
                research_run_id,
                "BLOCKED_SCOPE",
                record.frontier_id,
                None,
                False,
                eligible_before=eligible_before,
                eligible_after=eligible_after,
                selected_goal_kind=record.goal_kind,
                selected_path=record.candidate_path,
                compiled_capability=plan.required_capability,
            )
        experiment_id = new_opaque_id()
        with self._uow_factory.open() as uow:
            self._prepare.execute(
                PreparePlannedExperimentCommand(
                    experiment_id=experiment_id,
                    research_run_id=research_run_id,
                    plan=plan,
                ),
                unit_of_work=uow,
            )
            uow.commit()
        identity_id = None if record.identity_id == ANONYMOUS_IDENTITY_ID else record.identity_id
        identity = None
        if identity_id and start.identity_by_id:
            identity = start.identity_by_id.get(identity_id)
        loop = self._execute.execute(
            ExecutePlannedExperimentCommand(
                experiment_id=experiment_id,
                plan=plan,
                scope=scope,
                approval=approval,
                compiled_scope=start.compiled_scope,
                program_policy=program_policy,
                identity_id=identity_id,
                identity=identity,
            )
        )
        worker_invoked = loop.status not in {
            ResearchLoopStatus.DISPATCH_DENIED,
            ResearchLoopStatus.HUMAN_REVIEW_REQUIRED,
        }
        if loop.status is ResearchLoopStatus.INVOCATION_FAILED:
            stop_reason = (
                "INVOCATION_START_FAILED"
                if loop.invocation_status is InvocationStatus.START_FAILED
                else "INVOCATION_FAILED"
            )
            with self._uow_factory.open() as uow:
                # The attempt/run fault written by ExecutePlannedExperiment
                # is the operational source of truth.  This frontier event
                # only makes the selected discovery item terminal and prevents
                # it from being selected again; it is not an observation.
                self._append_event(
                    uow, record.frontier_id, "FAILED_TERMINAL", now, reason_code=stop_reason
                )
                uow.commit()
            return SurfaceDiscoveryCycleResult(
                research_run_id,
                stop_reason,
                record.frontier_id,
                experiment_id,
                worker_invoked,
            )
        if loop.status is ResearchLoopStatus.UNKNOWN_OUTCOME:
            return SurfaceDiscoveryCycleResult(
                research_run_id, "UNKNOWN_OUTCOME", record.frontier_id, experiment_id, True
            )
        reauthorization_explicit_oos = False
        reauthorization_resolved = False
        reauthorization_followup = None
        if (
            loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED
            and start.compiled_scope is not None
            and isinstance(loop.reauthorization_request, Mapping)
        ):
            reauthorization_check = evaluate_reauthorization_request(
                loop.reauthorization_request, start.compiled_scope
            )
            reauthorization_explicit_oos = _explicit_out_of_scope(reauthorization_check)
            reauthorization_followup = compile_allowed_same_origin_redirect_frontier(
                loop.reauthorization_request,
                reauthorization_check,
                record=record,
                normalized_origin=start.config.normalized_origin,
            )
        with self._uow_factory.open() as uow:
            reauthorization_control_event_id = None
            for result in uow.worker_results.list_for_research_run(research_run_id):
                control_event = ingest_control_event_from_worker_result(
                    uow, result, created_at=now, target_reference=target_reference
                )
                if (
                    loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED
                    and loop.worker_result_id is not None
                    and result.worker_result_id == loop.worker_result_id
                    and control_event is not None
                ):
                    reauthorization_control_event_id = control_event.control_event_id
            reconcile_missing_projections(
                uow, research_run_id, created_at=now, target_reference=target_reference
            )
            if loop.status is ResearchLoopStatus.OBSERVATION_PRODUCED:
                self._append_event(uow, record.frontier_id, "OBSERVED", now)
                for observation in uow.observations.list_for_experiment(experiment_id):
                    self._remember_live_page(
                        research_run_id, observation, identity_id=record.identity_id
                    )
            else:
                if record.proposed_capability == BROWSER_PAGE_CAPABILITY:
                    self._drop_live_pages(
                        research_run_id,
                        origin=record.candidate_origin.rstrip("/"),
                        identity_id=record.identity_id,
                    )
                if loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED:
                    if reauthorization_explicit_oos:
                        uow.experiments.set_execution_state(
                            experiment_id, ExperimentExecutionState.BLOCKED.value
                        )
                        self._append_event(
                            uow,
                            record.frontier_id,
                            "BLOCKED_SCOPE",
                            now,
                            reason_code="REAUTHORIZATION_TARGET_OUT_OF_SCOPE",
                        )
                    elif (
                        reauthorization_followup is not None
                        and reauthorization_control_event_id is not None
                    ):
                        # The original request has already stopped at the redirect.
                        # Close that control branch; never redispatch its attempt.
                        uow.experiments.set_execution_state(
                            experiment_id, ExperimentExecutionState.BLOCKED.value
                        )
                        self._append_event(
                            uow,
                            record.frontier_id,
                            "AWAITING_REAUTHORIZATION",
                            now,
                            reason_code="CORE_REEVALUATION_REQUIRED",
                        )
                        existing_followup = next(
                            (
                                item
                                for item in uow.frontier_items.list_for_research_run(
                                    research_run_id
                                )
                                if item.dedupe_identity
                                == reauthorization_followup.dedupe_identity
                            ),
                            None,
                        )
                        if existing_followup is None:
                            self._insert_control_frontier(
                                uow,
                                reauthorization_followup,
                                created_at=now,
                                control_event_id=reauthorization_control_event_id,
                            )
                        self._append_event(
                            uow,
                            record.frontier_id,
                            "SUPERSEDED",
                            now,
                            reason_code="REAUTHORIZATION_ALLOWED_FRESH_WORK",
                        )
                        reauthorization_resolved = True
                    else:
                        self._append_event(
                            uow, record.frontier_id, "AWAITING_REAUTHORIZATION", now
                        )
                elif loop.status is ResearchLoopStatus.DISPATCH_DENIED:
                    self._append_event(
                        uow, record.frontier_id, "BLOCKED_AUTH", now, reason_code="CORE_DENIED"
                    )
                elif loop.status is ResearchLoopStatus.HUMAN_REVIEW_REQUIRED:
                    self._append_event(
                        uow,
                        record.frontier_id,
                        "AWAITING_REAUTHORIZATION",
                        now,
                        reason_code="HUMAN_REVIEW_REQUIRED",
                    )
                else:
                    self._append_event(
                        uow,
                        record.frontier_id,
                        "FAILED_TERMINAL",
                        now,
                        reason_code=loop.status.value,
                    )
            bound_stop = _bound_stop_reason(uow, start.config)
            from zest.application.discovery.lifecycle import (
                apply_discovery_exit_dispositions,
                discovery_exit_audit,
            )

            apply_discovery_exit_dispositions(uow, research_run_id, created_at=now)
            eligible_after = _snapshot_eligible_count(uow, research_run_id)
            audit = discovery_exit_audit(uow, research_run_id, considered=eligible_after == 0)
            observation_id = None
            if loop.status is ResearchLoopStatus.OBSERVATION_PRODUCED:
                observations = uow.observations.list_for_experiment(experiment_id)
                if observations:
                    observation_id = observations[-1].observation_id
            uow.commit()
        if loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED:
            reauthorization_stop_reason = (
                "BLOCKED_SCOPE"
                if reauthorization_explicit_oos
                else (
                    bound_stop
                    if reauthorization_resolved
                    else "REAUTHORIZATION_REQUIRED"
                )
            )
            return SurfaceDiscoveryCycleResult(
                research_run_id,
                reauthorization_stop_reason,
                record.frontier_id,
                experiment_id,
                worker_invoked,
                eligible_before=eligible_before,
                eligible_after=eligible_after,
                selected_goal_kind=record.goal_kind,
                selected_path=record.candidate_path,
                compiled_capability=plan.required_capability,
                worker_status=loop.status.value,
                observation_id=observation_id,
                discovery_exhausted=(
                    audit.discovery_exit_allowed
                    if reauthorization_resolved
                    and reauthorization_stop_reason is None
                    else False
                ),
            )
        if loop.status is ResearchLoopStatus.HUMAN_REVIEW_REQUIRED:
            return SurfaceDiscoveryCycleResult(
                research_run_id,
                "HUMAN_REVIEW_REQUIRED",
                record.frontier_id,
                experiment_id,
                worker_invoked,
                eligible_before=eligible_before,
                eligible_after=eligible_after,
                selected_goal_kind=record.goal_kind,
                selected_path=record.candidate_path,
                compiled_capability=plan.required_capability,
                worker_status=loop.status.value,
                observation_id=observation_id,
                discovery_exhausted=False,
            )
        if bound_stop is not None:
            return SurfaceDiscoveryCycleResult(
                research_run_id,
                bound_stop,
                record.frontier_id,
                experiment_id,
                worker_invoked,
                eligible_before=eligible_before,
                eligible_after=eligible_after,
                selected_goal_kind=record.goal_kind,
                selected_path=record.candidate_path,
                compiled_capability=plan.required_capability,
                worker_status=loop.status.value,
                observation_id=observation_id,
            )
        return SurfaceDiscoveryCycleResult(
            research_run_id,
            None,
            record.frontier_id,
            experiment_id,
            worker_invoked,
            eligible_before=eligible_before,
            eligible_after=eligible_after,
            selected_goal_kind=record.goal_kind,
            selected_path=record.candidate_path,
            compiled_capability=plan.required_capability,
            worker_status=loop.status.value,
            observation_id=observation_id,
            discovery_exhausted=audit.discovery_exit_allowed,
        )

    def _seed(self, uow, start: SurfaceDiscoveryStart, *, created_at) -> None:
        config = start.config
        item = seed_inspect_path_frontier(config, frontier_id=new_opaque_id())
        self._insert_frontier(uow, item, created_at=created_at, seed_run_id=config.research_run_id)
        sessions = dict(start.session_context_by_identity or {})
        allowed = config.bounds.max_identity_variants
        seeded = 0
        for identity in start.identities:
            if identity == ANONYMOUS_IDENTITY_ID:
                continue
            if allowed == 0 or seeded >= allowed:
                break
            signature = canonical_key(
                "OBSERVE_UNDER_IDENTITY",
                config.normalized_origin,
                config.normalized_path,
                identity,
            )
            variant = FrontierItem(
                frontier_id=new_opaque_id(),
                research_run_id=config.research_run_id,
                goal_kind=DiscoveryGoalKind.OBSERVE_UNDER_IDENTITY,
                candidate_origin=config.normalized_origin,
                candidate_path=config.normalized_path,
                identity_id=identity,
                proposed_capability=BROWSER_PAGE_CAPABILITY,
                proposed_action=BROWSER_PAGE_OBSERVE_ACTION,
                expected_side_effect=0,
                budget_class=0,
                structural_signature=signature,
                dedupe_identity=signature,
                session_context_id=sessions.get(identity),
                scope_hint="configured_identity_variant",
            )
            self._insert_frontier(
                uow, variant, created_at=created_at, seed_run_id=config.research_run_id
            )
            seeded += 1

    def _insert_frontier(self, uow, item: FrontierItem, *, created_at, seed_run_id: str) -> None:
        uow.frontier_items.insert(
            FrontierItemRecord(
                frontier_id=item.frontier_id,
                research_run_id=item.research_run_id,
                strategy_version=item.strategy_version,
                goal_kind=item.goal_kind.value,
                candidate_origin=item.candidate_origin,
                candidate_path=item.candidate_path,
                identity_id=item.identity_id,
                proposed_capability=item.proposed_capability,
                proposed_action=item.proposed_action,
                expected_side_effect=item.expected_side_effect,
                budget_class=item.budget_class,
                structural_signature=item.structural_signature,
                dedupe_identity=item.dedupe_identity,
                created_at=created_at,
                session_context_id=item.session_context_id,
                scope_hint=item.scope_hint,
                attributes=item.attributes,
                current_state="ELIGIBLE",
                state_version=2,
            )
        )
        uow.frontier_sources.insert(
            FrontierSourceRecord(
                source_row_id=new_opaque_id(),
                research_run_id=item.research_run_id,
                frontier_id=item.frontier_id,
                created_at=created_at,
                seed_config_run_id=seed_run_id,
            )
        )
        self._append_event(uow, item.frontier_id, "CREATED", created_at, sequence=1)
        self._append_event(uow, item.frontier_id, "ELIGIBLE", created_at, sequence=2)

    def _insert_control_frontier(
        self,
        uow,
        item: FrontierItem,
        *,
        created_at,
        control_event_id: str,
    ) -> None:
        """Persist fresh work derived from a control event, not from seed authority."""

        uow.frontier_items.insert(
            FrontierItemRecord(
                frontier_id=item.frontier_id,
                research_run_id=item.research_run_id,
                strategy_version=item.strategy_version,
                goal_kind=item.goal_kind.value,
                candidate_origin=item.candidate_origin,
                candidate_path=item.candidate_path,
                identity_id=item.identity_id,
                proposed_capability=item.proposed_capability,
                proposed_action=item.proposed_action,
                expected_side_effect=item.expected_side_effect,
                budget_class=item.budget_class,
                structural_signature=item.structural_signature,
                dedupe_identity=item.dedupe_identity,
                created_at=created_at,
                session_context_id=item.session_context_id,
                scope_hint=item.scope_hint,
                attributes=item.attributes,
                current_state="ELIGIBLE",
                state_version=2,
            )
        )
        uow.frontier_sources.insert(
            FrontierSourceRecord(
                source_row_id=new_opaque_id(),
                research_run_id=item.research_run_id,
                frontier_id=item.frontier_id,
                created_at=created_at,
                control_event_id=control_event_id,
            )
        )
        self._append_event(uow, item.frontier_id, "CREATED", created_at, sequence=1)
        self._append_event(uow, item.frontier_id, "ELIGIBLE", created_at, sequence=2)

    def _ensure_hypothesis(self, uow, research_run_id: str, *, created_at) -> None:
        existing = uow.hypotheses.list_for_research_run(research_run_id)
        if existing:
            return
        uow.hypotheses.insert(
            HypothesisRecord(
                hypothesis_id=new_opaque_id(),
                research_run_id=research_run_id,
                claim=DISCOVERY_HYPOTHESIS_CLAIM,
                created_at=created_at,
                origin_reference="surface-discovery-v1",
            )
        )

    def _block(self, uow, frontier_id: str, kind: str, created_at) -> None:
        self._append_event(uow, frontier_id, kind, created_at)

    def _reobserve(self, uow, record: FrontierItemRecord, *, created_at) -> None:
        config_record = uow.discovery_run_configs.get(record.research_run_id)
        assert config_record is not None
        bounds = config_from_record(config_record).bounds
        existing = uow.frontier_items.list_for_research_run(record.research_run_id)
        same_route = [
            item
            for item in existing
            if item.goal_kind == DiscoveryGoalKind.INSPECT_PATH.value
            and item.identity_id == record.identity_id
            and item.candidate_origin == record.candidate_origin
            and item.candidate_path == record.candidate_path
        ]
        if len(same_route) >= bounds.max_per_route_revisit:
            self._append_event(uow, record.frontier_id, "NO_NEW_INFORMATION", created_at)
            return
        signature = canonical_key(
            "INSPECT_PATH",
            record.candidate_origin,
            record.candidate_path,
            record.identity_id,
            str(len(same_route)),
        )
        if any(item.dedupe_identity == signature for item in existing):
            self._append_event(uow, record.frontier_id, "NO_NEW_INFORMATION", created_at)
            return
        self._append_event(uow, record.frontier_id, "FAILED_TRANSIENT", created_at)
        observations = uow.observations.list_for_research_run(record.research_run_id)
        observation_id = observations[-1].observation_id if observations else None
        inspect_id = new_opaque_id()
        uow.frontier_items.insert(
            FrontierItemRecord(
                frontier_id=inspect_id,
                research_run_id=record.research_run_id,
                strategy_version=record.strategy_version,
                goal_kind=DiscoveryGoalKind.INSPECT_PATH.value,
                candidate_origin=record.candidate_origin,
                candidate_path=record.candidate_path,
                identity_id=record.identity_id,
                proposed_capability=BROWSER_PAGE_CAPABILITY,
                proposed_action=BROWSER_PAGE_OBSERVE_ACTION,
                expected_side_effect=0,
                budget_class=0,
                structural_signature=signature,
                dedupe_identity=signature,
                created_at=created_at,
                session_context_id=record.session_context_id,
                current_state="ELIGIBLE",
                state_version=2,
            )
        )
        uow.frontier_sources.insert(
            FrontierSourceRecord(
                source_row_id=new_opaque_id(),
                research_run_id=record.research_run_id,
                frontier_id=inspect_id,
                created_at=created_at,
                observation_id=observation_id,
                seed_config_run_id=None if observation_id else record.research_run_id,
            )
        )
        self._append_event(uow, inspect_id, "CREATED", created_at, sequence=1)
        self._append_event(uow, inspect_id, "ELIGIBLE", created_at, sequence=2)
        self._append_event(uow, record.frontier_id, "ELIGIBLE", created_at)

    def _remember_live_page(
        self, research_run_id: str, observation: ObservationRecord, *, identity_id: str
    ) -> None:
        payload = dict(observation.payload)
        origin = str(payload.get("authorized_origin") or "")
        path = str(payload.get("path") or "/")
        normalized = payload.get("normalized_url")
        if isinstance(normalized, str) and "://" in normalized:
            from urllib.parse import urlsplit

            parsed = urlsplit(normalized)
            origin = f"{parsed.scheme}://{parsed.hostname}"
            if parsed.port:
                origin = f"{origin}:{parsed.port}"
            path = parsed.path or "/"
        origin = origin.rstrip("/")
        if not path.startswith("/"):
            path = "/" + path
        fingerprint = payload.get("snapshot_fingerprint")
        context_ref = payload.get("browser_context_reference")
        page_ref = payload.get("page_reference")
        if not isinstance(fingerprint, str) or not isinstance(context_ref, str) or not isinstance(page_ref, str):
            return
        controls = []
        for item in payload.get("controls") or []:
            if not isinstance(item, dict):
                continue
            element_ref = item.get("element_reference")
            if not isinstance(element_ref, str):
                continue
            controls.append(
                LiveControlView(
                    element_reference=element_ref,
                    snapshot_fingerprint=str(item.get("snapshot_fingerprint") or fingerprint),
                    tag=str(item.get("tag") or ""),
                    name=str(item.get("name") or ""),
                    role=str(item.get("role") or ""),
                    input_type=str(item.get("input_type") or ""),
                )
            )
        self._live_pages = {
            key: value
            for key, value in self._live_pages.items()
            if value.browser_context_reference != context_ref
        }
        self._live_pages[(research_run_id, origin, path or "/", identity_id)] = LivePageSnapshot(
            snapshot_fingerprint=fingerprint,
            browser_context_reference=context_ref,
            page_reference=page_ref,
            controls=tuple(controls),
        )

    def _append_event(
        self,
        uow,
        frontier_id: str,
        kind: str,
        created_at,
        sequence: int | None = None,
        *,
        reason_code: str | None = None,
    ) -> None:
        existing = uow.frontier_events.list_for_frontier(frontier_id)
        research_run_id = (
            existing[0].research_run_id
            if existing
            else uow.frontier_items.get(frontier_id).research_run_id
        )
        next_sequence = sequence or (existing[-1].sequence + 1 if existing else 1)
        uow.frontier_events.insert(
            FrontierEventRecord(
                event_id=new_opaque_id(),
                frontier_id=frontier_id,
                research_run_id=research_run_id,
                event_kind=kind,
                sequence=next_sequence,
                created_at=created_at,
                reason_code=reason_code,
            )
        )
        uow.frontier_items.set_cache_state(frontier_id, kind, next_sequence)


def _explicit_out_of_scope(check) -> bool:
    """Only an explicit frozen-scope exclusion may be auto-denied.

    No matching rule is UNKNOWN in CompiledScope and therefore does not satisfy
    this predicate. Expired/ambiguous scope is human-gated, never auto-allowed.
    """

    return (
        check.decision is ScopeDecision.DENY
        and check.classification is ScopeClassification.OUT_OF_SCOPE
        and bool(check.matched_rule_ids)
    )


def _known_control_destination_is_explicit_oos(
    record: FrontierItemRecord, compiled_scope: CompiledScope | None
) -> bool:
    if compiled_scope is None or record.goal_kind != DiscoveryGoalKind.INSPECT_CONTROL.value:
        return False
    attributes = record.attributes if isinstance(record.attributes, Mapping) else {}
    href_origin = str(attributes.get("href_origin") or "").rstrip("/")
    if not href_origin.startswith(("http://", "https://")):
        return False
    href_path = str(attributes.get("href_path") or "/")
    if not href_path.startswith("/"):
        href_path = "/" + href_path
    candidate = normalize_url(href_origin + href_path)
    return _explicit_out_of_scope(evaluate_scope_candidate(candidate, compiled_scope))


def _bound_stop_reason(uow, config: DiscoveryRunConfig) -> str | None:
    """0 means no allowance. Counts already-committed work before the next grant."""

    bounds = config.bounds
    research_run_id = config.research_run_id
    cycles = len(uow.experiments.list_for_research_run(research_run_id))
    if cycles >= bounds.max_discovery_cycles:
        return "MAX_DISCOVERY_CYCLES"
    items = uow.frontier_items.list_for_research_run(research_run_id)
    if len(items) >= bounds.max_frontier_items:
        return "MAX_FRONTIER_ITEMS"
    results = uow.worker_results.list_for_research_run(research_run_id)
    browser = sum(1 for item in results if item.worker_capability == BROWSER_PAGE_CAPABILITY)
    if browser >= bounds.max_browser_actions:
        return "MAX_BROWSER_ACTIONS"
    http = sum(1 for item in results if item.worker_capability == HTTP_TRANSACTION_CAPABILITY)
    if http >= bounds.max_http_transactions:
        return "MAX_HTTP_TRANSACTIONS"
    return None


def _eligible_count(domain_items, events_by) -> int:
    return len(
        runnable_discovery_frontier_items(
            domain_items, events_by, max_side_effect=SURFACE_DISCOVERY_MAX_SIDE_EFFECT
        )
    )


def _snapshot_eligible_count(uow, research_run_id: str) -> int:
    items = uow.frontier_items.list_for_research_run(research_run_id)
    events_by = {
        item.frontier_id: tuple(
            _domain_events(uow.frontier_events.list_for_frontier(item.frontier_id))
        )
        for item in items
    }
    return _eligible_count(tuple(_domain_item(item) for item in items), events_by)


def _domain_item(record: FrontierItemRecord) -> FrontierItem:
    return FrontierItem(
        frontier_id=record.frontier_id,
        research_run_id=record.research_run_id,
        goal_kind=DiscoveryGoalKind(record.goal_kind),
        candidate_origin=record.candidate_origin,
        candidate_path=record.candidate_path,
        identity_id=record.identity_id,
        proposed_capability=record.proposed_capability,
        proposed_action=record.proposed_action,
        expected_side_effect=record.expected_side_effect,
        budget_class=record.budget_class,
        structural_signature=record.structural_signature,
        dedupe_identity=record.dedupe_identity,
        strategy_version=record.strategy_version,
        session_context_id=record.session_context_id,
        scope_hint=record.scope_hint,
        attributes=record.attributes,
    )


def _domain_events(records: list[FrontierEventRecord]) -> list[FrontierEvent]:
    return [
        FrontierEvent(
            event_id=item.event_id,
            frontier_id=item.frontier_id,
            research_run_id=item.research_run_id,
            event_kind=FrontierEventKind(item.event_kind),
            sequence=item.sequence,
            selection_generation=item.selection_generation,
            execution_attempt_id=item.execution_attempt_id,
            reason_code=item.reason_code,
        )
        for item in records
    ]
