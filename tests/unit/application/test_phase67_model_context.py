"""Phase 6.7 Model Context / Reasoning Context production acceptance."""

from __future__ import annotations

import json
import unittest

import pathsetup  # noqa: F401

from zest.application.pack_research_reasoning_context import (
    MODEL_CONTEXT_NOT_CONNECTED,
    pack_research_reasoning_context,
)
from zest.application.propose_research_hypothesis import (
    ProposeResearchHypothesis,
    ProposeResearchHypothesisCommand,
)
from zest.research.admission import AdmissionOutcome
from zest.research.context import (
    ContextBudget,
    ObservationSource,
    ResearchContextBuilder,
)
from zest.research.cycle import context_model_payload
from zest.research.exploration import (
    DiagnosticOpportunitySources,
    OpportunityKind,
    propose_diagnostic_opportunities,
)
from zest.research.model_context_census import (
    CONNECTED_MODEL_CONTEXT_ENGINES,
    MODEL_CONTEXT_COMPONENTS,
    MODEL_CONTEXT_SOURCE,
)
from zest.research.orchestration import OrchestrationState
from zest.safe_data import REDACTED
from support.fake_model import ScriptedModelPort, default_generator_output
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run


class FixedClock:
    def now(self):
        return CREATED_AT


def _command(**overrides) -> ProposeResearchHypothesisCommand:
    values = dict(
        research_run_id="run-1",
        research_question="Does the diagnostic capability return the submitted value?",
        budget_id="budget-1",
        target_reference="target-1",
        correlation_id="corr-phase67",
    )
    values.update(overrides)
    return ProposeResearchHypothesisCommand(**values)


