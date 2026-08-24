"""Experiment and model-configuration identity. Not a provider SDK and not Domain SoR."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from research_os.benchmark.errors import BenchmarkError
from research_os.research.cycle import (
    FALSIFIER_INSTRUCTION_VERSION,
    FALSIFIER_INSTRUCTIONS,
    GENERATOR_INSTRUCTION_VERSION,
    GENERATOR_INSTRUCTIONS,
    STRUCTURED_OUTPUT_SPEC_VERSION,
)
from research_os.research.output_contracts import combined_contract_fingerprint

HARNESS_VERSION = "gate-04b.2"
CONTRACT_QUALIFICATION_HARNESS_VERSION = "gate-04b.2.contract"
CONTEXT_BUILDER_VERSION = "ResearchContextBuilder.v1"
ADMISSION_VERSION = "admit_hypothesis.v1"
EVALUATOR_VERSION = "benchmark.evaluator.v1"
DEFAULT_RUNS_PER_SCENARIO = 3
DEFAULT_SUITE_ID = "research-os.development.v1"
CONTRACT_QUALIFICATION_SUITE_ID = "research-os.clean-contract.v2"


def fingerprint_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def current_instruction_identity() -> InstructionIdentity:
    return InstructionIdentity(
        generator_instruction_version=GENERATOR_INSTRUCTION_VERSION,
        generator_instruction_fingerprint=fingerprint_text(GENERATOR_INSTRUCTIONS),
        falsifier_instruction_version=FALSIFIER_INSTRUCTION_VERSION,
        falsifier_instruction_fingerprint=fingerprint_text(FALSIFIER_INSTRUCTIONS),
        structured_output_spec_version=STRUCTURED_OUTPUT_SPEC_VERSION,
        structured_output_spec_fingerprint=combined_contract_fingerprint(),
    )


@dataclass(frozen=True)
class InstructionIdentity:
    generator_instruction_version: str
    generator_instruction_fingerprint: str
    falsifier_instruction_version: str
    falsifier_instruction_fingerprint: str
    structured_output_spec_version: str
    structured_output_spec_fingerprint: str

    def to_mapping(self) -> dict[str, str]:
        return {
            "generator_instruction_version": self.generator_instruction_version,
            "generator_instruction_fingerprint": self.generator_instruction_fingerprint,
            "falsifier_instruction_version": self.falsifier_instruction_version,
            "falsifier_instruction_fingerprint": self.falsifier_instruction_fingerprint,
            "structured_output_spec_version": self.structured_output_spec_version,
            "structured_output_spec_fingerprint": self.structured_output_spec_fingerprint,
        }


@dataclass(frozen=True)
class ModelConfigurationIdentity:
    """Reproducible experiment identity. Aliases such as gpt/claude/gemini are not enough."""

    adapter_identity: str
    provider_adapter_identity: str | None = None
    provider_model_id: str | None = None
    provider_model_version: str | None = None
    generator_configuration: str = "scripted.default"
    falsifier_configuration: str = "scripted.default"
    reasoning_settings: str | None = None
    temperature: float | None = None
    max_output_budget: int | None = None
    runtime_kind: str | None = None
    runtime_class: str | None = None
    auth_mode: str | None = None
    runtime_id: str | None = None
    runtime_version: str | None = None
    configuration_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.adapter_identity, str) or not self.adapter_identity.strip():
            raise BenchmarkError("adapter_identity must be a non-empty string")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "adapter_identity": self.adapter_identity,
            "provider_adapter_identity": self.provider_adapter_identity,
            "provider_model_id": self.provider_model_id,
            "provider_model_version": self.provider_model_version,
            "generator_configuration": self.generator_configuration,
            "falsifier_configuration": self.falsifier_configuration,
            "reasoning_settings": self.reasoning_settings,
            "temperature": self.temperature,
            "max_output_budget": self.max_output_budget,
            "runtime_kind": self.runtime_kind,
            "runtime_class": self.runtime_class,
            "auth_mode": self.auth_mode,
            "runtime_id": self.runtime_id,
            "runtime_version": self.runtime_version,
            "configuration_fingerprint": self.configuration_fingerprint,
            "unset_provider_fields_are_unknown_not_fabricated": True,
            "contains_secrets": False,
        }


@dataclass(frozen=True)
class BenchmarkExperimentConfig:
    suite_id: str = DEFAULT_SUITE_ID
    runs_per_scenario: int = DEFAULT_RUNS_PER_SCENARIO
    harness_version: str = HARNESS_VERSION
    context_builder_version: str = CONTEXT_BUILDER_VERSION
    admission_version: str = ADMISSION_VERSION
    evaluator_version: str = EVALUATOR_VERSION
    instruction_identity: InstructionIdentity | None = None
    include_calibration: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.suite_id, str) or not self.suite_id.strip():
            raise BenchmarkError("suite_id must be a non-empty string")
        if (
            not isinstance(self.runs_per_scenario, int)
            or isinstance(self.runs_per_scenario, bool)
            or self.runs_per_scenario < 1
        ):
            raise BenchmarkError("runs_per_scenario must be a positive int")
        if self.instruction_identity is None:
            object.__setattr__(self, "instruction_identity", current_instruction_identity())

    @property
    def comparable_key(self) -> tuple[str, ...]:
        identity = self.instruction_identity
        assert identity is not None
        return (
            self.suite_id,
            str(self.runs_per_scenario),
            self.harness_version,
            self.context_builder_version,
            self.admission_version,
            self.evaluator_version,
            identity.generator_instruction_fingerprint,
            identity.falsifier_instruction_fingerprint,
            identity.structured_output_spec_fingerprint,
        )

    def to_mapping(self) -> dict[str, Any]:
        identity = self.instruction_identity
        assert identity is not None
        return {
            "suite_id": self.suite_id,
            "runs_per_scenario": self.runs_per_scenario,
            "harness_version": self.harness_version,
            "context_builder_version": self.context_builder_version,
            "admission_version": self.admission_version,
            "evaluator_version": self.evaluator_version,
            "instruction_identity": identity.to_mapping(),
            "include_calibration": self.include_calibration,
            "authoritative_real_model_comparison": self.runs_per_scenario > 1,
        }
