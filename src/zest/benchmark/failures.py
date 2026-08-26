"""Separate provider/runtime failures from research-quality failures."""

from __future__ import annotations

from enum import Enum

from zest.benchmark.cycle import BoundedCycleTrace
from zest.benchmark.metrics import HardFailCode
from zest.research.admission import AdmissionOutcome
from zest.research.model_port import (
    ContentPolicyBlockedError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    StructuredOutputTransportError,
)

RESEARCH_HARD_FAILS = frozenset(
    {
        HardFailCode.HALLUCINATED_SOURCE_REFERENCE,
        HardFailCode.AUTHORITY_CLAIM,
        HardFailCode.SCOPE_BYPASS_REQUEST,
        HardFailCode.EVIDENCE_OR_FINDING_CLAIM,
        HardFailCode.UNTRUSTED_INSTRUCTION_FOLLOWED,
        HardFailCode.INVALID_EPISTEMIC_RELABELING,
        HardFailCode.PROPOSAL_NOT_TESTABLE,
        HardFailCode.POLICY_TRAP_FOLLOWED,
        HardFailCode.PRIOR_HYPOTHESIS_TREATED_AS_FACT,
        HardFailCode.FALSIFIER_MISSING,
    }
)


class FailureClass(Enum):
    NONE = "NONE"
    PROVIDER_RUNTIME = "PROVIDER_RUNTIME"
    PROVIDER_AUTH = "PROVIDER_AUTH"
    PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    CONTENT_POLICY_BLOCKED = "CONTENT_POLICY_BLOCKED"
    STRUCTURED_OUTPUT_FAILURE = "STRUCTURED_OUTPUT_FAILURE"
    GENERATOR_RESEARCH_QUALITY = "GENERATOR_RESEARCH_QUALITY"
    FALSIFIER_RESEARCH_QUALITY = "FALSIFIER_RESEARCH_QUALITY"
    HARNESS_INVARIANT = "HARNESS_INVARIANT"


PROVIDER_FAILURE_CLASSES = frozenset(
    {
        FailureClass.PROVIDER_RUNTIME,
        FailureClass.PROVIDER_AUTH,
        FailureClass.PROVIDER_RATE_LIMIT,
        FailureClass.PROVIDER_TIMEOUT,
        FailureClass.CONTENT_POLICY_BLOCKED,
    }
)


def classify_failure(
    trace: BoundedCycleTrace, hard_fails: tuple[HardFailCode, ...]
) -> FailureClass:
    if HardFailCode.HIDDEN_BENCHMARK_DATA_LEAKAGE in hard_fails:
        return FailureClass.HARNESS_INVARIANT
    if trace.provider_failure_class:
        try:
            mapped = FailureClass(trace.provider_failure_class)
        except ValueError:
            mapped = FailureClass.PROVIDER_RUNTIME
        if mapped is not FailureClass.NONE:
            return mapped
    if (
        trace.provider_runtime_error
        or trace.admission.outcome is AdmissionOutcome.MODEL_INVOCATION_FAILED
    ):
        return FailureClass.PROVIDER_RUNTIME
    if HardFailCode.MALFORMED_STRUCTURED_OUTPUT in hard_fails:
        return FailureClass.STRUCTURED_OUTPUT_FAILURE
    if HardFailCode.FALSIFIER_MISSING in hard_fails:
        return FailureClass.FALSIFIER_RESEARCH_QUALITY
    if RESEARCH_HARD_FAILS.intersection(hard_fails):
        return FailureClass.GENERATOR_RESEARCH_QUALITY
    return FailureClass.NONE


def classify_provider_error(exc: BaseException) -> FailureClass:
    if isinstance(exc, StructuredOutputTransportError):
        return FailureClass.STRUCTURED_OUTPUT_FAILURE
    if isinstance(exc, ProviderAuthError):
        return FailureClass.PROVIDER_AUTH
    if isinstance(exc, ProviderRateLimitError):
        return FailureClass.PROVIDER_RATE_LIMIT
    if isinstance(exc, ProviderTimeoutError):
        return FailureClass.PROVIDER_TIMEOUT
    if isinstance(exc, ContentPolicyBlockedError):
        return FailureClass.CONTENT_POLICY_BLOCKED
    return FailureClass.PROVIDER_RUNTIME
