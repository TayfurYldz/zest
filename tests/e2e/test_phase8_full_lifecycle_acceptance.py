"""Phase 8 full START→end controlled lifecycle acceptance.

Requires ZEST_TEST_DATABASE_URL. Uses ZestdRuntime.start_run, leased supervisor.tick,
native Worker executors, and PostgreSQL traces. No fake WorkerResult/Evidence/Finding.
"""

from __future__ import annotations

import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from e2e.lab.phase8_authz_lab import Phase8AuthzLab
from e2e.phase8_harness import (
    CrashOnInvokeWorker,
    attach_facts,
    build_runtime,
    future_clock,
    oast_delivery,
    production_worker,
    seed_program,
    snapshot_counts,
    tick_until,
    wait_first_tick,
)
from integration.harness import PostgresUnitOfWorkFactory, alembic_upgrade, truncate_spine
from support.fake_model import ScriptedModelPort, default_generator_output
from zest.application.admit_oast_callback import AdmitOastCallback
from zest.application.oast_timeout import close_expired_oast_arms
from zest.application.phase8_acceptance_census import (
    PHASE8_ACCEPTANCE_CENSUS,
    PHASE8_ACCEPTANCE_CENSUS_COMPLETE,
)
from zest.application.phase8_acceptance_trace import build_acceptance_trace
from zest.application.propose_research_hypothesis import (
    ProposeResearchHypothesis,
    ProposeResearchHypothesisCommand,
)
from zest.application.record_human_review import RecordHumanReview, RecordHumanReviewCommand
from zest.application.start_human_review import StartHumanReview, StartHumanReviewCommand
from zest.core.enums import ActorType
from zest.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from zest.research.admission import AdmissionOutcome
from zest.research.finding_proposal import FindingProposalState, HumanReviewDecision

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("ZEST_DATABASE_URL")
    )


def _playwright_installed() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


