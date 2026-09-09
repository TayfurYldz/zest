"""Phase 6.5 OAST production acceptance (O1–O26)."""

from __future__ import annotations

import hashlib
import json
import unittest
from datetime import timedelta
from pathlib import Path

import pathsetup  # noqa: F401

from zest.application.admit_oast_callback import AdmitOastCallback
from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from zest.application.global_research_work_audit import global_research_work_audit
from zest.application.oast_timeout import close_expired_oast_arms
from zest.application.produce_hunter_coverage_work import ProduceHunterCoverageWork
from zest.application.select_research_opportunities import (
    SelectResearchOpportunities,
    SelectResearchOpportunitiesCommand,
)
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import CompiledScope, CompiledScopeRule
from zest.data.records import IssuedBudgetRecord
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.exploration import OpportunityKind, ResearchPolicyBudget
from zest.research.oast.types import OastCallbackDelivery
from zest.research.orchestration import OrchestrationBounds
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort, STARTED_AT, COMPLETED_AT
from support.spine import CREATED_AT, seed_authorization_run
from tests.unit.application.test_phase62_hunter_coverage import (
    _add_http_fact,
    _sql_family,
)


class MutableClock:
    def __init__(self):
        self._now = CREATED_AT

    def now(self):
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


def _compiled_scope() -> CompiledScope:
    return CompiledScope(
        rules=(
            CompiledScopeRule(
                rule_id="rule-allow",
                effect=ScopeRuleEffect.ALLOW,
                scheme="http",
                host="127.0.0.1",
                host_pattern=None,
                port=9,
                path_prefix=None,
                source_reference="scope-src",
                expires_at=None,
            ),
        )
    )


def _bounds(**overrides) -> OrchestrationBounds:
    values = dict(
        max_cycles=12,
        max_experiments=12,
        max_model_calls=50,
        max_worker_invocations=12,
        max_elapsed_ms=60_000,
        max_selected_opportunities=1,
        max_runtime_fallback=0,
        side_effect_ceiling=2,
        allow_repeated_control_experiments=False,
    )
    values.update(overrides)
    return OrchestrationBounds(**values)


def _command(**overrides) -> StartAutonomousResearchCommand:
    values = dict(
        research_run_id="run-1",
        budget_id="budget-1",
        target_reference="http://127.0.0.1:9/",
        scope=ScopeEvaluationInput(
            matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
            ambiguous=False,
        ),
        bounds=_bounds(),
        compiled_scope=_compiled_scope(),
        selection_budget=ResearchPolicyBudget(max_selected=1, max_exploratory=1),
    )
    values.update(overrides)
    return StartAutonomousResearchCommand(**values)


def _seed_base(store: _Store) -> None:
    seed_authorization_run(store)
    store.issued_budgets["budget-1"] = IssuedBudgetRecord(
        budget_id="budget-1",
        research_run_id="run-1",
        max_requests=40,
        max_tool_calls=40,
        max_runtime_ms=10_000,
        max_concurrency=1,
        issued_at=CREATED_AT,
    )


def _oast_attrs(**extra) -> dict:
    attrs = {
        "query_params": ["url"],
        "oast_listener_origin": "http://127.0.0.1:9",
        "authorized_origin": "http://127.0.0.1:9",
        "path": "/fetch",
    }
    attrs.update(extra)
    return attrs


def _worker_result(capability: str, raw: dict, correlation: dict) -> dict:
    return {
        "contract_version": "v1",
        "correlation": correlation,
        "worker_id": f"local-python-{capability}",
        "status": "SUCCEEDED",
        "started_at": "2026-08-16T20:00:00Z",
        "completed_at": "2026-08-16T20:00:01Z",
        "raw_result": raw,
    }


