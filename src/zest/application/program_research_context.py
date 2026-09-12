"""Program-scoped research context: compiled scope + policy. Core data, not prompt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from zest.core.enums import ReasonCode, ScopeRuleEffect
from zest.core.scope_compiler import (
    CompiledScope,
    ScopeRuleDefinition,
    compile_scope_rules,
)
from zest.data.records import (
    ProgramPolicyRecord,
    ProgramRecord,
    RateLimitProfileRecord,
    ScopeRuleV2Record,
)
from zest.data.unit_of_work import UnitOfWork


@dataclass(frozen=True)
class ProgramPolicyView:
    """Dispatch-relevant policy projection. Not a grant and not scope authority."""

    loopback_fixture: bool
    max_response_bytes: int
    timeout_ms: int
    action_policy: Mapping[str, Any]
    rate_limit_profile: RateLimitProfileRecord | None = None

    def allows_action(self, action_name: str) -> bool:
        """Default-allow unless the canonical action is explicitly denied.

        Dashboard ``forbidden_actions`` entries are exact action identifiers,
        not natural-language policy interpretation.
        """

        if not isinstance(action_name, str) or not action_name.strip():
            return False

        normalized_action = action_name.strip().casefold()

        if "forbidden_actions" in self.action_policy:
            forbidden = self.action_policy.get("forbidden_actions")

            if isinstance(forbidden, str):
                forbidden_items = forbidden.splitlines()
            elif isinstance(forbidden, (list, tuple, set, frozenset)):
                forbidden_items = forbidden
            elif forbidden is None:
                forbidden_items = ()
            else:
                # Malformed explicit safety policy fails closed.
                return False

            for item in forbidden_items:
                if not isinstance(item, str):
                    return False
                candidate = item.strip()
                if candidate and candidate.casefold() == normalized_action:
                    return False

        decision = None
        for key, value in self.action_policy.items():
            if (
                isinstance(key, str)
                and key.strip().casefold() == normalized_action
            ):
                decision = value
                break

        if decision is None:
            return True

        if isinstance(decision, dict):
            decision = decision.get("decision")

        if isinstance(decision, str):
            return decision.strip().upper() != "DENY"

        return bool(decision)


@dataclass(frozen=True)
class ProgramResearchContext:
    """Bound program context loaded from SoR. Not an authorization source."""

    program: ProgramRecord
    compiled_scope: CompiledScope
    policy: ProgramPolicyView


def load_program_research_context(
    uow: UnitOfWork,
    program_id: str,
    *,
    now: datetime | None = None,
) -> ProgramResearchContext | None:
    """Load program, scope rules v2, and policy from the authoritative spine."""

    program = uow.programs.get(program_id)
    if program is None:
        return None
    rules = uow.scope_rules_v2.list_for_program(program_id)
    policy_record = uow.program_policies.get(program_id)
    if policy_record is None:
        policy_record = _default_policy(program_id, now=now)
    rate_limit_profiles = uow.rate_limit_profiles.list_for_program(program_id)
    rate_limit_profile = rate_limit_profiles[0] if rate_limit_profiles else None
    return ProgramResearchContext(
        program=program,
        compiled_scope=_compile_rules(rules),
        policy=_policy_view(policy_record, rate_limit_profile=rate_limit_profile),
    )


def _compile_rules(records: list[ScopeRuleV2Record]) -> CompiledScope:
    definitions: list[ScopeRuleDefinition] = []
    for record in records:
        effect = record.effect
        if isinstance(effect, str):
            effect = ScopeRuleEffect(effect)
        definitions.append(
            ScopeRuleDefinition(
                rule_id=record.rule_id,
                effect=effect,
                scheme=record.scheme,
                host=record.host,
                host_pattern=record.host_pattern,
                port=record.port,
                path_prefix=record.path_prefix,
                source_reference=record.source_reference,
                expires_at=record.expires_at,
            )
        )
    return compile_scope_rules(tuple(definitions))


def _policy_view(
    record: ProgramPolicyRecord,
    *,
    rate_limit_profile: RateLimitProfileRecord | None = None,
) -> ProgramPolicyView:
    return ProgramPolicyView(
        loopback_fixture=record.loopback_fixture,
        max_response_bytes=record.max_response_bytes,
        timeout_ms=record.timeout_ms,
        action_policy=record.action_policy or {},
        rate_limit_profile=rate_limit_profile,
    )


def _default_policy(program_id: str, now: datetime | None = None) -> ProgramPolicyRecord:
    evaluated_at = now if now is not None else datetime.now(timezone.utc)
    return ProgramPolicyRecord(
        program_id=program_id,
        loopback_fixture=False,
        max_response_bytes=4096,
        timeout_ms=2000,
        created_at=evaluated_at,
        updated_at=evaluated_at,
        action_policy={},
    )


def derive_loopback_only(
    *,
    program_policy: ProgramPolicyView | None,
    compiled_scope: CompiledScope | None,
) -> bool:
    """True only for loopback fixtures or when no real program scope is present."""

    if program_policy is None:
        return True
    if program_policy.loopback_fixture:
        return True
    if compiled_scope is None or not compiled_scope.rules:
        return True
    return False


def action_policy_reason_code(action_name: str) -> ReasonCode:
    return ReasonCode.PROGRAM_POLICY_DENIED
