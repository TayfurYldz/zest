from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.chain import (
    ChainEdgeKind,
    ChainHypothesis,
    ChainNodeKind,
    ChainOutcome,
    ChainSearchLimits,
    ChainStep,
    admit_chain_hypothesis,
    chain_structural_identity,
    compose_diagnostic_echo_chains,
    experiment_plan_for_chain_step,
)
from zest.research.target_model import TargetEpistemicStatus, TargetObservationView


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


def _step(**overrides) -> ChainStep:
    values = dict(
        step_index=0,
        node_kind=ChainNodeKind.OBSERVATION,
        source_ref="obs-a",
        epistemic_status=TargetEpistemicStatus.OBSERVED,
        state_signature="input=alpha",
        side_effect_level=0,
        statement="Diagnostic observation obs-a was produced.",
        experiment_id="exp-1",
    )
    values.update(overrides)
    return ChainStep(**values)


class DiagnosticChainTests(unittest.TestCase):
    def test_two_diagnostic_steps_compose(self) -> None:
        decisions = compose_diagnostic_echo_chains(
            "run-1",
            (
                _view(),
                _view(
                    observation_id="obs-b",
                    experiment_id="exp-2",
                    payload={"echoed": "beta"},
                    submitted_input="beta",
                ),
            ),
            chain_id_prefix="chain",
            invariant_id="inv-1",
        )
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].outcome, ChainOutcome.ADMITTED)
        assert decisions[0].hypothesis is not None
        self.assertGreaterEqual(decisions[0].hypothesis.depth, 1)
        self.assertTrue(
            any(
                step.epistemic_status is TargetEpistemicStatus.HYPOTHESIZED
                for step in decisions[0].hypothesis.steps
            )
        )

    def test_missing_precondition_is_rejected(self) -> None:
        decisions = compose_diagnostic_echo_chains(
            "run-1", (_view(),), chain_id_prefix="chain"
        )
        self.assertEqual(decisions[0].outcome, ChainOutcome.REJECTED_MISSING_PRECONDITION)

    def test_unsupported_causal_leap_is_rejected(self) -> None:
        steps = (
            _step(),
            _step(
                step_index=1,
                source_ref="obs-b",
                state_signature="input=beta",
                incoming_edge=ChainEdgeKind.PRODUCES,
                experiment_id="exp-2",
                statement="Observation obs-b was produced.",
            ),
        )
        draft = ChainHypothesis(
            chain_id="chain-1",
            research_run_id="run-1",
            steps=steps,
            source_refs=("obs-a", "obs-b"),
            preconditions=("none",),
            expected_resulting_capability="CAN_OBSERVE_ECHO",
            unresolved_assumptions=(),
            falsification_points=("mismatch",),
            strategy_version="chain.diagnostic.echo.v1",
            structural_identity=chain_structural_identity(steps),
        )
        decision = admit_chain_hypothesis(
            draft,
            research_run_id="run-1",
            resolvable_source_ids=frozenset({"obs-a", "obs-b"}),
        )
        self.assertEqual(
            decision.outcome, ChainOutcome.REJECTED_UNSUPPORTED_CAUSAL_LEAP
        )

    def test_inferred_intermediate_remains_inferred(self) -> None:
        decisions = compose_diagnostic_echo_chains(
            "run-1",
            (
                _view(),
                _view(
                    observation_id="obs-b",
                    experiment_id="exp-2",
                    payload={"echoed": "beta"},
                    submitted_input="beta",
                ),
            ),
            chain_id_prefix="chain",
            inferred_intermediate=_step(
                node_kind=ChainNodeKind.STATE,
                source_ref="state-inferred",
                epistemic_status=TargetEpistemicStatus.INFERRED,
                state_signature="input=alpha|inferred",
                statement="Inferred intermediate diagnostic state. Not observed.",
            ),
        )
        self.assertEqual(decisions[0].outcome, ChainOutcome.ADMITTED)
        assert decisions[0].hypothesis is not None
        inferred = [
            step
            for step in decisions[0].hypothesis.steps
            if step.epistemic_status is TargetEpistemicStatus.INFERRED
        ]
        self.assertEqual(len(inferred), 1)
        self.assertEqual(inferred[0].epistemic_status, TargetEpistemicStatus.INFERRED)

    def test_cycle_is_rejected(self) -> None:
        steps = (
            _step(),
            _step(
                step_index=1,
                incoming_edge=ChainEdgeKind.ENABLES,
                statement="Repeated diagnostic observation obs-a was produced.",
            ),
        )
        draft = ChainHypothesis(
            chain_id="chain-1",
            research_run_id="run-1",
            steps=steps,
            source_refs=("obs-a", "obs-a"),
            preconditions=("none",),
            expected_resulting_capability="CAN_OBSERVE_ECHO",
            unresolved_assumptions=(),
            falsification_points=("mismatch",),
            strategy_version="chain.diagnostic.echo.v1",
            structural_identity=chain_structural_identity(steps),
        )
        decision = admit_chain_hypothesis(
            draft,
            research_run_id="run-1",
            resolvable_source_ids=frozenset({"obs-a"}),
        )
        self.assertEqual(decision.outcome, ChainOutcome.REJECTED_CYCLE)

    def test_same_actions_different_context_have_distinct_identity(self) -> None:
        first = compose_diagnostic_echo_chains(
            "run-1",
            (
                _view(),
                _view(
                    observation_id="obs-b",
                    experiment_id="exp-2",
                    payload={"echoed": "beta"},
                    submitted_input="beta",
                ),
            ),
            chain_id_prefix="chain-a",
        )
        second = compose_diagnostic_echo_chains(
            "run-1",
            (
                _view(submitted_input="gamma", payload={"echoed": "gamma"}),
                _view(
                    observation_id="obs-b",
                    experiment_id="exp-2",
                    payload={"echoed": "delta"},
                    submitted_input="delta",
                ),
            ),
            chain_id_prefix="chain-b",
        )
        assert first[0].hypothesis is not None
        assert second[0].hypothesis is not None
        self.assertNotEqual(
            first[0].hypothesis.structural_identity,
            second[0].hypothesis.structural_identity,
        )

    def test_structurally_equal_chain_same_context_has_same_identity(self) -> None:
        views = (
            _view(),
            _view(
                observation_id="obs-b",
                experiment_id="exp-2",
                payload={"echoed": "beta"},
                submitted_input="beta",
            ),
        )
        first = compose_diagnostic_echo_chains(
            "run-1", views, chain_id_prefix="chain-a"
        )
        second = compose_diagnostic_echo_chains(
            "run-1", views, chain_id_prefix="chain-b"
        )
        assert first[0].hypothesis is not None
        assert second[0].hypothesis is not None
        self.assertEqual(
            first[0].hypothesis.structural_identity,
            second[0].hypothesis.structural_identity,
        )

    def test_max_branching_is_respected(self) -> None:
        views = (
            _view(),
            _view(
                observation_id="obs-b",
                experiment_id="exp-2",
                payload={"echoed": "beta"},
                submitted_input="beta",
            ),
            _view(
                observation_id="obs-c",
                experiment_id="exp-3",
                payload={"echoed": "gamma"},
                submitted_input="gamma",
            ),
        )
        decisions = compose_diagnostic_echo_chains(
            "run-1",
            views,
            chain_id_prefix="chain",
            limits=ChainSearchLimits(max_depth=4, max_branching=1, max_generated_chains=2),
        )
        self.assertEqual(len(decisions), 1)

    def test_max_depth_zero_rejects(self) -> None:
        decisions = compose_diagnostic_echo_chains(
            "run-1",
            (
                _view(),
                _view(observation_id="obs-b", experiment_id="exp-2", submitted_input="beta"),
            ),
            chain_id_prefix="chain",
            limits=ChainSearchLimits(max_depth=0, max_branching=1, max_generated_chains=2),
        )
        self.assertEqual(decisions[0].outcome, ChainOutcome.REJECTED_LIMIT)

    def test_plan_from_chain_does_not_dispatch(self) -> None:
        step = _step(side_effect_level=3)
        plan = experiment_plan_for_chain_step(
            step,
            hypothesis_id="hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
        )
        self.assertEqual(plan.side_effect_level, 0)
        self.assertFalse(hasattr(plan, "invoke"))


if __name__ == "__main__":
    unittest.main()
