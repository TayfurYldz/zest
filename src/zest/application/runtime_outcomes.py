"""Map ModelPort exceptions to RuntimeOutcome. Do not infer policy block from generic failure."""

from __future__ import annotations

from zest.research.model_port import (
    ContentPolicyBlockedError,
    ModelPortError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderRuntimeError,
    ProviderTimeoutError,
    RuntimeCancelledError,
    RuntimeProcessError,
    RuntimeUnavailableError,
    StructuredOutputTransportError,
)
from zest.research.model_runtime import RuntimeOutcome
from zest.research.orchestration import StopReason


def runtime_outcome_from_exception(exc: BaseException) -> RuntimeOutcome:
    if isinstance(exc, ContentPolicyBlockedError):
        return RuntimeOutcome.CONTENT_POLICY_BLOCKED
    if isinstance(exc, ProviderAuthError):
        return RuntimeOutcome.AUTH_FAILED
    if isinstance(exc, ProviderRateLimitError):
        return RuntimeOutcome.RATE_LIMITED
    if isinstance(exc, ProviderTimeoutError):
        return RuntimeOutcome.TIMED_OUT
    if isinstance(exc, RuntimeUnavailableError):
        return RuntimeOutcome.UNAVAILABLE
    if isinstance(exc, RuntimeCancelledError):
        return RuntimeOutcome.CANCELLED
    if isinstance(exc, RuntimeProcessError):
        return RuntimeOutcome.PROCESS_FAILED
    if isinstance(exc, StructuredOutputTransportError):
        return RuntimeOutcome.STRUCTURED_OUTPUT_INVALID
    if isinstance(exc, ProviderRuntimeError):
        return RuntimeOutcome.PROCESS_FAILED
    if isinstance(exc, ModelPortError):
        return RuntimeOutcome.PROTOCOL_ERROR
    return RuntimeOutcome.PROCESS_FAILED


def stop_reason_for_runtime_outcome(outcome: RuntimeOutcome) -> StopReason:
    if outcome is RuntimeOutcome.CONTENT_POLICY_BLOCKED:
        return StopReason.CONTENT_POLICY_BLOCKED
    if outcome is RuntimeOutcome.UNAVAILABLE:
        return StopReason.NO_COMPATIBLE_RUNTIME
    if outcome is RuntimeOutcome.AUTH_FAILED:
        return StopReason.AUTH_REQUIRED
    if outcome is RuntimeOutcome.RATE_LIMITED:
        return StopReason.RATE_LIMITED
    if outcome is RuntimeOutcome.CANCELLED:
        return StopReason.CANCELLED
    return StopReason.OPERATIONAL_FAILURE
