"""Evaluation artifacts. Not Evidence, Finding, Candidate, or SoR truth."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

from zest.benchmark.cycle import run_bounded_cycle
from zest.benchmark.errors import BenchmarkError
from zest.benchmark.failures import classify_failure
from zest.benchmark.leakage import leakage_hits
from zest.benchmark.metrics import (
    HardFailCode,
    QualityObservation,
    collect_hard_fails,
    collect_quality,
    normalize_claim,
)
from zest.benchmark.scenarios import (
    BenchmarkScenario,
    assert_hidden_matches_context,
    context_from_visible,
)
from zest.research.model_port import ModelCallTelemetry, ModelPort


@dataclass(frozen=True)
class ScenarioRunResult:
    scenario_id: str
    version: str
    category: str
    split: str
    adapter_identity: str
    admission_outcome: str
    admission_reason_code: str
    hard_failures: tuple[str, ...]
    quality: tuple[QualityObservation, ...]
    generator_calls: int
    falsifier_calls: int
    normalized_claim: str | None
    elapsed_ms: int
    leakage: tuple[str, ...]
    parse_error: str | None
    failure_class: str = "NONE"
    provider_runtime_error: bool = False
    source_references: tuple[str, ...] = ()
    experiment_direction: str | None = None
    run_index: int = 1
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider_reported_cost: float | None = None
    pricing_reference: str | None = None
    retries: int | None = None
    timeout: bool | None = None

    @property
    def harness_invariant_failed(self) -> bool:
        return HardFailCode.HIDDEN_BENCHMARK_DATA_LEAKAGE.value in self.hard_failures

    def to_mapping(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "version": self.version,
            "category": self.category,
            "split": self.split,
            "adapter_identity": self.adapter_identity,
            "admission_outcome": self.admission_outcome,
            "admission_reason_code": self.admission_reason_code,
            "hard_failures": list(self.hard_failures),
            "quality": [
                {"dimension": item.dimension, "passed": item.passed, "detail": item.detail}
                for item in self.quality
            ],
            "generator_calls": self.generator_calls,
            "falsifier_calls": self.falsifier_calls,
            "normalized_claim": self.normalized_claim,
            "elapsed_ms": self.elapsed_ms,
            "leakage": list(self.leakage),
            "parse_error": self.parse_error,
            "failure_class": self.failure_class,
            "provider_runtime_error": self.provider_runtime_error,
            "source_references": list(self.source_references),
            "experiment_direction": self.experiment_direction,
            "run_index": self.run_index,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "provider_reported_cost": self.provider_reported_cost,
            "pricing_reference": self.pricing_reference,
            "retries": self.retries,
            "timeout": self.timeout,
            "unset_provider_telemetry_not_fabricated": True,
        }


@dataclass(frozen=True)
class ModelBenchmarkReport:
    adapter_identity: str
    scenario_results: tuple[ScenarioRunResult, ...]

    @property
    def harness_invariant_failed(self) -> bool:
        return any(item.harness_invariant_failed for item in self.scenario_results)

    def hard_fail_counts(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for result in self.scenario_results:
            counts.update(result.hard_failures)
        return dict(counts)

    def quality_pass_counts(self) -> dict[str, tuple[int, int]]:
        totals: dict[str, list[int]] = {}
        for result in self.scenario_results:
            for item in result.quality:
                pair = totals.setdefault(item.dimension, [0, 0])
                pair[1] += 1
                if item.passed:
                    pair[0] += 1
        return {key: (value[0], value[1]) for key, value in totals.items()}

    def admission_counts(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for result in self.scenario_results:
            counts[result.admission_outcome] += 1
        return dict(counts)

    def duplicate_claim_groups(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        grouped: dict[str, list[str]] = {}
        for result in self.scenario_results:
            if not result.normalized_claim:
                continue
            grouped.setdefault(result.normalized_claim, []).append(result.scenario_id)
        return tuple(
            (claim, tuple(scenario_ids))
            for claim, scenario_ids in grouped.items()
            if len(scenario_ids) > 1
        )

    def quality_pass_total(self) -> int:
        return sum(1 for result in self.scenario_results for item in result.quality if item.passed)

    def hard_fail_event_count(self) -> int:
        return sum(len(result.hard_failures) for result in self.scenario_results)

    def to_mapping(self) -> dict[str, Any]:
        quality = {
            name: {"passed": passed, "total": total}
            for name, (passed, total) in self.quality_pass_counts().items()
        }
        return {
            "kind": "ModelBenchmarkReport",
            "not_evidence": True,
            "not_finding": True,
            "not_candidate": True,
            "not_sor_truth": True,
            "no_aggregate_model_score": True,
            "adapter_identity": self.adapter_identity,
            "hard_fail_counts": self.hard_fail_counts(),
            "hard_fail_event_count": self.hard_fail_event_count(),
            "quality_pass_counts": quality,
            "admission_counts": self.admission_counts(),
            "duplicate_claim_groups": [
                {"normalized_claim": claim, "scenario_ids": list(ids)}
                for claim, ids in self.duplicate_claim_groups()
            ],
            "harness_invariant_failed": self.harness_invariant_failed,
            "scenario_results": [item.to_mapping() for item in self.scenario_results],
        }


def evaluate_scenario(
    scenario: BenchmarkScenario,
    model: ModelPort,
    *,
    adapter_identity: str,
    correlation_id: str | None = None,
    run_index: int = 1,
) -> ScenarioRunResult:
    started = time.perf_counter()
    context = context_from_visible(scenario.visible_input)
    assert_hidden_matches_context(scenario, context)
    trace = run_bounded_cycle(
        context,
        model,
        correlation_id=correlation_id or f"bench:{scenario.identity}:r{run_index}",
    )
    leaks = leakage_hits(scenario, context, trace.requests)
    fails = collect_hard_fails(scenario, context, trace)
    quality = collect_quality(scenario, context, trace)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    claim = None if trace.proposal is None else normalize_claim(trace.proposal.proposed_claim)
    direction = None
    if trace.proposal is not None:
        direction = normalize_claim(trace.proposal.suggested_disconfirming_test)
    telemetry = _combined_telemetry(trace.generator_telemetry, trace.falsifier_telemetry)
    return ScenarioRunResult(
        scenario_id=scenario.scenario_id,
        version=scenario.version,
        category=scenario.category.value,
        split=scenario.split.value,
        adapter_identity=adapter_identity,
        admission_outcome=trace.admission.outcome.value,
        admission_reason_code=trace.admission.reason_code,
        hard_failures=tuple(item.value for item in fails),
        quality=quality,
        generator_calls=trace.generator_calls,
        falsifier_calls=trace.falsifier_calls,
        normalized_claim=claim,
        elapsed_ms=elapsed_ms,
        leakage=leaks,
        parse_error=trace.parse_error,
        failure_class=classify_failure(trace, fails).value,
        provider_runtime_error=trace.provider_runtime_error,
        source_references=() if trace.proposal is None else trace.proposal.source_references,
        experiment_direction=direction,
        run_index=run_index,
        latency_ms=telemetry.latency_ms,
        input_tokens=telemetry.input_tokens,
        output_tokens=telemetry.output_tokens,
        provider_reported_cost=telemetry.provider_reported_cost,
        pricing_reference=telemetry.provider_cost_provenance,
        retries=telemetry.retries,
    )


def evaluate_suite(
    scenarios: tuple[BenchmarkScenario, ...],
    model: ModelPort,
    *,
    adapter_identity: str,
) -> ModelBenchmarkReport:
    if not scenarios:
        raise BenchmarkError("benchmark suite is empty")
    results = tuple(
        evaluate_scenario(scenario, model, adapter_identity=adapter_identity)
        for scenario in scenarios
    )
    return ModelBenchmarkReport(adapter_identity=adapter_identity, scenario_results=results)


def format_scorecard(report: ModelBenchmarkReport) -> str:
    lines = [
        f"adapter: {report.adapter_identity}",
        f"scenarios: {len(report.scenario_results)}",
        "hard failures:",
    ]
    counts = report.hard_fail_counts()
    if not counts:
        lines.append("  (none)")
    else:
        for code in sorted(counts):
            lines.append(f"  {code}: {counts[code]}")
    lines.append("quality passes:")
    for name, (passed, total) in sorted(report.quality_pass_counts().items()):
        lines.append(f"  {name}: {passed}/{total}")
    lines.append("admission outcomes:")
    admissions = report.admission_counts()
    if not admissions:
        lines.append("  (none)")
    else:
        for name in sorted(admissions):
            lines.append(f"  {name}: {admissions[name]}")
    duplicates = report.duplicate_claim_groups()
    lines.append(f"exact duplicate claim groups: {len(duplicates)}")
    lines.append(
        "harness invariant: "
        + ("FAIL leakage" if report.harness_invariant_failed else "PASS")
    )
    lines.append("no aggregate model score")
    return "\n".join(lines)


def _combined_telemetry(left: object, right: object) -> ModelCallTelemetry:
    items = [
        item
        for item in (left, right)
        if isinstance(item, ModelCallTelemetry)
    ]
    if not items:
        return ModelCallTelemetry()
    latency = [item.latency_ms for item in items if item.latency_ms is not None]
    inputs = [item.input_tokens for item in items if item.input_tokens is not None]
    outputs = [item.output_tokens for item in items if item.output_tokens is not None]
    retries = [item.retries for item in items if item.retries is not None]
    costs = [item.provider_reported_cost for item in items if item.provider_reported_cost is not None]
    provenances = [
        item.provider_cost_provenance
        for item in items
        if item.provider_cost_provenance
    ]
    return ModelCallTelemetry(
        latency_ms=sum(latency) if latency else None,
        input_tokens=sum(inputs) if inputs else None,
        output_tokens=sum(outputs) if outputs else None,
        retries=sum(retries) if retries else None,
        provider_reported_cost=sum(costs) if costs else None,
        provider_cost_provenance=provenances[0] if len(provenances) == 1 else None,
    )
