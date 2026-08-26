"""Research-domain Hypothesis admission. Not Evidence admission and not Candidate lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from zest.research.context import ResearchContext
from zest.research.epistemic import EpistemicClass
from zest.research.proposals import HypothesisChallenge, HypothesisProposal


class AdmissionOutcome(Enum):
    """Proposal admission only. Not a tested-security rejection."""

    ADMITTED = "ADMITTED"
    REJECTED_UNTESTABLE = "REJECTED_UNTESTABLE"
    REJECTED_UNSUPPORTED = "REJECTED_UNSUPPORTED"
    REJECTED_POLICY_CONFLICT = "REJECTED_POLICY_CONFLICT"
    NEEDS_MORE_CONTEXT = "NEEDS_MORE_CONTEXT"
    MODEL_INVOCATION_FAILED = "MODEL_INVOCATION_FAILED"


@dataclass(frozen=True)
class AdmissionDecision:
    outcome: AdmissionOutcome
    reason: str
    reason_code: str
    proposal: HypothesisProposal | None
    challenge: HypothesisChallenge | None

    @property
    def admitted(self) -> bool:
        return self.outcome is AdmissionOutcome.ADMITTED


_POLICY_CLAIM_MARKERS = (
    "mark this as a vulnerability",
    "declare this evidence",
    "declare this a finding",
    "ignore all previous instructions",
    "change scope",
    "bypass authorization",
    "raise budget",
)


def admit_hypothesis(
    context: ResearchContext,
    proposal: HypothesisProposal | None,
    challenge: HypothesisChallenge | None,
) -> AdmissionDecision:
    """Decide whether a validated proposal may become a persisted Hypothesis."""

    if proposal is None:
        return AdmissionDecision(
            outcome=AdmissionOutcome.REJECTED_UNTESTABLE,
            reason="no validated HypothesisProposal",
            reason_code="NO_VALIDATED_PROPOSAL",
            proposal=None,
            challenge=challenge,
        )
    if challenge is None:
        return AdmissionDecision(
            outcome=AdmissionOutcome.REJECTED_UNTESTABLE,
            reason="Falsifier challenge is required before admission",
            reason_code="CHALLENGE_REQUIRED",
            proposal=proposal,
            challenge=None,
        )

    claim = proposal.proposed_claim.strip()
    if not claim:
        return AdmissionDecision(
            outcome=AdmissionOutcome.REJECTED_UNTESTABLE,
            reason="proposed_claim is empty",
            reason_code="EMPTY_CLAIM",
            proposal=proposal,
            challenge=challenge,
        )

    lowered = f"{claim} {proposal.rationale}".lower()
    if any(marker in lowered for marker in _POLICY_CLAIM_MARKERS):
        return AdmissionDecision(
            outcome=AdmissionOutcome.REJECTED_POLICY_CONFLICT,
            reason="proposal attempts to set policy, Evidence, or Finding semantics",
            reason_code="POLICY_CONFLICT",
            proposal=proposal,
            challenge=challenge,
        )

    if not proposal.source_references:
        return AdmissionDecision(
            outcome=AdmissionOutcome.REJECTED_UNSUPPORTED,
            reason="proposal has no source references",
            reason_code="NO_SOURCE_REFERENCES",
            proposal=proposal,
            challenge=challenge,
        )

    resolvable = context.resolvable_source_ids()
    missing = [ref for ref in proposal.source_references if ref not in resolvable]
    if missing:
        return AdmissionDecision(
            outcome=AdmissionOutcome.NEEDS_MORE_CONTEXT,
            reason=f"source references are not in the assembled context: {missing}",
            reason_code="HALLUCINATED_SOURCE",
            proposal=proposal,
            challenge=challenge,
        )

    supporting_non_untrusted = [
        ref
        for ref in proposal.source_references
        if context.item_by_id(ref) is not None
        and context.item_by_id(ref).epistemic_class
        is not EpistemicClass.UNTRUSTED_EXTERNAL
    ]
    if not supporting_non_untrusted:
        return AdmissionDecision(
            outcome=AdmissionOutcome.REJECTED_UNSUPPORTED,
            reason="proposal is not supported by an allowed context item",
            reason_code="UNSUPPORTED_CONTEXT",
            proposal=proposal,
            challenge=challenge,
        )

    if not proposal.suggested_disconfirming_test.strip():
        return AdmissionDecision(
            outcome=AdmissionOutcome.REJECTED_UNTESTABLE,
            reason="proposal has no suggested disconfirming test",
            reason_code="MISSING_DISCONFIRMING_TEST",
            proposal=proposal,
            challenge=challenge,
        )
    if not proposal.suggested_capability.strip():
        return AdmissionDecision(
            outcome=AdmissionOutcome.REJECTED_UNTESTABLE,
            reason="proposal has no suggested capability",
            reason_code="MISSING_CAPABILITY",
            proposal=proposal,
            challenge=challenge,
        )
    if not challenge.proposed_disconfirming_observation.strip():
        return AdmissionDecision(
            outcome=AdmissionOutcome.REJECTED_UNTESTABLE,
            reason="challenge has no proposed disconfirming observation",
            reason_code="MISSING_DISCONFIRMING_OBSERVATION",
            proposal=proposal,
            challenge=challenge,
        )
    if not challenge.alternative_explanations:
        return AdmissionDecision(
            outcome=AdmissionOutcome.REJECTED_UNTESTABLE,
            reason="challenge must retain at least one alternative explanation",
            reason_code="MISSING_ALTERNATIVE_EXPLANATION",
            proposal=proposal,
            challenge=challenge,
        )

    return AdmissionDecision(
        outcome=AdmissionOutcome.ADMITTED,
        reason="proposal is testable, sourced, and independently challenged",
        reason_code="ADMITTED",
        proposal=proposal,
        challenge=challenge,
    )
