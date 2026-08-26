"""Bounded Generator/Falsifier/admission cycle for evaluation. No persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from zest.research.admission import AdmissionDecision, AdmissionOutcome, admit_hypothesis
from zest.research.context import ResearchContext
from zest.research.cycle import generate_challenge, generate_proposal
from zest.research.model_port import (
    ContentPolicyBlockedError,
    ModelCallRequest,
    ModelCallResult,
    ModelPort,
    ModelPortError,
    ModelRole,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    StructuredOutputTransportError,
)
from zest.research.proposals import (
    HypothesisChallenge,
    HypothesisProposal,
    ProposalAuthorityError,
)
from zest.research.types import ResearchInputError


@dataclass
class RecordingModelPort:
    """Records ModelCallRequest envelopes. Does not alter structured output."""

    inner: ModelPort
    requests: list[ModelCallRequest] = field(default_factory=list)

    def complete(self, request: ModelCallRequest) -> ModelCallResult:
        self.requests.append(request)
        return self.inner.complete(request)


@dataclass(frozen=True)
class BoundedCycleTrace:
    context: ResearchContext
    admission: AdmissionDecision
    generator_calls: int
    falsifier_calls: int
    requests: tuple[ModelCallRequest, ...]
    proposal: HypothesisProposal | None = None
    challenge: HypothesisChallenge | None = None
    generator_output: Mapping[str, Any] | None = None
    falsifier_output: Mapping[str, Any] | None = None
    parse_error: str | None = None
    provider_runtime_error: bool = False
    provider_failure_class: str | None = None
    generator_telemetry: object | None = None
    falsifier_telemetry: object | None = None


def run_bounded_cycle(
    context: ResearchContext,
    model: ModelPort,
    *,
    correlation_id: str,
) -> BoundedCycleTrace:
    recorder = RecordingModelPort(model)
    proposal: HypothesisProposal | None = None
    challenge: HypothesisChallenge | None = None
    generator_output: Mapping[str, Any] | None = None
    falsifier_output: Mapping[str, Any] | None = None
    parse_error: str | None = None
    generator_telemetry = None
    falsifier_telemetry = None

    try:
        generated = generate_proposal(
            context, recorder, correlation_id=correlation_id
        )
        generator_output = dict(generated.model_result.structured_output)
        proposal = generated.proposal
        generator_telemetry = generated.model_result.telemetry
    except ModelPortError as exc:
        admission = AdmissionDecision(
            outcome=AdmissionOutcome.MODEL_INVOCATION_FAILED,
            reason=str(exc),
            reason_code="MODEL_INVOCATION_FAILED",
            proposal=None,
            challenge=None,
        )
        return BoundedCycleTrace(
            context=context,
            admission=admission,
            generator_calls=_role_calls(recorder, ModelRole.GENERATOR),
            falsifier_calls=0,
            requests=tuple(recorder.requests),
            parse_error=str(exc),
            provider_runtime_error=not isinstance(exc, StructuredOutputTransportError),
            provider_failure_class=_provider_failure_value(exc),
        )
    except ProposalAuthorityError as exc:
        generator_output = _output_from_exc(exc)
        admission = AdmissionDecision(
            outcome=AdmissionOutcome.REJECTED_POLICY_CONFLICT,
            reason=str(exc),
            reason_code="POLICY_CONFLICT",
            proposal=None,
            challenge=None,
        )
        return BoundedCycleTrace(
            context=context,
            admission=admission,
            generator_calls=_role_calls(recorder, ModelRole.GENERATOR),
            falsifier_calls=0,
            requests=tuple(recorder.requests),
            generator_output=generator_output,
            parse_error=str(exc),
        )
    except ResearchInputError as exc:
        generator_output = _output_from_exc(exc)
        admission = AdmissionDecision(
            outcome=AdmissionOutcome.REJECTED_UNTESTABLE,
            reason=str(exc),
            reason_code="INVALID_STRUCTURED_OUTPUT",
            proposal=None,
            challenge=None,
        )
        return BoundedCycleTrace(
            context=context,
            admission=admission,
            generator_calls=_role_calls(recorder, ModelRole.GENERATOR),
            falsifier_calls=0,
            requests=tuple(recorder.requests),
            generator_output=generator_output,
            parse_error=str(exc),
        )

    try:
        challenged = generate_challenge(
            context, proposal, recorder, correlation_id=correlation_id
        )
        falsifier_output = dict(challenged.model_result.structured_output)
        challenge = challenged.challenge
        falsifier_telemetry = challenged.model_result.telemetry
    except ModelPortError as exc:
        admission = AdmissionDecision(
            outcome=AdmissionOutcome.MODEL_INVOCATION_FAILED,
            reason=str(exc),
            reason_code="MODEL_INVOCATION_FAILED",
            proposal=proposal,
            challenge=None,
        )
        return BoundedCycleTrace(
            context=context,
            admission=admission,
            generator_calls=_role_calls(recorder, ModelRole.GENERATOR),
            falsifier_calls=_role_calls(recorder, ModelRole.FALSIFIER),
            requests=tuple(recorder.requests),
            proposal=proposal,
            generator_output=generator_output,
            parse_error=str(exc),
            provider_runtime_error=not isinstance(exc, StructuredOutputTransportError),
            provider_failure_class=_provider_failure_value(exc),
            generator_telemetry=generator_telemetry,
        )
    except ProposalAuthorityError as exc:
        falsifier_output = _output_from_exc(exc)
        parse_error = str(exc)
        admission = AdmissionDecision(
            outcome=AdmissionOutcome.REJECTED_POLICY_CONFLICT,
            reason=str(exc),
            reason_code="POLICY_CONFLICT",
            proposal=proposal,
            challenge=None,
        )
        return BoundedCycleTrace(
            context=context,
            admission=admission,
            generator_calls=_role_calls(recorder, ModelRole.GENERATOR),
            falsifier_calls=_role_calls(recorder, ModelRole.FALSIFIER),
            requests=tuple(recorder.requests),
            proposal=proposal,
            generator_output=generator_output,
            falsifier_output=falsifier_output,
            parse_error=parse_error,
        )
    except ResearchInputError as exc:
        falsifier_output = _output_from_exc(exc)
        parse_error = str(exc)
        admission = AdmissionDecision(
            outcome=AdmissionOutcome.REJECTED_UNTESTABLE,
            reason=str(exc),
            reason_code="INVALID_STRUCTURED_OUTPUT",
            proposal=proposal,
            challenge=None,
        )
        return BoundedCycleTrace(
            context=context,
            admission=admission,
            generator_calls=_role_calls(recorder, ModelRole.GENERATOR),
            falsifier_calls=_role_calls(recorder, ModelRole.FALSIFIER),
            requests=tuple(recorder.requests),
            proposal=proposal,
            generator_output=generator_output,
            falsifier_output=falsifier_output,
            parse_error=parse_error,
        )

    admission = admit_hypothesis(context, proposal, challenge)
    return BoundedCycleTrace(
        context=context,
        admission=admission,
        generator_calls=_role_calls(recorder, ModelRole.GENERATOR),
        falsifier_calls=_role_calls(recorder, ModelRole.FALSIFIER),
        requests=tuple(recorder.requests),
        proposal=proposal,
        challenge=challenge,
        generator_output=generator_output,
        falsifier_output=falsifier_output,
        generator_telemetry=generator_telemetry,
        falsifier_telemetry=falsifier_telemetry,
    )


def _provider_failure_value(exc: ModelPortError) -> str:
    if isinstance(exc, StructuredOutputTransportError):
        return "STRUCTURED_OUTPUT_FAILURE"
    if isinstance(exc, ContentPolicyBlockedError):
        return "CONTENT_POLICY_BLOCKED"
    if isinstance(exc, ProviderAuthError):
        return "PROVIDER_AUTH"
    if isinstance(exc, ProviderRateLimitError):
        return "PROVIDER_RATE_LIMIT"
    if isinstance(exc, ProviderTimeoutError):
        return "PROVIDER_TIMEOUT"
    return "PROVIDER_RUNTIME"


def _role_calls(recorder: RecordingModelPort, role: ModelRole) -> int:
    return sum(1 for request in recorder.requests if request.role is role)


def _output_from_exc(exc: BaseException) -> Mapping[str, Any] | None:
    result = getattr(exc, "model_result", None)
    if result is None:
        return None
    return dict(result.structured_output)
