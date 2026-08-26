"""Invariant mining. Expected-behavior hypotheses, not facts, rules, or vulnerabilities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from zest.research.differential import DifferentialObservation
from zest.research.target_model import TargetObservationView
from zest.research.types import ResearchInputError

INVARIANT_STRATEGY_VERSION = "invariant.diagnostic.echo.v1"
DIAGNOSTIC_INVARIANT_BEHAVIOR = (
    "for diagnostic.echo, output should correspond to the submitted input"
)
DIAGNOSTIC_FALSIFICATION = (
    "submit a diagnostic echo value and observe a mismatch or missing echo"
)
FORBIDDEN_INVARIANT_KEYS = frozenset(
    {
        "severity",
        "cvss",
        "cve",
        "vulnerability",
        "idor",
        "confidence",
        "evidence",
        "candidate",
        "finding",
        "authorization",
        "scope",
        "token",
        "session_token",
        "password",
    }
)
POLICY_MARKERS = (
    "vulnerability",
    "idor",
    "change scope",
    "bypass authorization",
    "scope rule",
    "only owner",
)


class InvariantKind(Enum):
    """Expectation class. Not a vulnerability class."""

    ACCESS_RELATION = "ACCESS_RELATION"
    STATE_TRANSITION = "STATE_TRANSITION"
    OWNERSHIP_RELATION = "OWNERSHIP_RELATION"
    ROLE_BOUNDARY = "ROLE_BOUNDARY"
    SESSION_BINDING = "SESSION_BINDING"
    RESOURCE_ISOLATION = "RESOURCE_ISOLATION"
    IMMUTABILITY_AFTER_STATE = "IMMUTABILITY_AFTER_STATE"
    SEQUENCE_PRECONDITION = "SEQUENCE_PRECONDITION"
    INPUT_OUTPUT_RELATION = "INPUT_OUTPUT_RELATION"
    OTHER = "OTHER"


class InvariantStatus(Enum):
    """Epistemic lifecycle of an invariant hypothesis. Not Candidate state."""

    PROPOSED = "PROPOSED"
    TESTABLE = "TESTABLE"
    CHALLENGED = "CHALLENGED"
    RETIRED = "RETIRED"


class InvariantAdmissionOutcome(Enum):
    ADMITTED = "ADMITTED"
    REJECTED_UNTESTABLE = "REJECTED_UNTESTABLE"
    REJECTED_BROKEN_PROVENANCE = "REJECTED_BROKEN_PROVENANCE"
    REJECTED_CONTRADICTED = "REJECTED_CONTRADICTED"
    REJECTED_CROSS_RUN = "REJECTED_CROSS_RUN"
    REJECTED_POLICY_CONFLICT = "REJECTED_POLICY_CONFLICT"
    NEEDS_MORE_CONTEXT = "NEEDS_MORE_CONTEXT"


DIAGNOSTIC_INVARIANT_KINDS = frozenset({InvariantKind.INPUT_OUTPUT_RELATION})
LEGAL_INVARIANT_TRANSITIONS = frozenset(
    {
        (InvariantStatus.TESTABLE, InvariantStatus.CHALLENGED),
        (InvariantStatus.TESTABLE, InvariantStatus.RETIRED),
        (InvariantStatus.CHALLENGED, InvariantStatus.RETIRED),
    }
)


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
    found = FORBIDDEN_INVARIANT_KEYS.intersection(payload.keys())
    if found:
        raise ResearchInputError(f"{field_name} must not contain {sorted(found)}")
    return dict(payload)


@dataclass(frozen=True)
class InvariantProposal:
    """Untrusted expected-behavior proposal. Not a fact and not a ScopeRule."""

    proposal_id: str
    research_run_id: str
    invariant_kind: InvariantKind
    subject_refs: tuple[str, ...]
    expected_behavior: str
    source_refs: tuple[str, ...]
    applicability_context: Mapping[str, Any]
    assumptions: tuple[str, ...]
    known_counterexample_refs: tuple[str, ...]
    falsification_direction: str
    proposer_provenance: str
    strategy_version: str = INVARIANT_STRATEGY_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", _require_text(self.proposal_id, "proposal_id"))
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        if not isinstance(self.invariant_kind, InvariantKind):
            raise ResearchInputError("invariant_kind must be an InvariantKind")
        object.__setattr__(
            self, "subject_refs", _require_ids(self.subject_refs, "subject_refs")
        )
        object.__setattr__(
            self,
            "expected_behavior",
            _require_text(self.expected_behavior, "expected_behavior"),
        )
        object.__setattr__(self, "source_refs", _require_ids(self.source_refs, "source_refs"))
        object.__setattr__(
            self,
            "applicability_context",
            _reject_forbidden(self.applicability_context, "applicability_context"),
        )
        object.__setattr__(
            self,
            "assumptions",
            tuple(
                _require_text(item, f"assumptions[{index}]")
                for index, item in enumerate(self.assumptions)
            ),
        )
        object.__setattr__(
            self,
            "known_counterexample_refs",
            tuple(
                _require_text(item, f"known_counterexample_refs[{index}]")
                for index, item in enumerate(self.known_counterexample_refs)
            ),
        )
        object.__setattr__(
            self,
            "falsification_direction",
            _require_text(self.falsification_direction, "falsification_direction"),
        )
        object.__setattr__(
            self,
            "proposer_provenance",
            _require_text(self.proposer_provenance, "proposer_provenance"),
        )
        object.__setattr__(
            self,
            "strategy_version",
            _require_text(self.strategy_version, "strategy_version"),
        )


@dataclass(frozen=True)
class InvariantHypothesis:
    """Admitted expected-behavior hypothesis. Never OBSERVED. Never Core scope."""

    invariant_id: str
    research_run_id: str
    invariant_kind: InvariantKind
    status: InvariantStatus
    subject_refs: tuple[str, ...]
    expected_behavior: str
    source_refs: tuple[str, ...]
    applicability_context: Mapping[str, Any]
    assumptions: tuple[str, ...]
    counterexample_refs: tuple[str, ...]
    falsification_direction: str
    proposer_provenance: str
    strategy_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "invariant_id", _require_text(self.invariant_id, "invariant_id")
        )
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        if not isinstance(self.invariant_kind, InvariantKind):
            raise ResearchInputError("invariant_kind must be an InvariantKind")
        if not isinstance(self.status, InvariantStatus):
            raise ResearchInputError("status must be an InvariantStatus")
        if self.status is InvariantStatus.PROPOSED:
            raise ResearchInputError("admitted invariant cannot remain PROPOSED")
        object.__setattr__(
            self,
            "expected_behavior",
            _require_text(self.expected_behavior, "expected_behavior"),
        )


@dataclass(frozen=True)
class InvariantAdmissionDecision:
    outcome: InvariantAdmissionOutcome
    reason_codes: tuple[str, ...]
    hypothesis: InvariantHypothesis | None

    @property
    def admitted(self) -> bool:
        return self.outcome is InvariantAdmissionOutcome.ADMITTED


@dataclass(frozen=True)
class InvariantCounterexample:
    """Context-bound contradiction. Not a global disproof and not a vulnerability."""

    counterexample_id: str
    invariant_id: str
    source_ref: str
    applicability_context: Mapping[str, Any]


def propose_diagnostic_echo_invariant(
    research_run_id: str,
    views: tuple[TargetObservationView, ...],
    *,
    proposal_id: str,
    differential: DifferentialObservation | None = None,
) -> InvariantProposal | None:
    """Deterministic diagnostic INPUT_OUTPUT_RELATION. Not a security invariant."""

    run_id = _require_text(research_run_id, "research_run_id")
    diagnostic = tuple(
        view
        for view in views
        if view.research_run_id == run_id
        and view.capability == "diagnostic.echo"
        and view.action == "echo"
        and view.submitted_input is not None
        and isinstance(view.payload.get("echoed"), str)
    )
    if not diagnostic:
        return None
    source_refs = tuple(view.observation_id for view in diagnostic)
    if differential is not None:
        if differential.research_run_id != run_id:
            raise ResearchInputError("differential research_run_id mismatch")
        source_refs = source_refs + (differential.differential_id,)
    counterexamples = tuple(
        view.observation_id
        for view in diagnostic
        if view.payload.get("echoed") != view.submitted_input
    )
    return InvariantProposal(
        proposal_id=proposal_id,
        research_run_id=run_id,
        invariant_kind=InvariantKind.INPUT_OUTPUT_RELATION,
        subject_refs=tuple(view.resource_handle for view in diagnostic),
        expected_behavior=DIAGNOSTIC_INVARIANT_BEHAVIOR,
        source_refs=source_refs,
        applicability_context={
            "capability": "diagnostic.echo",
            "action": "echo",
            "not_authorization": True,
            "not_a_vulnerability": True,
        },
        assumptions=("diagnostic.echo is a plumbing capability, not an access control check",),
        known_counterexample_refs=counterexamples,
        falsification_direction=DIAGNOSTIC_FALSIFICATION,
        proposer_provenance="deterministic.diagnostic.echo.v1",
    )


def admit_invariant(
    proposal: InvariantProposal,
    *,
    research_run_id: str,
    resolvable_source_ids: frozenset[str],
    contradicting_source_ids: frozenset[str] = frozenset(),
) -> InvariantAdmissionDecision:
    """Admit an expected-behavior hypothesis. Never a fact, ScopeRule, or Finding."""

    if proposal.research_run_id != research_run_id:
        return InvariantAdmissionDecision(
            outcome=InvariantAdmissionOutcome.REJECTED_CROSS_RUN,
            reason_codes=("CROSS_RUN_SOURCE",),
            hypothesis=None,
        )
    if proposal.invariant_kind not in DIAGNOSTIC_INVARIANT_KINDS:
        return InvariantAdmissionDecision(
            outcome=InvariantAdmissionOutcome.REJECTED_UNTESTABLE,
            reason_codes=("NON_DIAGNOSTIC_INVARIANT_KIND",),
            hypothesis=None,
        )
    lowered = f"{proposal.expected_behavior} {' '.join(proposal.assumptions)}".lower()
    if any(marker in lowered for marker in POLICY_MARKERS):
        return InvariantAdmissionDecision(
            outcome=InvariantAdmissionOutcome.REJECTED_POLICY_CONFLICT,
            reason_codes=("POLICY_OR_AUTHORIZATION_CLAIM",),
            hypothesis=None,
        )
    if not proposal.source_refs:
        return InvariantAdmissionDecision(
            outcome=InvariantAdmissionOutcome.REJECTED_BROKEN_PROVENANCE,
            reason_codes=("NO_SOURCE_REFERENCES",),
            hypothesis=None,
        )
    missing = [ref for ref in proposal.source_refs if ref not in resolvable_source_ids]
    if missing:
        return InvariantAdmissionDecision(
            outcome=InvariantAdmissionOutcome.NEEDS_MORE_CONTEXT,
            reason_codes=("HALLUCINATED_SOURCE",),
            hypothesis=None,
        )
    missing_counters = [
        ref for ref in proposal.known_counterexample_refs if ref not in resolvable_source_ids
    ]
    if missing_counters:
        return InvariantAdmissionDecision(
            outcome=InvariantAdmissionOutcome.REJECTED_BROKEN_PROVENANCE,
            reason_codes=("HALLUCINATED_COUNTEREXAMPLE",),
            hypothesis=None,
        )
    if not proposal.falsification_direction.strip():
        return InvariantAdmissionDecision(
            outcome=InvariantAdmissionOutcome.REJECTED_UNTESTABLE,
            reason_codes=("MISSING_FALSIFICATION_DIRECTION",),
            hypothesis=None,
        )
    if not proposal.applicability_context:
        return InvariantAdmissionDecision(
            outcome=InvariantAdmissionOutcome.REJECTED_UNTESTABLE,
            reason_codes=("APPLICABILITY_CONTEXT_REQUIRED",),
            hypothesis=None,
        )
    unacknowledged = contradicting_source_ids.difference(proposal.known_counterexample_refs)
    if unacknowledged:
        return InvariantAdmissionDecision(
            outcome=InvariantAdmissionOutcome.REJECTED_CONTRADICTED,
            reason_codes=("UNACKNOWLEDGED_COUNTEREXAMPLE",),
            hypothesis=None,
        )
    status = (
        InvariantStatus.CHALLENGED
        if proposal.known_counterexample_refs
        else InvariantStatus.TESTABLE
    )
    return InvariantAdmissionDecision(
        outcome=InvariantAdmissionOutcome.ADMITTED,
        reason_codes=("INVARIANT_HYPOTHESIS_ADMITTED", "NOT_A_FACT"),
        hypothesis=InvariantHypothesis(
            invariant_id=proposal.proposal_id,
            research_run_id=proposal.research_run_id,
            invariant_kind=proposal.invariant_kind,
            status=status,
            subject_refs=proposal.subject_refs,
            expected_behavior=proposal.expected_behavior,
            source_refs=proposal.source_refs,
            applicability_context=dict(proposal.applicability_context),
            assumptions=proposal.assumptions,
            counterexample_refs=proposal.known_counterexample_refs,
            falsification_direction=proposal.falsification_direction,
            proposer_provenance=proposal.proposer_provenance,
            strategy_version=proposal.strategy_version,
        ),
    )


def apply_invariant_counterexample(
    hypothesis: InvariantHypothesis,
    counterexample: InvariantCounterexample,
) -> InvariantHypothesis:
    """Record a context-bound contradiction. Does not globally falsify the invariant."""

    if counterexample.invariant_id != hypothesis.invariant_id:
        raise ResearchInputError("counterexample invariant_id mismatch")
    if hypothesis.status is InvariantStatus.RETIRED:
        raise ResearchInputError("retired invariant cannot accept counterexamples")
    next_status = InvariantStatus.CHALLENGED
    if (
        hypothesis.status is not InvariantStatus.CHALLENGED
        and (hypothesis.status, next_status) not in LEGAL_INVARIANT_TRANSITIONS
    ):
        raise ResearchInputError("illegal invariant status transition")
    refs = hypothesis.counterexample_refs
    if counterexample.source_ref not in refs:
        refs = refs + (counterexample.source_ref,)
    return InvariantHypothesis(
        invariant_id=hypothesis.invariant_id,
        research_run_id=hypothesis.research_run_id,
        invariant_kind=hypothesis.invariant_kind,
        status=next_status,
        subject_refs=hypothesis.subject_refs,
        expected_behavior=hypothesis.expected_behavior,
        source_refs=hypothesis.source_refs,
        applicability_context=dict(hypothesis.applicability_context),
        assumptions=hypothesis.assumptions,
        counterexample_refs=refs,
        falsification_direction=hypothesis.falsification_direction,
        proposer_provenance=hypothesis.proposer_provenance,
        strategy_version=hypothesis.strategy_version,
    )
