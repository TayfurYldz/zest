"""Load versioned security ground-truth scenarios. Hidden evaluation is not pipeline input."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from zest.security_benchmark.types import ExpectedSecurityClass, PromotionStage

MANDATORY_SCENARIO_IDS = (
    "S01_TRUE_BOLA",
    "S02_SECURE_OBJECT_AUTHORIZATION",
    "S03_PUBLIC_OBJECT_LEGITIMATE_200",
    "S04_EXPLICIT_DELEGATED_ACCESS",
    "S05_DECEPTIVE_200_NO_OWNERSHIP_PROOF",
    "S06_SHARED_RESOURCE",
    "S07_CONTRADICTORY_VERIFICATION",
    "S08_OPERATIONAL_TIMEOUT",
    "S09_REDIRECT_BOUNDARY",
    "S10_OUT_OF_SCOPE",
)
MANDATORY_WORKFLOW_SCENARIO_IDS = (
    "W01_TRUE_ROLE_BYPASS",
    "W02_TRUE_SEQUENCE_SKIP",
    "W03_SECURE_ROLE_ENFORCEMENT",
    "W04_SECURE_SEQUENCE_ENFORCEMENT",
    "W05_DECEPTIVE_200_NO_STATE_CHANGE",
    "W06_IDEMPOTENT_REPEAT",
    "W07_LEGITIMATE_DELEGATED_REVIEWER",
    "W08_STALE_CLIENT_STATE",
    "W09_CONTRADICTORY_VERIFICATION",
    "W10_OPERATIONAL_TIMEOUT",
    "W11_OUT_OF_SCOPE",
    "W12_REDIRECT_BOUNDARY",
)
MANDATORY_RESEARCH_SELECTION_SCENARIO_IDS = (
    "R01_BOLA_TRUE_WORKFLOW_DECOY",
    "R02_WORKFLOW_TRUE_BOLA_DECOY",
    "R03_BOTH_TRUE",
    "R04_BOTH_BENIGN",
    "R05_AMBIGUOUS_NEEDS_CONTEXT",
    "R06_CONTRADICTION_CHANGES_DIRECTION",
    "R07_BUDGET_CONSTRAINED_SELECTION",
    "R08_REDUNDANT_EXPERIMENT_AVOIDANCE",
    "R09_CONTEXT_BOUND_NEGATIVE_KNOWLEDGE",
    "R10_CORE_DENIAL_ALTERNATIVE_PATH",
    "R11A_COUNTERFACTUAL_BOLA_PRIVATE",
    "R11B_COUNTERFACTUAL_BOLA_PUBLIC",
    "R12A_COUNTERFACTUAL_WORKFLOW_APPROVED",
    "R12B_COUNTERFACTUAL_WORKFLOW_UNCHANGED",
)


@dataclass(frozen=True)
class ScenarioHarness:
    """Operator/harness configuration. Must not be copied into WorkerRequest."""

    fixture_kind: str
    actor: str
    own_object: str
    cross_object: str
    verification_actor: str | None = None
    verification_own_object: str | None = None
    verification_cross_object: str | None = None
    attempt_finding: bool = False
    human_decision: str | None = None
    target_reference: str | None = None
    in_scope: bool = True
    resource_id: str | None = None
    transition: str | None = None
    verification_resource_id: str | None = None
    area: str = "workflow"
    object_fixture: str | None = None
    workflow_fixture: str | None = None
    second_actor: str | None = None
    second_own_object: str | None = None
    second_cross_object: str | None = None
    max_cycles: int | None = None
    max_experiments: int | None = None
    pause_after_cycles: int | None = None
    candidate_origin: str | None = None


@dataclass(frozen=True)
class HiddenEvaluation:
    expected_class: ExpectedSecurityClass
    expected_max_promotion_stage: PromotionStage
    security_violation: bool
    leakage_canary: str
    required_controls: tuple[str, ...]
    forbidden_promotions: tuple[str, ...]
    expected_classification: str | None = None
    expected_surviving_hypothesis_classes: tuple[str, ...] = ()
    required_falsified_classes: tuple[str, ...] = ()
    expected_terminal_research_state: str | None = None
    required_branch_difference: bool = False
    pair_group: str | None = None


@dataclass(frozen=True)
class SecurityGroundTruthScenario:
    scenario_id: str
    version: str
    harness: ScenarioHarness
    hidden_evaluation: HiddenEvaluation

    @property
    def identity(self) -> str:
        return f"{self.scenario_id}@{self.version}"


def load_scenario(path: Path) -> SecurityGroundTruthScenario:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    harness_raw = payload.get("harness")
    hidden_raw = payload.get("hidden_evaluation")
    if not isinstance(harness_raw, Mapping) or not isinstance(hidden_raw, Mapping):
        raise ValueError(f"{path} must split harness and hidden_evaluation")
    return SecurityGroundTruthScenario(
        scenario_id=_text(payload.get("scenario_id"), "scenario_id"),
        version=_text(payload.get("version"), "version"),
        harness=ScenarioHarness(
            fixture_kind=_text(harness_raw.get("fixture_kind"), "harness.fixture_kind"),
            actor=_text(harness_raw.get("actor"), "harness.actor"),
            own_object=_text(harness_raw.get("own_object"), "harness.own_object"),
            cross_object=_text(harness_raw.get("cross_object"), "harness.cross_object"),
            verification_actor=_optional_text(harness_raw.get("verification_actor")),
            verification_own_object=_optional_text(harness_raw.get("verification_own_object")),
            verification_cross_object=_optional_text(
                harness_raw.get("verification_cross_object")
            ),
            attempt_finding=bool(harness_raw.get("attempt_finding", False)),
            human_decision=_optional_text(harness_raw.get("human_decision")),
            target_reference=_optional_text(harness_raw.get("target_reference")),
            in_scope=bool(harness_raw.get("in_scope", True)),
            resource_id=_optional_text(harness_raw.get("resource_id")),
            transition=_optional_text(harness_raw.get("transition")),
            verification_resource_id=_optional_text(
                harness_raw.get("verification_resource_id")
            ),
            area=_text(harness_raw.get("area") or "workflow", "harness.area"),
            object_fixture=_optional_text(harness_raw.get("object_fixture")),
            workflow_fixture=_optional_text(harness_raw.get("workflow_fixture")),
            second_actor=_optional_text(harness_raw.get("second_actor")),
            second_own_object=_optional_text(harness_raw.get("second_own_object")),
            second_cross_object=_optional_text(harness_raw.get("second_cross_object")),
            max_cycles=_optional_int(harness_raw.get("max_cycles")),
            max_experiments=_optional_int(harness_raw.get("max_experiments")),
            pause_after_cycles=_optional_int(harness_raw.get("pause_after_cycles")),
            candidate_origin=_optional_text(harness_raw.get("candidate_origin")),
        ),
        hidden_evaluation=HiddenEvaluation(
            expected_class=ExpectedSecurityClass(
                _text(hidden_raw.get("expected_class"), "hidden_evaluation.expected_class")
            ),
            expected_max_promotion_stage=PromotionStage(
                _text(
                    hidden_raw.get("expected_max_promotion_stage"),
                    "hidden_evaluation.expected_max_promotion_stage",
                )
            ),
            security_violation=bool(hidden_raw.get("security_violation")),
            leakage_canary=_text(
                hidden_raw.get("leakage_canary"), "hidden_evaluation.leakage_canary"
            ),
            required_controls=_texts(
                hidden_raw.get("required_controls", []), "required_controls"
            ),
            forbidden_promotions=_texts(
                hidden_raw.get("forbidden_promotions", []), "forbidden_promotions"
            ),
            expected_classification=_optional_text(
                hidden_raw.get("expected_classification")
            ),
            expected_surviving_hypothesis_classes=_texts(
                hidden_raw.get("expected_surviving_hypothesis_classes", []),
                "expected_surviving_hypothesis_classes",
            ),
            required_falsified_classes=_texts(
                hidden_raw.get("required_falsified_classes", []),
                "required_falsified_classes",
            ),
            expected_terminal_research_state=_optional_text(
                hidden_raw.get("expected_terminal_research_state")
            ),
            required_branch_difference=bool(
                hidden_raw.get("required_branch_difference", False)
            ),
            pair_group=_optional_text(hidden_raw.get("pair_group")),
        ),
    )


def load_scenarios(directory: Path) -> tuple[SecurityGroundTruthScenario, ...]:
    paths = sorted(directory.glob("*.json"))
    scenarios = tuple(load_scenario(path) for path in paths)
    ids = tuple(item.scenario_id for item in scenarios)
    missing = [item for item in MANDATORY_SCENARIO_IDS if item not in ids]
    if missing:
        raise ValueError(f"missing mandatory security scenarios: {missing}")
    return scenarios


def load_workflow_scenarios(directory: Path) -> tuple[SecurityGroundTruthScenario, ...]:
    paths = sorted(directory.glob("*.json"))
    scenarios = tuple(load_scenario(path) for path in paths)
    ids = tuple(item.scenario_id for item in scenarios)
    missing = [item for item in MANDATORY_WORKFLOW_SCENARIO_IDS if item not in ids]
    if missing:
        raise ValueError(f"missing mandatory workflow scenarios: {missing}")
    extra = [item for item in ids if item not in MANDATORY_WORKFLOW_SCENARIO_IDS]
    if extra:
        raise ValueError(f"unexpected workflow scenarios: {extra}")
    return scenarios


def load_research_selection_scenarios(
    directory: Path,
) -> tuple[SecurityGroundTruthScenario, ...]:
    paths = sorted(directory.glob("*.json"))
    scenarios = tuple(load_scenario(path) for path in paths)
    ids = tuple(item.scenario_id for item in scenarios)
    missing = [item for item in MANDATORY_RESEARCH_SELECTION_SCENARIO_IDS if item not in ids]
    if missing:
        raise ValueError(f"missing mandatory research-selection scenarios: {missing}")
    extra = [item for item in ids if item not in MANDATORY_RESEARCH_SELECTION_SCENARIO_IDS]
    if extra:
        raise ValueError(f"unexpected research-selection scenarios: {extra}")
    return scenarios


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("optional int fields must be >= 0 when present")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("optional text fields must be non-empty when present")
    return value.strip()


def _texts(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, tuple):
        value = list(value)
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings")
    return tuple(_text(item, f"{field_name}[]") for item in value)