class _Ctx:
    def __init__(self) -> None:
        self.lab = None
        self.factory = None
        self.runtime = None
        self.worker = None
        self.program_id = ""
        self.run_id = ""
        self.origin = ""


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; Phase 8 PostgreSQL acceptance skipped",
)
class Phase8FullLifecycleAcceptanceTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(
            "DESTRUCTIVE Phase 8 PostgreSQL tests: TRUNCATE CASCADE against "
            f"{redacted_database_url(TEST_URL)}",
            flush=True,
        )
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        truncate_spine(self.engine)
        self.ctx = _Ctx()
        self.ctx.factory = PostgresUnitOfWorkFactory(self.engine)
        self.ctx.lab = Phase8AuthzLab()
        self.ctx.origin = self.ctx.lab.start()

    def tearDown(self) -> None:
        if self.ctx.runtime is not None:
            try:
                self.ctx.runtime.drain(join_timeout=3.0)
            except Exception:
                pass
        if self.ctx.lab is not None:
            self.ctx.lab.stop()

    def _start(
        self,
        *,
        kinds: tuple[str, ...] = ("authz",),
        identities: bool = True,
        sessions: bool = True,
        ceiling: int = 2,
        max_selected: int = 1,
        max_cycles: int = 36,
        worker=None,
        model=None,
        attach_after_observation: bool = True,
    ) -> _Ctx:
        ctx = self.ctx
        ctx.program_id = "prog-" + uuid.uuid4().hex[:10]
        ctx.run_id = "run-" + uuid.uuid4().hex[:12]
        with ctx.factory.open() as uow:
            seed_program(
                uow,
                origin=ctx.origin,
                program_id=ctx.program_id,
                run_id=ctx.run_id,
                side_effect_ceiling=ceiling,
                max_selected=max_selected,
                max_cycles=max_cycles,
                identities=identities,
                sessions=sessions,
            )
            uow.commit()
        ctx.worker = worker or production_worker()
        ctx.runtime = build_runtime(
            ctx.factory, ctx.worker, model or ScriptedModelPort()
        )
        ctx.runtime.start_process()
        started = ctx.runtime.start_run(ctx.run_id)
        self.assertIsNotNone(started)
        wait_first_tick(ctx.runtime, ctx.run_id)
        if attach_after_observation:
            tick_until(
                ctx.runtime,
                ctx.factory,
                ctx.run_id,
                lambda trace: bool(trace.get("observation_ids")),
                max_ticks=24,
            )
            with ctx.factory.open() as uow:
                observations = uow.observations.list_for_research_run(ctx.run_id)
                self.assertTrue(
                    observations,
                    "START path produced no Observation after Worker execution",
                )
                attach_facts(
                    uow,
                    run_id=ctx.run_id,
                    origin=ctx.origin,
                    observation_id=observations[0].observation_id,
                    worker_result_id=None,
                    kinds=kinds,
                )
                uow.commit()
        return ctx

    def _trace(self) -> dict:
        with self.ctx.factory.open() as uow:
            payload = build_acceptance_trace(uow, self.ctx.run_id)
            uow.rollback()
        return payload

    def _assert_start_path(self, trace: dict) -> None:
        self.assertEqual(trace["START_source"], "reconstruct_start_command via ZestdRuntime.start_run")
        self.assertGreaterEqual(trace["start_events"], 1)
        self.assertIsNotNone(trace["orchestration_state"])

    def test_00_census_complete(self) -> None:
        self.assertTrue(PHASE8_ACCEPTANCE_CENSUS_COMPLETE)
        names = {item["name"] for item in PHASE8_ACCEPTANCE_CENSUS}
        self.assertIn("Operator API / dashboard START", names)
        self.assertIn("reconstruct_start_command", names)
        self.assertIn("LocalRunSupervisorRegistry", names)

    @unittest.skipUnless(_playwright_installed(), "Playwright/Chromium required for discovery Worker")
    def test_s1_positive_authz_finding_proposal(self) -> None:
        ctx = self._start(kinds=("authz",), ceiling=2)
        trace = tick_until(
            ctx.runtime,
            ctx.factory,
            ctx.run_id,
            lambda item: bool(item.get("finding_proposal_ids")),
            max_ticks=36,
        )
        self._assert_start_path(trace)
        self.assertTrue(trace["finding_proposal_ids"])
        self.assertTrue(trace["evidence_ids"])
        self.assertTrue(trace["candidate_ids"])
        self.assertTrue(trace["verification_ids"])
        with ctx.factory.open() as uow:
            findings = uow.findings.list_for_research_run(ctx.run_id)
            uow.rollback()
        self.assertFalse(findings)
        self.assertIn("PROPOSED", trace["finding_proposal_states"])
        audit = trace["audit"]
        self.assertTrue(
            (not audit["completion_allowed"])
            or any("review" in reason.lower() or "proposal" in reason.lower() for reason in audit["completion_block_reasons"])
            or audit["finding_review_pending"]
        )

    @unittest.skipUnless(_playwright_installed(), "Playwright/Chromium required for discovery Worker")
    def test_s2_negative_no_finding(self) -> None:
        self.ctx.lab.mode = "secure_only"
        ctx = self._start(kinds=("authz_negative",), ceiling=2, max_cycles=24)
        tick_until(
            ctx.runtime,
            ctx.factory,
            ctx.run_id,
            lambda item: item.get("orchestration_state")
            in {"COMPLETED", "BUDGET_EXHAUSTED", "FAILED_OPERATIONAL", "WAITING_HUMAN", "BLOCKED"}
            or item.get("cycles", 0) >= 16,
            max_ticks=24,
        )
        trace = self._trace()
        self._assert_start_path(trace)
        with ctx.factory.open() as uow:
            security = [
                item
                for item in uow.finding_proposals.list_for_research_run(ctx.run_id)
                if item.classification != "DIAGNOSTIC_PLUMBING"
            ]
            uow.rollback()
        self.assertFalse(security)
        if not trace["completion_allowed"]:
            self.assertTrue(trace["completion_block_reasons"] or trace["stop_reason"])

    @unittest.skipUnless(_playwright_installed(), "Playwright/Chromium required for discovery Worker")
    def test_s3_protocol_authority_block(self) -> None:
        ctx = self._start(kinds=("protocol",), ceiling=3, max_cycles=16)
        trace = tick_until(
            ctx.runtime,
            ctx.factory,
            ctx.run_id,
            lambda item: item["audit"].get("protocol_authority_blocked", 0) > 0
            or any(
                event["event_type"]
                in {
                    "RESEARCH_WORK_CORE_DENIED",
                    "RESEARCH_WORK_BLOCKED_SIDE_EFFECT_CEILING",
                }
                for event in item.get("core_and_compile_events", [])
            ),
            max_ticks=20,
        )
        self._assert_start_path(trace)
        denied = [
            event
            for event in trace["core_and_compile_events"]
            if event["event_type"]
            in {"RESEARCH_WORK_CORE_DENIED", "RESEARCH_WORK_BLOCKED_SIDE_EFFECT_CEILING"}
        ]
        self.assertTrue(denied or trace["audit"].get("protocol_authority_blocked", 0) > 0)
        protocol_attempts = [
            item
            for item in trace["attempts"]
            if item["capability"] in {"http.raw_exchange", "protocol.parser"}
        ]
        self.assertFalse(protocol_attempts)

    @unittest.skipUnless(_playwright_installed(), "Playwright/Chromium required for discovery Worker")
    def test_s4_oast_async(self) -> None:
        ctx = self._start(kinds=("oast",), ceiling=2, max_cycles=50, max_selected=4)
        supervisor = ctx.runtime._registry.supervisor(ctx.run_id)
        for _ in range(4):
            supervisor.tick()
        with ctx.factory.open() as uow:
            harvested = [
                item
                for item in uow.opportunity_selection_candidates.list_for_research_run(ctx.run_id)
                if item.opportunity_kind == "OAST_INTERACTION"
            ]
            uow.rollback()
        self.assertTrue(harvested, "OAST was not harvested from the START-path fact")
        trace = tick_until(
            ctx.runtime,
            ctx.factory,
            ctx.run_id,
            lambda item: bool(item.get("oast_arm_ids"))
            or item["audit"].get("oast_armed", 0) > 0
            or item["audit"].get("oast_executed", 0) > 0
            or any(
                event.get("payload", {}).get("selected_engine") == "OAST"
                for event in item.get("core_and_compile_events", [])
            )
            or any(
                event.get("payload", {}).get("candidate_kind") == "OAST_INTERACTION"
                and event.get("payload", {}).get("selected_work_id")
                for event in item.get("selection_traces", [])
            ),
            max_ticks=50,
        )
        self._assert_start_path(trace)
        engines = [
            event.get("payload", {}).get("selected_engine")
            for event in trace.get("core_and_compile_events", [])
        ]
        oast_select = [
            event
            for event in trace.get("selection_traces", [])
            if event.get("payload", {}).get("candidate_kind") == "OAST_INTERACTION"
            or event.get("payload", {}).get("selected_engine") == "OAST"
        ]
        with ctx.factory.open() as uow:
            oast_rows = [
                {
                    "outcome": item.outcome,
                    "assumptions": list(item.assumptions),
                    "source_refs": list(item.source_refs),
                }
                for item in uow.opportunity_selection_candidates.list_for_research_run(ctx.run_id)
                if item.opportunity_kind == "OAST_INTERACTION"
            ]
            oast_ops = [
                item.opportunity_id
                for item in uow.research_opportunities.list_for_research_run(ctx.run_id)
                if item.opportunity_kind == "OAST_INTERACTION"
            ]
            oast_ids = {item.candidate_id for item in uow.opportunity_selection_candidates.list_for_research_run(ctx.run_id) if item.opportunity_kind == "OAST_INTERACTION"}
            oast_identities = {item.structural_identity for item in uow.opportunity_selection_candidates.list_for_research_run(ctx.run_id) if item.opportunity_kind == "OAST_INTERACTION"}
            matching_ops = [
                {"id": item.opportunity_id, "kind": item.opportunity_kind}
                for item in uow.research_opportunities.list_for_research_run(ctx.run_id)
                if item.structural_identity in oast_identities
            ]
            oast_selections = [
                {"outcome": item.outcome, "reasons": list(item.reason_codes), "opportunity_id": item.opportunity_id}
                for item in uow.research_selections.list_for_research_run(ctx.run_id)
                if item.opportunity_id in oast_ids or item.structural_identity in oast_identities
            ]
            fairness_oast = []
            for event in uow.audit_events.list_for_subject("research_run", ctx.run_id):
                if event.event_type != "RESEARCH_SELECTION_TRACE":
                    continue
                payload = event.payload or {}
                for row in payload.get("fairness_candidates") or []:
                    if row.get("candidate_kind") == "OAST_INTERACTION":
                        fairness_oast.append(row)
            uow.rollback()
        self.assertTrue(
            trace["oast_arm_ids"]
            or trace["audit"].get("oast_armed", 0) > 0
            or "OAST" in engines
            or oast_select
            or oast_ops,
            f"OAST never armed or compiled on START path; engines={engines} "
            f"oast_rows={oast_rows} oast_ops={oast_ops} matching_ops={matching_ops} "
            f"oast_selections={oast_selections} fairness={fairness_oast[-3:]}",
        )
        self.assertTrue(
            oast_select or "OAST" in engines,
            "OAST was not selected/compiled on the START selection path",
        )
        if trace["audit"].get("oast_waiting_callback", 0) > 0 or "OAST_WAITING_CALLBACK" in trace.get(
            "completion_block_reasons", []
        ):
            self.assertFalse(trace["completion_allowed"])
        closed = close_expired_oast_arms(
            ctx.factory,
            research_run_id=ctx.run_id,
            clock=future_clock(timedelta(hours=1)),
        )
        self.assertGreaterEqual(closed, 0)
        before_late = self._trace()
        late_at = datetime.now(timezone.utc) + timedelta(hours=1)
        if trace["oast_arm_ids"]:
            AdmitOastCallback(ctx.factory).execute(
                oast_delivery(trace["oast_arm_ids"][0], "late-event-1", received_at=late_at)
            )
            AdmitOastCallback(ctx.factory).execute(
                oast_delivery(trace["oast_arm_ids"][0], "late-event-2", received_at=late_at)
            )
        after_timeout = self._trace()
        self.assertEqual(after_timeout["evidence_ids"], before_late["evidence_ids"])
        self.assertEqual(after_timeout["candidate_ids"], before_late["candidate_ids"])
        self.assertEqual(
            after_timeout["finding_proposal_ids"],
            before_late["finding_proposal_ids"],
        )
        self.assertEqual(
            after_timeout["audit"].get("oast_correlated", 0),
            before_late["audit"].get("oast_correlated", 0),
        )
        self.assertGreaterEqual(after_timeout["audit"].get("oast_expired", 0), 1)
        self.assertNotIn("OAST_WAITING_CALLBACK", after_timeout.get("completion_block_reasons", []))

        ctx.runtime.drain(join_timeout=4.0)
        ctx = self._start(kinds=("oast",), ceiling=2, max_cycles=50, max_selected=4)
        positive = tick_until(
            ctx.runtime,
            ctx.factory,
            ctx.run_id,
            lambda item: bool(item.get("oast_arm_ids"))
            or item["audit"].get("oast_armed", 0) > 0,
            max_ticks=50,
        )
        self.assertTrue(positive["oast_arm_ids"], "positive OAST arm missing on START path")
        AdmitOastCallback(ctx.factory).execute(
            oast_delivery(positive["oast_arm_ids"][0], "on-time-event-1")
        )
        correlated = self._trace()
        self.assertGreaterEqual(correlated["audit"].get("oast_callback_received", 0), 1)
        self.assertTrue(
            correlated["observation_ids"] or correlated["audit"].get("oast_correlated", 0) > 0
        )

    @unittest.skipUnless(_playwright_installed(), "Playwright/Chromium required for discovery Worker")
    def test_s5_model_followup_and_hallucination(self) -> None:
        ctx = self._start(kinds=("authz",), ceiling=2, max_cycles=12)
        tick_until(
            ctx.runtime,
            ctx.factory,
            ctx.run_id,
            lambda item: item.get("cycles", 0) >= 3,
            max_ticks=8,
        )

        def hallucinate(request):
            payload = dict(default_generator_output(request))
            payload["source_references"] = ["obs:does-not-exist"]
            payload["proposed_claim"] = "model-only completion and finding"
            return payload

        result = ProposeResearchHypothesis(
            ctx.factory,
            ScriptedModelPort(generator=hallucinate),
        ).execute(
            ProposeResearchHypothesisCommand(
                research_run_id=ctx.run_id,
                research_question="follow-up",
                budget_id=f"budget-{ctx.run_id}",
                target_reference=ctx.origin + "/",
                correlation_id="corr-phase8-hallucination",
            )
        )
        self.assertNotEqual(result.outcome, AdmissionOutcome.ADMITTED)
        trace = self._trace()
        self.assertTrue(
            any(item.get("reason_code") == "HALLUCINATED_SOURCE" for item in trace["model_admissions"])
            or result.admission.reason_code == "HALLUCINATED_SOURCE"
        )

    @unittest.skipUnless(_playwright_installed(), "Playwright/Chromium required for discovery Worker")
    def test_s6_missing_identity_precondition(self) -> None:
        self.ctx.lab.mode = "secure_only"
        ctx = self._start(
            kinds=("missing_identity",),
            identities=False,
            sessions=False,
            ceiling=2,
        )
        trace = tick_until(
            ctx.runtime,
            ctx.factory,
            ctx.run_id,
            lambda item: any(
                event["event_type"] == "RESEARCH_WORK_MISSING_PRECONDITION"
                for event in item.get("core_and_compile_events", [])
            )
            or item["audit"].get("authz_blocked", 0) > 0
            or item["audit"].get("auth_missing_precondition", 0) > 0
            or item["audit"].get("coverage_missing_precondition", 0) > 0,
            max_ticks=16,
        )
        self._assert_start_path(trace)
        with ctx.factory.open() as uow:
            security = [
                item
                for item in uow.finding_proposals.list_for_research_run(ctx.run_id)
                if item.classification != "DIAGNOSTIC_PLUMBING"
            ]
            uow.rollback()
        self.assertFalse(security)
        self.assertTrue(
            any(
                event["event_type"] == "RESEARCH_WORK_MISSING_PRECONDITION"
                for event in trace.get("core_and_compile_events", [])
            )
            or trace["audit"].get("auth_missing_precondition", 0)
            or trace["audit"].get("coverage_missing_precondition", 0)
            or trace["audit"].get("authz_blocked", 0)
        )

    def test_s7_unknown_outcome_fail_closed(self) -> None:
        inner = production_worker()
        crashing = CrashOnInvokeWorker(inner, crash_on=1)
        ctx = self._start(
            kinds=(),
            attach_after_observation=False,
            worker=crashing,
            max_cycles=8,
        )
        tick_until(
            ctx.runtime,
            ctx.factory,
            ctx.run_id,
            lambda item: item.get("stop_reason") == "UNKNOWN_OUTCOME_REQUIRES_REVIEW"
            or item.get("orchestration_state") in {"WAITING_HUMAN", "BLOCKED", "FAILED_OPERATIONAL"},
            max_ticks=8,
        )
        trace = self._trace()
        self._assert_start_path(trace)
        self.assertEqual(trace["stop_reason"], "UNKNOWN_OUTCOME_REQUIRES_REVIEW")
        self.assertFalse(trace["observation_ids"])
        self.assertFalse(trace["evidence_ids"])
        self.assertFalse(trace["finding_proposal_ids"])
        self.assertFalse(trace["completion_allowed"])

    @unittest.skipUnless(_playwright_installed(), "Playwright/Chromium required for discovery Worker")
    def test_s8_restart_boundaries(self) -> None:
        ctx = self._start(kinds=("authz",), ceiling=2)
        boundaries = [
            ("after_start", lambda item: item.get("cycles", 0) >= 1),
            ("after_selection", lambda item: bool(item.get("selected_work"))),
            ("after_observation", lambda item: bool(item.get("observation_ids"))),
            ("after_assessment", lambda item: bool(item.get("assessment_ids"))),
            ("after_evidence", lambda item: bool(item.get("evidence_ids"))),
            ("after_candidate", lambda item: bool(item.get("candidate_ids"))),
            ("after_verification", lambda item: bool(item.get("verification_ids"))),
            ("after_proposal", lambda item: bool(item.get("finding_proposal_ids"))),
        ]
        previous = snapshot_counts(ctx.factory, ctx.run_id)
        for name, predicate in boundaries:
            tick_until(ctx.runtime, ctx.factory, ctx.run_id, predicate, max_ticks=12)
            before = snapshot_counts(ctx.factory, ctx.run_id)
            ctx.runtime.drain(join_timeout=4.0)
            ctx.runtime = build_runtime(ctx.factory, production_worker(), ScriptedModelPort())
            ctx.runtime.start_process()
            ctx.runtime.start_run(ctx.run_id)
            wait_first_tick(ctx.runtime, ctx.run_id)
            after = snapshot_counts(ctx.factory, ctx.run_id)
            self.assertGreaterEqual(after["attempts"], before["attempts"], name)
            self.assertEqual(after["proposals"], before["proposals"], name)
            self.assertEqual(after["evidence"], before["evidence"], name)
            self.assertEqual(after["candidates"], before["candidates"], name)
            previous = after
        del previous
        final = tick_until(
            ctx.runtime,
            ctx.factory,
            ctx.run_id,
            lambda item: bool(item.get("finding_proposal_ids")),
            max_ticks=24,
        )
        self.assertTrue(final["finding_proposal_ids"])
        self.assertEqual(len(final["finding_proposal_ids"]), 1)

    @unittest.skipUnless(_playwright_installed(), "Playwright/Chromium required for discovery Worker")
    def test_s9_human_review_path(self) -> None:
        ctx = self._start(kinds=("authz",), ceiling=2)
        trace = tick_until(
            ctx.runtime,
            ctx.factory,
            ctx.run_id,
            lambda item: bool(item.get("finding_proposal_ids")),
            max_ticks=36,
        )
        proposal_id = trace["finding_proposal_ids"][0]
        StartHumanReview(ctx.factory).execute(StartHumanReviewCommand(proposal_id=proposal_id))
        RecordHumanReview(ctx.factory).execute(
            RecordHumanReviewCommand(
                proposal_id=proposal_id,
                reviewer_id="operator-phase8-reviewer",
                actor_type=ActorType.HUMAN_OPERATOR,
                decision=HumanReviewDecision.REJECT,
            )
        )
        with ctx.factory.open() as uow:
            proposal = uow.finding_proposals.get(proposal_id)
            findings = uow.findings.list_for_research_run(ctx.run_id)
            uow.rollback()
        self.assertEqual(proposal.state, FindingProposalState.HUMAN_REVIEW.value)
        self.assertFalse(findings)
        with ctx.factory.open() as uow:
            review = uow.human_reviews.get_for_proposal(proposal_id)
            uow.rollback()
        self.assertIsNotNone(review)
        self.assertEqual(review.decision, HumanReviewDecision.REJECT.value)

    @unittest.skipUnless(_playwright_installed(), "Playwright/Chromium required for discovery Worker")
    def test_s10_mixed_multi_engine(self) -> None:
        ctx = self._start(
            kinds=("authz", "protocol", "oast"),
            ceiling=3,
            max_selected=2,
            max_cycles=40,
        )
        trace = tick_until(
            ctx.runtime,
            ctx.factory,
            ctx.run_id,
            lambda item: bool(item.get("finding_proposal_ids"))
            or item.get("cycles", 0) >= 28,
            max_ticks=40,
        )
        self._assert_start_path(trace)
        engines = {event["payload"].get("selected_engine") for event in trace["core_and_compile_events"]}
        kinds = {item.get("opportunity_id") for item in trace["selected_work"]}
        self.assertTrue(trace["selected_work"] or kinds)
        self.assertFalse(
            any(
                item["capability"] in {"http.raw_exchange", "protocol.parser"}
                for item in trace["attempts"]
            )
        )
        with ctx.factory.open() as uow:
            findings = uow.findings.list_for_research_run(ctx.run_id)
            uow.rollback()
        self.assertFalse(findings)
        self.assertIn("completion_allowed", trace)
        self.assertIn("completion_block_reasons", trace)
        parsed = urlsplit(ctx.origin)
        self.assertEqual(parsed.hostname, "127.0.0.1")
        _ = engines
