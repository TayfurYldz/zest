from __future__ import annotations

import unittest
from datetime import datetime, timezone

import pathsetup  # noqa: F401

from zest.application.transition_a.diagnostic_echo import (
    DIAGNOSTIC_ECHO_NORMALIZER_VERSION,
    DiagnosticEchoNormalizer,
)
from zest.application.transition_a.drafts import ObservationDraft
from zest.application.transition_a.errors import (
    MalformedNormalizedPayloadError,
    UnsupportedNormalizerError,
)
from zest.application.transition_a.registry import NormalizerRegistry
from zest.data.errors import PersistenceInputError
from support.worker_requests import valid_worker_request


def _result(**overrides):
    result = {
        "contract_version": "v1",
        "correlation": valid_worker_request()["correlation"],
        "worker_id": "local-python-diagnostic",
        "status": "SUCCEEDED",
        "started_at": "2026-08-16T20:00:00Z",
        "completed_at": "2026-08-16T20:00:01Z",
        "raw_result": {"echoed": "ping", "capability": "diagnostic.echo"},
    }
    result.update(overrides)
    return result


class TransitionANormalizerTests(unittest.TestCase):
    def test_successful_echo_emits_one_observation_draft(self) -> None:
        drafts = DiagnosticEchoNormalizer().normalize(valid_worker_request(), _result())
        self.assertEqual(len(drafts), 1)
        draft = drafts[0]
        self.assertEqual(draft.observation_kind, "diagnostic.echo")
        self.assertEqual(draft.payload, {"echoed": "ping"})
        self.assertEqual(draft.normalization_version, DIAGNOSTIC_ECHO_NORMALIZER_VERSION)
        self.assertEqual(
            draft.observed_at, datetime(2026, 8, 16, 20, 0, 1, tzinfo=timezone.utc)
        )
        self.assertFalse(hasattr(draft, "severity"))
        self.assertFalse(hasattr(draft, "confidence"))
        self.assertFalse(hasattr(draft, "evidence_status"))
        self.assertFalse(hasattr(draft, "finding_status"))

    def test_same_input_is_deterministic(self) -> None:
        first = DiagnosticEchoNormalizer().normalize(valid_worker_request(), _result())
        second = DiagnosticEchoNormalizer().normalize(valid_worker_request(), _result())
        self.assertEqual(first, second)

    def test_blocked_result_emits_no_target_observation(self) -> None:
        drafts = DiagnosticEchoNormalizer().normalize(
            valid_worker_request(), _result(status="BLOCKED")
        )
        self.assertEqual(drafts, ())

    def test_malformed_succeeded_payload_rejected(self) -> None:
        with self.assertRaises(MalformedNormalizedPayloadError):
            DiagnosticEchoNormalizer().normalize(
                valid_worker_request(),
                _result(raw_result={"not": "echoed"}),
            )

    def test_normalizer_selected_from_trusted_request_not_raw_result(self) -> None:
        request = valid_worker_request()
        result = _result(raw_result={"echoed": "ping", "capability": "http.request"})
        normalizer = NormalizerRegistry().get(
            str(request["worker_capability"]), str(request["action"])
        )
        drafts = normalizer.normalize(request, result)
        self.assertEqual(drafts[0].observation_kind, "diagnostic.echo")

    def test_unknown_capability_has_no_normalizer(self) -> None:
        with self.assertRaises(UnsupportedNormalizerError):
            NormalizerRegistry().get("not.a.scanner", "scan")

    def test_draft_rejects_severity_payload(self) -> None:
        with self.assertRaises(PersistenceInputError):
            ObservationDraft(
                observation_kind="diagnostic.echo",
                payload={"echoed": "ping", "severity": "high"},
                normalization_version="diagnostic.echo.v1",
                observed_at=datetime(2026, 8, 16, 20, 0, 1, tzinfo=timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
