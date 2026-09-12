from __future__ import annotations

from dataclasses import replace
import unittest

import pathsetup  # noqa: F401

from application.test_browser_page import (
    CREATED_AT,
    FixedClock,
    ORIGIN,
    _allow_scope,
    _compiled_scope,
    _use_case,
)
from zest.application.execute_planned_experiment import (
    AuthorizedDispatch,
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
    _request_consumption_amount,
)
from zest.application.program_research_context import (
    ProgramPolicyView,
)
from zest.core.enums import ReasonCode
from zest.data.records import (
    BudgetConsumptionRecord,
    ExecutionAttemptRecord,
    RateLimitProfileRecord,
)
from zest.research.browser_page import (
    plan_browser_observe,
)
from zest.tools.capabilities import (
    BROWSER_PAGE_CAPABILITY,
    HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY,
    HTTP_STATE_TRANSITION_CAPABILITY,
)
from support.fake_unit_of_work import (
    FakeUnitOfWorkFactory,
    _Store,
)
from support.spine import seed_spine


def _profile(
    max_requests: int,
) -> RateLimitProfileRecord:
    return RateLimitProfileRecord(
        profile_id="rl-1",
        program_id="prog-1",
        max_requests_per_window=max_requests,
        window_seconds=60,
        created_at=CREATED_AT,
    )


def _policy(
    profile: RateLimitProfileRecord,
) -> ProgramPolicyView:
    return ProgramPolicyView(
        loopback_fixture=True,
        max_response_bytes=4096,
        timeout_ms=2000,
        action_policy={},
        rate_limit_profile=profile,
    )


def _browser_command(
    *,
    experiment_id: str = "exp-1",
    hypothesis_id: str = "hyp-1",
    budget_id: str = "budget-1",
    policy: ProgramPolicyView,
) -> ExecutePlannedExperimentCommand:
    return ExecutePlannedExperimentCommand(
        experiment_id=experiment_id,
        plan=plan_browser_observe(
            hypothesis_id,
            budget_id=budget_id,
            target_reference="target-1",
            authorized_origin=ORIGIN,
            path="/",
        ),
        scope=_allow_scope(),
        compiled_scope=_compiled_scope(),
        program_policy=policy,
    )


def _seed_second_run(
    store: _Store,
) -> None:
    store.research_runs["run-2"] = replace(
        store.research_runs["run-1"],
        research_run_id="run-2",
    )
    store.issued_budgets["budget-2"] = replace(
        store.issued_budgets["budget-1"],
        budget_id="budget-2",
        research_run_id="run-2",
    )
    store.hypotheses["hyp-2"] = replace(
        store.hypotheses["hyp-1"],
        hypothesis_id="hyp-2",
        research_run_id="run-2",
        origin_reference="human-seed-2",
    )
    store.experiments["exp-2"] = replace(
        store.experiments["exp-1"],
        experiment_id="exp-2",
        research_run_id="run-2",
        hypothesis_id="hyp-2",
        budget_id="budget-2",
    )


