"""Core-derived network bounds for HTTP/browser Workers. Not a second scope authority."""

from __future__ import annotations

from dataclasses import dataclass

from zest.core.enums import ScopeDecision, ScopeRuleEffect
from zest.core.scope import ScopeCheck
from zest.core.scope_compiler import CompiledScope, CompiledScopeRule, ScopeCandidate


@dataclass(frozen=True)
class AuthorizedNetworkEnvelope:
    """Immutable dispatch-time projection of a successful compiled-scope evaluation.

    Worker may only enforce this more strictly. Worker cannot expand it.
    This is not persisted as an independent grant.
    """

    normalized_scheme: str
    normalized_host: str
    normalized_port: int
    document_path: str
    origin_wide: bool
    allowed_path_prefixes: tuple[str, ...]
    denied_path_prefixes: tuple[str, ...]
    loopback_only: bool
    source_scope_rule_ids: tuple[str, ...]
    authorization_decision_reference: str | None = None

    def __post_init__(self) -> None:
        if "*" in self.normalized_host or "*" in self.normalized_scheme:
            raise ValueError("wildcard envelope is not allowed")
        if self.normalized_port < 1:
            raise ValueError("normalized_port must be a positive integer")
        if not self.document_path.startswith("/"):
            raise ValueError("document_path must be an absolute path")

    def to_mapping(self) -> dict[str, object]:
        """Serialize dispatch-time bounds. Not a grant and not Worker scope policy."""

        payload: dict[str, object] = {
            "normalized_scheme": self.normalized_scheme,
            "normalized_host": self.normalized_host,
            "normalized_port": self.normalized_port,
            "document_path": self.document_path,
            "origin_wide": self.origin_wide,
            "allowed_path_prefixes": list(self.allowed_path_prefixes),
            "denied_path_prefixes": list(self.denied_path_prefixes),
            "loopback_only": self.loopback_only,
            "source_scope_rule_ids": list(self.source_scope_rule_ids),
        }
        if self.authorization_decision_reference is not None:
            payload["authorization_decision_reference"] = self.authorization_decision_reference
        return payload


def envelope_from_mapping(payload: object) -> AuthorizedNetworkEnvelope | None:
    if not isinstance(payload, dict):
        return None
    try:
        allowed = payload.get("allowed_path_prefixes") or ()
        denied = payload.get("denied_path_prefixes") or ()
        rule_ids = payload.get("source_scope_rule_ids") or ()
        return AuthorizedNetworkEnvelope(
            normalized_scheme=str(payload["normalized_scheme"]),
            normalized_host=str(payload["normalized_host"]),
            normalized_port=int(payload["normalized_port"]),
            document_path=str(payload["document_path"]),
            origin_wide=bool(payload.get("origin_wide")),
            allowed_path_prefixes=tuple(str(item) for item in allowed),
            denied_path_prefixes=tuple(str(item) for item in denied),
            loopback_only=bool(payload.get("loopback_only")),
            source_scope_rule_ids=tuple(str(item) for item in rule_ids),
            authorization_decision_reference=(
                str(payload["authorization_decision_reference"])
                if payload.get("authorization_decision_reference") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def derive_authorized_network_envelope(
    candidate: ScopeCandidate,
    compiled: CompiledScope,
    check: ScopeCheck,
    *,
    loopback_only: bool,
    authorization_decision_reference: str | None = None,
) -> AuthorizedNetworkEnvelope | None:
    """Build Worker bounds from the same evaluation Core used. Not a grant."""

    if check.decision is not ScopeDecision.ALLOW:
        return None
    if (
        candidate.normalized_scheme is None
        or candidate.normalized_host is None
        or candidate.normalized_port is None
        or candidate.scope_match_path is None
    ):
        return None
    matched = set(check.matched_rule_ids)
    origin_allows = []
    origin_denies = []
    for rule in compiled.rules:
        if not _origin_matches(candidate, rule):
            continue
        if rule.effect is ScopeRuleEffect.ALLOW:
            origin_allows.append(rule)
        elif rule.effect in (ScopeRuleEffect.DENY, ScopeRuleEffect.OUT_OF_SCOPE):
            origin_denies.append(rule)
    matched_allows = [rule for rule in origin_allows if rule.rule_id in matched]
    origin_wide = any(rule.path_prefix is None for rule in matched_allows)
    allowed_prefixes = tuple(
        rule.path_prefix for rule in matched_allows if rule.path_prefix is not None
    )
    denied_prefixes = tuple(rule.path_prefix or "/" for rule in origin_denies)
    return AuthorizedNetworkEnvelope(
        normalized_scheme=candidate.normalized_scheme,
        normalized_host=candidate.normalized_host,
        normalized_port=candidate.normalized_port,
        document_path=candidate.scope_match_path,
        origin_wide=origin_wide,
        allowed_path_prefixes=allowed_prefixes,
        denied_path_prefixes=denied_prefixes,
        loopback_only=loopback_only,
        source_scope_rule_ids=check.matched_rule_ids,
        authorization_decision_reference=authorization_decision_reference,
    )


def _origin_matches(candidate: ScopeCandidate, rule: CompiledScopeRule) -> bool:
    return (
        candidate.normalized_scheme == rule.scheme
        and candidate.normalized_host == rule.host
        and candidate.normalized_port == rule.port
    )
