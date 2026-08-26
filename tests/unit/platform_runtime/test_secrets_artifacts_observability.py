from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from research_os.application.operator_status import OperatorStatusSnapshot, render_operator_status
from research_os.maturity import (
    GATE_04B_STATUS,
    GATE_15_STATUS,
    GATE_16_STATUS,
    LIVE_MODEL_VALIDATED,
    PRODUCTION_READY,
    SECURITY_RESEARCH_VALIDATED,
)
from research_os.platform.artifacts import ArtifactStoreError, LocalArtifactStore
from research_os.platform.health import ComponentHealth, HealthCheck
from research_os.platform.observability import InMemoryObservability, TelemetryEvent
from research_os.platform.secrets import (
    EnvSecretResolver,
    SecretReference,
    SecretResolutionStatus,
    SecretScheme,
    UnavailableSecretResolver,
)
from research_os.platform.strix import classify_strix_process, StrixProcessClass


class SecretPortTests(unittest.TestCase):
    def test_env_reference_resolves_and_unavailable_fails_closed(self) -> None:
        resolver = EnvSecretResolver({"RESEARCH_OS_DEV_SECRET": "value"})
        resolved = resolver.resolve(
            SecretReference(SecretScheme.ENV_REFERENCE, "RESEARCH_OS_DEV_SECRET")
        )
        self.assertEqual(resolved.status, SecretResolutionStatus.RESOLVED)
        missing = resolver.resolve(SecretReference(SecretScheme.ENV_REFERENCE, "MISSING"))
        self.assertEqual(missing.status, SecretResolutionStatus.UNAVAILABLE)
        self.assertIsNone(missing.value)
        closed = UnavailableSecretResolver().resolve(
            SecretReference(SecretScheme.LOCAL_DEV, "x")
        )
        self.assertEqual(closed.status, SecretResolutionStatus.UNAVAILABLE)

    def test_secret_reference_mapping_has_no_value(self) -> None:
        mapping = SecretReference(SecretScheme.ENV_REFERENCE, "OPENAI_API_KEY").to_mapping()
        self.assertNotIn("value", mapping)
        self.assertNotIn("secret", mapping)


class ArtifactStoreTests(unittest.TestCase):
    def test_traversal_and_size_and_hash_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalArtifactStore(Path(tmp), max_bytes=16)
            with self.assertRaises(ArtifactStoreError):
                store.persist("../escape", b"abc")
            with self.assertRaises(ArtifactStoreError):
                store.persist("too-big", b"0123456789abcdef!")
            ref = store.persist("ok.bin", b"hello")
            content = store.verify("ok.bin", ref.sha256)
            self.assertEqual(content, b"hello")
            with self.assertRaises(ArtifactStoreError):
                store.verify("ok.bin", "0" * 64)
            store.mark_evidence_linked("ok.bin")
            with self.assertRaises(ArtifactStoreError):
                store.delete("ok.bin")


class ObservabilityAndHealthTests(unittest.TestCase):
    def test_telemetry_rejects_secret_keys(self) -> None:
        with self.assertRaises(ValueError):
            TelemetryEvent(event="x", outcome="ok", fields={"api_key": "secret"})
        obs = InMemoryObservability()
        obs.emit(TelemetryEvent(event="cycle", outcome="CONTINUE", research_run_id="run-1"))
        obs.increment("experiments_executed")
        self.assertEqual(obs.snapshot()["experiments_executed"], 1)
        check = HealthCheck("postgresql", ComponentHealth.HEALTHY, "select 1")
        self.assertTrue(check.to_mapping()["not_research_truth"])

    def test_strix_unavailable_and_crash_classification(self) -> None:
        self.assertEqual(
            classify_strix_process(
                executable_found=False, exit_code=None, timed_out=False, cancelled=False
            ),
            StrixProcessClass.UNAVAILABLE,
        )
        self.assertEqual(
            classify_strix_process(
                executable_found=True, exit_code=-9, timed_out=False, cancelled=False
            ),
            StrixProcessClass.CRASHED,
        )

    def test_status_renderer_has_no_secrets_and_keeps_maturity_honest(self) -> None:
        text = render_operator_status(
            OperatorStatusSnapshot(
                postgresql="HEALTHY",
                worker={"local-python": "HEALTHY"},
                model_runtimes={
                    "API": "UNAVAILABLE",
                    "CLI_SESSION": "UNAVAILABLE",
                    "LOCAL_MODEL": "UNAVAILABLE",
                    "EXTERNAL_AGENT": "UNAVAILABLE",
                },
                strix="UNAVAILABLE",
                auth="no live credentials resolved",
                orchestrator="READY",
                budget_ledger="append-only",
                reconciliation="available",
                observability="in-memory",
                gate_04b="PENDING",
            )
        )
        self.assertIn("GATE 04B:", text)
        self.assertIn("PENDING", text)
        self.assertIn("GATE 14:", text)
        self.assertIn("PASS", text)
        self.assertIn("GATE 15:", text)
        self.assertEqual(GATE_15_STATUS, "PASS")
        self.assertIn("GATE 16:", text)
        self.assertEqual(GATE_16_STATUS, "PASS")
        self.assertEqual(GATE_04B_STATUS, "PASS")
        self.assertIn(f"LIVE_MODEL_VALIDATED: {LIVE_MODEL_VALIDATED}", text)
        self.assertIn(f"PRODUCTION_READY: {PRODUCTION_READY}", text)
        self.assertIn(f"SECURITY_RESEARCH_VALIDATED: {SECURITY_RESEARCH_VALIDATED}", text)
        self.assertNotIn("sk-", text)
        self.assertFalse(PRODUCTION_READY)
        self.assertTrue(LIVE_MODEL_VALIDATED)
        self.assertFalse(SECURITY_RESEARCH_VALIDATED)
        with self.assertRaises(ValueError):
            OperatorStatusSnapshot(
                postgresql="HEALTHY token=secret",
                worker={"local-python": "HEALTHY"},
                model_runtimes={"API": "UNAVAILABLE"},
                strix="UNAVAILABLE",
                auth="none",
                orchestrator="READY",
                budget_ledger="append-only",
                reconciliation="available",
                observability="in-memory",
            )


if __name__ == "__main__":
    unittest.main()
