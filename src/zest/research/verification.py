"""Deterministic Verification Engine. Not Candidate lifecycle authority. Not Finding."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from zest.research.assessment import AssessmentOutcome
from zest.research.candidate import CandidateState, transition_candidate
from zest.research.evidence import (
    DIAGNOSTIC_ECHO_MATCHED_CLAIM,
    DIAGNOSTIC_ECHO_MISMATCHED_CLAIM,
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM,
    HTTP_STATE_TRANSITION_CLAIM,
    EvidencePolarity,
)
from zest.research.types import ResearchInputError

DIAGNOSTIC_VERIFICATION_STRATEGY = "diagnostic.echo.reproduction.v1"
HTTP_AUTHORIZATION_DIFFERENTIAL_VERIFICATION_STRATEGY = (
    "http.authorization.differential.reproduction.v1"
)
HTTP_STATE_TRANSITION_VERIFICATION_STRATEGY = "http.state_transition.reproduction.v1"
DIAGNOSTIC_VERIFIER_KIND = "DETERMINISTIC"
DIAGNOSTIC_VERIFIER_IDENTITY = "diagnostic.echo.verifier.v1"
HTTP_AUTHORIZATION_DIFFERENTIAL_VERIFIER_IDENTITY = (
    "http.authorization.differential.verifier.v1"
)
HTTP_STATE_TRANSITION_VERIFIER_IDENTITY = "http.state_transition.verifier.v1"
DIAGNOSTIC_NEGATIVE_CONTROL_TOKEN = "__diagnostic_control_fail__"

FORBIDDEN_VERIFICATION_KEYS = frozenset(
    {
        "severity",
        "cvss",
        "finding",
        "finding_proposal",
        "exploitability",
        "authorization",
        "confidence",
    }
)


class VerificationOutcome(Enum):
    """Verifier proposal. Not Candidate state until Research transition rules apply."""

    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    DUPLICATE = "DUPLICATE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_ids(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ResearchInputError(f"{field_name} must be a tuple")
    return tuple(_require_text(item, f"{field_name}[{index}]") for index, item in enumerate(value))


def _reject_forbidden(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ResearchInputError(f"{field_name} must be a mapping")
    found = FORBIDDEN_VERIFICATION_KEYS.intersection(payload.keys())
    if found:
        raise ResearchInputError(f"{field_name} must not contain {sorted(found)}")
    return dict(payload)


@dataclass(frozen=True)
class VerificationEvidenceRef:
    """Evidence consumed by Verification. Not a WorkerResult and not Candidate state."""

    evidence_id: str
    research_run_id: str
    experiment_id: str
    request_id: str
    observation_ids: tuple[str, ...]
    polarity: str
    claim_scope: str
    observed_echo: str | None = None
    observed_facts: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence_id", _require_text(self.evidence_id, "evidence_id")
        )
        object.__setattr__(
            self,
            "research_run_id",
            _require_text(self.research_run_id, "research_run_id"),
        )
        object.__setattr__(
            self, "experiment_id", _require_text(self.experiment_id, "experiment_id")
        )
        object.__setattr__(self, "request_id", _require_text(self.request_id, "request_id"))
        object.__setattr__(
            self,
            "observation_ids",
            _require_ids(self.observation_ids, "observation_ids"),
        )
        object.__setattr__(self, "polarity", _require_text(self.polarity, "polarity"))
        object.__setattr__(
            self, "claim_scope", _require_text(self.claim_scope, "claim_scope")
        )
        if self.observed_echo is not None:
            object.__setattr__(
                self,
                "observed_echo",
                _require_text(self.observed_echo, "observed_echo"),
            )
        if self.observed_facts is not None:
            object.__setattr__(
                self,
                "observed_facts",
                _reject_forbidden(self.observed_facts, "observed_facts"),
            )


@dataclass(frozen=True)
class VerificationPlan:
    """Required checks for one Candidate. Not severity and not a Finding."""

    candidate_id: str
    verification_strategy: str
    expected_security_behavior: str
    observed_behavior_to_confirm: str
    negative_control_intent: str
    alternative_explanations_to_test: tuple[str, ...]
    required_original_evidence_ids: tuple[str, ...]
    maximum_side_effect_level: int
    negative_control_token: str = DIAGNOSTIC_NEGATIVE_CONTROL_TOKEN

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_id", _require_text(self.candidate_id, "candidate_id")
        )
        object.__setattr__(
            self,
            "verification_strategy",
            _require_text(self.verification_strategy, "verification_strategy"),
        )
        object.__setattr__(
            self,
            "expected_security_behavior",
            _require_text(self.expected_security_behavior, "expected_security_behavior"),
        )
        object.__setattr__(
            self,
            "observed_behavior_to_confirm",
            _require_text(
                self.observed_behavior_to_confirm, "observed_behavior_to_confirm"
            ),
        )
        object.__setattr__(
            self,
            "negative_control_intent",
            _require_text(self.negative_control_intent, "negative_control_intent"),
        )
        if not isinstance(self.alternative_explanations_to_test, tuple):
            raise ResearchInputError("alternative_explanations_to_test must be a tuple")
        object.__setattr__(
            self,
            "required_original_evidence_ids",
            _require_ids(
                self.required_original_evidence_ids, "required_original_evidence_ids"
            ),
        )
        if self.maximum_side_effect_level not in (0, 1, 2, 3):
            raise ResearchInputError("maximum_side_effect_level must be 0, 1, 2, or 3")
        object.__setattr__(
            self,
            "negative_control_token",
            _require_text(self.negative_control_token, "negative_control_token"),
        )


@dataclass(frozen=True)
class VerificationContext:
    """Inputs the verifier may consume. Not arbitrary prose. Not trusted WorkerResult."""

    candidate_id: str
    candidate_state: CandidateState
    research_run_id: str
    hypothesis_id: str
    claim: str
    plan: VerificationPlan
    original_evidence: VerificationEvidenceRef
    reproduction_evidence: VerificationEvidenceRef | None = None
    negative_control_evidence: VerificationEvidenceRef | None = None
    reproduction_execution_unusable: bool = False
    authoritative_out_of_scope: bool = False
    duplicate_of_candidate_id: str | None = None
    known_duplicate_exists: bool = False
    reproduction_assessment_outcome: AssessmentOutcome | None = None
    reproduction_experiment_id: str | None = None
    reproduction_request_id: str | None = None
    reproduction_observation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_id", _require_text(self.candidate_id, "candidate_id")
        )
        if not isinstance(self.candidate_state, CandidateState):
            raise ResearchInputError("candidate_state must be a CandidateState")
        object.__setattr__(
            self,
            "research_run_id",
            _require_text(self.research_run_id, "research_run_id"),
        )
        object.__setattr__(
            self, "hypothesis_id", _require_text(self.hypothesis_id, "hypothesis_id")
        )
        object.__setattr__(self, "claim", _require_text(self.claim, "claim"))
        if not isinstance(self.reproduction_execution_unusable, bool):
            raise ResearchInputError("reproduction_execution_unusable must be bool")
        if not isinstance(self.authoritative_out_of_scope, bool):
            raise ResearchInputError("authoritative_out_of_scope must be bool")
        if not isinstance(self.known_duplicate_exists, bool):
            raise ResearchInputError("known_duplicate_exists must be bool")
        if self.duplicate_of_candidate_id is not None:
            object.__setattr__(
                self,
                "duplicate_of_candidate_id",
                _require_text(
                    self.duplicate_of_candidate_id, "duplicate_of_candidate_id"
                ),
            )
        if self.reproduction_experiment_id is not None:
            object.__setattr__(
                self,
                "reproduction_experiment_id",
                _require_text(
                    self.reproduction_experiment_id, "reproduction_experiment_id"
                ),
            )
        if self.reproduction_request_id is not None:
            object.__setattr__(
                self,
                "reproduction_request_id",
                _require_text(self.reproduction_request_id, "reproduction_request_id"),
            )
        if not isinstance(self.reproduction_observation_ids, tuple):
            raise ResearchInputError("reproduction_observation_ids must be a tuple")


@dataclass(frozen=True)
class VerificationResult:
    """Verifier proposal. Research transition rules commit Candidate state."""

    outcome: VerificationOutcome
    reason_codes: tuple[str, ...]
    proposed_candidate_state: CandidateState
    original_evidence_ids: tuple[str, ...]
    reproduction_evidence_ids: tuple[str, ...]
    negative_control_evidence_ids: tuple[str, ...]
    alternative_explanation_checks: Mapping[str, Any]
    verifier_kind: str
    verifier_identity: str
    strategy: str

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, VerificationOutcome):
            raise ResearchInputError("outcome must be a VerificationOutcome")
        if not isinstance(self.proposed_candidate_state, CandidateState):
            raise ResearchInputError("proposed_candidate_state must be a CandidateState")
        if self.proposed_candidate_state.value != self.outcome.value:
            raise ResearchInputError("proposed_candidate_state must match outcome")
        object.__setattr__(
            self, "reason_codes", _require_ids(self.reason_codes, "reason_codes")
        )
        object.__setattr__(
            self,
            "original_evidence_ids",
            _require_ids(self.original_evidence_ids, "original_evidence_ids")
            if self.original_evidence_ids
            else (),
        )
        if not isinstance(self.reproduction_evidence_ids, tuple):
            raise ResearchInputError("reproduction_evidence_ids must be a tuple")
        if not isinstance(self.negative_control_evidence_ids, tuple):
            raise ResearchInputError("negative_control_evidence_ids must be a tuple")
        object.__setattr__(
            self,
            "alternative_explanation_checks",
            _reject_forbidden(
                self.alternative_explanation_checks, "alternative_explanation_checks"
            ),
        )
        object.__setattr__(
            self, "verifier_kind", _require_text(self.verifier_kind, "verifier_kind")
        )
        object.__setattr__(
            self,
            "verifier_identity",
            _require_text(self.verifier_identity, "verifier_identity"),
        )
        object.__setattr__(self, "strategy", _require_text(self.strategy, "strategy"))


def plan_diagnostic_verification(
    candidate_id: str,
    original_evidence_ids: tuple[str, ...],
) -> VerificationPlan:
    return VerificationPlan(
        candidate_id=candidate_id,
        verification_strategy=DIAGNOSTIC_VERIFICATION_STRATEGY,
        expected_security_behavior=(
            "diagnostic echo returns the submitted value of a NEW experiment plan"
        ),
        observed_behavior_to_confirm="echoed value equals the reproduction submitted value",
        negative_control_intent=(
            "echo must not equal the diagnostic negative-control fail token"
        ),
        alternative_explanations_to_test=(
            "original observation reused as sole proof",
            "control fail token would satisfy the claim",
        ),
        required_original_evidence_ids=original_evidence_ids,
        maximum_side_effect_level=0,
    )


def plan_authorization_differential_verification(
    candidate_id: str,
    original_evidence_ids: tuple[str, ...],
) -> VerificationPlan:
    return VerificationPlan(
        candidate_id=candidate_id,
        verification_strategy=HTTP_AUTHORIZATION_DIFFERENTIAL_VERIFICATION_STRATEGY,
        expected_security_behavior=(
            "object access control prevents an authenticated actor from reading another "
            "actor's account object"
        ),
        observed_behavior_to_confirm=(
            "fresh independent experiment reproduces cross-object account read with the "
            "returned object owner proven"
        ),
        negative_control_intent=(
            "secure-control and unauthenticated requests remain denied"
        ),
        alternative_explanations_to_test=(
            "original observation reused as sole proof",
            "HTTP 200 without proven object owner",
            "secure control would also return the other actor's object",
        ),
        required_original_evidence_ids=original_evidence_ids,
        maximum_side_effect_level=0,
    )


def plan_state_transition_verification(
    candidate_id: str,
    original_evidence_ids: tuple[str, ...],
) -> VerificationPlan:
    return VerificationPlan(
        candidate_id=candidate_id,
        verification_strategy=HTTP_STATE_TRANSITION_VERIFICATION_STRATEGY,
        expected_security_behavior=(
            "workflow authorization prevents a requester from approving or skipping "
            "required states"
        ),
        observed_behavior_to_confirm=(
            "fresh independent experiment reproduces an unauthorized workflow state "
            "transition with authoritative pre-state and post-state"
        ),
        negative_control_intent=(
            "control-path transition remains denied or conflicted"
        ),
        alternative_explanations_to_test=(
            "original observation reused as sole proof",
            "HTTP 200 without authoritative state change",
            "explicit delegated reviewer authority",
            "idempotent repeat of an already completed transition",
        ),
        required_original_evidence_ids=original_evidence_ids,
        maximum_side_effect_level=1,
    )


def _independent(original: VerificationEvidenceRef, reproduction: VerificationEvidenceRef) -> bool:
    if original.evidence_id == reproduction.evidence_id:
        return False
    if original.experiment_id == reproduction.experiment_id:
        return False
    if original.request_id == reproduction.request_id:
        return False
    if set(original.observation_ids) & set(reproduction.observation_ids):
        return False
    return True


def _result(
    outcome: VerificationOutcome,
    context: VerificationContext,
    reason_codes: tuple[str, ...],
    *,
    reproduction_ids: tuple[str, ...] = (),
    control_ids: tuple[str, ...] = (),
    checks: Mapping[str, Any] | None = None,
    verifier_identity: str | None = None,
) -> VerificationResult:
    return VerificationResult(
        outcome=outcome,
        reason_codes=reason_codes,
        proposed_candidate_state=CandidateState(outcome.value),
        original_evidence_ids=(context.original_evidence.evidence_id,),
        reproduction_evidence_ids=reproduction_ids,
        negative_control_evidence_ids=control_ids,
        alternative_explanation_checks=dict(checks or {}),
        verifier_kind=DIAGNOSTIC_VERIFIER_KIND,
        verifier_identity=verifier_identity or DIAGNOSTIC_VERIFIER_IDENTITY,
        strategy=context.plan.verification_strategy,
    )


def evaluate_diagnostic_verification(context: VerificationContext) -> VerificationResult:
    """Deterministic diagnostic verifier. Does not mutate Candidate. Does not create Finding."""

    if context.plan.candidate_id != context.candidate_id:
        raise ResearchInputError("VerificationPlan candidate_id mismatch")
    if context.plan.verification_strategy != DIAGNOSTIC_VERIFICATION_STRATEGY:
        raise ResearchInputError("unsupported verification strategy")
    if context.original_evidence.research_run_id != context.research_run_id:
        raise ResearchInputError("original evidence is not in the Candidate research run")

    if context.authoritative_out_of_scope:
        return _result(
            VerificationOutcome.OUT_OF_SCOPE,
            context,
            ("AUTHORITATIVE_OUT_OF_SCOPE",),
        )
    if context.duplicate_of_candidate_id is not None:
        if not context.known_duplicate_exists:
            return _result(
                VerificationOutcome.INCONCLUSIVE,
                context,
                ("UNKNOWN_DUPLICATE_REFERENCE",),
            )
        return _result(
            VerificationOutcome.DUPLICATE,
            context,
            ("EXPLICIT_DUPLICATE_REFERENCE",),
        )
    if context.reproduction_execution_unusable:
        return _result(
            VerificationOutcome.INCONCLUSIVE,
            context,
            ("REPRODUCTION_UNUSABLE", "FAILURE_TO_VERIFY_IS_NOT_REJECTION"),
        )
    reproduction = context.reproduction_evidence
    if reproduction is None:
        return _result(
            VerificationOutcome.INCONCLUSIVE,
            context,
            ("MISSING_REPRODUCTION_EVIDENCE", "CANNOT_SELF_VALIDATE"),
        )
    if not _independent(context.original_evidence, reproduction):
        return _result(
            VerificationOutcome.INCONCLUSIVE,
            context,
            ("REPRODUCTION_NOT_INDEPENDENT", "CANNOT_SELF_VALIDATE"),
            reproduction_ids=(reproduction.evidence_id,),
        )
    if reproduction.research_run_id != context.research_run_id:
        return _result(
            VerificationOutcome.INCONCLUSIVE,
            context,
            ("REPRODUCTION_WRONG_RUN",),
            reproduction_ids=(reproduction.evidence_id,),
        )

    control_ids = ()
    if context.negative_control_evidence is not None:
        control_ids = (context.negative_control_evidence.evidence_id,)

    if (
        reproduction.polarity == EvidencePolarity.CONTRADICTING.value
        and reproduction.claim_scope == DIAGNOSTIC_ECHO_MISMATCHED_CLAIM
    ):
        return _result(
            VerificationOutcome.REJECTED,
            context,
            ("REPRODUCTION_CONTRADICTS_CLAIM",),
            reproduction_ids=(reproduction.evidence_id,),
            control_ids=control_ids,
        )

    if not (
        reproduction.polarity == EvidencePolarity.SUPPORTING.value
        and reproduction.claim_scope == DIAGNOSTIC_ECHO_MATCHED_CLAIM
    ):
        return _result(
            VerificationOutcome.INCONCLUSIVE,
            context,
            ("REPRODUCTION_DOES_NOT_SUPPORT_CLAIM",),
            reproduction_ids=(reproduction.evidence_id,),
            control_ids=control_ids,
        )

    observed = reproduction.observed_echo
    token = context.plan.negative_control_token
    control_held = observed is not None and observed != token
    checks = {
        "negative_control_token": token,
        "reproduction_observed_echo": observed,
        "negative_control_held": control_held,
        "not_a_vulnerability": True,
        "not_a_finding": True,
    }
    if context.negative_control_evidence is not None:
        control = context.negative_control_evidence
        if (
            control.polarity == EvidencePolarity.CONTRADICTING.value
            and control.claim_scope == DIAGNOSTIC_ECHO_MISMATCHED_CLAIM
            and _independent(context.original_evidence, control)
            and control.evidence_id != reproduction.evidence_id
        ):
            checks["mismatch_fixture_held"] = True
        else:
            return _result(
                VerificationOutcome.INCONCLUSIVE,
                context,
                ("NEGATIVE_CONTROL_EVIDENCE_UNUSABLE",),
                reproduction_ids=(reproduction.evidence_id,),
                control_ids=control_ids,
                checks=checks,
            )
    if not control_held:
        return _result(
            VerificationOutcome.INCONCLUSIVE,
            context,
            ("NEGATIVE_CONTROL_DID_NOT_HOLD",),
            reproduction_ids=(reproduction.evidence_id,),
            control_ids=control_ids,
            checks=checks,
        )
    return _result(
        VerificationOutcome.VALIDATED,
        context,
        ("REPRODUCTION_INDEPENDENT", "NEGATIVE_CONTROL_HELD", "DIAGNOSTIC_PLUMBING_ONLY"),
        reproduction_ids=(reproduction.evidence_id,),
        control_ids=control_ids,
        checks=checks,
    )


def evaluate_authorization_differential_verification(
    context: VerificationContext,
) -> VerificationResult:
    """Independent reproduction verifier. Original Evidence cannot self-validate."""

    identity = HTTP_AUTHORIZATION_DIFFERENTIAL_VERIFIER_IDENTITY
    if context.plan.candidate_id != context.candidate_id:
        raise ResearchInputError("VerificationPlan candidate_id mismatch")
    if context.plan.verification_strategy != HTTP_AUTHORIZATION_DIFFERENTIAL_VERIFICATION_STRATEGY:
        raise ResearchInputError("unsupported verification strategy")
    if context.original_evidence.research_run_id != context.research_run_id:
        raise ResearchInputError("original evidence is not in the Candidate research run")
    if context.authoritative_out_of_scope:
        return _result(
            VerificationOutcome.OUT_OF_SCOPE,
            context,
            ("AUTHORITATIVE_OUT_OF_SCOPE",),
            verifier_identity=identity,
        )
    if context.reproduction_execution_unusable:
        return _result(
            VerificationOutcome.INCONCLUSIVE,
            context,
            ("REPRODUCTION_UNUSABLE", "FAILURE_TO_VERIFY_IS_NOT_REJECTION"),
            verifier_identity=identity,
        )
    if not _reproduction_independent_of_original(context):
        return _result(
            VerificationOutcome.INCONCLUSIVE,
            context,
            ("REPRODUCTION_NOT_INDEPENDENT", "CANNOT_SELF_VALIDATE"),
            reproduction_ids=_reproduction_ids(context),
            verifier_identity=identity,
        )
    reproduction = context.reproduction_evidence
    if (
        reproduction is not None
        and reproduction.polarity == EvidencePolarity.SUPPORTING.value
        and reproduction.claim_scope == HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM
        and _controls_held(reproduction.observed_facts)
    ):
        return _result(
            VerificationOutcome.VALIDATED,
            context,
            (
                "REPRODUCTION_INDEPENDENT",
                "NEGATIVE_CONTROL_HELD",
                "HTTP_AUTHORIZATION_DIFFERENTIAL_REPRODUCED",
            ),
            reproduction_ids=(reproduction.evidence_id,),
            checks={
                "negative_control_held": True,
                "not_a_finding": True,
            },
            verifier_identity=identity,
        )
    if context.reproduction_assessment_outcome is AssessmentOutcome.CONTRADICTS_PREDICTION:
        return _result(
            VerificationOutcome.REJECTED,
            context,
            ("REPRODUCTION_CONTRADICTS_CLAIM", "OBJECT_ACCESS_CONTROL_HELD"),
            reproduction_ids=_reproduction_ids(context),
            verifier_identity=identity,
        )
    return _result(
        VerificationOutcome.INCONCLUSIVE,
        context,
        ("REPRODUCTION_DOES_NOT_SUPPORT_CLAIM",),
        reproduction_ids=_reproduction_ids(context),
        verifier_identity=identity,
    )


def evaluate_state_transition_verification(
    context: VerificationContext,
) -> VerificationResult:
    """Independent reproduction verifier. Original Evidence cannot self-validate."""

    identity = HTTP_STATE_TRANSITION_VERIFIER_IDENTITY
    if context.plan.candidate_id != context.candidate_id:
        raise ResearchInputError("VerificationPlan candidate_id mismatch")
    if context.plan.verification_strategy != HTTP_STATE_TRANSITION_VERIFICATION_STRATEGY:
        raise ResearchInputError("unsupported verification strategy")
    if context.original_evidence.research_run_id != context.research_run_id:
        raise ResearchInputError("original evidence is not in the Candidate research run")
    if context.authoritative_out_of_scope:
        return _result(
            VerificationOutcome.OUT_OF_SCOPE,
            context,
            ("AUTHORITATIVE_OUT_OF_SCOPE",),
            verifier_identity=identity,
        )
    if context.reproduction_execution_unusable:
        return _result(
            VerificationOutcome.INCONCLUSIVE,
            context,
            ("REPRODUCTION_UNUSABLE", "FAILURE_TO_VERIFY_IS_NOT_REJECTION"),
            verifier_identity=identity,
        )
    if not _reproduction_independent_of_original(context):
        return _result(
            VerificationOutcome.INCONCLUSIVE,
            context,
            ("REPRODUCTION_NOT_INDEPENDENT", "CANNOT_SELF_VALIDATE"),
            reproduction_ids=_reproduction_ids(context),
            verifier_identity=identity,
        )
    reproduction = context.reproduction_evidence
    if (
        reproduction is not None
        and reproduction.polarity == EvidencePolarity.SUPPORTING.value
        and reproduction.claim_scope == HTTP_STATE_TRANSITION_CLAIM
        and _workflow_controls_held(reproduction.observed_facts)
    ):
        return _result(
            VerificationOutcome.VALIDATED,
            context,
            (
                "REPRODUCTION_INDEPENDENT",
                "NEGATIVE_CONTROL_HELD",
                "HTTP_STATE_TRANSITION_AUTHORIZATION_REPRODUCED",
            ),
            reproduction_ids=(reproduction.evidence_id,),
            checks={
                "negative_control_held": True,
                "not_a_finding": True,
            },
            verifier_identity=identity,
        )
    if context.reproduction_assessment_outcome is AssessmentOutcome.CONTRADICTS_PREDICTION:
        return _result(
            VerificationOutcome.REJECTED,
            context,
            ("REPRODUCTION_CONTRADICTS_CLAIM", "WORKFLOW_CONTROL_HELD"),
            reproduction_ids=_reproduction_ids(context),
            verifier_identity=identity,
        )
    return _result(
        VerificationOutcome.INCONCLUSIVE,
        context,
        ("REPRODUCTION_DOES_NOT_SUPPORT_CLAIM",),
        reproduction_ids=_reproduction_ids(context),
        verifier_identity=identity,
    )


def _reproduction_ids(context: VerificationContext) -> tuple[str, ...]:
    if context.reproduction_evidence is None:
        return ()
    return (context.reproduction_evidence.evidence_id,)


def _reproduction_independent_of_original(context: VerificationContext) -> bool:
    original = context.original_evidence
    reproduction = context.reproduction_evidence
    experiment_id = context.reproduction_experiment_id
    request_id = context.reproduction_request_id
    observation_ids = context.reproduction_observation_ids
    if reproduction is not None:
        experiment_id = reproduction.experiment_id
        request_id = reproduction.request_id
        observation_ids = reproduction.observation_ids
        if original.evidence_id == reproduction.evidence_id:
            return False
    if not experiment_id or not request_id:
        return False
    if original.experiment_id == experiment_id:
        return False
    if original.request_id == request_id:
        return False
    if set(original.observation_ids) & set(observation_ids):
        return False
    return True


def _controls_held(facts: Mapping[str, Any] | None) -> bool:
    if not isinstance(facts, Mapping):
        return False
    secure = facts.get("secure_control_status")
    unauth = facts.get("unauthenticated_control_status")
    cross_owner = facts.get("cross_object_request_object_owner")
    cross_status = facts.get("cross_object_request_status")
    actor = facts.get("actor")
    visibility = facts.get("cross_object_request_visibility")
    kind = facts.get("cross_object_request_resource_kind")
    readers = facts.get("cross_object_request_authorized_readers")
    if isinstance(visibility, str) and visibility.strip().upper() == "PUBLIC":
        return False
    if isinstance(kind, str) and kind.strip().upper() == "SHARED":
        return False
    if isinstance(readers, list) and isinstance(actor, str) and actor in readers:
        return False
    return (
        secure == 403
        and unauth in {401, 403}
        and cross_status == 200
        and isinstance(cross_owner, str)
        and bool(cross_owner.strip())
        and isinstance(actor, str)
        and bool(actor.strip())
        and cross_owner != actor
    )


def _workflow_controls_held(facts: Mapping[str, Any] | None) -> bool:
    if not isinstance(facts, Mapping):
        return False
    control = facts.get("control_status")
    post_state = facts.get("post_state")
    pre_state = facts.get("pre_state")
    actor = facts.get("actor")
    actor_role = facts.get("actor_role")
    approved_by = facts.get("approved_by")
    readers = facts.get("delegated_reviewers")
    if isinstance(readers, list) and isinstance(actor, str) and actor in readers:
        return False
    return (
        control in {401, 403, 409}
        and post_state == "APPROVED"
        and pre_state != post_state
        and actor_role == "requester"
        and isinstance(actor, str)
        and bool(actor.strip())
        and approved_by == actor
    )


def apply_verification_to_candidate(
    current: CandidateState,
    result: VerificationResult,
) -> CandidateState:
    """Research transition rules. Verifier output is a proposal, not a write."""

    return transition_candidate(current, result.proposed_candidate_state)
