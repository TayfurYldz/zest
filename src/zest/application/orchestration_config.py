"""Reconstruct authoritative orchestration configuration from durable state.

Persisted ResearchOrchestrationRecord is the control-plane source of truth.
Command bounds cannot silently widen a run. Fingerprint is integrity, not authorization.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from zest.application.errors import OrchestrationIntegrityError
from zest.application.program_research_context import (
    ProgramPolicyView,
)
from zest.core.scope import ScopeEvaluationInput
from zest.core.scope_compiler import CompiledScope
from zest.data.records import ResearchOrchestrationRecord
from zest.research.orchestration import (
    OrchestrationBounds,
    bounds_from_config,
    orchestration_config_fingerprint,
)


def scope_fingerprint(scope: ScopeEvaluationInput) -> str:
    payload = {
        "ambiguous": scope.ambiguous,
        "matches": [
            {
                "rule_id": item.rule_id,
                "effect": item.effect.value,
                "matched": item.matched,
                "source_reference": item.source_reference,
            }
            for item in scope.matches
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sha256_payload(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(
        encoded.encode("utf-8")
    ).hexdigest()


def _canonical_policy_value(value):
    if value is None or isinstance(
        value,
        (str, int, float, bool),
    ):
        return value

    if isinstance(value, Mapping):
        return {
            str(key): _canonical_policy_value(item)
            for key, item in sorted(
                value.items(),
                key=lambda pair: str(pair[0]),
            )
        }

    if isinstance(value, (list, tuple)):
        return [
            _canonical_policy_value(item)
            for item in value
        ]

    if isinstance(value, (set, frozenset)):
        normalized = [
            _canonical_policy_value(item)
            for item in value
        ]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ),
        )

    raise OrchestrationIntegrityError(
        "program policy contains a "
        "non-canonical value"
    )


def compiled_scope_fingerprint(
    compiled_scope: CompiledScope | None,
) -> str | None:
    """Fingerprint the complete compiled scope, not one target evaluation."""

    if compiled_scope is None:
        return None

    rules = sorted(
        compiled_scope.rules,
        key=lambda item: item.rule_id,
    )

    payload = {
        "rules": [
            {
                "rule_id": rule.rule_id,
                "effect": rule.effect.value,
                "scheme": rule.scheme,
                "host": rule.host,
                "host_pattern": rule.host_pattern,
                "port": rule.port,
                "path_prefix": rule.path_prefix,
                "source_reference": (
                    rule.source_reference
                ),
                "expires_at": (
                    rule.expires_at.isoformat()
                    if rule.expires_at is not None
                    else None
                ),
            }
            for rule in rules
        ],
    }

    return _sha256_payload(payload)


def program_policy_fingerprint(
    policy: ProgramPolicyView | None,
) -> str | None:
    """Fingerprint all program policy semantics relevant to a run."""

    if policy is None:
        return None

    rate_limit = policy.rate_limit_profile

    payload = {
        "loopback_fixture": (
            policy.loopback_fixture
        ),
        "max_response_bytes": (
            policy.max_response_bytes
        ),
        "timeout_ms": policy.timeout_ms,
        "action_policy": (
            _canonical_policy_value(
                policy.action_policy
            )
        ),
        "daily_llm_budget_microdollars": (
            policy.daily_llm_budget_microdollars
        ),
        "rate_limit_profile": (
            None
            if rate_limit is None
            else {
                "profile_id": (
                    rate_limit.profile_id
                ),
                "program_id": (
                    rate_limit.program_id
                ),
                "max_requests_per_window": (
                    rate_limit.max_requests_per_window
                ),
                "window_seconds": (
                    rate_limit.window_seconds
                ),
                "created_at": (
                    rate_limit.created_at.isoformat()
                ),
            }
        ),
    }

    return _sha256_payload(payload)


@dataclass(frozen=True)
class EffectiveOrchestrationConfiguration:
    research_run_id: str
    budget_id: str
    target_reference: str
    research_question: str
    policy_version: str
    routing_policy_version: str | None
    scope_fingerprint: str | None
    bounds: OrchestrationBounds
    fingerprint: str
    compiled_scope_fingerprint: str | None = None
    program_policy_fingerprint: str | None = None

    def config_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "research_run_id": self.research_run_id,
            "budget_id": self.budget_id,
            "target_reference": self.target_reference,
            "research_question": self.research_question,
            "policy_version": self.policy_version,
            "routing_policy_version": (
                self.routing_policy_version
            ),
            "scope_fingerprint": (
                self.scope_fingerprint
            ),
            "max_cycles": self.bounds.max_cycles,
            "max_experiments": (
                self.bounds.max_experiments
            ),
            "max_model_calls": (
                self.bounds.max_model_calls
            ),
            "max_worker_invocations": (
                self.bounds.max_worker_invocations
            ),
            "max_elapsed_ms": (
                self.bounds.max_elapsed_ms
            ),
            "max_selected_opportunities": (
                self.bounds.max_selected_opportunities
            ),
            "max_runtime_fallback": (
                self.bounds.max_runtime_fallback
            ),
            "side_effect_ceiling": (
                self.bounds.side_effect_ceiling
            ),
            "allow_repeated_control_experiments": (
                self.bounds
                .allow_repeated_control_experiments
            ),
        }

        if (
            self.compiled_scope_fingerprint
            is not None
        ):
            payload[
                "compiled_scope_fingerprint"
            ] = self.compiled_scope_fingerprint

        if (
            self.program_policy_fingerprint
            is not None
        ):
            payload[
                "program_policy_fingerprint"
            ] = self.program_policy_fingerprint

        return payload


def fingerprint_for_start(
    *,
    research_run_id: str,
    budget_id: str,
    target_reference: str,
    research_question: str,
    policy_version: str,
    bounds: OrchestrationBounds,
    routing_policy_version: str | None,
    scope_fp: str | None,
    compiled_scope_fp: str | None = None,
    program_policy_fp: str | None = None,
) -> str:
    payload: dict[str, object] = {
        "research_run_id": research_run_id,
        "budget_id": budget_id,
        "target_reference": target_reference,
        "research_question": research_question,
        "policy_version": policy_version,
        "routing_policy_version": (
            routing_policy_version
        ),
        "scope_fingerprint": scope_fp,
        "max_cycles": bounds.max_cycles,
        "max_experiments": bounds.max_experiments,
        "max_model_calls": bounds.max_model_calls,
        "max_worker_invocations": (
            bounds.max_worker_invocations
        ),
        "max_elapsed_ms": bounds.max_elapsed_ms,
        "max_selected_opportunities": (
            bounds.max_selected_opportunities
        ),
        "max_runtime_fallback": (
            bounds.max_runtime_fallback
        ),
        "side_effect_ceiling": (
            bounds.side_effect_ceiling
        ),
        "allow_repeated_control_experiments": (
            bounds.allow_repeated_control_experiments
        ),
    }

    if compiled_scope_fp is not None:
        payload[
            "compiled_scope_fingerprint"
        ] = compiled_scope_fp

    if program_policy_fp is not None:
        payload[
            "program_policy_fingerprint"
        ] = program_policy_fp

    return orchestration_config_fingerprint(
        payload
    )


def configuration_from_record(
    record: ResearchOrchestrationRecord,
) -> EffectiveOrchestrationConfiguration:
    payload: dict[str, object] = {
        "research_run_id": record.research_run_id,
        "budget_id": record.budget_id,
        "target_reference": record.target_reference,
        "research_question": (
            record.research_question
        ),
        "policy_version": record.policy_version,
        "routing_policy_version": (
            record.routing_policy_version
        ),
        "scope_fingerprint": (
            record.scope_fingerprint
        ),
        "max_cycles": record.max_cycles,
        "max_experiments": (
            record.max_experiments
        ),
        "max_model_calls": (
            record.max_model_calls
        ),
        "max_worker_invocations": (
            record.max_worker_invocations
        ),
        "max_elapsed_ms": (
            record.max_elapsed_ms
        ),
        "max_selected_opportunities": (
            record.max_selected_opportunities
        ),
        "max_runtime_fallback": (
            record.max_runtime_fallback
        ),
        "side_effect_ceiling": (
            record.side_effect_ceiling
        ),
        "allow_repeated_control_experiments": (
            record.allow_repeated_control_experiments
        ),
    }

    if (
        record.compiled_scope_fingerprint
        is not None
    ):
        payload[
            "compiled_scope_fingerprint"
        ] = record.compiled_scope_fingerprint

    if (
        record.program_policy_fingerprint
        is not None
    ):
        payload[
            "program_policy_fingerprint"
        ] = record.program_policy_fingerprint

    expected = orchestration_config_fingerprint(
        payload
    )

    if expected != record.configuration_fingerprint:
        raise OrchestrationIntegrityError(
            "orchestration configuration "
            "fingerprint mismatch"
        )

    return EffectiveOrchestrationConfiguration(
        research_run_id=record.research_run_id,
        budget_id=record.budget_id,
        target_reference=record.target_reference,
        research_question=record.research_question,
        policy_version=record.policy_version,
        routing_policy_version=(
            record.routing_policy_version
        ),
        scope_fingerprint=(
            record.scope_fingerprint
        ),
        bounds=bounds_from_config(payload),
        fingerprint=expected,
        compiled_scope_fingerprint=(
            record.compiled_scope_fingerprint
        ),
        program_policy_fingerprint=(
            record.program_policy_fingerprint
        ),
    )


def assert_command_matches_configuration(
    *,
    config: EffectiveOrchestrationConfiguration,
    bounds: OrchestrationBounds | None,
    budget_id: str | None,
    target_reference: str | None,
    research_question: str | None,
    scope: ScopeEvaluationInput | None,
    compiled_scope: CompiledScope | None = None,
    program_policy: ProgramPolicyView | None = None,
) -> None:
    if (
        bounds is not None
        and bounds != config.bounds
    ):
        raise OrchestrationIntegrityError(
            "command bounds do not match "
            "persisted orchestration"
        )

    if (
        budget_id is not None
        and budget_id != config.budget_id
    ):
        raise OrchestrationIntegrityError(
            "command budget_id does not match "
            "persisted orchestration"
        )

    if (
        target_reference is not None
        and target_reference
        != config.target_reference
    ):
        raise OrchestrationIntegrityError(
            "command target_reference does not "
            "match persisted orchestration"
        )

    if (
        research_question is not None
        and research_question
        != config.research_question
    ):
        raise OrchestrationIntegrityError(
            "command research_question does not "
            "match persisted orchestration"
        )

    if scope is not None:
        incoming = scope_fingerprint(scope)

        if (
            config.scope_fingerprint is not None
            and incoming
            != config.scope_fingerprint
        ):
            raise OrchestrationIntegrityError(
                "command scope does not match "
                "persisted orchestration"
            )

    incoming_compiled = (
        compiled_scope_fingerprint(
            compiled_scope
        )
    )

    if (
        config.compiled_scope_fingerprint
        is not None
    ):
        if incoming_compiled is None:
            raise OrchestrationIntegrityError(
                "compiled scope is missing from "
                "a pinned orchestration"
            )

        if (
            incoming_compiled
            != config.compiled_scope_fingerprint
        ):
            raise OrchestrationIntegrityError(
                "compiled scope changed after "
                "orchestration start"
            )

    elif compiled_scope is not None:
        raise OrchestrationIntegrityError(
            "orchestration authority context "
            "does not contain a compiled-scope pin"
        )

    incoming_policy = (
        program_policy_fingerprint(
            program_policy
        )
    )

    if (
        config.program_policy_fingerprint
        is not None
    ):
        if incoming_policy is None:
            raise OrchestrationIntegrityError(
                "program policy is missing from "
                "a pinned orchestration"
            )

        if (
            incoming_policy
            != config.program_policy_fingerprint
        ):
            raise OrchestrationIntegrityError(
                "program policy changed after "
                "orchestration start"
            )

    elif program_policy is not None:
        raise OrchestrationIntegrityError(
            "orchestration authority context "
            "does not contain a program-policy pin"
        )
