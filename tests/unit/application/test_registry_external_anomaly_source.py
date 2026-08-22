from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from research_os.application.registry_external_anomaly_source import (
    SOURCE_SYSTEM,
    admit_registry_external_anomaly_candidates,
    load_identity_anomaly_context,
)
from research_os.application.select_research_opportunities import (
    SelectResearchOpportunities,
    SelectResearchOpportunitiesCommand,
)
from research_os.data.records import (
    DiscoveryFactRecord,
    DiscoveryFactSourceRecord,
    HunterFamilyRecord,
    ObservationRecord,
    WorkerResultRecord,
)
from research_os.research.exploration import OpportunityKind, ResearchPolicyBudget
from research_os.research.identity_anomaly import IDENTITY_ANOMALY_STRATEGY_VERSION, IdentityAnomalyClass
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run


class FixedClock:
    def now(self):
        return CREATED_AT


def _worker_result(
    *,
    worker_result_id: str = "wr-1",
    research_run_id: str = "run-1",
    experiment_id: str = "exp-source",
) -> WorkerResultRecord:
    return WorkerResultRecord(
        worker_result_id=worker_result_id,
        experiment_id=experiment_id,
        research_run_id=research_run_id,
        request_id=f"req-{worker_result_id}",
        correlation_id=f"corr-{worker_result_id}",
        worker_capability="http.authorization.differential",
        action="probe",
        authorization_decision_reference="authz-1",
        budget_id="budget-1",
        side_effect_level=0,
        contract_version="v1",
        worker_id="local-python-http",
        status="SUCCEEDED",
        received_at=CREATED_AT,
    )


def _authz_observation(
    *,
    observation_id: str = "obs-1",
    worker_result_id: str = "wr-1",
    cross_status: int = 200,
    actor: str = "alice",
    own_object: str = "alice",
    cross_object: str = "bob",
    origin: str = "http://127.0.0.1:9",
) -> ObservationRecord:
    return ObservationRecord(
        observation_id=observation_id,
        worker_result_id=worker_result_id,
        observation_kind="HTTP_AUTHORIZATION_DIFFERENTIAL",
        payload={
            "authorized_origin": origin,
            "actor": actor,
            "own_object": own_object,
            "cross_object": cross_object,
            "mode": "vulnerable",
            "cross_object_request_status": cross_status,
        },
        normalization_version="http.authorization.differential.v1",
        observed_at=CREATED_AT,
        created_at=CREATED_AT,
    )


def _seed_identity_observation(store: _Store, *, cross_status: int = 200) -> None:
    seed_authorization_run(store)
    store.worker_results["wr-1"] = _worker_result()
    store.observations["obs-1"] = _authz_observation(cross_status=cross_status)


