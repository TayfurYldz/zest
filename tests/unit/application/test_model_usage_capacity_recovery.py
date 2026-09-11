from __future__ import annotations

from datetime import timedelta
import unittest

from zest.application.classify_runtime_recovery import (
    ClassifyRuntimeRecovery,
    RuntimeRecoveryAction,
)
from zest.application.model_usage_capacity_recovery import (
    RecoverModelUsageCapacity,
    model_usage_capacity_recovery_proof,
)
from zest.data.errors import LeaseFencingError
from zest.data.records import (
    ResearchAdmissionRecord,
)
from zest.research.admission import AdmissionOutcome
from zest.research.orchestration import (
    OrchestrationPhase,
    OrchestrationState,
    StopReason,
)

from support.fake_unit_of_work import (
    FakeUnitOfWorkFactory,
)
from support.spine import CREATED_AT

from tests.unit.application.test_zestd import (
    FixedClock,
    _attempt,
    _orchestration_record,
    _seed,
)


def _admission(
    admission_record_id: str,
    *,
    reason_code: str = "MODEL_USAGE_LIMITED",
    created_at=CREATED_AT,
) -> ResearchAdmissionRecord:
    return ResearchAdmissionRecord(
        admission_record_id=admission_record_id,
        research_run_id="run-1",
        outcome=(
            AdmissionOutcome
            .MODEL_INVOCATION_FAILED.value
        ),
        reason="provider model failure",
        reason_code=reason_code,
        context_fingerprint="ctx-recovery",
        created_at=created_at,
    )


def _blocked_usage_record(
    *,
    checkpoint_at=CREATED_AT,
    active_cycle_id=None,
):
    return _orchestration_record(
        state=OrchestrationState.BLOCKED.value,
        stop_reason=StopReason.RATE_LIMITED.value,
        current_phase=(
            OrchestrationPhase.CYCLE_COMPLETE.value
        ),
        last_phase="model_runtime_outcome",
        active_cycle_id=active_cycle_id,
        checkpoint_at=checkpoint_at,
    )


class ModelUsageCapacityProofTests(
    unittest.TestCase
):
    def test_exact_usage_block_is_recoverable(
        self,
    ) -> None:
        store = _seed()

        orchestration = _blocked_usage_record()

        proof = (
            model_usage_capacity_recovery_proof(
                orchestration,
                [
                    _admission(
                        "adm-usage-1"
                    )
                ],
                [],
            )
        )

        self.assertIsNotNone(proof)
        self.assertEqual(
            proof.admission_record_id,
            "adm-usage-1",
        )

    def test_generic_rate_limit_is_not_usage_recovery(
        self,
    ) -> None:
        proof = (
            model_usage_capacity_recovery_proof(
                _blocked_usage_record(),
                [
                    _admission(
                        "adm-generic-1",
                        reason_code=(
                            "MODEL_RATE_LIMITED"
                        ),
                    )
                ],
                [],
            )
        )

        self.assertIsNone(proof)

    def test_unsafe_worker_attempt_blocks_recovery(
        self,
    ) -> None:
        for state in (
            "AUTHORIZED",
            "DISPATCHING",
            "UNKNOWN_OUTCOME",
        ):
            with self.subTest(state=state):
                proof = (
                    model_usage_capacity_recovery_proof(
                        _blocked_usage_record(),
                        [
                            _admission(
                                "adm-usage-1"
                            )
                        ],
                        [
                            _attempt(state)
                        ],
                    )
                )

                self.assertIsNone(proof)

    def test_created_at_not_opaque_id_selects_latest(
        self,
    ) -> None:
        checkpoint = (
            CREATED_AT
            + timedelta(seconds=10)
        )

        # Lexical id order intentionally points the opposite way:
        # "adm-z-old" sorts after "adm-a-new".
        admissions = [
            _admission(
                "adm-z-old",
                reason_code=(
                    "MODEL_USAGE_LIMITED"
                ),
                created_at=(
                    CREATED_AT
                    + timedelta(seconds=1)
                ),
            ),
            _admission(
                "adm-a-new",
                reason_code=(
                    "MODEL_RATE_LIMITED"
                ),
                created_at=(
                    CREATED_AT
                    + timedelta(seconds=2)
                ),
            ),
        ]

        proof = (
            model_usage_capacity_recovery_proof(
                _blocked_usage_record(
                    checkpoint_at=checkpoint
                ),
                admissions,
                [],
            )
        )

        self.assertIsNone(proof)

    def test_ambiguous_latest_timestamp_fails_closed(
        self,
    ) -> None:
        latest = (
            CREATED_AT
            + timedelta(seconds=1)
        )

        proof = (
            model_usage_capacity_recovery_proof(
                _blocked_usage_record(
                    checkpoint_at=(
                        CREATED_AT
                        + timedelta(seconds=2)
                    )
                ),
                [
                    _admission(
                        "adm-usage-a",
                        created_at=latest,
                    ),
                    _admission(
                        "adm-usage-b",
                        created_at=latest,
                    ),
                ],
                [],
            )
        )

        self.assertIsNone(proof)

    def test_post_checkpoint_admission_fails_closed(
        self,
    ) -> None:
        proof = (
            model_usage_capacity_recovery_proof(
                _blocked_usage_record(),
                [
                    _admission(
                        "adm-future",
                        created_at=(
                            CREATED_AT
                            + timedelta(seconds=1)
                        ),
                    )
                ],
                [],
            )
        )

        self.assertIsNone(proof)

    def test_active_cycle_is_never_revived(
        self,
    ) -> None:
        proof = (
            model_usage_capacity_recovery_proof(
                _blocked_usage_record(
                    active_cycle_id="cycle-stale"
                ),
                [
                    _admission(
                        "adm-usage-1"
                    )
                ],
                [],
            )
        )

        self.assertIsNone(proof)