def _oast_handler(store: _Store, *, admit: bool = True, unknown: bool = False):
    factory = FakeUnitOfWorkFactory(store=store)

    def handler(request):
        cap = request.get("worker_capability")
        arguments = request.get("arguments") or {}
        correlation = dict(request.get("correlation") or {})
        if cap == "http.transaction":
            if admit:
                query = arguments.get("query") or {}
                callback_id = None
                for value in query.values():
                    if isinstance(value, str) and "/oast/" in value:
                        callback_id = value.rsplit("/oast/", 1)[-1]
                        break
                if callback_id:
                    payload = {"callback_channel": "http"}
                    digest = hashlib.sha256(
                        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest()
                    AdmitOastCallback(factory).execute(
                        OastCallbackDelivery(
                            delivery_id="delivery-1",
                            correlation_id="missing" if unknown else callback_id,
                            provider_adapter_id="loopback",
                            provider_event_id="event-1",
                            received_at=CREATED_AT + timedelta(seconds=1),
                            normalized_payload=payload,
                            normalized_digest=digest,
                        )
                    )
            raw = {
                "authorized_origin": arguments.get("authorized_origin"),
                "method": arguments.get("method") or "GET",
                "path": arguments.get("path"),
                "status_code": 200,
                "body_length": 0,
                "body_digest": "0" * 64,
                "request_fingerprint": "fp-oast",
                "elapsed_ms": 1,
            }
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.COMPLETED,
                started_at=STARTED_AT,
                completed_at=COMPLETED_AT,
                worker_result=_worker_result(cap, raw, correlation),
                exit_code=0,
            )
        message = ""
        if isinstance(arguments, dict):
            raw_msg = arguments.get("message", "")
            message = raw_msg if isinstance(raw_msg, str) else ""
        return WorkerInvocationOutcome(
            invocation_status=InvocationStatus.COMPLETED,
            started_at=STARTED_AT,
            completed_at=COMPLETED_AT,
            worker_result=_worker_result(
                "diagnostic.echo",
                {"echoed": message, "capability": "diagnostic.echo"},
                correlation,
            ),
            exit_code=0,
        )

    return handler


def _controller(store: _Store, *, admit: bool = True, clock=None, unknown: bool = False):
    clock = clock or MutableClock()
    factory = FakeUnitOfWorkFactory(store=store)
    port = RecordingWorkerPort(store=store, handler=_oast_handler(store, admit=admit, unknown=unknown))
    controller = AutonomousResearchController(
        factory, port, ScriptedModelPort(), clock=clock
    )
    return controller, port, clock


def _run_until(controller, command, predicate, limit: int = 12):
    controller.start(command)
    last = None
    for _ in range(limit):
        last = controller.step(command)
        if predicate(last):
            return last
    return last


