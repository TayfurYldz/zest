from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.orchestration import (
    NextCycleAction,
    OrchestrationBounds,
    OrchestrationUsage,
    StopReason,
    check_orchestration_bounds,
    next_cycle_action,
)
from zest.research.types import ResearchInputError


def _bounds(**overrides) -> OrchestrationBounds:
    values = dict(
        max_cycles=3,
        max_experiments=3,
        max_model_calls=12,
        max_worker_invocations=3,
        max_elapsed_ms=60_000,
        max_selected_opportunities=1,
        max_runtime_fallback=0,
        side_effect_ceiling=0,
    )
    values.update(overrides)
    return OrchestrationBounds(**values)


def _usage(**overrides) -> OrchestrationUsage:
    values = dict(
        cycles_completed=0,
        experiments_executed=0,
        model_calls=0,
        worker_invocations=0,
        elapsed_ms=0,
        opportunities_selected=0,
        runtime_fallbacks=0,
    )
    values.update(overrides)
    return OrchestrationUsage(**values)


class OrchestrationPolicyTests(unittest.TestCase):
    def test_zero_max_cycles_is_no_allowance(self) -> None:
        check = check_orchestration_bounds(_bounds(max_cycles=0), _usage())
        self.assertFalse(check.allowed)
        self.assertEqual(check.stop_reason, StopReason.MAX_CYCLES_REACHED)

    def test_negative_bound_is_invalid(self) -> None:
        with self.assertRaises(ResearchInputError):
            _bounds(max_cycles=-1)

    def test_unknown_outcome_stops_without_retry_license(self) -> None:
        action, reason = next_cycle_action(
            bounds=_bounds(),
            usage=_usage(),
            selected_count=0,
            hypothesis_count=0,
            unknown_outcome_open=True,
        )
        self.assertEqual(action, NextCycleAction.STOP)
        self.assertEqual(reason, StopReason.OPERATIONAL_FAILURE)

    def test_empty_run_bootstraps_diagnostic(self) -> None:
        action, reason = next_cycle_action(
            bounds=_bounds(),
            usage=_usage(),
            selected_count=0,
            hypothesis_count=0,
            unknown_outcome_open=False,
        )
        self.assertEqual(action, NextCycleAction.BOOTSTRAP_DIAGNOSTIC)
        self.assertIsNone(reason)

    def test_no_more_opportunities_when_hypotheses_exist(self) -> None:
        action, reason = next_cycle_action(
            bounds=_bounds(),
            usage=_usage(cycles_completed=1),
            selected_count=0,
            hypothesis_count=1,
            unknown_outcome_open=False,
        )
        self.assertEqual(action, NextCycleAction.STOP)
        self.assertEqual(reason, StopReason.COMPLETED_NO_MORE_OPPORTUNITIES)

    def test_finding_is_not_an_orchestration_state(self) -> None:
        from zest.research.orchestration import OrchestrationState

        names = {item.value for item in OrchestrationState}
        self.assertNotIn("VULNERABILITY_FOUND", names)
        self.assertNotIn("FINDING_CREATED", names)


if __name__ == "__main__":
    unittest.main()
