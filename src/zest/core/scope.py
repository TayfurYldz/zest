"""Scope decision semantics over pre-evaluated rule matches. No target parsing."""

from dataclasses import dataclass

from zest.core.enums import (
    ReasonCode,
    ScopeClassification,
    ScopeDecision,
    ScopeRuleEffect,
)
from zest.core.errors import CoreInputError
from zest.core.identity import require_opaque_id


@dataclass(frozen=True)
class ScopeRuleMatch:
    rule_id: str
    effect: ScopeRuleEffect
    matched: bool
    source_reference: str

    def __post_init__(self) -> None:
        require_opaque_id(self.rule_id, "rule_id")
        require_opaque_id(self.source_reference, "source_reference")
        if not isinstance(self.effect, ScopeRuleEffect):
            raise CoreInputError("effect must be ScopeRuleEffect")
        if not isinstance(self.matched, bool):
            raise CoreInputError("matched must be bool")


@dataclass(frozen=True)
class ScopeEvaluationInput:
    matches: tuple[ScopeRuleMatch, ...]
    ambiguous: bool

    def __post_init__(self) -> None:
        if not isinstance(self.matches, tuple):
            raise CoreInputError("matches must be a tuple")
        if not isinstance(self.ambiguous, bool):
            raise CoreInputError("ambiguous must be bool")


@dataclass(frozen=True)
class ScopeCheck:
    decision: ScopeDecision
    reason_code: ReasonCode
    matched_rule_ids: tuple[str, ...]
    classification: ScopeClassification = ScopeClassification.OUT_OF_SCOPE


def check_scope(evaluation: ScopeEvaluationInput) -> ScopeCheck:
    if not isinstance(evaluation, ScopeEvaluationInput):
        raise CoreInputError("scope evaluation is required")
    matched = tuple(item for item in evaluation.matches if item.matched)
    matched_ids = tuple(item.rule_id for item in matched)

    if any(
        item.effect in (ScopeRuleEffect.DENY, ScopeRuleEffect.OUT_OF_SCOPE)
        for item in matched
    ):
        return ScopeCheck(
            ScopeDecision.DENY,
            ReasonCode.SCOPE_DENIED,
            matched_ids,
            ScopeClassification.OUT_OF_SCOPE,
        )

    if evaluation.ambiguous:
        return ScopeCheck(
            ScopeDecision.REQUIRE_HUMAN_REVIEW,
            ReasonCode.SCOPE_AMBIGUOUS,
            matched_ids,
            ScopeClassification.OUT_OF_SCOPE,
        )

    if any(item.effect is ScopeRuleEffect.ALLOW for item in matched):
        return ScopeCheck(
            ScopeDecision.ALLOW,
            ReasonCode.ALLOWED,
            matched_ids,
            ScopeClassification.IN_SCOPE,
        )

    if any(item.effect is ScopeRuleEffect.UNKNOWN for item in matched):
        return ScopeCheck(
            ScopeDecision.DENY,
            ReasonCode.SCOPE_UNKNOWN_CLASSIFICATION,
            matched_ids,
            ScopeClassification.UNKNOWN,
        )

    return ScopeCheck(
        ScopeDecision.DENY,
        ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED,
        matched_ids,
        ScopeClassification.OUT_OF_SCOPE,
    )
