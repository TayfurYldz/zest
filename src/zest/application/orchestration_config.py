"""Reconstruct authoritative orchestration configuration from durable state.

Persisted ResearchOrchestrationRecord is the control-plane source of truth.
Command bounds cannot silently widen a run. Fingerprint is integrity, not authorization.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from zest.application.errors import OrchestrationIntegrityError
from zest.core.scope import ScopeEvaluationInput
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

    def config_payload(self) -> dict[str, object]:
        return {
            "research_run_id": self.research_run_id,
            "budget_id": self.budget_id,
            "target_reference": self.target_reference,
            "research_question": self.research_question,
            "policy_version": self.policy_version,
            "routing_policy_version": self.routing_policy_version,
            "scope_fingerprint": self.scope_fingerprint,
            "max_cycles": self.bounds.max_cycles,
            "max_experiments": self.bounds.max_experiments,
            "max_model_calls": self.bounds.max_model_calls,
            "max_worker_invocations": self.bounds.max_worker_invocations,
            "max_elapsed_ms": self.bounds.max_elapsed_ms,
            "max_selected_opportunities": self.bounds.max_selected_opportunities,
            "max_runtime_fallback": self.bounds.max_runtime_fallback,
            "side_effect_ceiling": self.bounds.side_effect_ceiling,
            "allow_repeated_control_experiments": (
                self.bounds.allow_repeated_control_experiments
            ),
        }


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
) -> str:
    return orchestration_config_fingerprint(
        {
            "research_run_id": research_run_id,
            "budget_id": budget_id,
            "target_reference": target_reference,
            "research_question": research_question,
            "policy_version": policy_version,
            "routing_policy_version": routing_policy_version,
            "scope_fingerprint": scope_fp,
            "max_cycles": bounds.max_cycles,
            "max_experiments": bounds.max_experiments,
            "max_model_calls": bounds.max_model_calls,
            "max_worker_invocations": bounds.max_worker_invocations,
            "max_elapsed_ms": bounds.max_elapsed_ms,
            "max_selected_opportunities": bounds.max_selected_opportunities,
            "max_runtime_fallback": bounds.max_runtime_fallback,
            "side_effect_ceiling": bounds.side_effect_ceiling,
            "allow_repeated_control_experiments": bounds.allow_repeated_control_experiments,
        }
    )


def configuration_from_record(
    record: ResearchOrchestrationRecord,
) -> EffectiveOrchestrationConfiguration:
    payload = {
        "research_run_id": record.research_run_id,
        "budget_id": record.budget_id,
        "target_reference": record.target_reference,
        "research_question": record.research_question,
        "policy_version": record.policy_version,
        "routing_policy_version": record.routing_policy_version,
        "scope_fingerprint": record.scope_fingerprint,
        "max_cycles": record.max_cycles,
        "max_experiments": record.max_experiments,
        "max_model_calls": record.max_model_calls,
        "max_worker_invocations": record.max_worker_invocations,
        "max_elapsed_ms": record.max_elapsed_ms,
        "max_selected_opportunities": record.max_selected_opportunities,
        "max_runtime_fallback": record.max_runtime_fallback,
        "side_effect_ceiling": record.side_effect_ceiling,
        "allow_repeated_control_experiments": record.allow_repeated_control_experiments,
    }
    expected = orchestration_config_fingerprint(payload)
    if expected != record.configuration_fingerprint:
        raise OrchestrationIntegrityError(
            "orchestration configuration fingerprint mismatch"
        )
    return EffectiveOrchestrationConfiguration(
        research_run_id=record.research_run_id,
        budget_id=record.budget_id,
        target_reference=record.target_reference,
        research_question=record.research_question,
        policy_version=record.policy_version,
        routing_policy_version=record.routing_policy_version,
        scope_fingerprint=record.scope_fingerprint,
        bounds=bounds_from_config(payload),
        fingerprint=expected,
    )


def assert_command_matches_configuration(
    *,
    config: EffectiveOrchestrationConfiguration,
    bounds: OrchestrationBounds | None,
    budget_id: str | None,
    target_reference: str | None,
    research_question: str | None,
    scope: ScopeEvaluationInput | None,
) -> None:
    if bounds is not None and bounds != config.bounds:
        raise OrchestrationIntegrityError("command bounds do not match persisted orchestration")
    if budget_id is not None and budget_id != config.budget_id:
        raise OrchestrationIntegrityError("command budget_id does not match persisted orchestration")
    if target_reference is not None and target_reference != config.target_reference:
        raise OrchestrationIntegrityError(
            "command target_reference does not match persisted orchestration"
        )
    if research_question is not None and research_question != config.research_question:
        raise OrchestrationIntegrityError(
            "command research_question does not match persisted orchestration"
        )
    if scope is not None:
        incoming = scope_fingerprint(scope)
        if config.scope_fingerprint is not None and incoming != config.scope_fingerprint:
            raise OrchestrationIntegrityError(
                "command scope does not match persisted orchestration"
            )
