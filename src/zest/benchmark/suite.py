"""Suite fingerprint/manifest. Hidden evaluator contents are not report payload."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from zest.benchmark.errors import BenchmarkError
from zest.benchmark.scenarios import BenchmarkScenario, HiddenEvaluation, VisibleInput


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=True, separators=(",", ":"))


def _visible_mapping(visible: VisibleInput) -> dict[str, Any]:
    return {
        "research_run_id": visible.research_run_id,
        "research_question": visible.research_question,
        "observations": [
            {
                "observation_id": item.observation_id,
                "observation_kind": item.observation_kind,
                "payload": dict(item.payload),
                "experiment_id": item.experiment_id,
            }
            for item in visible.observations
        ],
        "prior_hypotheses": [
            {"hypothesis_id": item.hypothesis_id, "claim": item.claim}
            for item in visible.prior_hypotheses
        ],
        "experiments": [
            {
                "experiment_id": item.experiment_id,
                "hypothesis_id": item.hypothesis_id,
                "execution_state": item.execution_state,
            }
            for item in visible.experiments
        ],
        "untrusted_external": [
            {
                "external_id": item.external_id,
                "source_reference": item.source_reference,
                "content": item.content,
            }
            for item in visible.untrusted_external
        ],
        "unresolved_questions": list(visible.unresolved_questions),
    }


def _hidden_mapping(hidden: HiddenEvaluation) -> dict[str, Any]:
    return {
        "leakage_canary": hidden.leakage_canary,
        "known_source_ids": list(hidden.known_source_ids),
        "forbidden_fabricated_source_ids": list(hidden.forbidden_fabricated_source_ids),
        "expected_admission_outcomes": list(hidden.expected_admission_outcomes),
        "expected_epistemic_distinctions": list(hidden.expected_epistemic_distinctions),
        "known_benign_explanations": list(hidden.known_benign_explanations),
        "required_negative_control_concepts": list(hidden.required_negative_control_concepts),
        "policy_traps": list(hidden.policy_traps),
        "injection_needles": list(hidden.injection_needles),
        "scenario_invariants": list(hidden.scenario_invariants),
        "evaluation_tags": list(hidden.evaluation_tags),
        "unexpected_admit_is_hard_fail": hidden.unexpected_admit_is_hard_fail,
        "relevant_source_ids": list(hidden.relevant_source_ids),
        "required_source_groups": [list(group) for group in hidden.required_source_groups],
        "irrelevant_source_ids": list(hidden.irrelevant_source_ids),
        "scenario_specific_tokens": list(hidden.scenario_specific_tokens),
    }


def scenario_integrity_hash(scenario: BenchmarkScenario) -> str:
    payload = {
        "scenario_id": scenario.scenario_id,
        "version": scenario.version,
        "category": scenario.category.value,
        "split": scenario.split.value,
        "variant_of": scenario.variant_of,
        "variant_kind": scenario.variant_kind,
        "visible_input": _visible_mapping(scenario.visible_input),
        "hidden_evaluation": _hidden_mapping(scenario.hidden_evaluation),
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SuiteManifest:
    suite_id: str
    suite_version: str
    scenario_count: int
    suite_fingerprint: str
    scenario_identities: tuple[str, ...]
    sealed: bool = False

    def to_mapping(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
            "scenario_count": self.scenario_count,
            "suite_fingerprint": self.suite_fingerprint,
            "scenario_identities": list(self.scenario_identities),
            "sealed": self.sealed,
            "hidden_evaluation_omitted": True,
        }


def build_suite_manifest(
    scenarios: tuple[BenchmarkScenario, ...],
    *,
    suite_id: str,
    suite_version: str = "1",
    sealed: bool = False,
) -> SuiteManifest:
    if not scenarios:
        raise BenchmarkError("cannot fingerprint an empty suite")
    identities = tuple(item.identity for item in scenarios)
    material = [
        {"identity": item.identity, "integrity": scenario_integrity_hash(item)}
        for item in scenarios
    ]
    digest = hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()
    return SuiteManifest(
        suite_id=suite_id,
        suite_version=suite_version,
        scenario_count=len(scenarios),
        suite_fingerprint=digest,
        scenario_identities=identities,
        sealed=sealed,
    )
