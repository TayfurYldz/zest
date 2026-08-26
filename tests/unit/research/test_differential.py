from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.differential import (
    DifferentialCase,
    DifferentialDimension,
    DifferentialInterpretation,
    DifferentialOutcome,
    compare_diagnostic_differential,
)
from zest.research.target_model import TargetObservationView


def _view(**overrides) -> TargetObservationView:
    values = dict(
        observation_id="obs-a",
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


def _case(**overrides) -> DifferentialCase:
    values = dict(
        case_id="case-1",
        research_run_id="run-1",
        baseline_observation_ids=("obs-a",),
        variant_observation_ids=("obs-b",),
        changed_dimensions=(DifferentialDimension.INPUT,),
        common_dimensions=(
            DifferentialDimension.ACTOR,
            DifferentialDimension.ACTION,
            DifferentialDimension.RESOURCE,
        ),
    )
    values.update(overrides)
    return DifferentialCase(**values)


class DiagnosticDifferentialTests(unittest.TestCase):
    def test_controlled_input_difference_is_not_a_vulnerability(self) -> None:
        decision = compare_diagnostic_differential(
            _case(),
            (
                _view(),
                _view(
                    observation_id="obs-b",
                    experiment_id="exp-2",
                    payload={"echoed": "beta"},
                    submitted_input="beta",
                ),
            ),
            differential_id="diff-1",
        )
        self.assertEqual(decision.outcome, DifferentialOutcome.COMPARED)
        assert decision.observation is not None
        self.assertEqual(
            decision.observation.interpretation,
            DifferentialInterpretation.CONTROLLED_DIFFERENCE,
        )
        self.assertIn(DifferentialDimension.INPUT, decision.observation.changed_dimensions)
        self.assertIn(DifferentialDimension.ACTION, decision.observation.common_dimensions)
        self.assertTrue(decision.observation.observed_similarities["not_a_vulnerability"])
        self.assertIn("INPUT", decision.observation.observed_differences)

    def test_unrelated_action_change_is_incomparable(self) -> None:
        decision = compare_diagnostic_differential(
            _case(),
            (
                _view(),
                _view(
                    observation_id="obs-b",
                    action="other",
                    payload={"echoed": "beta"},
                    submitted_input="beta",
                ),
            ),
            differential_id="diff-1",
        )
        self.assertEqual(decision.outcome, DifferentialOutcome.REJECTED_UNCONTROLLED)

    def test_same_response_is_not_authorization_truth(self) -> None:
        decision = compare_diagnostic_differential(
            _case(changed_dimensions=(DifferentialDimension.STATE,)),
            (
                _view(),
                _view(observation_id="obs-b", experiment_id="exp-2"),
            ),
            differential_id="diff-1",
        )
        self.assertEqual(decision.outcome, DifferentialOutcome.COMPARED)
        assert decision.observation is not None
        self.assertEqual(
            decision.observation.interpretation, DifferentialInterpretation.EQUIVALENT
        )
        self.assertTrue(
            decision.observation.observed_similarities["not_authorization_proof"]
        )

    def test_missing_source_is_rejected(self) -> None:
        decision = compare_diagnostic_differential(
            _case(),
            (_view(),),
            differential_id="diff-1",
        )
        self.assertEqual(decision.outcome, DifferentialOutcome.REJECTED_MISSING_SOURCE)

    def test_cross_run_is_rejected(self) -> None:
        decision = compare_diagnostic_differential(
            _case(),
            (
                _view(),
                _view(
                    observation_id="obs-b",
                    research_run_id="run-2",
                    submitted_input="beta",
                    payload={"echoed": "beta"},
                ),
            ),
            differential_id="diff-1",
        )
        self.assertEqual(decision.outcome, DifferentialOutcome.REJECTED_CROSS_RUN)

    def test_time_dimension_is_deferred(self) -> None:
        decision = compare_diagnostic_differential(
            _case(changed_dimensions=(DifferentialDimension.TIME,)),
            (_view(), _view(observation_id="obs-b")),
            differential_id="diff-1",
        )
        self.assertEqual(
            decision.outcome, DifferentialOutcome.REJECTED_MISSING_TEMPORAL_PROVENANCE
        )

    def test_time_dimension_requires_material_snapshot_backed_change(self) -> None:
        from datetime import datetime, timezone

        from zest.research.temporal import ResearchSnapshot

        t1 = datetime(2026, 8, 17, 1, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 8, 17, 2, 0, tzinfo=timezone.utc)
        snapshots = (
            ResearchSnapshot(
                snapshot_id="snap-1",
                research_run_id="run-1",
                program_id="prog-1",
                target_identity="target-1",
                observation_ids=("obs-a",),
                captured_at=t1,
                strategy_version="temporal.diagnostic.echo.v1",
            ),
            ResearchSnapshot(
                snapshot_id="snap-2",
                research_run_id="run-1",
                program_id="prog-1",
                target_identity="target-1",
                observation_ids=("obs-b",),
                captured_at=t2,
                strategy_version="temporal.diagnostic.echo.v1",
            ),
        )
        decision = compare_diagnostic_differential(
            _case(
                changed_dimensions=(DifferentialDimension.TIME, DifferentialDimension.INPUT),
                common_dimensions=(DifferentialDimension.ACTION,),
                baseline_snapshot_id="snap-1",
                variant_snapshot_id="snap-2",
                variant_observation_ids=("obs-b",),
            ),
            (
                _view(),
                _view(
                    observation_id="obs-b",
                    submitted_input="beta",
                    payload={"echoed": "beta"},
                ),
            ),
            differential_id="diff-1",
            snapshots=snapshots,
        )
        self.assertEqual(decision.outcome, DifferentialOutcome.COMPARED)


    def test_timestamp_only_time_is_not_temporal(self) -> None:
        from datetime import datetime, timezone

        from zest.research.temporal import ResearchSnapshot

        t1 = datetime(2026, 8, 17, 1, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 8, 17, 2, 0, tzinfo=timezone.utc)
        snapshots = (
            ResearchSnapshot(
                snapshot_id="snap-1",
                research_run_id="run-1",
                program_id="prog-1",
                target_identity="target-1",
                observation_ids=("obs-a",),
                captured_at=t1,
                strategy_version="temporal.diagnostic.echo.v1",
            ),
            ResearchSnapshot(
                snapshot_id="snap-2",
                research_run_id="run-1",
                program_id="prog-1",
                target_identity="target-1",
                observation_ids=("obs-a",),
                captured_at=t2,
                strategy_version="temporal.diagnostic.echo.v1",
            ),
        )
        decision = compare_diagnostic_differential(
            _case(
                changed_dimensions=(DifferentialDimension.TIME,),
                variant_observation_ids=("obs-a",),
                baseline_snapshot_id="snap-1",
                variant_snapshot_id="snap-2",
            ),
            (_view(),),
            differential_id="diff-1",
            snapshots=snapshots,
        )
        self.assertEqual(decision.outcome, DifferentialOutcome.REJECTED_UNCONTROLLED)
        self.assertIn("TIMESTAMP_ONLY_NOT_TEMPORAL", decision.reason_codes)


if __name__ == "__main__":
    unittest.main()