class Phase67ModelContextTests(unittest.TestCase):
    def test_r1_model_census(self) -> None:
        self.assertEqual(MODEL_CONTEXT_SOURCE, "CONNECTED")
        names = {item["name"] for item in MODEL_CONTEXT_COMPONENTS}
        self.assertIn("unified_reasoning_context_packer", names)
        self.assertIn("hypothesis_admission", names)
        self.assertTrue(all(item["status"] == "CONNECTED" for item in MODEL_CONTEXT_COMPONENTS))

    def test_r2_full_engine_context(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        with FakeUnitOfWorkFactory(store=store).open() as uow:
            packed = pack_research_reasoning_context(
                uow, research_run_id="run-1", research_question="q"
            )
            uow.rollback()
        engines = {item.engine for item in packed.engine_signals}
        for engine in CONNECTED_MODEL_CONTEXT_ENGINES:
            self.assertIn(engine, engines)
        self.assertEqual(packed.not_connected, MODEL_CONTEXT_NOT_CONNECTED)
        self.assertEqual(MODEL_CONTEXT_NOT_CONNECTED, 0)

    def test_r3_redaction(self) -> None:
        context = ResearchContextBuilder().build(
            research_run_id="run-1",
            research_question="q",
            observations=(
                ObservationSource(
                    observation_id="obs-secret",
                    observation_kind="http.transaction.result",
                    payload={
                        "cookie": "session=SUPERSECRET",
                        "password": "hunter2",
                        "authorization": "Bearer secret-token",
                    },
                ),
            ),
        )
        blob = json.dumps(context_model_payload(context), default=str)
        self.assertNotIn("SUPERSECRET", blob)
        self.assertNotIn("hunter2", blob)
        self.assertNotIn("secret-token", blob)
        item = context.item_by_id("obs-secret")
        assert item is not None
        assert item.payload is not None
        self.assertEqual(item.payload["cookie"], REDACTED)
        self.assertEqual(item.payload["password"], REDACTED)

    def test_r4_bounding_is_deterministic(self) -> None:
        observations = tuple(
            ObservationSource(
                observation_id=f"obs-{index:02d}",
                observation_kind="diagnostic.echo.result",
                payload={"n": index},
            )
            for index in range(20, 0, -1)
        )
        first = ResearchContextBuilder().build(
            research_run_id="run-1",
            research_question="q",
            observations=observations,
            budget=ContextBudget(max_observation_items=8),
        )
        second = ResearchContextBuilder().build(
            research_run_id="run-1",
            research_question="q",
            observations=observations,
            budget=ContextBudget(max_observation_items=8),
        )
        self.assertEqual(len(first.observations), 8)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.omission.omitted_observation_ids, second.omission.omitted_observation_ids)
        self.assertTrue(first.omission.omitted_observation_ids)

    def test_r5_provenance(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        with FakeUnitOfWorkFactory(store=store).open() as uow:
            packed = pack_research_reasoning_context(
                uow, research_run_id="run-1", research_question="q"
            )
            context = ResearchContextBuilder().build(
                research_run_id="run-1",
                research_question="q",
                engine_signals=packed.engine_signals,
            )
            uow.rollback()
        for item in context.engine_signals:
            self.assertTrue(item.source_references)
            self.assertTrue(item.item_id.startswith("eng:"))

    def test_r6_authority_awareness(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        with FakeUnitOfWorkFactory(store=store).open() as uow:
            packed = pack_research_reasoning_context(
                uow, research_run_id="run-1", research_question="q"
            )
            uow.rollback()
        authority = next(item for item in packed.engine_signals if item.engine == "AUTHORITY")
        self.assertIn("side_effect_ceiling", authority.payload)
        self.assertTrue(authority.payload["core_deny_is_not_coverage"])
        protocol = next(item for item in packed.engine_signals if item.engine == "PROTOCOL")
        self.assertTrue(protocol.payload["core_deny_is_not_coverage"])

    def test_r7_unknown_not_converted_to_safe(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        with FakeUnitOfWorkFactory(store=store).open() as uow:
            packed = pack_research_reasoning_context(
                uow, research_run_id="run-1", research_question="q"
            )
            uow.rollback()
        invariant = next(item for item in packed.engine_signals if item.engine == "INVARIANT")
        self.assertTrue(invariant.payload["unknown_is_not_safe"])
        oast = next(item for item in packed.engine_signals if item.engine == "OAST")
        self.assertTrue(oast.payload["no_callback_is_not_global_safe"])
        blob = " ".join(item.statement.lower() for item in packed.engine_signals)
        self.assertNotIn("proven safe", blob)

    def test_r8_r9_r10_hypothesis_falsification_admission(self) -> None:
        store = _Store()
        seed_authorization_run(store)

        def generator(request):
            payload = dict(default_generator_output(request))
            engines = request.payload["research_context"]["engine_signals"]
            ids = [item["item_id"] for item in engines if isinstance(item, dict)]
            payload["source_references"] = ids[:3] + ["proc:research-question"]
            payload["proposed_claim"] = (
                "Target-specific next research move uses multi-engine context "
                + ",".join(ids[:2])
            )
            return payload

        result = ProposeResearchHypothesis(
            FakeUnitOfWorkFactory(store=store),
            ScriptedModelPort(generator=generator),
            clock=FixedClock(),
        ).execute(_command())
        self.assertEqual(result.outcome, AdmissionOutcome.ADMITTED)
        self.assertIsNotNone(result.hypothesis_id)
        self.assertIsNotNone(result.experiment_plan)
        self.assertIn("multi-engine", result.admission.proposal.proposed_claim)
        self.assertTrue(result.admission.challenge.alternative_explanations)
        engines = {item.payload["engine"] for item in result.context.engine_signals}
        self.assertTrue(set(CONNECTED_MODEL_CONTEXT_ENGINES) <= engines)

    def test_r11_admission_fail_persists_reason(self) -> None:
        store = _Store()
        seed_authorization_run(store)

        def hallucinate(request):
            payload = dict(default_generator_output(request))
            payload["source_references"] = ["obs:does-not-exist"]
            payload["proposed_claim"] = "unsupported model-only vulnerability"
            return payload

        result = ProposeResearchHypothesis(
            FakeUnitOfWorkFactory(store=store),
            ScriptedModelPort(generator=hallucinate),
            clock=FixedClock(),
        ).execute(_command())
        self.assertEqual(result.outcome, AdmissionOutcome.NEEDS_MORE_CONTEXT)
        self.assertEqual(store.hypotheses, {})
        admission = next(iter(store.research_admissions.values()))
        self.assertEqual(admission.reason_code, "HALLUCINATED_SOURCE")
        self.assertIsNone(admission.admitted_hypothesis_id)

    def test_r12_scheduler_handoff(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        result = ProposeResearchHypothesis(
            FakeUnitOfWorkFactory(store=store),
            ScriptedModelPort(),
            clock=FixedClock(),
        ).execute(_command())
        self.assertEqual(result.outcome, AdmissionOutcome.ADMITTED)
        generated = propose_diagnostic_opportunities(
            "run-1",
            DiagnosticOpportunitySources(hypothesis_ids=(result.hypothesis_id,)),
            id_prefix="phase67",
        )
        kinds = {item.opportunity_kind for item in generated}
        self.assertIn(OpportunityKind.HYPOTHESIS_FOLLOWUP, kinds)

    def test_r13_no_direct_dispatch_or_finding(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        ProposeResearchHypothesis(
            FakeUnitOfWorkFactory(store=store),
            ScriptedModelPort(),
            clock=FixedClock(),
        ).execute(_command())
        self.assertEqual(store.worker_results, {})
        self.assertEqual(store.findings, {})
        self.assertEqual(store.finding_proposals, {})
        self.assertEqual(store.evidence, {})

    def test_r14_recovery_does_not_duplicate(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        use_case = ProposeResearchHypothesis(
            FakeUnitOfWorkFactory(store=store),
            ScriptedModelPort(),
            clock=FixedClock(),
        )
        first = use_case.execute(_command())
        second = use_case.execute(_command())
        self.assertEqual(first.outcome, AdmissionOutcome.ADMITTED)
        self.assertEqual(second.outcome, AdmissionOutcome.ADMITTED)
        self.assertEqual(second.generator_calls, 0)
        self.assertEqual(len(store.research_admissions), 1)
        self.assertEqual(len(store.research_reasoning), 2)
        self.assertEqual(len(store.hypotheses), 1)
        self.assertEqual(first.hypothesis_id, second.hypothesis_id)

    def test_r15_model_cannot_force_completion(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        ProposeResearchHypothesis(
            FakeUnitOfWorkFactory(store=store),
            ScriptedModelPort(),
            clock=FixedClock(),
        ).execute(_command())
        orchestration = store.research_orchestrations.get("run-1")
        self.assertTrue(
            orchestration is None
            or orchestration.state != OrchestrationState.COMPLETED.value
        )
        completion = None
        with FakeUnitOfWorkFactory(store=store).open() as uow:
            packed = pack_research_reasoning_context(
                uow, research_run_id="run-1", research_question="q"
            )
            uow.rollback()
        completion = next(item for item in packed.engine_signals if item.engine == "COMPLETION")
        self.assertTrue(completion.payload["model_cannot_force_completion"])


if __name__ == "__main__":
    unittest.main()