class ProgramRequestRateLimitTests(unittest.TestCase):
    def test_browser_fanout_capped_to_remaining_program_capacity(self) -> None:
        use_case, _, port, store = _use_case(
            max_requests=150
        )

        profile = _profile(10)
        store.rate_limit_profiles[
            profile.profile_id
        ] = profile

        outcome = use_case.execute(
            _browser_command(
                policy=_policy(profile)
            )
        )

        self.assertEqual(
            outcome.status,
            ResearchLoopStatus.OBSERVATION_PRODUCED,
        )
        self.assertEqual(len(port.calls), 1)

        self.assertEqual(
            port.calls[0]["request"][
                "max_attempted_requests"
            ],
            10,
        )

        reservations = [
            item
            for item in store.budget_consumptions.values()
            if item.resource_type == "REQUEST"
        ]

        self.assertEqual(len(reservations), 1)
        self.assertEqual(
            reservations[0].amount,
            10,
        )

    def test_program_limit_aggregates_across_runs(self) -> None:
        use_case, _, port, store = _use_case(
            max_requests=150
        )
        _seed_second_run(store)

        profile = _profile(10)
        store.rate_limit_profiles[
            profile.profile_id
        ] = profile

        attempt = ExecutionAttemptRecord(
            attempt_id="ea-other",
            request_id="req-other",
            experiment_id="exp-2",
            research_run_id="run-2",
            correlation_id="corr-other",
            worker_capability=BROWSER_PAGE_CAPABILITY,
            action="observe",
            target_reference="target-1",
            budget_id="budget-2",
            side_effect_level=0,
            authorization_decision_reference=(
                "authz-other"
            ),
            state="COMPLETED",
            created_at=CREATED_AT,
            authorized_at=CREATED_AT,
        )

        store.execution_attempts[
            attempt.attempt_id
        ] = attempt

        store.execution_attempts_by_request[
            attempt.request_id
        ] = attempt.attempt_id

        store.budget_consumptions[
            "cons-other"
        ] = BudgetConsumptionRecord(
            consumption_id="cons-other",
            budget_id="budget-2",
            research_run_id="run-2",
            resource_type="REQUEST",
            amount=10,
            unit="count",
            occurred_at=CREATED_AT,
            provenance="cross-run-test",
            experiment_id="exp-2",
            request_id="req-other",
        )

        outcome = use_case.execute(
            _browser_command(
                policy=_policy(profile)
            )
        )

        self.assertEqual(
            outcome.status,
            ResearchLoopStatus.DISPATCH_DENIED,
        )
        self.assertEqual(
            outcome.core_reason_code,
            ReasonCode.RATE_LIMIT_DENIED,
        )
        self.assertEqual(len(port.calls), 0)

    def test_dispatch_recheck_closes_two_authorizations_race(self) -> None:
        use_case_1, factory, port, store = _use_case(
            max_requests=150
        )
        _seed_second_run(store)

        profile = _profile(10)
        store.rate_limit_profiles[
            profile.profile_id
        ] = profile
        policy = _policy(profile)

        use_case_2 = ExecutePlannedExperiment(
            factory,
            port,
            clock=FixedClock(),
        )

        first = use_case_1.authorize(
            _browser_command(
                policy=policy,
            )
        )

        second = use_case_2.authorize(
            _browser_command(
                experiment_id="exp-2",
                hypothesis_id="hyp-2",
                budget_id="budget-2",
                policy=policy,
            )
        )

        self.assertIsInstance(
            first,
            AuthorizedDispatch,
        )
        self.assertIsInstance(
            second,
            AuthorizedDispatch,
        )

        assert isinstance(first, AuthorizedDispatch)
        assert isinstance(second, AuthorizedDispatch)

        first_outcome = use_case_1.dispatch(first)
        second_outcome = use_case_2.dispatch(second)

        self.assertEqual(
            first_outcome.status,
            ResearchLoopStatus.OBSERVATION_PRODUCED,
        )
        self.assertEqual(
            second_outcome.status,
            ResearchLoopStatus.DISPATCH_DENIED,
        )
        self.assertEqual(
            second_outcome.core_reason_code,
            ReasonCode.RATE_LIMIT_DENIED,
        )

        self.assertEqual(len(port.calls), 1)

        reservations = [
            item
            for item in store.budget_consumptions.values()
            if item.resource_type == "REQUEST"
        ]

        self.assertEqual(
            sum(
                item.amount
                for item in reservations
            ),
            10,
        )

        self.assertEqual(
            store.execution_attempts[
                second.attempt_id
            ].state,
            "CANCELLED",
        )

    def test_four_request_capabilities_reserve_four(self) -> None:
        store = _Store()
        seed_spine(store)

        factory = FakeUnitOfWorkFactory(store)

        capabilities = (
            HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY,
            HTTP_STATE_TRANSITION_CAPABILITY,
        )

        with factory.open() as uow:
            issued = uow.issued_budgets.get(
                "budget-1"
            )
            assert issued is not None

            for index, capability in enumerate(
                capabilities
            ):
                attempt = ExecutionAttemptRecord(
                    attempt_id=(
                        f"ea-four-{index}"
                    ),
                    request_id=(
                        f"req-four-{index}"
                    ),
                    experiment_id="exp-1",
                    research_run_id="run-1",
                    correlation_id=(
                        f"corr-four-{index}"
                    ),
                    worker_capability=capability,
                    action="probe",
                    target_reference="target-1",
                    budget_id="budget-1",
                    side_effect_level=0,
                    authorization_decision_reference=(
                        f"authz-four-{index}"
                    ),
                    state="AUTHORIZED",
                    created_at=CREATED_AT,
                    authorized_at=CREATED_AT,
                )

                self.assertEqual(
                    _request_consumption_amount(
                        uow,
                        attempt,
                        issued,
                    ),
                    4,
                )

            uow.rollback()


if __name__ == "__main__":
    unittest.main()
