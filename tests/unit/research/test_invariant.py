from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.invariant import (
    InvariantAdmissionOutcome,
    InvariantCounterexample,
    InvariantKind,
    InvariantProposal,
    InvariantStatus,
    admit_invariant,
    apply_invariant_counterexample,
    propose_diagnostic_echo_invariant,
)
from zest.research.target_model import TargetObservationView


def _view(**overrides) -> TargetObservationView:
    values = dict(
        observation_id="obs-1",
        research_run_id="run-1",
        experiment_id="exp-1",
        observation_kind="diagnostic.echo",
        payload={"echoed": "alpha"},
        capability="diagnostic.echo",
        action="echo",
        actor_handle="worker-local",
        resource_handle="target-1",
        submitted_input="alpha",
    )
    values.update(overrides)
    return TargetObservationView(**values)


class DiagnosticInvariantTests(unittest.TestCase):
    def test_deterministic_proposal_is_admitted_as_testable_hypothesis(self) -> None:
        proposal = propose_diagnostic_echo_invariant(
            "run-1", (_view(),), proposal_id="inv-1"
        )
        assert proposal is not None
        self.assertEqual(proposal.invariant_kind, InvariantKind.INPUT_OUTPUT_RELATION)
        decision = admit_invariant(
            proposal,
            research_run_id="run-1",
            resolvable_source_ids=frozenset({"obs-1"}),
        )
        self.assertEqual(decision.outcome, InvariantAdmissionOutcome.ADMITTED)
        assert decision.hypothesis is not None
        self.assertEqual(decision.hypothesis.status, InvariantStatus.TESTABLE)
        self.assertNotEqual(decision.hypothesis.status.name, "OBSERVED")

    def test_empty_views_are_untestable(self) -> None:
        self.assertIsNone(propose_diagnostic_echo_invariant("run-1", (), proposal_id="inv-1"))

    def test_hallucinated_refs_are_rejected(self) -> None:
        proposal = propose_diagnostic_echo_invariant(
            "run-1", (_view(),), proposal_id="inv-1"
        )
        assert proposal is not None
        decision = admit_invariant(
            proposal,
            research_run_id="run-1",
            resolvable_source_ids=frozenset({"obs-other"}),
        )
        self.assertEqual(decision.outcome, InvariantAdmissionOutcome.NEEDS_MORE_CONTEXT)

    def test_cross_run_is_rejected(self) -> None:
        proposal = propose_diagnostic_echo_invariant(
            "run-1", (_view(),), proposal_id="inv-1"
        )
        assert proposal is not None
        decision = admit_invariant(
            proposal,
            research_run_id="run-2",
            resolvable_source_ids=frozenset({"obs-1"}),
        )
        self.assertEqual(decision.outcome, InvariantAdmissionOutcome.REJECTED_CROSS_RUN)

    def test_unacknowledged_contradiction_is_rejected(self) -> None:
        proposal = propose_diagnostic_echo_invariant(
            "run-1", (_view(),), proposal_id="inv-1"
        )
        assert proposal is not None
        decision = admit_invariant(
            proposal,
            research_run_id="run-1",
            resolvable_source_ids=frozenset({"obs-1"}),
            contradicting_source_ids=frozenset({"obs-1"}),
        )
        self.assertEqual(decision.outcome, InvariantAdmissionOutcome.REJECTED_CONTRADICTED)

    def test_authorization_claim_is_rejected(self) -> None:
        decision = admit_invariant(
            InvariantProposal(
                proposal_id="inv-1",
                research_run_id="run-1",
                invariant_kind=InvariantKind.INPUT_OUTPUT_RELATION,
                subject_refs=("target-1",),
                expected_behavior="only owner should change scope",
                source_refs=("obs-1",),
                applicability_context={"capability": "diagnostic.echo"},
                assumptions=("none",),
                known_counterexample_refs=(),
                falsification_direction="submit a mismatch",
                proposer_provenance="test",
            ),
            research_run_id="run-1",
            resolvable_source_ids=frozenset({"obs-1"}),
        )
        self.assertEqual(
            decision.outcome, InvariantAdmissionOutcome.REJECTED_POLICY_CONFLICT
        )

    def test_acknowledged_mismatch_is_challenged_not_globally_false(self) -> None:
        proposal = propose_diagnostic_echo_invariant(
            "run-1",
            (_view(payload={"echoed": "nope"}, submitted_input="alpha"),),
            proposal_id="inv-1",
        )
        assert proposal is not None
        self.assertEqual(proposal.known_counterexample_refs, ("obs-1",))
        decision = admit_invariant(
            proposal,
            research_run_id="run-1",
            resolvable_source_ids=frozenset({"obs-1"}),
            contradicting_source_ids=frozenset({"obs-1"}),
        )
        self.assertTrue(decision.admitted)
        assert decision.hypothesis is not None
        self.assertEqual(decision.hypothesis.status, InvariantStatus.CHALLENGED)

    def test_counterexample_is_context_bound_and_never_observed(self) -> None:
        proposal = propose_diagnostic_echo_invariant(
            "run-1", (_view(),), proposal_id="inv-1"
        )
        assert proposal is not None
        decision = admit_invariant(
            proposal,
            research_run_id="run-1",
            resolvable_source_ids=frozenset({"obs-1"}),
        )
        assert decision.hypothesis is not None
        updated = apply_invariant_counterexample(
            decision.hypothesis,
            InvariantCounterexample(
                counterexample_id="cx-1",
                invariant_id="inv-1",
                source_ref="obs-1",
                applicability_context={"input": "alpha", "not_global": True},
            ),
        )
        self.assertEqual(updated.status, InvariantStatus.CHALLENGED)
        self.assertEqual(updated.counterexample_refs, ("obs-1",))
        self.assertEqual(
            updated.applicability_context.get("capability"), "diagnostic.echo"
        )
        self.assertNotEqual(updated.status.name, "OBSERVED")
        self.assertNotEqual(updated.status.name, "CONFIRMED_TRUE")


if __name__ == "__main__":
    unittest.main()
