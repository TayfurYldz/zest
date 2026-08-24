"""Repeated-run experiments, stability, and paired comparison. No magic winner."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from research_os.benchmark.checkpoint import (
    BenchmarkCheckpointSession,
    BenchmarkPlanCheckpoint,
)
from research_os.benchmark.errors import BenchmarkError
from research_os.benchmark.evaluate import ScenarioRunResult, evaluate_scenario
from research_os.benchmark.failures import FailureClass, PROVIDER_FAILURE_CLASSES
from research_os.benchmark.holdout import HoldoutLoad
from research_os.benchmark.identity import (
    BenchmarkExperimentConfig,
    ModelConfigurationIdentity,
)
from research_os.benchmark.scenarios import BenchmarkScenario
from research_os.benchmark.source_provenance import SourceProvenance
from research_os.benchmark.suite import SuiteManifest, build_suite_manifest
from research_os.research.model_port import ModelPort


def _fraction(count: int, total: int) -> str:
    return f"{count}/{total}"


@dataclass(frozen=True)
class ScenarioRepeatSummary:
    scenario_id: str
    version: str
    category: str
    attempted: int
    completed: int
    provider_runtime_failures: int
    provider_auth_failures: int
    provider_rate_limit_failures: int
    provider_timeout_failures: int
    structured_output_failures: int
    research_quality_failures: int
    harness_invariant_failures: int
    hard_fail_occurrence: dict[str, str]
    admission_distribution: dict[str, int]
    exact_duplicate_rate: str
    unique_claims: int
    unique_source_sets: int
    unique_experiment_directions: int
    runs: tuple[ScenarioRunResult, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "version": self.version,
            "category": self.category,
            "attempted": self.attempted,
            "completed": self.completed,
            "provider_runtime_failures": self.provider_runtime_failures,
            "provider_auth_failures": self.provider_auth_failures,
            "provider_rate_limit_failures": self.provider_rate_limit_failures,
            "provider_timeout_failures": self.provider_timeout_failures,
            "structured_output_failures": self.structured_output_failures,
            "research_quality_failures": self.research_quality_failures,
            "harness_invariant_failures": self.harness_invariant_failures,
            "hard_fail_occurrence": dict(self.hard_fail_occurrence),
            "admission_distribution": dict(self.admission_distribution),
            "exact_duplicate_rate": self.exact_duplicate_rate,
            "unique_claims": self.unique_claims,
            "unique_source_sets": self.unique_source_sets,
            "unique_experiment_directions": self.unique_experiment_directions,
            "no_average_hides_hard_fail": True,
            "runs": [item.to_mapping() for item in self.runs],
        }


@dataclass(frozen=True)
class ExperimentReport:
    run_id: str
    created_at: str
    git_commit: str
    config: BenchmarkExperimentConfig
    model: ModelConfigurationIdentity
    suite: SuiteManifest
    summaries: tuple[ScenarioRepeatSummary, ...]
    holdout: HoldoutLoad | None = None
    source_provenance: SourceProvenance | None = None

    @property
    def harness_invariant_failed(self) -> bool:
        return any(item.harness_invariant_failures for item in self.summaries)

    @property
    def authoritative_real_model_comparison(self) -> bool:
        if self.config.runs_per_scenario <= 1:
            return False
        if self.source_provenance is not None and not self.source_provenance.authoritative:
            return False
        return True

    def to_mapping(self) -> dict[str, Any]:
        return {
            "kind": "ExperimentReport",
            "not_evidence": True,
            "not_finding": True,
            "not_candidate": True,
            "not_sor_truth": True,
            "no_aggregate_model_score": True,
            "no_automatic_winner": True,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "git_commit": self.git_commit,
            "source_provenance": None
            if self.source_provenance is None
            else self.source_provenance.to_mapping(),
            "config": self.config.to_mapping(),
            "model_configuration": self.model.to_mapping(),
            "suite": self.suite.to_mapping(),
            "holdout": None if self.holdout is None else self.holdout.to_mapping(),
            "authoritative_real_model_comparison": self.authoritative_real_model_comparison,
            "summaries": [item.to_mapping() for item in self.summaries],
        }


@dataclass(frozen=True)
class PairedScenarioObservation:
    scenario_id: str
    version: str
    left: dict[str, Any]
    right: dict[str, Any]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "version": self.version,
            "left": self.left,
            "right": self.right,
        }


@dataclass(frozen=True)
class PairedComparison:
    comparable: bool
    reason: str
    left_adapter: str
    right_adapter: str
    left_configuration: str | None = None
    right_configuration: str | None = None
    left_model_id: str | None = None
    right_model_id: str | None = None
    scenarios: tuple[PairedScenarioObservation, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        return {
            "kind": "PairedComparison",
            "comparable": self.comparable,
            "reason": self.reason,
            "left_adapter": self.left_adapter,
            "right_adapter": self.right_adapter,
            "left_configuration": self.left_configuration,
            "right_configuration": self.right_configuration,
            "left_model_id": self.left_model_id,
            "right_model_id": self.right_model_id,
            "no_automatic_winner": True,
            "scenarios": [item.to_mapping() for item in self.scenarios],
        }


def summarize_repeats(
    scenario: BenchmarkScenario, runs: tuple[ScenarioRunResult, ...]
) -> ScenarioRepeatSummary:
    attempted = len(runs)
    completed = sum(
        1
        for item in runs
        if item.failure_class not in {cls.value for cls in PROVIDER_FAILURE_CLASSES}
        and not item.provider_runtime_error
    )
    provider = sum(1 for item in runs if item.failure_class == FailureClass.PROVIDER_RUNTIME.value)
    auth = sum(1 for item in runs if item.failure_class == FailureClass.PROVIDER_AUTH.value)
    rate = sum(1 for item in runs if item.failure_class == FailureClass.PROVIDER_RATE_LIMIT.value)
    timeout = sum(1 for item in runs if item.failure_class == FailureClass.PROVIDER_TIMEOUT.value)
    structured = sum(
        1 for item in runs if item.failure_class == FailureClass.STRUCTURED_OUTPUT_FAILURE.value
    )
    research = sum(
        1
        for item in runs
        if item.failure_class
        in {
            FailureClass.GENERATOR_RESEARCH_QUALITY.value,
            FailureClass.FALSIFIER_RESEARCH_QUALITY.value,
        }
    )
    harness = sum(
        1 for item in runs if item.failure_class == FailureClass.HARNESS_INVARIANT.value
    )
    occurrence: dict[str, int] = {}
    for run in runs:
        for code in set(run.hard_failures):
            occurrence[code] = occurrence.get(code, 0) + 1
    admissions: Counter[str] = Counter(item.admission_outcome for item in runs)
    claims = [item.normalized_claim for item in runs if item.normalized_claim]
    unique_claims = len(set(claims))
    duplicates = max(0, len(claims) - unique_claims)
    sources = {tuple(item.source_references) for item in runs if item.source_references}
    directions = {item.experiment_direction for item in runs if item.experiment_direction}
    return ScenarioRepeatSummary(
        scenario_id=scenario.scenario_id,
        version=scenario.version,
        category=scenario.category.value,
        attempted=attempted,
        completed=completed,
        provider_runtime_failures=provider,
        provider_auth_failures=auth,
        provider_rate_limit_failures=rate,
        provider_timeout_failures=timeout,
        structured_output_failures=structured,
        research_quality_failures=research,
        harness_invariant_failures=harness,
        hard_fail_occurrence={
            code: _fraction(count, attempted) for code, count in sorted(occurrence.items())
        },
        admission_distribution=dict(admissions),
        exact_duplicate_rate=_fraction(duplicates, max(len(claims), 1)),
        unique_claims=unique_claims,
        unique_source_sets=len(sources),
        unique_experiment_directions=len(directions),
        runs=runs,
    )


def run_experiment(
    scenarios: tuple[BenchmarkScenario, ...],
    model: ModelPort,
    *,
    config: BenchmarkExperimentConfig,
    model_identity: ModelConfigurationIdentity,
    git_commit: str = "unknown",
    holdout: HoldoutLoad | None = None,
    suite_version: str = "1",
    source_provenance: SourceProvenance | None = None,
    checkpoint_session: BenchmarkCheckpointSession | None = None,
    checkpoint_plan: BenchmarkPlanCheckpoint | None = None,
) -> ExperimentReport:
    if not scenarios:
        raise BenchmarkError("experiment suite is empty")
    if checkpoint_session is not None and checkpoint_plan is not None:
        raise BenchmarkError("checkpoint_session and checkpoint_plan are mutually exclusive")
    checkpoint = checkpoint_plan
    if checkpoint is None and checkpoint_session is not None:
        checkpoint = checkpoint_session.open_plan(
            scenarios,
            config=config,
            model_identity=model_identity,
            git_commit=git_commit,
            suite_version=suite_version,
        )
    summaries: list[ScenarioRepeatSummary] = []
    for scenario in scenarios:
        run_results: list[ScenarioRunResult] = []
        for index in range(1, config.runs_per_scenario + 1):
            run_model = (
                model
                if checkpoint is None
                else checkpoint.wrap_model(
                    model,
                    scenario_identity=scenario.identity,
                    run_index=index,
                )
            )
            run_results.append(
                evaluate_scenario(
                    scenario,
                    run_model,
                    adapter_identity=model_identity.adapter_identity,
                    run_index=index,
                )
            )
        runs = tuple(run_results)
        summaries.append(summarize_repeats(scenario, runs))
    return ExperimentReport(
        run_id=str(uuid4()),
        created_at=datetime.now(timezone.utc).isoformat(),
        git_commit=git_commit or "unknown",
        config=config,
        model=model_identity,
        suite=build_suite_manifest(
            scenarios, suite_id=config.suite_id, suite_version=suite_version
        ),
        summaries=tuple(summaries),
        holdout=holdout,
        source_provenance=source_provenance,
    )


def compare_experiments(left: ExperimentReport, right: ExperimentReport) -> PairedComparison:
    if left.git_commit != right.git_commit:
        return PairedComparison(
            comparable=False,
            reason="Research OS commit differs; results are not directly comparable",
            left_adapter=left.model.adapter_identity,
            right_adapter=right.model.adapter_identity,
            left_configuration=left.model.runtime_id,
            right_configuration=right.model.runtime_id,
            left_model_id=left.model.provider_model_id,
            right_model_id=right.model.provider_model_id,
        )
    if left.suite.suite_fingerprint != right.suite.suite_fingerprint:
        return PairedComparison(
            comparable=False,
            reason="suite fingerprints differ; results are not directly comparable",
            left_adapter=left.model.adapter_identity,
            right_adapter=right.model.adapter_identity,
            left_configuration=left.model.runtime_id,
            right_configuration=right.model.runtime_id,
            left_model_id=left.model.provider_model_id,
            right_model_id=right.model.provider_model_id,
        )
    if left.config.comparable_key != right.config.comparable_key:
        return PairedComparison(
            comparable=False,
            reason="experiment config/instruction versions differ; not directly comparable",
            left_adapter=left.model.adapter_identity,
            right_adapter=right.model.adapter_identity,
            left_configuration=left.model.runtime_id,
            right_configuration=right.model.runtime_id,
            left_model_id=left.model.provider_model_id,
            right_model_id=right.model.provider_model_id,
        )
    right_by_id = {(item.scenario_id, item.version): item for item in right.summaries}
    paired: list[PairedScenarioObservation] = []
    for item in left.summaries:
        other = right_by_id.get((item.scenario_id, item.version))
        if other is None:
            return PairedComparison(
                comparable=False,
                reason=f"missing paired scenario {item.scenario_id}@{item.version}",
                left_adapter=left.model.adapter_identity,
                right_adapter=right.model.adapter_identity,
                left_configuration=left.model.runtime_id,
                right_configuration=right.model.runtime_id,
                left_model_id=left.model.provider_model_id,
                right_model_id=right.model.provider_model_id,
            )
        paired.append(
            PairedScenarioObservation(
                scenario_id=item.scenario_id,
                version=item.version,
                left=_summary_view(item),
                right=_summary_view(other),
            )
        )
    return PairedComparison(
        comparable=True,
        reason="same suite fingerprint, instruction identity, and runs_per_scenario",
        left_adapter=left.model.adapter_identity,
        right_adapter=right.model.adapter_identity,
        left_configuration=left.model.runtime_id,
        right_configuration=right.model.runtime_id,
        left_model_id=left.model.provider_model_id,
        right_model_id=right.model.provider_model_id,
        scenarios=tuple(paired),
    )


def _summary_view(summary: ScenarioRepeatSummary) -> dict[str, Any]:
    return {
        "attempted": summary.attempted,
        "completed": summary.completed,
        "provider_runtime_failures": summary.provider_runtime_failures,
        "provider_auth_failures": summary.provider_auth_failures,
        "provider_rate_limit_failures": summary.provider_rate_limit_failures,
        "provider_timeout_failures": summary.provider_timeout_failures,
        "research_quality_failures": summary.research_quality_failures,
        "structured_output_failures": summary.structured_output_failures,
        "hard_fail_occurrence": dict(summary.hard_fail_occurrence),
        "admission_distribution": dict(summary.admission_distribution),
        "exact_duplicate_rate": summary.exact_duplicate_rate,
        "unique_experiment_directions": summary.unique_experiment_directions,
    }


def format_experiment_scorecard(report: ExperimentReport) -> str:
    lines = [
        f"run_id: {report.run_id}",
        f"adapter: {report.model.adapter_identity}",
        f"configuration: {report.model.runtime_id or report.model.provider_adapter_identity}",
        f"model: {report.model.provider_model_id or 'unknown'}",
        f"configuration_fingerprint: {report.model.configuration_fingerprint or 'unknown'}",
        f"suite: {report.suite.suite_id} fingerprint={report.suite.suite_fingerprint[:12]}",
        f"scenarios: {report.suite.scenario_count}",
        f"runs_per_scenario: {report.config.runs_per_scenario}",
        f"git_commit: {report.git_commit}",
        "authoritative_real_model_comparison: "
        + ("yes" if report.authoritative_real_model_comparison else "no (single run)"),
        "no automatic winner",
        "hard-fail occurrence (not a success percentage):",
    ]
    any_fail = False
    for summary in report.summaries:
        if not summary.hard_fail_occurrence:
            continue
        any_fail = True
        lines.append(f"  {summary.scenario_id}@{summary.version}:")
        for code, fraction in summary.hard_fail_occurrence.items():
            lines.append(f"    {code}: {fraction}")
    if not any_fail:
        lines.append("  (none)")
    lines.append("admission distributions:")
    for summary in report.summaries:
        dist = ", ".join(
            f"{name}={count}" for name, count in sorted(summary.admission_distribution.items())
        )
        lines.append(f"  {summary.scenario_id}: {dist}")
    if report.holdout is not None:
        lines.append(
            "sealed holdout: "
            + ("available " + report.holdout.manifest.suite_fingerprint[:12] if report.holdout.available and report.holdout.manifest else report.holdout.reason)
        )
    lines.append(
        "harness invariant: " + ("FAIL leakage" if report.harness_invariant_failed else "PASS")
    )
    return "\n".join(lines)


def format_paired(comparison: PairedComparison) -> str:
    left_label = comparison.left_configuration or comparison.left_adapter
    right_label = comparison.right_configuration or comparison.right_adapter
    lines = [
        f"paired comparison: {left_label} ({comparison.left_model_id or comparison.left_adapter}) "
        f"vs {right_label} ({comparison.right_model_id or comparison.right_adapter})",
        f"comparable: {comparison.comparable}",
        f"reason: {comparison.reason}",
        "no automatic winner",
    ]
    if not comparison.comparable:
        return "\n".join(lines)
    for item in comparison.scenarios:
        lines.append(f"{item.scenario_id}@{item.version}:")
        lines.append(f"  {left_label}: {item.left}")
        lines.append(f"  {right_label}: {item.right}")
    return "\n".join(lines)


def write_immutable_report(directory: Path, report: ExperimentReport) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = report.created_at.replace(":", "").replace("+", "Z")
    path = directory / f"{stamp}_{report.run_id}.json"
    if path.exists():
        raise BenchmarkError(f"refusing to overwrite benchmark report: {path}")
    path.write_text(
        json.dumps(report.to_mapping(), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return path
