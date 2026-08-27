from __future__ import annotations

import unittest
from urllib.parse import urlsplit

import pathsetup  # noqa: F401

from zest.research.assessment import AssessmentOutcome
from zest.research.evidence import (
    EvidenceAdmissionContext,
    EvidenceObservationRef,
    propose_authorization_differential_evidence,
)
from zest.research.evaluators.authorization_differential import (
    HttpAuthorizationDifferentialEvaluator,
)
from zest.research.feedback import ExperimentFeedback, ObservedFact
from zest.research.planning import plan_authorization_differential
from zest.worker_runtime.python.http_authorization import execute_http_authorization
from e2e.lab.http_ground_truth_lab import (
    DECEPTIVE_200,
    DELEGATED_ACCESS,
    PUBLIC_OBJECT,
    SHARED_RESOURCE,
    TRUE_BOLA,
    GroundTruthLab,
)


def _envelope_for(origin: str) -> dict[str, object]:
    parsed = urlsplit(origin)
    return {
        "normalized_scheme": parsed.scheme or "http",
        "normalized_host": parsed.hostname or "127.0.0.1",
        "normalized_port": parsed.port or 80,
        "document_path": "/",
        "origin_wide": True,
        "allowed_path_prefixes": [],
        "denied_path_prefixes": [],
        "loopback_only": True,
        "source_scope_rule_ids": ["test"],
    }


def _plan(**overrides):
    values = dict(
        hypothesis_id="hyp-1",
        budget_id="budget-1",
        target_reference="http://127.0.0.1:9",
        authorized_origin="http://127.0.0.1:9",
        actor="alice",
        own_object="alice",
        cross_object="bob",
        mode="vulnerable",
    )
    values.update(overrides)
    return plan_authorization_differential(**values)


def _payload(**overrides):
    values = dict(
        authorized_origin="http://127.0.0.1:9",
        mode="vulnerable",
        actor="alice",
        own_object="alice",
        cross_object="bob",
        owner_request_status=200,
        owner_request_object_owner="alice",
        cross_object_request_status=200,
        cross_object_request_object_owner="bob",
        secure_control_status=403,
        unauthenticated_control_status=401,
    )
    values.update(overrides)
    return values


def _feedback(payload):
    return ExperimentFeedback(
        hypothesis_id="hyp-1",
        experiment_id="exp-1",
        research_run_id="run-1",
        expected_observation=(
            "authenticated actor reads own object, reads another actor's object with that "
            "object's owner proven, unauthenticated access is denied, and the secure control "
            "denies cross-object access"
        ),
        disconfirming_observation=(
            "cross-object access is denied or the returned object is not the other actor's object"
        ),
        evaluation_strategy="http.authorization.differential.v1",
        execution_outcome="OBSERVATION_PRODUCED",
        observations=(
            ObservedFact(
                observation_id="obs-1",
                observation_kind="HTTP_AUTHORIZATION_DIFFERENTIAL",
                payload=payload,
            ),
        ),
        invocation_status="SUCCEEDED",
        experiment_execution_state="EXECUTION_SUCCEEDED",
        attempt_state="COMPLETED",
    )


def _no_evidence(assessment) -> None:
    context = EvidenceAdmissionContext(
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        experiment_id="exp-1",
        evaluation_strategy="http.authorization.differential.v1",
        observations=(
            EvidenceObservationRef(
                observation_id="obs-1",
                research_run_id="run-1",
                worker_result_id="wr-1",
                observation_kind="HTTP_AUTHORIZATION_DIFFERENTIAL",
            ),
        ),
        assessment_id="assess-1",
        assessment_outcome=assessment.outcome,
    )
    assert propose_authorization_differential_evidence(context, proposal_id="prop-1") is None


