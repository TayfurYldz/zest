from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from research_os.research.compiler_registry import CompilerOutcome
from research_os.research.exploratory import (
    ExploratoryHypothesisDraft,
    ExploratorySignal,
    ExploratorySignalKind,
)
from research_os.research.exploratory_research_compile import compile_exploratory_research
from research_os.research.proposals import NoveltyBasis
from research_os.tools.capabilities import (
    DIAGNOSTIC_ECHO_CAPABILITY,
    HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY,
)


def _draft(**overrides) -> ExploratoryHypothesisDraft:
    values = dict(
        draft_id="draft-1",
        research_run_id="run-1",
        hypothesis_claim="Explore whether object access differs across actors.",
        proposed_family_name="exploratory.cross_object.read.v1",
        proposed_family_rationale="Registry-external object-access anomaly, not a HunterFamily.",
        source_refs=("change-1",),
        signal_ids=("sig-1",),
        target_node_kinds=("OBJECT",),
        structural_identity="identity-exploratory-1",
        novelty_basis=NoveltyBasis.UNCLASSIFIED,
    )
    values.update(overrides)
    return ExploratoryHypothesisDraft(**values)


class ExploratoryResearchCompileTests(unittest.TestCase):
    def test_authorization_fields_compile_to_non_diagnostic_capability(self) -> None:
        result = compile_exploratory_research(
            _draft(),
            hypothesis_id="hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
            compile_arguments={
                "authorized_origin": "http://127.0.0.1:9",
                "actor": "alice",
                "own_object": "alice",
                "cross_object": "bob",
                "mode": "vulnerable",
                "query": "id=1 OR 1=1",
                "body": '{"drop":true}',
                "headers": {"X-Attack": "1"},
            },
        )
        self.assertTrue(result.compiled)
        assert result.plan is not None
        self.assertEqual(result.plan.required_capability, HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY)
        self.assertNotEqual(result.plan.required_capability, DIAGNOSTIC_ECHO_CAPABILITY)
        self.assertNotIn("query", result.plan.arguments)
        self.assertNotIn("body", result.plan.arguments)
        self.assertNotIn("headers", result.plan.arguments)

    def test_missing_semantics_is_blocked_not_diagnostic_echo(self) -> None:
        result = compile_exploratory_research(
            _draft(),
            hypothesis_id="hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
            compile_arguments={"query": "1=1", "body": "x"},
        )
        self.assertFalse(result.compiled)
        self.assertEqual(result.outcome, CompilerOutcome.BLOCKED_MISSING_SEMANTICS)
        self.assertIsNone(result.plan)

    def test_signal_object_is_not_required_for_ephemeral_draft(self) -> None:
        draft = _draft()
        self.assertTrue(draft.registry_external)
        self.assertFalse(draft.may_write_hunter_registry)
        _ = ExploratorySignal(
            signal_id="sig-1",
            research_run_id="run-1",
            kind=ExploratorySignalKind.GRAPH_STRUCTURE_ANOMALY,
            description="Graph neighborhood changed around an object node.",
            source_refs=("change-1",),
            target_node_kind="OBJECT",
        )


if __name__ == "__main__":
    unittest.main()