class RegistryExternalAnomalySourceTests(unittest.TestCase):
    def test_durable_identity_observation_emits_one_candidate(self) -> None:
        store = _Store()
        _seed_identity_observation(store)
        factory = FakeUnitOfWorkFactory(store)
        with factory.open() as uow:
            result = admit_registry_external_anomaly_candidates(
                uow, research_run_id="run-1", now=CREATED_AT, actor_id="control-plane"
            )
            uow.commit()
        self.assertEqual(result.candidates_created, 1)
        self.assertEqual(result.skipped_known_family, 0)
        (candidate,) = store.opportunity_selection_candidates.values()
        self.assertEqual(candidate.source_system, SOURCE_SYSTEM)
        self.assertEqual(
            candidate.opportunity_kind, OpportunityKind.REGISTRY_EXTERNAL_EXPLORATORY.value
        )
        self.assertEqual(candidate.source_refs, ("obs-1",))
        self.assertEqual(candidate.strategy_version, IDENTITY_ANOMALY_STRATEGY_VERSION)

    def test_same_source_is_deduplicated(self) -> None:
        store = _Store()
        _seed_identity_observation(store)
        factory = FakeUnitOfWorkFactory(store)
        with factory.open() as uow:
            first = admit_registry_external_anomaly_candidates(
                uow, research_run_id="run-1", now=CREATED_AT, actor_id="control-plane"
            )
            second = admit_registry_external_anomaly_candidates(
                uow, research_run_id="run-1", now=CREATED_AT, actor_id="control-plane"
            )
            uow.commit()
        self.assertEqual(first.candidates_created, 1)
        self.assertEqual(second.candidates_created, 0)
        self.assertEqual(second.skipped_duplicate, 1)
        self.assertEqual(len(store.opportunity_selection_candidates), 1)

    def test_same_identity_context_from_a_second_observation_is_one_candidate(self) -> None:
        store = _Store()
        _seed_identity_observation(store)
        store.worker_results["wr-2"] = _worker_result(
            worker_result_id="wr-2", experiment_id="exp-follow"
        )
        store.observations["obs-2"] = _authz_observation(
            observation_id="obs-2", worker_result_id="wr-2"
        )
        factory = FakeUnitOfWorkFactory(store)
        with factory.open() as uow:
            result = admit_registry_external_anomaly_candidates(
                uow, research_run_id="run-1", now=CREATED_AT, actor_id="control-plane"
            )
            uow.commit()
        self.assertEqual(result.candidates_created, 1)
        self.assertEqual(result.skipped_duplicate, 1)
        self.assertEqual(len(store.opportunity_selection_candidates), 1)

    def test_unresolved_source_is_rejected(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        with factory.open() as uow:
            context = load_identity_anomaly_context(
                uow, research_run_id="run-1", source_id="obs-does-not-exist"
            )
            result = admit_registry_external_anomaly_candidates(
                uow, research_run_id="run-1", now=CREATED_AT, actor_id="control-plane"
            )
            uow.commit()
        self.assertEqual(context.classification, IdentityAnomalyClass.REJECT_UNRESOLVED_SOURCE)
        self.assertEqual(result.candidates_created, 0)
        self.assertEqual(store.opportunity_selection_candidates, {})

    def test_cross_run_source_is_rejected(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        store.worker_results["wr-other"] = _worker_result(
            worker_result_id="wr-other", research_run_id="run-other"
        )
        store.observations["obs-cross"] = _authz_observation(
            observation_id="obs-cross", worker_result_id="wr-other"
        )
        factory = FakeUnitOfWorkFactory(store)
        with factory.open() as uow:
            result = admit_registry_external_anomaly_candidates(
                uow, research_run_id="run-1", now=CREATED_AT, actor_id="control-plane"
            )
            uow.commit()
        self.assertEqual(result.candidates_created, 0)
        self.assertEqual(store.opportunity_selection_candidates, {})

    def test_secure_cross_deny_is_not_an_opportunity(self) -> None:
        store = _Store()
        _seed_identity_observation(store, cross_status=403)
        factory = FakeUnitOfWorkFactory(store)
        with factory.open() as uow:
            result = admit_registry_external_anomaly_candidates(
                uow, research_run_id="run-1", now=CREATED_AT, actor_id="control-plane"
            )
            uow.commit()
        self.assertEqual(result.candidates_created, 0)
        self.assertEqual(store.opportunity_selection_candidates, {})

    def test_known_family_match_is_not_exploratory(self) -> None:
        store = _Store()
        _seed_identity_observation(store)
        store.hunter_families["hf-object-authz:1"] = HunterFamilyRecord(
            family_id="hf-object-authz",
            name="OBJECT_AUTHORIZATION",
            target_node_kinds=("HTTP_OPERATION", "RESOURCE_INSTANCE_CANDIDATE"),
            preconditions={"scope_classification": "IN_SCOPE"},
            claim_template="Object boundary may allow cross-owner access.",
            evidence_requirements={"required_observation_kinds": ["HTTP_AUTHORIZATION_DIFFERENTIAL"]},
            validation_tier="V3",
            enabled=True,
            version=1,
            created_at=CREATED_AT,
        )
        store.discovery_facts["fact-1"] = DiscoveryFactRecord(
            fact_id="fact-1",
            research_run_id="run-1",
            fact_kind="HTTP_OPERATION",
            canonical_key="GET http://127.0.0.1:9/accounts",
            epistemic_status="OBSERVED",
            identity_id="alice",
            target_reference="target-1",
            created_at=CREATED_AT,
            normalized_origin="http://127.0.0.1:9",
            normalized_path="/accounts",
            http_method="GET",
            attributes={"scope_classification": "IN_SCOPE"},
        )
        store.discovery_fact_sources["src-1"] = DiscoveryFactSourceRecord(
            source_row_id="src-1",
            research_run_id="run-1",
            fact_id="fact-1",
            created_at=CREATED_AT,
            observation_id="obs-1",
        )
        factory = FakeUnitOfWorkFactory(store)
        with factory.open() as uow:
            result = admit_registry_external_anomaly_candidates(
                uow, research_run_id="run-1", now=CREATED_AT, actor_id="control-plane"
            )
            uow.commit()
        self.assertEqual(result.candidates_created, 0)
        self.assertEqual(result.skipped_known_family, 1)
        self.assertEqual(store.opportunity_selection_candidates, {})
        self.assertTrue(
            any(
                event.event_type == "REGISTRY_EXTERNAL_ANOMALY_ROUTED_KNOWN_FAMILY"
                for event in store.audit_events.values()
            )
        )

    def test_select_admits_exploratory_opportunity_without_a_second_scheduler(self) -> None:
        store = _Store()
        _seed_identity_observation(store)
        selected = SelectResearchOpportunities(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(
            SelectResearchOpportunitiesCommand(
                research_run_id="run-1",
                budget=ResearchPolicyBudget(max_selected=1, max_exploratory=1),
            )
        )
        self.assertEqual(len(selected.selected), 1)
        opportunity = selected.selected[0].opportunity
        self.assertEqual(
            opportunity.opportunity_kind, OpportunityKind.REGISTRY_EXTERNAL_EXPLORATORY
        )
        self.assertEqual(opportunity.source_refs, ("obs-1",))
        (canonical,) = store.research_opportunities.values()
        self.assertEqual(canonical.opportunity_id, opportunity.opportunity_id)


if __name__ == "__main__":
    unittest.main()
