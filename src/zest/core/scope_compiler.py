"""Scope compilation and evaluation over Platform-normalized candidates.

Core does not parse URLs. Wildcard patterns are supported in rules but never in
Worker envelopes. Exclusions win. Expired rules fall back to human review.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from zest.core.enums import (
    ReasonCode,
    ScopeClassification,
    ScopeDecision,
    ScopeRuleEffect,
)
from zest.core.errors import CoreInputError
from zest.core.identity import require_opaque_id
from zest.core.scope import ScopeCheck

DEFAULT_SCHEME_PORTS = {
    "http": 80,
    "https": 443,
}
NORMALIZATION_USERINFO = "USERINFO"
NORMALIZATION_WILDCARD = "WILDCARD"
NORMALIZATION_PATH_AMBIGUOUS = "PATH_AMBIGUOUS"


@dataclass(frozen=True)
class ScopeCandidate:
    raw_target: str
    normalized_scheme: str | None
    normalized_host: str | None
    normalized_port: int | None
    raw_path: str
    scope_match_path: str | None
    normalization_error: str | None


@dataclass(frozen=True)
class ScopeRuleDefinition:
    rule_id: str
    effect: ScopeRuleEffect
    scheme: str
    host: str | None = None
    port: int | None = None
    path_prefix: str | None = None
    source_reference: str = ""
    host_pattern: str | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.rule_id, "rule_id")
        require_opaque_id(self.source_reference, "source_reference")
        if not isinstance(self.effect, ScopeRuleEffect):
            raise CoreInputError("effect must be ScopeRuleEffect")
        if not isinstance(self.scheme, str) or not self.scheme.strip():
            raise CoreInputError("scheme must be a non-empty string")
        if self.host is not None and self.host_pattern is not None:
            raise CoreInputError("host and host_pattern are mutually exclusive")
        if self.host is None and self.host_pattern is None:
            raise CoreInputError("host or host_pattern is required")
        if self.host is not None:
            if not isinstance(self.host, str) or not self.host.strip():
                raise CoreInputError("host must be a non-empty string")
            if "*" in self.host:
                raise CoreInputError("wildcard is not allowed in exact host")
        if self.host_pattern is not None:
            if not isinstance(self.host_pattern, str) or not self.host_pattern.startswith("*."):
                raise CoreInputError("host_pattern must start with '*.'")
            apex = self.host_pattern[2:].strip().lower()
            if not apex or "*" in apex:
                raise CoreInputError("host_pattern apex must be a non-empty literal host")
        if "*" in self.scheme:
            raise CoreInputError("wildcard scheme is not allowed")
        if self.port is not None and (not isinstance(self.port, int) or self.port < 1):
            raise CoreInputError("port must be a positive integer or None")
        if self.path_prefix is not None and (
            not isinstance(self.path_prefix, str) or not self.path_prefix.startswith("/")
        ):
            raise CoreInputError("path_prefix must be an absolute path or None")
        if self.expires_at is not None:
            if not isinstance(self.expires_at, datetime):
                raise CoreInputError("expires_at must be a datetime")
            if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
                raise CoreInputError("expires_at must be timezone-aware")


@dataclass(frozen=True)
class CompiledScopeRule:
    rule_id: str
    effect: ScopeRuleEffect
    scheme: str
    host: str | None
    host_pattern: str | None
    port: int
    path_prefix: str | None
    source_reference: str
    expires_at: datetime | None


@dataclass(frozen=True)
class CompiledScope:
    rules: tuple[CompiledScopeRule, ...]


def compile_scope_rules(rules: tuple[ScopeRuleDefinition, ...]) -> CompiledScope:
    compiled: list[CompiledScopeRule] = []
    for rule in rules:
        scheme = rule.scheme.lower().strip()
        if scheme not in DEFAULT_SCHEME_PORTS:
            raise CoreInputError("scheme must be http or https")
        if rule.host is not None:
            host = rule.host.strip("[]").lower()
            host_pattern = None
        else:
            host = None
            host_pattern = rule.host_pattern.strip().lower() if rule.host_pattern else None
        port = rule.port if rule.port is not None else DEFAULT_SCHEME_PORTS[scheme]
        compiled.append(
            CompiledScopeRule(
                rule_id=rule.rule_id,
                effect=rule.effect,
                scheme=scheme,
                host=host,
                host_pattern=host_pattern,
                port=port,
                path_prefix=rule.path_prefix,
                source_reference=rule.source_reference,
                expires_at=rule.expires_at,
            )
        )
    return CompiledScope(rules=tuple(compiled))


def evaluate_scope_candidate(
    candidate: ScopeCandidate,
    compiled: CompiledScope,
    *,
    now: datetime | None = None,
) -> ScopeCheck:
    if not isinstance(candidate, ScopeCandidate):
        raise CoreInputError("scope candidate is required")
    if not isinstance(compiled, CompiledScope):
        raise CoreInputError("compiled scope is required")
    if candidate.normalization_error == NORMALIZATION_USERINFO:
        return _deny(ReasonCode.SCOPE_USERINFO_DENIED)
    if candidate.normalization_error == NORMALIZATION_WILDCARD:
        return _deny(ReasonCode.SCOPE_WILDCARD_REJECTED)
    if candidate.normalization_error == NORMALIZATION_PATH_AMBIGUOUS:
        return _deny(ReasonCode.SCOPE_PATH_AMBIGUOUS)
    if candidate.normalization_error is not None:
        return _deny(ReasonCode.SCOPE_NORMALIZATION_INVALID)
    if candidate.scope_match_path is None:
        return _deny(ReasonCode.SCOPE_PATH_AMBIGUOUS)

    evaluated_at = now if now is not None else datetime.now(timezone.utc)

    matched_exclusions: list[CompiledScopeRule] = []
    matched_allows: list[CompiledScopeRule] = []
    matched_unknown: list[CompiledScopeRule] = []
    for rule in compiled.rules:
        if not _origin_matches(candidate, rule):
            continue
        if rule.path_prefix is not None and not _path_prefix_matches(
            candidate.scope_match_path, rule.path_prefix
        ):
            continue
        if rule.expires_at is not None and rule.expires_at <= evaluated_at:
            return ScopeCheck(
                ScopeDecision.REQUIRE_HUMAN_REVIEW,
                ReasonCode.SCOPE_EXPIRED,
                (rule.rule_id,),
                ScopeClassification.OUT_OF_SCOPE,
            )
        if rule.effect in (ScopeRuleEffect.DENY, ScopeRuleEffect.OUT_OF_SCOPE):
            matched_exclusions.append(rule)
        elif rule.effect is ScopeRuleEffect.ALLOW:
            matched_allows.append(rule)
        elif rule.effect is ScopeRuleEffect.UNKNOWN:
            matched_unknown.append(rule)

    if matched_exclusions:
        return ScopeCheck(
            ScopeDecision.DENY,
            ReasonCode.SCOPE_DENIED,
            tuple(item.rule_id for item in matched_exclusions),
            ScopeClassification.OUT_OF_SCOPE,
        )
    if matched_allows:
        return ScopeCheck(
            ScopeDecision.ALLOW,
            ReasonCode.ALLOWED,
            tuple(item.rule_id for item in matched_allows),
            ScopeClassification.IN_SCOPE,
        )
    if matched_unknown:
        return ScopeCheck(
            ScopeDecision.DENY,
            ReasonCode.SCOPE_UNKNOWN_CLASSIFICATION,
            tuple(item.rule_id for item in matched_unknown),
            ScopeClassification.UNKNOWN,
        )
    # No rule matched and the candidate normalized cleanly. It is not explicitly
    # allowed, denied, or marked unknown by any rule, so it is classified UNKNOWN:
    # passive census may observe it, but active probing is denied.
    return ScopeCheck(
        ScopeDecision.DENY,
        ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED,
        (),
        ScopeClassification.UNKNOWN,
    )


def _deny(reason: ReasonCode) -> ScopeCheck:
    return ScopeCheck(
        ScopeDecision.DENY,
        reason,
        (),
        ScopeClassification.OUT_OF_SCOPE,
    )


def _origin_matches(candidate: ScopeCandidate, rule: CompiledScopeRule) -> bool:
    if candidate.normalized_scheme != rule.scheme:
        return False
    if not _host_matches(candidate.normalized_host, rule):
        return False
    return candidate.normalized_port == rule.port


def _host_matches(host: str | None, rule: CompiledScopeRule) -> bool:
    if host is None:
        return False
    if rule.host is not None:
        return host == rule.host
    if rule.host_pattern is not None:
        apex = rule.host_pattern[2:]
        return host != apex and host.endswith("." + apex)
    return False


def _path_prefix_matches(path: str, prefix: str) -> bool:
    if path == prefix:
        return True
    if not prefix.endswith("/"):
        prefix = prefix + "/"
    return path.startswith(prefix)
