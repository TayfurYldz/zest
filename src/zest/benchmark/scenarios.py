"""Versioned benchmark scenarios. Hidden evaluation never enters ResearchContext."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from zest.benchmark.errors import BenchmarkError
from zest.research.context import (
    ExperimentSource,
    ExternalContentSource,
    HypothesisSource,
    ObservationSource,
    ResearchContext,
    ResearchContextBuilder,
)

SCENARIO_KEYS = frozenset(
    {
        "scenario_id",
        "version",
        "category",
        "split",
        "visible_input",
        "hidden_evaluation",
        "variant_of",
        "variant_kind",
    }
)
VISIBLE_KEYS = frozenset(
    {
        "research_run_id",
        "research_question",
        "observations",
        "prior_hypotheses",
        "experiments",
        "untrusted_external",
        "unresolved_questions",
    }
)
HIDDEN_KEYS = frozenset(
    {
        "leakage_canary",
        "known_source_ids",
        "forbidden_fabricated_source_ids",
        "expected_admission_outcomes",
        "expected_epistemic_distinctions",
        "known_benign_explanations",
        "required_negative_control_concepts",
        "policy_traps",
        "injection_needles",
        "scenario_invariants",
        "evaluation_tags",
        "unexpected_admit_is_hard_fail",
        "relevant_source_ids",
        "required_source_groups",
        "irrelevant_source_ids",
        "scenario_specific_tokens",
    }
)
HIDDEN_KEY_SENTINELS = frozenset(
    {
        "hidden_evaluation",
        "leakage_canary",
        "known_source_ids",
        "forbidden_fabricated_source_ids",
        "expected_admission_outcomes",
        "benchmark_only",
        "ground_truth",
        "evaluation_tags",
        "known_benign_explanations",
        "required_negative_control_concepts",
        "policy_traps",
        "injection_needles",
        "scenario_invariants",
        "unexpected_admit_is_hard_fail",
        "relevant_source_ids",
        "required_source_groups",
        "irrelevant_source_ids",
        "scenario_specific_tokens",
    }
)


class ScenarioCategory(Enum):
    CLEAN_DIAGNOSTIC = "CLEAN_DIAGNOSTIC"
    UNSUPPORTED_SECURITY_CLAIM = "UNSUPPORTED_SECURITY_CLAIM"
    AMBIGUOUS_BEHAVIOR = "AMBIGUOUS_BEHAVIOR"
    SOURCE_HALLUCINATION_TRAP = "SOURCE_HALLUCINATION_TRAP"
    PROMPT_INJECTION_CONTENT = "PROMPT_INJECTION_CONTENT"
    PRIOR_HYPOTHESIS_POISONING = "PRIOR_HYPOTHESIS_POISONING"
    NEGATIVE_CONTEXT_SCOPE = "NEGATIVE_CONTEXT_SCOPE"
    DIFFERENTIAL_REASONING_SEED = "DIFFERENTIAL_REASONING_SEED"
    INCONCLUSIVE_CONTEXT = "INCONCLUSIVE_CONTEXT"
    POLICY_BOUNDARY_TRAP = "POLICY_BOUNDARY_TRAP"


class ScenarioSplit(Enum):
    DEVELOPMENT = "development"
    CALIBRATION = "calibration"
    SEALED_HOLDOUT = "sealed_holdout"


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BenchmarkError(f"{field_name} must be a mapping")
    return value


def _text_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise BenchmarkError(f"{field_name} must be a list of strings")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise BenchmarkError(f"{field_name}[{index}] must be a non-empty string")
        items.append(item.strip())
    return tuple(items)


def _source_groups(value: object, field_name: str) -> tuple[tuple[str, ...], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise BenchmarkError(f"{field_name} must be a list of string lists")
    groups: list[tuple[str, ...]] = []
    for index, item in enumerate(value):
        group = _text_tuple(item, f"{field_name}[{index}]")
        if not group:
            raise BenchmarkError(f"{field_name}[{index}] must be a non-empty list")
        groups.append(group)
    return tuple(groups)


def _unknown(raw: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    extra = frozenset(raw.keys()) - allowed
    if extra:
        raise BenchmarkError(f"{label} has unsupported keys: {sorted(extra)}")


@dataclass(frozen=True)
class VisibleInput:
    research_run_id: str
    research_question: str
    observations: tuple[ObservationSource, ...] = ()
    prior_hypotheses: tuple[HypothesisSource, ...] = ()
    experiments: tuple[ExperimentSource, ...] = ()
    untrusted_external: tuple[ExternalContentSource, ...] = ()
    unresolved_questions: tuple[str, ...] = ()


@dataclass(frozen=True)
class HiddenEvaluation:
    leakage_canary: str
    known_source_ids: tuple[str, ...]
    forbidden_fabricated_source_ids: tuple[str, ...] = ()
    expected_admission_outcomes: tuple[str, ...] = ()
    expected_epistemic_distinctions: tuple[str, ...] = ()
    known_benign_explanations: tuple[str, ...] = ()
    required_negative_control_concepts: tuple[str, ...] = ()
    policy_traps: tuple[str, ...] = ()
    injection_needles: tuple[str, ...] = ()
    scenario_invariants: tuple[str, ...] = ()
    evaluation_tags: tuple[str, ...] = ()
    unexpected_admit_is_hard_fail: bool = False
    relevant_source_ids: tuple[str, ...] = ()
    required_source_groups: tuple[tuple[str, ...], ...] = ()
    irrelevant_source_ids: tuple[str, ...] = ()
    scenario_specific_tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class BenchmarkScenario:
    scenario_id: str
    version: str
    category: ScenarioCategory
    split: ScenarioSplit
    visible_input: VisibleInput
    hidden_evaluation: HiddenEvaluation
    variant_of: str | None = None
    variant_kind: str | None = None
    source_path: str | None = None

    @property
    def identity(self) -> str:
        return f"{self.scenario_id}@{self.version}"


def parse_visible_input(raw: object) -> VisibleInput:
    mapping = _require_mapping(raw, "visible_input")
    _unknown(mapping, VISIBLE_KEYS, "visible_input")
    observations: list[ObservationSource] = []
    for index, item in enumerate(mapping.get("observations") or []):
        row = _require_mapping(item, f"observations[{index}]")
        observations.append(
            ObservationSource(
                observation_id=_require_text(row.get("observation_id"), "observation_id"),
                observation_kind=_require_text(
                    row.get("observation_kind"), "observation_kind"
                ),
                payload=_require_mapping(row.get("payload"), "payload"),
                experiment_id=row.get("experiment_id"),
            )
        )
    hypotheses: list[HypothesisSource] = []
    for index, item in enumerate(mapping.get("prior_hypotheses") or []):
        row = _require_mapping(item, f"prior_hypotheses[{index}]")
        hypotheses.append(
            HypothesisSource(
                hypothesis_id=_require_text(row.get("hypothesis_id"), "hypothesis_id"),
                claim=_require_text(row.get("claim"), "claim"),
            )
        )
    experiments: list[ExperimentSource] = []
    for index, item in enumerate(mapping.get("experiments") or []):
        row = _require_mapping(item, f"experiments[{index}]")
        experiments.append(
            ExperimentSource(
                experiment_id=_require_text(row.get("experiment_id"), "experiment_id"),
                hypothesis_id=_require_text(row.get("hypothesis_id"), "hypothesis_id"),
                execution_state=_require_text(
                    row.get("execution_state"), "execution_state"
                ),
            )
        )
    external: list[ExternalContentSource] = []
    for index, item in enumerate(mapping.get("untrusted_external") or []):
        row = _require_mapping(item, f"untrusted_external[{index}]")
        content = row.get("content")
        if not isinstance(content, str):
            raise BenchmarkError(f"untrusted_external[{index}].content must be a string")
        external.append(
            ExternalContentSource(
                external_id=_require_text(row.get("external_id"), "external_id"),
                content=content,
                source_reference=_require_text(
                    row.get("source_reference"), "source_reference"
                ),
            )
        )
    return VisibleInput(
        research_run_id=_require_text(mapping.get("research_run_id"), "research_run_id"),
        research_question=_require_text(
            mapping.get("research_question"), "research_question"
        ),
        observations=tuple(observations),
        prior_hypotheses=tuple(hypotheses),
        experiments=tuple(experiments),
        untrusted_external=tuple(external),
        unresolved_questions=_text_tuple(
            mapping.get("unresolved_questions"), "unresolved_questions"
        ),
    )


def parse_hidden_evaluation(raw: object) -> HiddenEvaluation:
    mapping = _require_mapping(raw, "hidden_evaluation")
    _unknown(mapping, HIDDEN_KEYS, "hidden_evaluation")
    unexpected = mapping.get("unexpected_admit_is_hard_fail", False)
    if not isinstance(unexpected, bool):
        raise BenchmarkError("unexpected_admit_is_hard_fail must be a boolean")
    known = _text_tuple(mapping.get("known_source_ids"), "known_source_ids")
    if not known:
        raise BenchmarkError("known_source_ids must be a non-empty list")
    return HiddenEvaluation(
        leakage_canary=_require_text(mapping.get("leakage_canary"), "leakage_canary"),
        known_source_ids=known,
        forbidden_fabricated_source_ids=_text_tuple(
            mapping.get("forbidden_fabricated_source_ids"),
            "forbidden_fabricated_source_ids",
        ),
        expected_admission_outcomes=_text_tuple(
            mapping.get("expected_admission_outcomes"), "expected_admission_outcomes"
        ),
        expected_epistemic_distinctions=_text_tuple(
            mapping.get("expected_epistemic_distinctions"),
            "expected_epistemic_distinctions",
        ),
        known_benign_explanations=_text_tuple(
            mapping.get("known_benign_explanations"), "known_benign_explanations"
        ),
        required_negative_control_concepts=_text_tuple(
            mapping.get("required_negative_control_concepts"),
            "required_negative_control_concepts",
        ),
        policy_traps=_text_tuple(mapping.get("policy_traps"), "policy_traps"),
        injection_needles=_text_tuple(mapping.get("injection_needles"), "injection_needles"),
        scenario_invariants=_text_tuple(
            mapping.get("scenario_invariants"), "scenario_invariants"
        ),
        evaluation_tags=_text_tuple(mapping.get("evaluation_tags"), "evaluation_tags"),
        unexpected_admit_is_hard_fail=unexpected,
        relevant_source_ids=_text_tuple(
            mapping.get("relevant_source_ids"), "relevant_source_ids"
        ),
        required_source_groups=_source_groups(
            mapping.get("required_source_groups"), "required_source_groups"
        ),
        irrelevant_source_ids=_text_tuple(
            mapping.get("irrelevant_source_ids"), "irrelevant_source_ids"
        ),
        scenario_specific_tokens=_text_tuple(
            mapping.get("scenario_specific_tokens"), "scenario_specific_tokens"
        ),
    )


def parse_scenario(raw: object, *, source_path: str | None = None) -> BenchmarkScenario:
    mapping = _require_mapping(raw, "scenario")
    _unknown(mapping, SCENARIO_KEYS, "scenario")
    try:
        category = ScenarioCategory(_require_text(mapping.get("category"), "category"))
    except ValueError as exc:
        raise BenchmarkError("category is not a known benchmark category") from exc
    try:
        raw_split = _require_text(mapping.get("split"), "split")
        if raw_split == "holdout":
            split = ScenarioSplit.SEALED_HOLDOUT
        else:
            split = ScenarioSplit(raw_split)
    except ValueError as exc:
        raise BenchmarkError(
            "split must be development, calibration, or sealed_holdout"
        ) from exc
    variant_of = mapping.get("variant_of")
    variant_kind = mapping.get("variant_kind")
    if variant_of is not None:
        variant_of = _require_text(variant_of, "variant_of")
    if variant_kind is not None:
        variant_kind = _require_text(variant_kind, "variant_kind")
    return BenchmarkScenario(
        scenario_id=_require_text(mapping.get("scenario_id"), "scenario_id"),
        version=_require_text(mapping.get("version"), "version"),
        category=category,
        split=split,
        visible_input=parse_visible_input(mapping.get("visible_input")),
        hidden_evaluation=parse_hidden_evaluation(mapping.get("hidden_evaluation")),
        variant_of=variant_of,
        variant_kind=variant_kind,
        source_path=source_path,
    )


def load_scenario(path: Path) -> BenchmarkScenario:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"scenario is not valid JSON: {path}") from exc
    return parse_scenario(raw, source_path=str(path))


def load_scenarios(
    directory: Path,
    *,
    include_holdout: bool = False,
    include_calibration: bool = False,
    include_sealed: bool = False,
) -> tuple[BenchmarkScenario, ...]:
    if not directory.is_dir():
        raise BenchmarkError(f"scenario directory not found: {directory}")
    include_sealed = include_sealed or include_holdout
    repo_development = _is_repo_development_tree(directory)
    loaded: list[BenchmarkScenario] = []
    for path in sorted(directory.glob("*.json")):
        scenario = load_scenario(path)
        if scenario.split is ScenarioSplit.SEALED_HOLDOUT and repo_development:
            raise BenchmarkError(
                "sealed holdout scenarios must not live in the development repository tree"
            )
        if scenario.split is ScenarioSplit.CALIBRATION and not include_calibration:
            continue
        if scenario.split is ScenarioSplit.SEALED_HOLDOUT and not include_sealed:
            continue
        loaded.append(scenario)
    identities = [scenario.identity for scenario in loaded]
    duplicates = sorted({item for item in identities if identities.count(item) > 1})
    if duplicates:
        raise BenchmarkError(f"duplicate scenario identity: {duplicates}")
    if not loaded:
        raise BenchmarkError(f"no matching scenarios in {directory}")
    return tuple(loaded)


def _is_repo_development_tree(directory: Path) -> bool:
    parts = directory.resolve().parts
    return len(parts) >= 3 and parts[-3:] == ("benchmarks", "research", "scenarios")


def context_from_visible(visible: VisibleInput) -> ResearchContext:
    """Build model-visible ResearchContext. Hidden evaluation is not an argument."""
    return ResearchContextBuilder().build(
        research_run_id=visible.research_run_id,
        research_question=visible.research_question,
        observations=visible.observations,
        prior_hypotheses=visible.prior_hypotheses,
        experiments=visible.experiments,
        untrusted_external=visible.untrusted_external,
        unresolved_questions=visible.unresolved_questions,
    )


def assert_hidden_matches_context(
    scenario: BenchmarkScenario, context: ResearchContext
) -> None:
    """Authoring check. Does not send hidden data to the model."""
    resolvable = context.resolvable_source_ids()
    missing = [
        item_id
        for item_id in scenario.hidden_evaluation.known_source_ids
        if item_id not in resolvable
    ]
    if missing:
        raise BenchmarkError(
            f"{scenario.identity} known_source_ids not in visible context: {missing}"
        )
    leaked = [
        item_id
        for item_id in scenario.hidden_evaluation.forbidden_fabricated_source_ids
        if item_id in resolvable
    ]
    if leaked:
        raise BenchmarkError(
            f"{scenario.identity} forbidden ids are present in visible context: {leaked}"
        )
