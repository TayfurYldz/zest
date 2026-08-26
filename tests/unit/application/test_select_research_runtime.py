from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.select_research_runtime import (
    RoleRoutedModelPort,
    SelectResearchRuntime,
    SelectResearchRuntimeCommand,
)
from zest.research.model_port import ModelCallRequest, ModelRole
from zest.research.model_runtime import RuntimeOutcome, api_runtime_identity
from zest.research.routing import (
    CandidateLocality,
    RoutingBudget,
    RoutingOutcome,
    RoutingRequest,
    RuntimeCandidate,
)
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run


class FixedClock:
    def now(self):
        return CREATED_AT


def _candidate(adapter_id: str, *, available: bool = True) -> RuntimeCandidate:
    return RuntimeCandidate(
        identity=api_runtime_identity(adapter_id=adapter_id, runtime_id=adapter_id),
        available=available,
        authenticated=True,
        structured_output_compatible=True,
        locality=CandidateLocality.REMOTE,
    )


def _request(*candidates: RuntimeCandidate, **kwargs) -> RoutingRequest:
    values = dict(
        role=ModelRole.GENERATOR,
        candidates=candidates,
        budget=RoutingBudget(max_runtime_attempts=2, max_fallback_attempts=1),
        operator_preference_order=tuple(item.identity.adapter_id for item in candidates),
    )
    values.update(kwargs)
    return RoutingRequest(**values)


class SelectResearchRuntimeTests(unittest.TestCase):
    def test_persists_routing_provenance_without_selecting_core_authority(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        result = SelectResearchRuntime(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(
            SelectResearchRuntimeCommand(
                research_run_id="run-1",
                request=_request(_candidate("openai.responses"), _candidate("missing", available=False)),
            )
        )
        self.assertEqual(result.decision.outcome, RoutingOutcome.SELECT)
        audit = next(iter(store.audit_events.values()))
        self.assertEqual(audit.event_type, "RUNTIME_ROUTING_DECISION")
        self.assertEqual(audit.payload["selected_runtime"]["adapter_id"], "openai.responses")
        self.assertTrue(audit.payload["not_authorization"])
        self.assertTrue(audit.payload["no_aggregate_model_score"])
        self.assertNotIn("api_key", audit.payload)

    def test_content_policy_reconsider_does_not_hop(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        use_case = SelectResearchRuntime(FakeUnitOfWorkFactory(store), clock=FixedClock())
        command = SelectResearchRuntimeCommand(
            research_run_id="run-1",
            request=_request(_candidate("a.runtime"), _candidate("b.runtime")),
        )
        first = use_case.execute(command)
        second = use_case.reconsider(command, first.decision, RuntimeOutcome.CONTENT_POLICY_BLOCKED)
        self.assertEqual(second.decision.outcome, RoutingOutcome.BLOCKED_POLICY)
        self.assertEqual(len(store.audit_events), 2)

    def test_role_routed_port_dispatches_without_rescoring(self) -> None:
        generator = ScriptedModelPort()
        falsifier = ScriptedModelPort()
        port = RoleRoutedModelPort(
            {ModelRole.GENERATOR: generator, ModelRole.FALSIFIER: falsifier}
        )
        request = ModelCallRequest(
            role=ModelRole.GENERATOR,
            correlation_id="c1",
            context_fingerprint="fp",
            instructions="propose",
            payload={"note": "ok"},
        )
        port.complete(request)
        self.assertEqual(len(generator.calls), 1)
        self.assertEqual(len(falsifier.calls), 0)


if __name__ == "__main__":
    unittest.main()
