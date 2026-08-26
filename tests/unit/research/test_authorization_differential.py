from __future__ import annotations

import ast
import unittest
from pathlib import Path
from urllib.parse import urlsplit

import pathsetup  # noqa: F401

from zest.application.transition_a.authorization_differential import (
    HTTP_AUTHORIZATION_DIFFERENTIAL_NORMALIZER_VERSION,
    HttpAuthorizationDifferentialNormalizer,
)
from zest.application.transition_a.registry import NormalizerRegistry
from zest.research.assessment import AssessmentOutcome, default_evaluator_registry
from zest.research.candidate import (
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION,
    admit_candidate,
    propose_authorization_differential_candidate,
)
from zest.research.evidence import (
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM,
    EvidenceAdmissionContext,
    EvidenceAdmissionOutcome,
    EvidenceObservationRef,
    EvidencePolarity,
    admit_evidence,
    propose_authorization_differential_evidence,
)
from zest.research.evaluators.authorization_differential import (
    HttpAuthorizationDifferentialEvaluator,
)
from zest.research.feedback import ExperimentFeedback, ObservedFact
from zest.research.planning import plan_authorization_differential
from zest.worker_runtime.python.http_authorization import execute_http_authorization
from support.worker_requests import valid_worker_request
from e2e.lab.http_idor_lab import Gate14Lab

REPO = Path(__file__).resolve().parents[3]
E2E_FILE = REPO / "tests" / "e2e" / "test_gate14_security_lab.py"


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


def _feedback(payload=None, **overrides):
    values = dict(
        hypothesis_id="hyp-1",
        experiment_id="exp-1",
        research_run_id="run-1",
        expected_observation="authenticated actor reads own object, reads another actor's object with that object's owner proven, unauthenticated access is denied, and the secure control denies cross-object access",
        disconfirming_observation="cross-object access is denied or the returned object is not the other actor's object",
        evaluation_strategy="http.authorization.differential.v1",
        execution_outcome="OBSERVATION_PRODUCED",
        observations=(
            ObservedFact(
                observation_id="obs-1",
                observation_kind="HTTP_AUTHORIZATION_DIFFERENTIAL",
                payload=_payload() if payload is None else payload,
            ),
        ),
        invocation_status="SUCCEEDED",
        experiment_execution_state="EXECUTION_SUCCEEDED",
        attempt_state="COMPLETED",
    )
    values.update(overrides)
    return ExperimentFeedback(**values)


def _http_result(raw_result, status="SUCCEEDED"):
    return {
        "contract_version": "v1",
        "correlation": valid_worker_request()["correlation"],
        "worker_id": "local-python-diagnostic",
        "status": status,
        "started_at": "2026-08-17T12:00:00Z",
        "completed_at": "2026-08-17T12:00:01Z",
        "raw_result": raw_result,
    }


