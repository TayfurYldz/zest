from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.orchestration_lease import (
    LeaseConfig,
)
from zest.application.preflight import (
    ModelReadinessInput,
)
from zest.application.zestd import (
    MODEL_CAPACITY_LIVE_RETRY_SECONDS,
    ZestdRuntime,
)
from zest.platform.health import (
    ComponentHealth,
    HealthCheck,
)
from zest.research.orchestration import (
    OrchestrationState,
    StopReason,
)

from support.fake_unit_of_work import (
    FakeUnitOfWorkFactory,
)

from application.test_model_usage_capacity_recovery import (
    _admission,
    _blocked_usage_record,
)

from application.test_zestd import (
    RecordingWorkerPort,
    ScriptedModelPort,
    _completed_worker_outcome,
    _healthy_browser_worker,
    _healthy_model,
    _ok_schema,
    _seed,
)


def _rate_limited_health():
    healthy = _healthy_model()

    return ModelReadinessInput(
        candidate=healthy.candidate,
        health=HealthCheck(
            "model",
            ComponentHealth.RATE_LIMITED,
            "last_runtime_outcome=MODEL_USAGE_LIMITED",
        ),
    )


class RuntimeCapacityRecoveryTests(
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

    def _runtime(
        self,
        store,
        *,
        health_probe=_healthy_model,
        recovery_probe=lambda: True,
        attach_results=(object(),),
    ):
        runtime = ZestdRuntime(
            FakeUnitOfWorkFactory(
                store
            ),
            RecordingWorkerPort(
                store=store,
                handler=(
                    _completed_worker_outcome
                ),
            ),
            ScriptedModelPort(),
            lease_config=LeaseConfig(
                heartbeat_interval_seconds=60.0,
                lease_ttl_seconds=120.0,
            ),
            cadence_seconds=30.0,
            probe_schema=_ok_schema,
            probe_worker=(
                _healthy_browser_worker
            ),
            probe_model=_healthy_model,
            probe_model_health=health_probe,
            probe_model_recovery=(
                recovery_probe
            ),
        )

        calls = []
        results = list(
            attach_results
        )

        def fake_attach(
            research_run_id,
            **kwargs,
        ):
            calls.append(
                (
                    research_run_id,
                    kwargs,
                )
            )

            if results:
                return results.pop(0)

            return object()

        runtime._attach_supervisor = (
            fake_attach
        )

        return runtime, calls

    def test_restart_recovers_exact_usage_block(
        self,
    ) -> None:
        store = self._store()

        runtime, attach_calls = (
            self._runtime(store)
        )

        try:
            runtime.start_process()

            current = (
                store.research_orchestrations[
                    "run-1"
                ]
            )

            self.assertEqual(
                current.state,
                OrchestrationState.READY.value,
            )

            self.assertIsNone(
                current.stop_reason
            )

            self.assertIsNone(
                current.active_cycle_id
            )

            self.assertEqual(
                len(attach_calls),
                1,
            )

            self.assertEqual(
                runtime
                ._pending_capacity_attaches,
                set(),
            )

        finally:
            runtime.drain()

    def test_passive_rate_limit_blocks_live_confirmation(
        self,
    ) -> None:
        store = self._store()
        live_calls = []

        runtime, attach_calls = (
            self._runtime(
                store,
                health_probe=(
                    _rate_limited_health
                ),
                recovery_probe=(
                    lambda:
                        live_calls.append(True)
                        or True
                ),
            )
        )

        try:
            runtime.start_process()

            current = (
                store.research_orchestrations[
                    "run-1"
                ]
            )

            self.assertEqual(
                current.state,
                OrchestrationState.BLOCKED.value,
            )

            self.assertEqual(
                current.stop_reason,
                StopReason.RATE_LIMITED.value,
            )

            self.assertEqual(
                live_calls,
                [],
            )

            self.assertEqual(
                attach_calls,
                [],
            )

        finally:
            runtime.drain()

    def test_failed_live_confirmation_is_immediately_backed_off(
        self,
    ) -> None:
        store = self._store()
        live_calls = []

        runtime, attach_calls = (
            self._runtime(
                store,
                recovery_probe=(
                    lambda:
                        live_calls.append(True)
                        or False
                ),
            )
        )

        try:
            runtime.start_process()

            self.assertEqual(
                len(live_calls),
                1,
            )

            self.assertEqual(
                attach_calls,
                [],
            )

            # Same clock instant: retry is refused without another
            # provider diagnostic.
            second = (
                runtime
                .recover_model_usage_limited_runs()
            )

            self.assertEqual(
                second,
                (),
            )

            self.assertEqual(
                len(live_calls),
                1,
            )

            self.assertIn(
                "run-1",
                runtime
                ._capacity_live_retry_after,
            )

            self.assertEqual(
                MODEL_CAPACITY_LIVE_RETRY_SECONDS,
                60.0,
            )

        finally:
            runtime.drain()

    def test_failed_attach_remains_explicitly_pending_and_retries(
        self,
    ) -> None:
        store = self._store()

        runtime, attach_calls = (
            self._runtime(
                store,
                attach_results=(
                    None,
                    object(),
                ),
            )
        )

        try:
            runtime.start_process()

            current = (
                store.research_orchestrations[
                    "run-1"
                ]
            )

            self.assertEqual(
                current.state,
                OrchestrationState.READY.value,
            )

            self.assertIn(
                "run-1",
                runtime
                ._pending_capacity_attaches,
            )

            self.assertEqual(
                len(attach_calls),
                1,
            )

            result = (
                runtime
                .recover_model_usage_limited_runs()
            )

            self.assertEqual(
                result,
                (),
            )

            self.assertEqual(
                len(attach_calls),
                2,
            )

            self.assertNotIn(
                "run-1",
                runtime
                ._pending_capacity_attaches,
            )

        finally:
            runtime.drain()

    def test_existing_live_proof_avoids_duplicate_diagnostic(
        self,
    ) -> None:
        store = self._store()
        live_calls = []

        runtime, attach_calls = (
            self._runtime(
                store,
                health_probe=(
                    _rate_limited_health
                ),
                recovery_probe=(
                    lambda:
                        live_calls.append(True)
                        or True
                ),
            )
        )

        try:
            # Startup sees RATE_LIMITED and therefore consumes no
            # provider diagnostic.
            runtime.start_process()

            self.assertEqual(
                live_calls,
                [],
            )

            self.assertEqual(
                store.research_orchestrations[
                    "run-1"
                ].state,
                OrchestrationState.BLOCKED.value,
            )

            # Simulate the immediately preceding reconciliation
            # iteration having already completed its live diagnostic.
            runtime._probe_model_health = (
                _healthy_model
            )

            recovered = (
                runtime
                .recover_model_usage_limited_runs(
                    live_already_confirmed=True
                )
            )

            self.assertEqual(
                recovered,
                ("run-1",),
            )

            self.assertEqual(
                live_calls,
                [],
            )

            self.assertEqual(
                len(attach_calls),
                1,
            )

            self.assertEqual(
                store.research_orchestrations[
                    "run-1"
                ].state,
                OrchestrationState.READY.value,
            )

        finally:
            runtime.drain()


if __name__ == "__main__":
    unittest.main()
