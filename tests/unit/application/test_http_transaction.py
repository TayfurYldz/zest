from __future__ import annotations

import unittest
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pathsetup  # noqa: F401

from e2e.lab.http_transaction_lab import EXTERNAL_REDIRECT, Gate19HttpLab
from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
)
from zest.application.scope_reauthorization import reevaluate_redirect_location
from zest.application.transition_a.http_transaction import (
    HTTP_TRANSACTION_OBSERVATION_KIND,
    HttpTransactionNormalizer,
)
from zest.application.transition_a.registry import NormalizerRegistry
from zest.core.enums import ExecutionDecisionKind, ReasonCode, ScopeDecision, ScopeRuleEffect
from zest.core.execution import evaluate_execution
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.http_transaction import plan_http_transaction_read
from zest.tools.registry import load_capability_registry
from zest.worker_runtime.python.runtime import build_result, utc_now_rfc3339
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort
from support.spine import seed_spine
from support.worker_requests import valid_worker_request
from core.fixtures import base_request


CREATED_AT = datetime(2026, 8, 17, tzinfo=timezone.utc)


class FixedClock:
    def now(self) -> datetime:
        return CREATED_AT


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _compiled_scope(origin: str, path_prefix: str | None = None) -> object:
    parsed = urlsplit(origin)
    return compile_scope_rules(
        (
            ScopeRuleDefinition(
                rule_id="rule-allow",
                effect=ScopeRuleEffect.ALLOW,
                scheme=parsed.scheme or "http",
                host=parsed.hostname or "127.0.0.1",
                port=parsed.port,
                path_prefix=path_prefix,
                source_reference="scope-src",
            ),
        )
    )


def _plan(origin: str, **overrides):
    values = dict(
        hypothesis_id="hyp-1",
        budget_id="budget-1",
        target_reference="target-1",
        authorized_origin=origin,
        path="/ok",
        method="GET",
    )
    values.update(overrides)
    return plan_http_transaction_read(**values)


def _in_process_worker(store: _Store) -> RecordingWorkerPort:
    def handler(request):
        result = build_result(request, utc_now_rfc3339())
        return WorkerInvocationOutcome(
            invocation_status=InvocationStatus.COMPLETED,
            started_at=CREATED_AT,
            completed_at=CREATED_AT,
            worker_result=result,
            exit_code=0,
        )

    return RecordingWorkerPort(store=store, handler=handler)


def _use_case(store: _Store | None = None):
    store = store or _Store()
    seed_spine(store)
    factory = FakeUnitOfWorkFactory(store=store)
    worker = _in_process_worker(store)
    return ExecutePlannedExperiment(factory, worker, clock=FixedClock()), factory, worker


class HttpTransactionApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lab = Gate19HttpLab()
        self.origin = self.lab.start()

    def tearDown(self) -> None:
        self.lab.stop()

    def test_authorized_get_produces_http_observation(self) -> None:
        use_case, factory, port = _use_case()
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_plan(self.origin),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertEqual(len(port.calls), 1)
        observations = list(factory.store.observations.values())
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].observation_kind, HTTP_TRANSACTION_OBSERVATION_KIND)
        self.assertEqual(observations[0].payload["status_class"], "2xx")
        self.assertNotIn("is_vulnerable", observations[0].payload)
        self.assertNotIn("severity", observations[0].payload)
        self.assertNotIn("finding", observations[0].payload)
        self.assertNotIn("confidence", observations[0].payload)
        blob = str(observations[0].payload).lower()
        self.assertNotIn("password", blob)
        self.assertNotIn("cookie", blob)

    def test_out_of_scope_origin_denied(self) -> None:
        use_case, _, port = _use_case()
        other = "http://127.0.0.1:9"
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_plan(self.origin),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(other),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(outcome.core_reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)
        self.assertEqual(len(port.calls), 0)

    def test_wrong_port_denied(self) -> None:
        use_case, _, port = _use_case()
        parsed = urlsplit(self.origin)
        wrong = f"http://{parsed.hostname}:9"
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_plan(self.origin),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(wrong),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(len(port.calls), 0)

    def test_wrong_scheme_denied(self) -> None:
        use_case, _, port = _use_case()
        parsed = urlsplit(self.origin)
        https_origin = f"https://{parsed.hostname}:{parsed.port}"
        compiled = compile_scope_rules(
            (
                ScopeRuleDefinition(
                    rule_id="rule-allow",
                    effect=ScopeRuleEffect.ALLOW,
                    scheme="http",
                    host=parsed.hostname or "127.0.0.1",
                    port=parsed.port,
                    path_prefix=None,
                    source_reference="scope-src",
                ),
            )
        )
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_plan(https_origin),
                scope=_allow_scope(),
                compiled_scope=compiled,
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(len(port.calls), 0)

    def test_missing_compiled_scope_denied(self) -> None:
        use_case, _, port = _use_case()
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_plan(self.origin),
                scope=_allow_scope(),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(outcome.core_reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)
        self.assertEqual(len(port.calls), 0)

    def test_redirect_requires_core_reevaluation(self) -> None:
        use_case, factory, port = _use_case()
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_plan(self.origin, path="/redirect"),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin, path_prefix="/redirect"),
            )
        )
        self.assertEqual(len(port.calls), 1)
        result = port.calls[0]["request"]
        del result
        self.assertEqual(outcome.status, ResearchLoopStatus.REAUTHORIZATION_REQUIRED)
        self.assertEqual(outcome.experiment_execution_state, "AUTHORIZATION_CHECK")
        self.assertIsNotNone(outcome.reauthorization_request)
        self.assertEqual(outcome.reauthorization_request["reason"], "redirect")
        self.assertFalse(outcome.reauthorization_request["discovery_context"]["followed"])
        invocation = factory.store.worker_results
        self.assertTrue(invocation)
        raw = next(iter(invocation.values())).raw_result
        diagnostics = next(iter(invocation.values())).diagnostics
        if isinstance(diagnostics, dict):
            check = reevaluate_redirect_location(
                diagnostics.get("raw_location") or diagnostics.get("location") or EXTERNAL_REDIRECT,
                _compiled_scope(self.origin),
                response_url=diagnostics.get("response_url"),
            )
            self.assertEqual(check.decision, ScopeDecision.DENY)
            self.assertEqual(check.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)
        self.assertEqual(raw.get("reason"), "redirect_or_new_origin")
        self.assertEqual(list(factory.store.observations.values()), [])

    def test_path_ambiguity_denied_before_dispatch(self) -> None:
        from zest.research.compiler import ExperimentCompileError

        use_case, _, port = _use_case()
        with self.assertRaises(ExperimentCompileError):
            _plan(self.origin, path="/ok/../secret")
        self.assertEqual(len(port.calls), 0)

    def test_method_action_side_effect_mismatch_denied_by_core(self) -> None:
        plan = _plan(self.origin)
        object.__setattr__(plan, "side_effect_level", 1)
        from zest.application.capability_binding import capability_view_for_plan

        view = capability_view_for_plan(plan)
        decision = evaluate_execution(
            base_request(side_effect_level=1, capability=view, requested_subject=plan.target_reference)
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.RISK_EXCEEDS_CAPABILITY)

    def test_normalizer_emits_facts_not_verdicts(self) -> None:
        request = valid_worker_request(
            worker_capability="http.transaction",
            action="read",
            arguments={"authorized_origin": self.origin, "method": "GET", "path": "/ok"},
        )
        result = {
            "contract_version": "v1",
            "correlation": request["correlation"],
            "worker_id": "local-python-diagnostic",
            "status": "SUCCEEDED",
            "started_at": "2026-08-17T12:00:00Z",
            "completed_at": "2026-08-17T12:00:01Z",
            "raw_result": {
                "authorized_origin": self.origin,
                "method": "GET",
                "path": "/ok",
                "status_code": 200,
                "content_type": "application/json",
                "response_headers": {"content-type": "application/json"},
                "body_length": 12,
                "body_digest": "a" * 64,
                "json_value_kind": "object",
                "json_top_level_keys": ["ok"],
                "elapsed_ms": 1,
                "request_fingerprint": "b" * 64,
            },
        }
        drafts = HttpTransactionNormalizer("read").normalize(request, result)
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].observation_kind, HTTP_TRANSACTION_OBSERVATION_KIND)
        self.assertEqual(drafts[0].payload["status_class"], "2xx")
        self.assertFalse(drafts[0].payload["redirect"])

    def test_registry_has_read_and_mutate_normalizers(self) -> None:
        registry = NormalizerRegistry()
        self.assertEqual(registry.get("http.transaction", "read").action, "read")
        self.assertEqual(registry.get("http.transaction", "mutate").action, "mutate")

    def test_compiled_scope_rule_ids_are_audited_not_generic_scope(self) -> None:
        use_case, factory, port = _use_case()
        generic = ScopeEvaluationInput(
            matches=(ScopeRuleMatch("rule-generic", ScopeRuleEffect.ALLOW, True, "scope-src"),),
            ambiguous=False,
        )
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_plan(self.origin),
                scope=generic,
                compiled_scope=_compiled_scope(self.origin),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        matched: list[str] = []
        for item in factory.store.audit_events.values():
            payload = item.payload
            if isinstance(payload, dict) and "matched_scope_rule_ids" in payload:
                matched.extend(payload["matched_scope_rule_ids"])
        self.assertIn("rule-allow", matched)
        self.assertNotIn("rule-generic", matched)
        self.assertEqual(len(port.calls), 1)

    def test_generic_allow_cannot_authorize_a_different_compiled_target(self) -> None:
        use_case, factory, port = _use_case()
        generic = ScopeEvaluationInput(
            matches=(ScopeRuleMatch("rule-generic", ScopeRuleEffect.ALLOW, True, "scope-src"),),
            ambiguous=False,
        )
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_plan(self.origin),
                scope=generic,
                compiled_scope=_compiled_scope("http://127.0.0.1:9"),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(len(port.calls), 0)
        matched: list[str] = []
        for item in factory.store.audit_events.values():
            payload = item.payload
            if isinstance(payload, dict) and "matched_scope_rule_ids" in payload:
                matched.extend(payload["matched_scope_rule_ids"])
        self.assertNotIn("rule-generic", matched)

    def test_authorized_dispatch_carries_core_derived_envelope(self) -> None:
        from zest.application.execute_planned_experiment import AuthorizedDispatch

        use_case, _, _ = _use_case()
        authorized = use_case.authorize(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_plan(self.origin, path="/ok"),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin, path_prefix="/ok"),
            )
        )
        self.assertIsInstance(authorized, AuthorizedDispatch)
        envelope = authorized.network_envelope
        self.assertIsNotNone(envelope)
        assert envelope is not None
        self.assertEqual(envelope.document_path, "/ok")
        self.assertFalse(envelope.origin_wide)
        self.assertEqual(envelope.allowed_path_prefixes, ("/ok",))
        self.assertTrue(envelope.loopback_only)
        self.assertEqual(envelope.source_scope_rule_ids, ("rule-allow",))
        self.assertEqual(
            envelope.authorization_decision_reference,
            authorized.authorization_decision_reference,
        )


class HttpTransactionCapabilityAuthorizationTests(unittest.TestCase):
    def test_fingerprint_mismatch_denied(self) -> None:
        registry = load_capability_registry()
        capability = registry.get("http.transaction")
        assert capability is not None
        plan = plan_http_transaction_read(
            "hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
            authorized_origin="http://127.0.0.1:9",
            path="/ok",
        )
        from zest.application.capability_binding import capability_view_for_plan
        from dataclasses import replace

        view = capability_view_for_plan(plan)
        view = replace(view, definition_fingerprint="c" * 64)
        decision = evaluate_execution(base_request(capability=view))
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.DEFINITION_FINGERPRINT_MISMATCH)

    def test_mutate_at_level_zero_denied(self) -> None:
        from zest.application.capability_binding import capability_view_for
        from zest.core.enums import SideEffectLevel

        view = capability_view_for("http.transaction", "mutate", effective_side_effect=0)
        decision = evaluate_execution(
            base_request(side_effect_level=SideEffectLevel.LEVEL_0, capability=view)
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.RISK_UNDERSTATEMENT)


if __name__ == "__main__":
    unittest.main()