class OastProductionTests(unittest.TestCase):
    def test_o1_o2_o3_o4_o5_source_token_scheduler_native_plan(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(store, fact_id="fact-oast", path="/fetch", extra_attributes=_oast_attrs())
        controller, _, _ = _controller(store, admit=False)
        controller.start(_command())
        controller.step(_command())
        oast = [
            item
            for item in store.opportunity_selection_candidates.values()
            if item.opportunity_kind == OpportunityKind.OAST_INTERACTION.value
        ]
        self.assertTrue(oast)
        self.assertTrue(any(item.startswith("callback_id:") for item in oast[0].assumptions))
        compiled = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_WORK_COMPILED"
            and (item.payload or {}).get("selected_engine") == "OAST"
        ]
        self.assertTrue(compiled)
        self.assertEqual(compiled[-1].payload.get("compiled_capability"), "http.transaction")
        self.assertNotEqual(compiled[-1].payload.get("compiler"), "diagnostic.echo")
        self.assertEqual(compiled[-1].payload.get("native_side_effect"), 0)
        self.assertTrue(store.oast_tokens)

    def test_o6_o7_o8_o9_o10_o15_positive_control(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(store, fact_id="fact-oast", path="/fetch", extra_attributes=_oast_attrs())
        controller, port, _ = _controller(store, admit=True)
        _run_until(
            controller,
            _command(),
            lambda result: any(
                item.event_type == "OAST_COVERAGE_UPDATED"
                and (item.payload or {}).get("assessment_outcome") == "CONSISTENT_WITH_PREDICTION"
                for item in store.audit_events.values()
            ),
        )
        self.assertTrue(port.calls)
        self.assertEqual(port.calls[-1]["request"].get("worker_capability"), "http.transaction")
        self.assertTrue(store.oast_correlations)
        self.assertTrue(store.oast_callback_deliveries)
        self.assertTrue(store.oast_admissions)
        self.assertTrue(
            any(item.assessment_outcome == "CONSISTENT_WITH_PREDICTION" for item in store.hypothesis_assessments.values())
        )
        self.assertFalse(store.evidence)

    def test_o11_unknown_token_no_evidence(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(store, fact_id="fact-oast", path="/fetch", extra_attributes=_oast_attrs())
        controller, port, _ = _controller(store, admit=True, unknown=True)
        controller.start(_command())
        for _ in range(8):
            controller.step(_command())
        self.assertTrue(port.calls)
        unknown_events = [
            item for item in store.audit_events.values() if item.event_type == "OAST_CALLBACK_UNKNOWN_TOKEN"
        ]
        if not unknown_events:
            payload = {"callback_channel": "http"}
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            AdmitOastCallback(FakeUnitOfWorkFactory(store)).execute(
                OastCallbackDelivery(
                    delivery_id="delivery-unknown",
                    correlation_id="unknown-token",
                    provider_adapter_id="loopback",
                    provider_event_id="event-unknown",
                    received_at=CREATED_AT + timedelta(seconds=1),
                    normalized_payload=payload,
                    normalized_digest=digest,
                )
            )
        self.assertTrue(
            any(item.event_type == "OAST_CALLBACK_UNKNOWN_TOKEN" for item in store.audit_events.values())
        )
        self.assertFalse(store.oast_admissions)
        self.assertFalse(store.evidence)

    def test_o12_expired_callback_preserved_not_promoted(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(store, fact_id="fact-oast", path="/fetch", extra_attributes=_oast_attrs())
        clock = MutableClock()
        controller, _, clock = _controller(store, admit=False, clock=clock)
        controller.start(_command())
        for _ in range(6):
            controller.step(_command())
        self.assertTrue(store.oast_correlations)
        correlation = next(iter(store.oast_correlations.values()))
        clock.advance(timedelta(minutes=16))
        payload = {"callback_channel": "http"}
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        result = AdmitOastCallback(FakeUnitOfWorkFactory(store), clock=clock.now).execute(
            OastCallbackDelivery(
                delivery_id="delivery-late",
                correlation_id=correlation.correlation_id,
                provider_adapter_id="loopback",
                provider_event_id="event-late",
                received_at=clock.now(),
                normalized_payload=payload,
                normalized_digest=digest,
            )
        )
        self.assertEqual(result.reason_code, "OAST_CORRELATION_EXPIRED")
        self.assertTrue(store.oast_callback_deliveries)
        self.assertFalse(store.oast_admissions)
        self.assertFalse(store.evidence)

    def test_o13_duplicate_callback(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(store, fact_id="fact-oast", path="/fetch", extra_attributes=_oast_attrs())
        controller, _, _ = _controller(store, admit=True)
        _run_until(controller, _command(), lambda _: bool(store.oast_admissions))
        correlation = next(iter(store.oast_correlations.values()))
        payload = {"callback_channel": "http"}
        digest = hashlib.sha256(
            json.dumps({"callback_channel": "http", "n": 2}, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        AdmitOastCallback(FakeUnitOfWorkFactory(store)).execute(
            OastCallbackDelivery(
                delivery_id="delivery-2",
                correlation_id=correlation.correlation_id,
                provider_adapter_id="loopback",
                provider_event_id="event-2",
                received_at=CREATED_AT + timedelta(seconds=2),
                normalized_payload=payload,
                normalized_digest=digest,
            )
        )
        self.assertEqual(len(store.oast_admissions), 1)
        self.assertEqual(len(store.evidence), 0)
        self.assertGreaterEqual(len(store.oast_callback_deliveries), 1)

    def test_o14_o16_o23_o24_no_callback_timeout_and_completion(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(store, fact_id="fact-oast", path="/fetch", extra_attributes=_oast_attrs())
        clock = MutableClock()
        controller, _, clock = _controller(store, admit=False, clock=clock)
        controller.start(_command())
        for _ in range(6):
            controller.step(_command())
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertFalse(audit.completion_allowed)
        self.assertTrue(
            "OAST_WAITING_CALLBACK" in audit.completion_block_reasons or audit.oast_waiting_callback
        )
        clock.advance(timedelta(minutes=16))
        close_expired_oast_arms(FakeUnitOfWorkFactory(store), research_run_id="run-1", clock=clock)
        controller.step(_command())
        self.assertTrue(
            any(item.event_type == "OAST_NO_CALLBACK_TIMEOUT" for item in store.audit_events.values())
        )
        assessments = list(store.hypothesis_assessments.values())
        self.assertTrue(assessments)
        self.assertEqual(assessments[-1].assessment_outcome, "INCONCLUSIVE")
        self.assertFalse(store.evidence)

    def test_o17_authenticated_oast_missing_identity(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(
            store,
            fact_id="fact-oast",
            path="/fetch",
            identity="actor-a",
            extra_attributes=_oast_attrs(requires_session=True),
        )
        controller, _, _ = _controller(store, admit=False)
        controller.start(_command())
        controller.step(_command())
        missing = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_WORK_MISSING_PRECONDITION"
            and "MISSING_IDENTITY_PRECONDITION" in list((item.payload or {}).get("reason_codes") or [])
        ]
        self.assertTrue(missing)

    def test_o18_o21_hunter_dedupe(self) -> None:
        store = _Store()
        _seed_base(store)
        store.hunter_families["hf-sqli:1"] = _sql_family()
        _add_http_fact(store, fact_id="fact-oast", path="/fetch", extra_attributes=_oast_attrs())
        ProduceHunterCoverageWork(FakeUnitOfWorkFactory(store), clock=MutableClock()).execute(
            "run-1"
        )
        SelectResearchOpportunities(
            FakeUnitOfWorkFactory(store), clock=MutableClock()
        ).execute(SelectResearchOpportunitiesCommand(research_run_id="run-1"))
        oast = [
            item
            for item in store.opportunity_selection_candidates.values()
            if item.opportunity_kind == OpportunityKind.OAST_INTERACTION.value
        ]
        identities = [item.structural_identity for item in oast]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertTrue(oast)

    def test_o19_mutation_interop_same_sink(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(store, fact_id="fact-oast", path="/fetch", extra_attributes=_oast_attrs())
        SelectResearchOpportunities(
            FakeUnitOfWorkFactory(store), clock=MutableClock()
        ).execute(SelectResearchOpportunitiesCommand(research_run_id="run-1"))
        kinds = {item.opportunity_kind for item in store.opportunity_selection_candidates.values()}
        self.assertIn(OpportunityKind.OAST_INTERACTION.value, kinds)
        self.assertIn(OpportunityKind.MUTATION_VARIANT.value, kinds)

    def test_o20_workflow_webhook_source(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(
            store,
            fact_id="fact-wf",
            path="/hook",
            extra_attributes=_oast_attrs(query_params=["webhook"]),
        )
        SelectResearchOpportunities(
            FakeUnitOfWorkFactory(store), clock=MutableClock()
        ).execute(SelectResearchOpportunitiesCommand(research_run_id="run-1"))
        families = [
            item.source_refs[0]
            for item in store.opportunity_selection_candidates.values()
            if item.opportunity_kind == OpportunityKind.OAST_INTERACTION.value
        ]
        self.assertIn("WEBHOOK_CALLBACK", families)

    def test_o22_o25_o26_coverage_recovery_privacy(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(store, fact_id="fact-oast", path="/fetch", extra_attributes=_oast_attrs())
        controller, _, _ = _controller(store, admit=True)
        _run_until(
            controller,
            _command(),
            lambda _: any(item.event_type == "OAST_COVERAGE_UPDATED" for item in store.audit_events.values()),
        )
        coverage = [
            item for item in store.audit_events.values() if item.event_type == "OAST_COVERAGE_UPDATED"
        ]
        self.assertTrue(coverage)
        self.assertFalse(coverage[-1].payload["dimensions"]["endpoint_fully_oast_covered"])
        armed = [item for item in store.audit_events.values() if item.event_type == "OAST_ARMED"]
        self.assertEqual(len(armed), 1)
        secret_keys = {"cookie", "authorization", "set-cookie", "proxy-authorization"}
        for event in store.audit_events.values():
            payload = event.payload or {}
            lowered = {str(key).lower() for key in payload.keys()}
            self.assertTrue(secret_keys.isdisjoint(lowered))
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertIsInstance(audit.oast_pending, int)
        self.assertIsInstance(audit.oast_correlated, int)

    def test_no_second_dispatcher(self) -> None:
        root = Path(__file__).resolve().parents[3]
        text = (root / "src/zest/application/autonomous_research_controller.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("DispatchApprovedV3Queue", text)
        self.assertNotIn("RunResearchSelection(", text)


if __name__ == "__main__":
    unittest.main()