class ModelUsageCapacityClassifierTests(
    unittest.TestCase
):
    def test_classifier_exposes_exact_usage_capacity_action(
        self,
    ) -> None:
        store = _seed()

        store.research_orchestrations[
            "run-1"
        ] = _blocked_usage_record()

        store.research_admissions[
            "adm-usage-1"
        ] = _admission(
            "adm-usage-1"
        )

        decision = ClassifyRuntimeRecovery(
            FakeUnitOfWorkFactory(store)
        ).execute("run-1")

        self.assertIs(
            decision.action,
            RuntimeRecoveryAction
            .SAFE_RETRY_AFTER_MODEL_CAPACITY,
        )

    def test_generic_blocked_rate_limit_stays_nonresumable(
        self,
    ) -> None:
        store = _seed()

        store.research_orchestrations[
            "run-1"
        ] = _blocked_usage_record()

        store.research_admissions[
            "adm-generic-1"
        ] = _admission(
            "adm-generic-1",
            reason_code="MODEL_RATE_LIMITED",
        )

        decision = ClassifyRuntimeRecovery(
            FakeUnitOfWorkFactory(store)
        ).execute("run-1")

        self.assertIs(
            decision.action,
            RuntimeRecoveryAction.DO_NOT_RESUME,
        )

    def test_blocked_row_is_visible_to_restart_scan(
        self,
    ) -> None:
        store = _seed()

        store.research_orchestrations[
            "run-1"
        ] = _blocked_usage_record()

        factory = FakeUnitOfWorkFactory(
            store
        )

        with factory.open() as uow:
            rows = (
                uow.research_orchestrations
                .list_recoverable()
            )
            uow.rollback()

        self.assertEqual(
            [
                item.research_run_id
                for item in rows
            ],
            ["run-1"],
        )


class RecoverModelUsageCapacityTests(
    unittest.TestCase
):
    def _store(self):
        store = _seed()

        store.research_orchestrations[
            "run-1"
        ] = _blocked_usage_record()

        store.research_admissions[
            "adm-usage-1"
        ] = _admission(
            "adm-usage-1"
        )

        return store

    def test_transition_is_new_cycle_boundary_not_old_cycle_revival(
        self,
    ) -> None:
        store = self._store()

        before = (
            store.research_orchestrations[
                "run-1"
            ]
        )

        before_cycle_count = len(
            store.research_cycles
        )

        result = RecoverModelUsageCapacity(
            FakeUnitOfWorkFactory(store),
            clock=FixedClock(),
        ).execute("run-1")

        self.assertTrue(
            result.recovered
        )

        self.assertEqual(
            result.admission_record_id,
            "adm-usage-1",
        )

        after = (
            store.research_orchestrations[
                "run-1"
            ]
        )

        self.assertEqual(
            after.state,
            OrchestrationState.READY.value,
        )

        self.assertIsNone(
            after.stop_reason
        )

        self.assertIsNone(
            after.pause_reason
        )

        self.assertEqual(
            after.current_phase,
            OrchestrationPhase
            .CYCLE_COMPLETE.value,
        )

        self.assertIsNone(
            after.active_cycle_id
        )

        self.assertEqual(
            after.cycle_number,
            before.cycle_number,
        )

        self.assertEqual(
            len(store.research_cycles),
            before_cycle_count,
        )

        events = [
            event
            for event
            in store.audit_events.values()
            if (
                event.event_type
                == "MODEL_USAGE_CAPACITY_RECOVERED"
            )
        ]

        self.assertEqual(
            len(events),
            1,
        )

        payload = events[0].payload

        self.assertFalse(
            payload["cycle_incremented"]
        )

        self.assertFalse(
            payload["active_cycle_restored"]
        )

        self.assertFalse(
            payload["authority_expanded"]
        )

        self.assertTrue(
            payload["not_authorization"]
        )

        self.assertTrue(
            payload["not_research_truth"]
        )

    def test_live_lease_refuses_recovery_write(
        self,
    ) -> None:
        store = self._store()

        factory = FakeUnitOfWorkFactory(
            store
        )

        with factory.open() as uow:
            acquired = (
                uow.research_orchestrations
                .acquire_lease(
                    "run-1",
                    owner_runtime_instance_id=(
                        "runtime-owner"
                    ),
                    ttl_seconds=90,
                )
            )
            uow.commit()

        self.assertIsNotNone(
            acquired.record
        )

        with self.assertRaises(
            LeaseFencingError
        ):
            RecoverModelUsageCapacity(
                factory,
                clock=FixedClock(),
            ).execute("run-1")

        after = (
            store.research_orchestrations[
                "run-1"
            ]
        )

        self.assertEqual(
            after.state,
            OrchestrationState.BLOCKED.value,
        )

        self.assertEqual(
            after.stop_reason,
            StopReason.RATE_LIMITED.value,
        )

    def test_nonmatching_provenance_is_noop(
        self,
    ) -> None:
        store = self._store()

        store.research_admissions[
            "adm-usage-1"
        ] = _admission(
            "adm-usage-1",
            reason_code="MODEL_RATE_LIMITED",
        )

        result = RecoverModelUsageCapacity(
            FakeUnitOfWorkFactory(store),
            clock=FixedClock(),
        ).execute("run-1")

        self.assertFalse(
            result.recovered
        )

        after = (
            store.research_orchestrations[
                "run-1"
            ]
        )

        self.assertEqual(
            after.state,
            OrchestrationState.BLOCKED.value,
        )


if __name__ == "__main__":
    unittest.main()