class AuthorizationFalsePositiveTests(unittest.TestCase):
    def test_http_200_alone_never_admits_bola_evidence(self) -> None:
        assessment = HttpAuthorizationDifferentialEvaluator().evaluate(
            _plan(),
            _feedback(_payload(cross_object_request_object_owner=None)),
        )
        self.assertEqual(assessment.outcome, AssessmentOutcome.NEEDS_MORE_CONTEXT)
        _no_evidence(assessment)

    def test_owner_mismatch_alone_never_admits_bola_evidence(self) -> None:
        assessment = HttpAuthorizationDifferentialEvaluator().evaluate(
            _plan(),
            _feedback(
                _payload(
                    owner_request_status=None,
                    owner_request_object_owner=None,
                    secure_control_status=None,
                    unauthenticated_control_status=None,
                )
            ),
        )
        self.assertNotEqual(assessment.outcome, AssessmentOutcome.CONSISTENT_WITH_PREDICTION)
        _no_evidence(assessment)

    def test_public_object_200_never_admits_bola_evidence(self) -> None:
        assessment = HttpAuthorizationDifferentialEvaluator().evaluate(
            _plan(),
            _feedback(_payload(cross_object_request_visibility="PUBLIC")),
        )
        self.assertEqual(assessment.outcome, AssessmentOutcome.CONTRADICTS_PREDICTION)
        self.assertEqual(assessment.rationale["reason_code"], "LEGITIMATE_PUBLIC_ACCESS")
        _no_evidence(assessment)

    def test_delegated_access_200_never_admits_bola_evidence(self) -> None:
        assessment = HttpAuthorizationDifferentialEvaluator().evaluate(
            _plan(),
            _feedback(_payload(cross_object_request_authorized_readers=["alice"])),
        )
        self.assertEqual(assessment.outcome, AssessmentOutcome.CONTRADICTS_PREDICTION)
        self.assertEqual(assessment.rationale["reason_code"], "LEGITIMATE_DELEGATED_ACCESS")
        _no_evidence(assessment)

    def test_shared_access_never_admits_bola_evidence(self) -> None:
        assessment = HttpAuthorizationDifferentialEvaluator().evaluate(
            _plan(cross_object="shared"),
            _feedback(
                _payload(
                    cross_object="shared",
                    cross_object_request_object_owner="shared",
                    cross_object_request_resource_kind="SHARED",
                )
            ),
        )
        self.assertEqual(assessment.outcome, AssessmentOutcome.CONTRADICTS_PREDICTION)
        self.assertEqual(assessment.rationale["reason_code"], "LEGITIMATE_SHARED_ACCESS")
        _no_evidence(assessment)

    def test_unknown_ownership_never_admits_bola_evidence(self) -> None:
        assessment = HttpAuthorizationDifferentialEvaluator().evaluate(
            _plan(),
            _feedback(_payload(cross_object_request_object_owner=None)),
        )
        self.assertEqual(assessment.outcome, AssessmentOutcome.NEEDS_MORE_CONTEXT)
        _no_evidence(assessment)

    def test_timeout_is_execution_unusable_not_rejected_vulnerability(self) -> None:
        assessment = HttpAuthorizationDifferentialEvaluator().evaluate(
            _plan(),
            ExperimentFeedback(
                hypothesis_id="hyp-1",
                experiment_id="exp-1",
                research_run_id="run-1",
                expected_observation="x",
                disconfirming_observation="y",
                evaluation_strategy="http.authorization.differential.v1",
                execution_outcome="INVOCATION_FAILED",
                observations=(),
                invocation_status="TIMED_OUT",
                experiment_execution_state="EXECUTION_FAILED",
                attempt_state="TIMED_OUT",
            ),
        )
        self.assertEqual(assessment.outcome, AssessmentOutcome.EXECUTION_UNUSABLE)
        _no_evidence(assessment)

    def test_lab_public_delegated_shared_and_deceptive_are_observed_not_labeled(self) -> None:
        with GroundTruthLab(PUBLIC_OBJECT) as lab:
            status, raw, _ = execute_http_authorization(
                {
                    "network_envelope": _envelope_for(lab.origin),
                    "arguments": {
                        "authorized_origin": lab.origin,
                        "actor": "alice",
                        "own_object": "alice",
                        "cross_object": "bob",
                        "mode": "vulnerable",
                    }
                }
            )
            self.assertEqual(status, "SUCCEEDED")
            self.assertEqual(raw["cross_object_request"]["object_visibility"], "PUBLIC")
            self.assertNotIn("expected_class", raw)
            self.assertTrue(lab.wait_for_request_count(4))
            self.assertEqual(lab.http_request_count(), 4)
            self.assertCountEqual(
                [item.path for item in lab.ledger],
                [
                    "/vulnerable/accounts/alice",
                    "/vulnerable/accounts/bob",
                    "/secure/accounts/bob",
                    "/vulnerable/accounts/bob",
                ],
            )
        with GroundTruthLab(DELEGATED_ACCESS) as lab:
            _, raw, _ = execute_http_authorization(
                {
                    "network_envelope": _envelope_for(lab.origin),
                    "arguments": {
                        "authorized_origin": lab.origin,
                        "actor": "alice",
                        "own_object": "alice",
                        "cross_object": "bob",
                        "mode": "vulnerable",
                    }
                }
            )
            self.assertEqual(raw["cross_object_request"]["object_authorized_readers"], ["alice"])
        with GroundTruthLab(SHARED_RESOURCE) as lab:
            _, raw, _ = execute_http_authorization(
                {
                    "network_envelope": _envelope_for(lab.origin),
                    "arguments": {
                        "authorized_origin": lab.origin,
                        "actor": "alice",
                        "own_object": "alice",
                        "cross_object": "shared",
                        "mode": "vulnerable",
                    }
                }
            )
            self.assertEqual(raw["cross_object_request"]["object_resource_kind"], "SHARED")
        with GroundTruthLab(DECEPTIVE_200) as lab:
            _, raw, _ = execute_http_authorization(
                {
                    "network_envelope": _envelope_for(lab.origin),
                    "arguments": {
                        "authorized_origin": lab.origin,
                        "actor": "alice",
                        "own_object": "alice",
                        "cross_object": "bob",
                        "mode": "vulnerable",
                    }
                }
            )
            self.assertNotIn("object_owner", raw["cross_object_request"])
        with GroundTruthLab(TRUE_BOLA) as lab:
            _, raw, _ = execute_http_authorization(
                {
                    "network_envelope": _envelope_for(lab.origin),
                    "arguments": {
                        "authorized_origin": lab.origin,
                        "actor": "alice",
                        "own_object": "alice",
                        "cross_object": "bob",
                        "mode": "vulnerable",
                    }
                }
            )
            self.assertEqual(raw["cross_object_request"]["object_owner"], "bob")


if __name__ == "__main__":
    unittest.main()