class HttpAuthorizationDifferentialUnitTests(unittest.TestCase):
    def test_registry_selects_http_normalizer_from_trusted_request(self) -> None:
        normalizer = NormalizerRegistry().get(
            "http.authorization.differential", "probe"
        )
        self.assertIsInstance(normalizer, HttpAuthorizationDifferentialNormalizer)

    def test_succeeded_worker_result_becomes_observation_not_finding(self) -> None:
        request = valid_worker_request(
            worker_capability="http.authorization.differential",
            action="probe",
            arguments={
                "authorized_origin": "http://127.0.0.1:9",
                "actor": "alice",
                "own_object": "alice",
                "cross_object": "bob",
                "mode": "vulnerable",
            },
        )
        drafts = HttpAuthorizationDifferentialNormalizer().normalize(
            request,
            _http_result(
                {
                    "mode": "vulnerable",
                    "authorized_origin": "http://127.0.0.1:9",
                    "owner_request": {"status": 200, "object_owner": "alice"},
                    "cross_object_request": {"status": 200, "object_owner": "bob"},
                    "secure_control": {"status": 403},
                    "unauthenticated_control": {"status": 401},
                }
            ),
        )
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].observation_kind, "HTTP_AUTHORIZATION_DIFFERENTIAL")
        self.assertEqual(
            drafts[0].normalization_version,
            HTTP_AUTHORIZATION_DIFFERENTIAL_NORMALIZER_VERSION,
        )
        self.assertNotIn("vulnerability", drafts[0].payload)
        self.assertNotIn("evidence", drafts[0].payload)
        self.assertNotIn("candidate", drafts[0].payload)
        self.assertNotIn("finding", drafts[0].payload)

    def test_reauthorization_required_emits_no_observation(self) -> None:
        drafts = HttpAuthorizationDifferentialNormalizer().normalize(
            valid_worker_request(
                worker_capability="http.authorization.differential",
                action="probe",
            ),
            _http_result({"stopped": True}, status="REAUTHORIZATION_REQUIRED"),
        )
        self.assertEqual(drafts, ())

    def test_full_differential_is_consistent(self) -> None:
        assessment = HttpAuthorizationDifferentialEvaluator().evaluate(
            _plan(), _feedback()
        )
        self.assertEqual(assessment.outcome, AssessmentOutcome.CONSISTENT_WITH_PREDICTION)
        self.assertNotIn("confidence", assessment.rationale)

    def test_http_200_alone_is_not_evidence(self) -> None:
        assessment = HttpAuthorizationDifferentialEvaluator().evaluate(
            _plan(),
            _feedback(
                payload=_payload(
                    cross_object_request_object_owner=None,
                )
            ),
        )
        self.assertEqual(assessment.outcome, AssessmentOutcome.NEEDS_MORE_CONTEXT)
        self.assertEqual(
            assessment.rationale["reason_code"],
            "STATUS_ALONE_IS_NOT_OBJECT_ACCESS_PROOF",
        )
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
        self.assertIsNone(
            propose_authorization_differential_evidence(context, proposal_id="prop-1")
        )

    def test_secure_control_contradicts_and_does_not_propose_security_evidence(self) -> None:
        assessment = HttpAuthorizationDifferentialEvaluator().evaluate(
            _plan(mode="secure_only"),
            _feedback(
                payload=_payload(
                    mode="secure_only",
                    cross_object_request_status=403,
                    cross_object_request_object_owner=None,
                )
            ),
        )
        self.assertEqual(assessment.outcome, AssessmentOutcome.CONTRADICTS_PREDICTION)
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
        self.assertIsNone(
            propose_authorization_differential_evidence(context, proposal_id="prop-1")
        )
        proposal = propose_authorization_differential_evidence(
            EvidenceAdmissionContext(
                research_run_id="run-1",
                hypothesis_id="hyp-1",
                experiment_id="exp-1",
                evaluation_strategy="http.authorization.differential.v1",
                observations=context.observations,
                assessment_id="assess-1",
                assessment_outcome=AssessmentOutcome.CONSISTENT_WITH_PREDICTION,
            ),
            proposal_id="prop-ok",
        )
        assert proposal is not None
        rejected = admit_evidence(proposal, context)
        self.assertFalse(rejected.creates_evidence)
        self.assertEqual(
            rejected.outcome, EvidenceAdmissionOutcome.REJECTED_INSUFFICIENT_SUPPORT
        )

    def test_candidate_requires_admitted_supporting_claim(self) -> None:
        from zest.research.candidate import CandidateAdmissionContext, CandidateEvidenceRef

        context = CandidateAdmissionContext(
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            evidence=(
                CandidateEvidenceRef(
                    evidence_id="ev-1",
                    research_run_id="run-1",
                    hypothesis_id="hyp-1",
                    experiment_id="exp-1",
                    polarity=EvidencePolarity.SUPPORTING.value,
                    claim_scope=HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM,
                ),
            ),
        )
        proposal = propose_authorization_differential_candidate(
            context, proposal_id="cand-1"
        )
        assert proposal is not None
        decision = admit_candidate(proposal, context)
        self.assertTrue(decision.creates_candidate)
        self.assertEqual(
            proposal.classification, HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION
        )

    def test_default_registry_includes_http_evaluator(self) -> None:
        registry = default_evaluator_registry()
        self.assertEqual(
            registry.get("http.authorization.differential.v1").strategy,
            "http.authorization.differential.v1",
        )

    def test_non_loopback_origin_is_blocked_without_contact(self) -> None:
        status, raw, diagnostics = execute_http_authorization(
            {
                "network_envelope": {
                    "normalized_scheme": "http",
                    "normalized_host": "127.0.0.1",
                    "normalized_port": 80,
                    "document_path": "/",
                    "origin_wide": True,
                    "allowed_path_prefixes": [],
                    "denied_path_prefixes": [],
                    "loopback_only": True,
                    "source_scope_rule_ids": ["test"],
                },
                "arguments": {
                    "authorized_origin": "http://8.8.8.8:80",
                    "actor": "alice",
                    "own_object": "alice",
                    "cross_object": "bob",
                    "mode": "vulnerable",
                },
            }
        )
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertEqual(raw, {})
        assert diagnostics is not None
        self.assertFalse(diagnostics.get("contacted"))

    def test_external_hostname_is_blocked_without_contact(self) -> None:
        status, _, diagnostics = execute_http_authorization(
            {
                "network_envelope": {
                    "normalized_scheme": "http",
                    "normalized_host": "127.0.0.1",
                    "normalized_port": 80,
                    "document_path": "/",
                    "origin_wide": True,
                    "allowed_path_prefixes": [],
                    "denied_path_prefixes": [],
                    "loopback_only": True,
                    "source_scope_rule_ids": ["test"],
                },
                "arguments": {
                    "authorized_origin": "http://example.com",
                    "actor": "alice",
                    "own_object": "alice",
                    "cross_object": "bob",
                    "mode": "vulnerable",
                },
            }
        )
        self.assertEqual(status, "EXECUTION_FAILED")
        assert diagnostics is not None
        self.assertFalse(diagnostics.get("contacted"))

    def test_lab_idor_and_redirect_stop(self) -> None:
        with Gate14Lab() as lab:
            self.assertTrue(lab.origin.startswith("http://127.0.0.1:"))
            parsed_origin = urlsplit(lab.origin)
            envelope = {
                "normalized_scheme": "http",
                "normalized_host": "127.0.0.1",
                "normalized_port": parsed_origin.port or 80,
                "document_path": "/",
                "origin_wide": True,
                "allowed_path_prefixes": [],
                "denied_path_prefixes": [],
                "loopback_only": True,
                "source_scope_rule_ids": ["test"],
            }
            status, raw, _ = execute_http_authorization(
                {
                    "network_envelope": envelope,
                    "arguments": {
                        "authorized_origin": lab.origin,
                        "actor": "alice",
                        "own_object": "alice",
                        "cross_object": "bob",
                        "mode": "vulnerable",
                    },
                }
            )
            self.assertEqual(status, "SUCCEEDED")
            self.assertEqual(raw["owner_request"]["object_owner"], "alice")
            self.assertEqual(raw["cross_object_request"]["object_owner"], "bob")
            self.assertEqual(raw["secure_control"]["status"], 403)
            self.assertIn(raw["unauthenticated_control"]["status"], {401, 403})
            redirect_status, redirect_raw, diagnostics = execute_http_authorization(
                {
                    "network_envelope": envelope,
                    "arguments": {
                        "authorized_origin": lab.origin,
                        "actor": "alice",
                        "own_object": "alice",
                        "cross_object": "bob",
                        "mode": "redirect",
                    },
                }
            )
            self.assertEqual(redirect_status, "REAUTHORIZATION_REQUIRED")
            self.assertTrue(redirect_raw.get("stopped"))
            assert diagnostics is not None
            self.assertFalse(diagnostics.get("followed"))
            self.assertTrue(diagnostics.get("requires_core_re_evaluation"))

    def test_gate14_e2e_module_does_not_import_model_runtime(self) -> None:
        tree = ast.parse(E2E_FILE.read_text(encoding="utf-8"), filename=str(E2E_FILE))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        self.assertNotIn("zest.research.model_runtime", names)
        self.assertNotIn("zest.integrations.models.cli_session", names)
        self.assertNotIn("zest.integrations.strix.adapter", names)
        self.assertNotIn("openai", names)


if __name__ == "__main__":
    unittest.main()
