from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.target_model import (
    TargetElementKind,
    TargetEpistemicStatus,
    TargetInferenceDraft,
    TargetInferenceOutcome,
    TargetObservationView,
    admit_target_inference,
    project_diagnostic_target_model,
)
from zest.research.types import ResearchInputError


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


class TargetModelProjectionTests(unittest.TestCase):
    def test_observed_and_derived_stay_distinct(self) -> None:
        projection = project_diagnostic_target_model("run-1", (_view(),))
        statuses = {item.element_id: item.epistemic_status for item in projection.elements}
        self.assertEqual(statuses["actor:worker-local"], TargetEpistemicStatus.OBSERVED)
        self.assertEqual(statuses["action:diagnostic.echo:echo"], TargetEpistemicStatus.OBSERVED)
        self.assertEqual(statuses["derived:echo:obs-1"], TargetEpistemicStatus.DERIVED)
        self.assertFalse(
            any(
                item.epistemic_status is TargetEpistemicStatus.INFERRED
                for item in projection.elements
            )
        )
        ownership = [
            item
            for item in projection.elements
            if item.kind is TargetElementKind.RELATIONSHIP
        ]
        self.assertTrue(all(item.attributes.get("not_ownership") for item in ownership))

    def test_session_secret_attributes_are_rejected(self) -> None:
        with self.assertRaises(ResearchInputError):
            _view(payload={"echoed": "alpha", "session_token": "secret"})

    def test_cross_run_view_is_rejected(self) -> None:
        with self.assertRaises(ResearchInputError):
            project_diagnostic_target_model("run-1", (_view(research_run_id="run-2"),))


class TargetInferenceAdmissionTests(unittest.TestCase):
    def test_inferred_stays_inferred(self) -> None:
        decision = admit_target_inference(
            TargetInferenceDraft(
                inference_id="inf-1",
                research_run_id="run-1",
                kind=TargetElementKind.RELATIONSHIP,
                epistemic_status=TargetEpistemicStatus.INFERRED,
                opaque_ref="maybe-related",
                statement="Actor handle may be related to the diagnostic resource.",
                source_refs=("obs-1",),
                attributes={"not_ownership": True},
            ),
            research_run_id="run-1",
            resolvable_source_ids=frozenset({"obs-1"}),
        )
        self.assertTrue(decision.admitted)
        assert decision.element is not None
        self.assertEqual(decision.element.epistemic_status, TargetEpistemicStatus.INFERRED)

    def test_inference_cannot_become_observed(self) -> None:
        decision = admit_target_inference(
            TargetInferenceDraft(
                inference_id="inf-1",
                research_run_id="run-1",
                kind=TargetElementKind.RELATIONSHIP,
                epistemic_status=TargetEpistemicStatus.OBSERVED,
                opaque_ref="owns",
                statement="Actor handle executed diagnostic action.",
                source_refs=("obs-1",),
                attributes={},
            ),
            research_run_id="run-1",
            resolvable_source_ids=frozenset({"obs-1"}),
        )
        self.assertEqual(
            decision.outcome, TargetInferenceOutcome.REJECTED_EPISTEMIC_UPGRADE
        )

    def test_hallucinated_source_is_rejected(self) -> None:
        decision = admit_target_inference(
            TargetInferenceDraft(
                inference_id="inf-1",
                research_run_id="run-1",
                kind=TargetElementKind.RELATIONSHIP,
                epistemic_status=TargetEpistemicStatus.HYPOTHESIZED,
                opaque_ref="ghost",
                statement="A related diagnostic handle may exist.",
                source_refs=("obs-missing",),
                attributes={},
            ),
            research_run_id="run-1",
            resolvable_source_ids=frozenset({"obs-1"}),
        )
        self.assertEqual(
            decision.outcome, TargetInferenceOutcome.REJECTED_HALLUCINATED_SOURCE
        )

    def test_cross_run_draft_is_rejected(self) -> None:
        decision = admit_target_inference(
            TargetInferenceDraft(
                inference_id="inf-1",
                research_run_id="run-2",
                kind=TargetElementKind.RELATIONSHIP,
                epistemic_status=TargetEpistemicStatus.INFERRED,
                opaque_ref="other",
                statement="A related diagnostic handle may exist.",
                source_refs=("obs-1",),
                attributes={},
            ),
            research_run_id="run-1",
            resolvable_source_ids=frozenset({"obs-1"}),
        )
        self.assertEqual(decision.outcome, TargetInferenceOutcome.REJECTED_CROSS_RUN)

    def test_vulnerability_claim_is_rejected(self) -> None:
        decision = admit_target_inference(
            TargetInferenceDraft(
                inference_id="inf-1",
                research_run_id="run-1",
                kind=TargetElementKind.RELATIONSHIP,
                epistemic_status=TargetEpistemicStatus.INFERRED,
                opaque_ref="idor",
                statement="This is an IDOR vulnerability because actor owns the resource.",
                source_refs=("obs-1",),
                attributes={},
            ),
            research_run_id="run-1",
            resolvable_source_ids=frozenset({"obs-1"}),
        )
        self.assertEqual(
            decision.outcome, TargetInferenceOutcome.REJECTED_POLICY_CONFLICT
        )


if __name__ == "__main__":
    unittest.main()
