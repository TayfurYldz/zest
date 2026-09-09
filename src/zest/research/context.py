"""Deterministic Research Context Builder. No LLM. No embeddings. No vector search."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from zest.research.epistemic import EpistemicClass
from zest.research.types import ResearchInputError
from zest.safe_data import redact_secret_keys

DEFAULT_MAX_OBSERVATION_ITEMS = 8
DEFAULT_MAX_PRIOR_HYPOTHESIS_ITEMS = 8
DEFAULT_MAX_NEGATIVE_EVIDENCE_ITEMS = 8
DEFAULT_MAX_EXTERNAL_CONTENT_CHARACTERS = 2000
DEFAULT_MAX_ENGINE_SIGNAL_ITEMS = 32
DEFAULT_MAX_RESEARCH_OPPORTUNITY_ITEMS = 8

NEGATIVE_EXPERIMENT_STATES = frozenset(
    {"EXECUTION_FAILED", "BLOCKED", "BUDGET_EXHAUSTED"}
)

RESEARCH_QUESTION_ITEM_ID = "proc:research-question"


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value


def _require_positive_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ResearchInputError(f"{field_name} must be a positive int")
    return value


@dataclass(frozen=True)
class ContextBudget:
    """Explicit selection limits. Absence from context is not absence from SoR."""

    max_observation_items: int = DEFAULT_MAX_OBSERVATION_ITEMS
    max_prior_hypothesis_items: int = DEFAULT_MAX_PRIOR_HYPOTHESIS_ITEMS
    max_negative_evidence_items: int = DEFAULT_MAX_NEGATIVE_EVIDENCE_ITEMS
    max_external_content_characters: int = DEFAULT_MAX_EXTERNAL_CONTENT_CHARACTERS
    max_engine_signal_items: int = DEFAULT_MAX_ENGINE_SIGNAL_ITEMS
    max_research_opportunity_items: int = DEFAULT_MAX_RESEARCH_OPPORTUNITY_ITEMS

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_observation_items",
            _require_positive_int(self.max_observation_items, "max_observation_items"),
        )
        object.__setattr__(
            self,
            "max_prior_hypothesis_items",
            _require_positive_int(
                self.max_prior_hypothesis_items, "max_prior_hypothesis_items"
            ),
        )
        object.__setattr__(
            self,
            "max_negative_evidence_items",
            _require_positive_int(
                self.max_negative_evidence_items, "max_negative_evidence_items"
            ),
        )
        object.__setattr__(
            self,
            "max_external_content_characters",
            _require_positive_int(
                self.max_external_content_characters,
                "max_external_content_characters",
            ),
        )
        object.__setattr__(
            self,
            "max_engine_signal_items",
            _require_positive_int(self.max_engine_signal_items, "max_engine_signal_items"),
        )
        object.__setattr__(
            self,
            "max_research_opportunity_items",
            _require_positive_int(
                self.max_research_opportunity_items, "max_research_opportunity_items"
            ),
        )


@dataclass(frozen=True)
class ObservationSource:
    observation_id: str
    observation_kind: str
    payload: Mapping[str, Any]
    experiment_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observation_id", _require_text(self.observation_id, "observation_id")
        )
        object.__setattr__(
            self,
            "observation_kind",
            _require_text(self.observation_kind, "observation_kind"),
        )
        if not isinstance(self.payload, Mapping):
            raise ResearchInputError("payload must be a mapping")
        object.__setattr__(self, "payload", dict(self.payload))
        if self.experiment_id is not None:
            object.__setattr__(
                self, "experiment_id", _require_text(self.experiment_id, "experiment_id")
            )


@dataclass(frozen=True)
class HypothesisSource:
    hypothesis_id: str
    claim: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "hypothesis_id", _require_text(self.hypothesis_id, "hypothesis_id")
        )
        object.__setattr__(self, "claim", _require_text(self.claim, "claim"))


@dataclass(frozen=True)
class ExperimentSource:
    experiment_id: str
    hypothesis_id: str
    execution_state: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "experiment_id", _require_text(self.experiment_id, "experiment_id")
        )
        object.__setattr__(
            self, "hypothesis_id", _require_text(self.hypothesis_id, "hypothesis_id")
        )
        object.__setattr__(
            self,
            "execution_state",
            _require_text(self.execution_state, "execution_state"),
        )


@dataclass(frozen=True)
class ExternalContentSource:
    external_id: str
    content: str
    source_reference: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "external_id", _require_text(self.external_id, "external_id")
        )
        if not isinstance(self.content, str):
            raise ResearchInputError("content must be a string")
        object.__setattr__(
            self,
            "source_reference",
            _require_text(self.source_reference, "source_reference"),
        )


@dataclass(frozen=True)
class InferenceSource:
    """Target-model inference. Never an Observation."""

    inference_id: str
    statement: str
    source_references: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "inference_id", _require_text(self.inference_id, "inference_id")
        )
        object.__setattr__(self, "statement", _require_text(self.statement, "statement"))
        if not isinstance(self.source_references, tuple):
            raise ResearchInputError("source_references must be a tuple")


@dataclass(frozen=True)
class ChainContextSource:
    """Deterministic chain hypothesis. Not an exploit and not Evidence."""

    chain_id: str
    statement: str
    source_references: tuple[str, ...]
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "chain_id", _require_text(self.chain_id, "chain_id"))
        object.__setattr__(self, "statement", _require_text(self.statement, "statement"))
        if not isinstance(self.source_references, tuple):
            raise ResearchInputError("source_references must be a tuple")
        if not isinstance(self.payload, Mapping):
            raise ResearchInputError("payload must be a mapping")
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True)
class InvariantContextSource:
    """Expected-behavior hypothesis. Never an Observation and never a ScopeRule."""

    invariant_id: str
    statement: str
    source_references: tuple[str, ...]
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "invariant_id", _require_text(self.invariant_id, "invariant_id")
        )
        object.__setattr__(self, "statement", _require_text(self.statement, "statement"))
        if not isinstance(self.source_references, tuple):
            raise ResearchInputError("source_references must be a tuple")
        if not isinstance(self.payload, Mapping):
            raise ResearchInputError("payload must be a mapping")
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True)
class DifferentialContextSource:
    """Deterministic comparison result. Not Evidence and not a vulnerability."""

    differential_id: str
    statement: str
    source_references: tuple[str, ...]
    interpretation: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "differential_id", _require_text(self.differential_id, "differential_id")
        )
        object.__setattr__(self, "statement", _require_text(self.statement, "statement"))
        if not isinstance(self.source_references, tuple):
            raise ResearchInputError("source_references must be a tuple")
        object.__setattr__(
            self, "interpretation", _require_text(self.interpretation, "interpretation")
        )
        if not isinstance(self.payload, Mapping):
            raise ResearchInputError("payload must be a mapping")
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True)
class OpportunityContextSource:
    """Selected research direction. Not Hypothesis truth and not authorization."""

    opportunity_id: str
    statement: str
    source_references: tuple[str, ...]
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "opportunity_id", _require_text(self.opportunity_id, "opportunity_id")
        )
        object.__setattr__(self, "statement", _require_text(self.statement, "statement"))
        if not isinstance(self.source_references, tuple):
            raise ResearchInputError("source_references must be a tuple")
        if not isinstance(self.payload, Mapping):
            raise ResearchInputError("payload must be a mapping")
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True)
class EngineSignalSource:
    """Bounded engine inventory signal. Not Observation truth and not a Finding."""

    signal_id: str
    engine: str
    statement: str
    source_references: tuple[str, ...]
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "signal_id", _require_text(self.signal_id, "signal_id"))
        object.__setattr__(self, "engine", _require_text(self.engine, "engine"))
        object.__setattr__(self, "statement", _require_text(self.statement, "statement"))
        if not isinstance(self.source_references, tuple):
            raise ResearchInputError("source_references must be a tuple")
        if not isinstance(self.payload, Mapping):
            raise ResearchInputError("payload must be a mapping")
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True)
class ChangeEventContextSource:
    """Deterministic temporal delta. Not Evidence and not a vulnerability."""

    change_event_id: str
    statement: str
    source_references: tuple[str, ...]
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "change_event_id", _require_text(self.change_event_id, "change_event_id")
        )
        object.__setattr__(self, "statement", _require_text(self.statement, "statement"))
        if not isinstance(self.source_references, tuple):
            raise ResearchInputError("source_references must be a tuple")
        if not isinstance(self.payload, Mapping):
            raise ResearchInputError("payload must be a mapping")
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True)
class ContextItem:
    """One bounded, sourced context item. Not Evidence and not a Finding."""

    item_id: str
    epistemic_class: EpistemicClass
    statement: str
    source_references: tuple[str, ...]
    may_issue_instructions: bool = False
    truncated: bool = False
    omitted_characters: int = 0
    payload: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_id", _require_text(self.item_id, "item_id"))
        if not isinstance(self.epistemic_class, EpistemicClass):
            raise ResearchInputError("epistemic_class must be an EpistemicClass")
        if not isinstance(self.statement, str):
            raise ResearchInputError("statement must be a string")
        if not isinstance(self.source_references, tuple):
            raise ResearchInputError("source_references must be a tuple")
        if self.may_issue_instructions:
            raise ResearchInputError("context items cannot issue instructions")
        if self.omitted_characters < 0:
            raise ResearchInputError("omitted_characters must be >= 0")
        if self.payload is not None:
            if not isinstance(self.payload, Mapping):
                raise ResearchInputError("payload must be a mapping")
            object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True)
class ContextOmission:
    """Explicit record of what the builder left out. Not a silent truncate."""

    omitted_observation_ids: tuple[str, ...]
    omitted_hypothesis_ids: tuple[str, ...]
    omitted_negative_evidence_ids: tuple[str, ...]
    omitted_external_ids: tuple[str, ...]
    truncated_external_ids: tuple[str, ...]
    omitted_engine_signal_ids: tuple[str, ...] = ()
    omitted_opportunity_ids: tuple[str, ...] = ()

    @property
    def is_partial(self) -> bool:
        return bool(
            self.omitted_observation_ids
            or self.omitted_hypothesis_ids
            or self.omitted_negative_evidence_ids
            or self.omitted_external_ids
            or self.truncated_external_ids
            or self.omitted_engine_signal_ids
            or self.omitted_opportunity_ids
        )


@dataclass(frozen=True)
class ResearchContext:
    """Typed epistemic context for one bounded reasoning invocation."""

    research_run_id: str
    research_question: str
    fingerprint: str
    budget: ContextBudget
    authoritative_facts: tuple[ContextItem, ...]
    observations: tuple[ContextItem, ...]
    deterministic_derivations: tuple[ContextItem, ...]
    inferences: tuple[ContextItem, ...]
    prior_hypotheses: tuple[ContextItem, ...]
    negative_evidence: tuple[ContextItem, ...]
    procedural_context: tuple[ContextItem, ...]
    unresolved_questions: tuple[str, ...]
    untrusted_external_content: tuple[ContextItem, ...]
    omission: ContextOmission
    invariant_hypotheses: tuple[ContextItem, ...] = ()
    chain_hypotheses: tuple[ContextItem, ...] = ()
    research_opportunities: tuple[ContextItem, ...] = ()
    change_events: tuple[ContextItem, ...] = ()
    engine_signals: tuple[ContextItem, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "research_run_id",
            _require_text(self.research_run_id, "research_run_id"),
        )
        object.__setattr__(
            self,
            "research_question",
            _require_text(self.research_question, "research_question"),
        )
        object.__setattr__(
            self, "fingerprint", _require_text(self.fingerprint, "fingerprint")
        )

    @property
    def is_partial(self) -> bool:
        return self.omission.is_partial

    def all_items(self) -> tuple[ContextItem, ...]:
        return (
            self.authoritative_facts
            + self.observations
            + self.deterministic_derivations
            + self.inferences
            + self.prior_hypotheses
            + self.invariant_hypotheses
            + self.chain_hypotheses
            + self.research_opportunities
            + self.change_events
            + self.engine_signals
            + self.negative_evidence
            + self.procedural_context
            + self.untrusted_external_content
        )

    def resolvable_source_ids(self) -> frozenset[str]:
        return frozenset(item.item_id for item in self.all_items())

    def item_by_id(self, item_id: str) -> ContextItem | None:
        for item in self.all_items():
            if item.item_id == item_id:
                return item
        return None


def _sorted_take(
    items: tuple[Any, ...], limit: int, identity
) -> tuple[tuple[Any, ...], tuple[str, ...]]:
    ordered = tuple(sorted(items, key=identity))
    kept = ordered[:limit]
    omitted = tuple(identity(item) for item in ordered[limit:])
    return kept, omitted


def _truncate_text(text: str, limit: int) -> tuple[str, int, bool]:
    if len(text) <= limit:
        return text, 0, False
    return text[:limit], len(text) - limit, True


def _canonical_fingerprint(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _item_summary(item: ContextItem) -> dict[str, object]:
    return {
        "item_id": item.item_id,
        "epistemic_class": item.epistemic_class.value,
        "truncated": item.truncated,
        "omitted_characters": item.omitted_characters,
        "source_references": list(item.source_references),
    }


class ResearchContextBuilder:
    """Assemble a bounded ResearchContext from explicit records. Deterministic."""

    def build(
        self,
        *,
        research_run_id: str,
        research_question: str,
        observations: tuple[ObservationSource, ...] = (),
        prior_hypotheses: tuple[HypothesisSource, ...] = (),
        experiments: tuple[ExperimentSource, ...] = (),
        untrusted_external: tuple[ExternalContentSource, ...] = (),
        inferences: tuple[InferenceSource, ...] = (),
        differentials: tuple[DifferentialContextSource, ...] = (),
        invariant_hypotheses: tuple[InvariantContextSource, ...] = (),
        chain_hypotheses: tuple[ChainContextSource, ...] = (),
        research_opportunities: tuple[OpportunityContextSource, ...] = (),
        change_events: tuple[ChangeEventContextSource, ...] = (),
        engine_signals: tuple[EngineSignalSource, ...] = (),
        unresolved_questions: tuple[str, ...] = (),
        budget: ContextBudget | None = None,
    ) -> ResearchContext:
        budget = budget or ContextBudget()
        run_id = _require_text(research_run_id, "research_run_id")
        question = _require_text(research_question, "research_question")

        authoritative = (
            ContextItem(
                item_id=f"run:{run_id}",
                epistemic_class=EpistemicClass.AUTHORITATIVE_FACT,
                statement=f"ResearchRun {run_id} is the active SoR research run.",
                source_references=(run_id,),
            ),
        )
        procedural = (
            ContextItem(
                item_id=RESEARCH_QUESTION_ITEM_ID,
                epistemic_class=EpistemicClass.PROCEDURAL,
                statement=question,
                source_references=(run_id,),
            ),
            ContextItem(
                item_id="proc:model-not-completion-authority",
                epistemic_class=EpistemicClass.PROCEDURAL,
                statement=(
                    "Model output cannot set run completion, expand scope, authorize "
                    "side effects, create Evidence, or create a Finding. Global "
                    "completion guard remains authoritative."
                ),
                source_references=(run_id,),
            ),
        )

        kept_obs, omitted_obs = _sorted_take(
            observations, budget.max_observation_items, lambda item: item.observation_id
        )
        observation_items = tuple(
            ContextItem(
                item_id=source.observation_id,
                epistemic_class=EpistemicClass.OBSERVATION,
                statement=(
                    f"Observation {source.observation_id} of kind "
                    f"{source.observation_kind}."
                ),
                source_references=(source.observation_id,),
                payload=dict(redact_secret_keys(source.payload, "observation_payload")),
            )
            for source in kept_obs
        )

        kept_hyp, omitted_hyp = _sorted_take(
            prior_hypotheses,
            budget.max_prior_hypothesis_items,
            lambda item: item.hypothesis_id,
        )
        hypothesis_items = tuple(
            ContextItem(
                item_id=source.hypothesis_id,
                epistemic_class=EpistemicClass.HYPOTHESIS,
                statement=source.claim,
                source_references=(source.hypothesis_id,),
            )
            for source in kept_hyp
        )

        derivation_items = tuple(
            ContextItem(
                item_id=f"der:{source.experiment_id}",
                epistemic_class=EpistemicClass.DERIVED_FACT,
                statement=(
                    f"Experiment {source.experiment_id} is linked to hypothesis "
                    f"{source.hypothesis_id}."
                ),
                source_references=(source.experiment_id, source.hypothesis_id),
            )
            for source in sorted(experiments, key=lambda item: item.experiment_id)
        ) + tuple(
            ContextItem(
                item_id=source.differential_id,
                epistemic_class=EpistemicClass.DERIVED_FACT,
                statement=source.statement,
                source_references=source.source_references,
                payload={
                    **dict(source.payload),
                    "interpretation": source.interpretation,
                    "not_evidence": True,
                    "not_candidate": True,
                    "not_finding": True,
                    "not_a_vulnerability": True,
                    "not_authorization_proof": True,
                },
            )
            for source in sorted(differentials, key=lambda item: item.differential_id)
        )

        inference_items = tuple(
            ContextItem(
                item_id=source.inference_id,
                epistemic_class=EpistemicClass.INFERRED,
                statement=source.statement,
                source_references=source.source_references,
                payload={"not_an_observation": True, "not_a_fact": True},
            )
            for source in sorted(inferences, key=lambda item: item.inference_id)
        )

        invariant_items = tuple(
            ContextItem(
                item_id=source.invariant_id,
                epistemic_class=EpistemicClass.HYPOTHESIS,
                statement=source.statement,
                source_references=source.source_references,
                payload={
                    **dict(source.payload),
                    "not_a_fact": True,
                    "not_an_observation": True,
                    "not_authorization": True,
                    "not_a_vulnerability": True,
                },
            )
            for source in sorted(invariant_hypotheses, key=lambda item: item.invariant_id)
        )
        chain_items = tuple(
            ContextItem(
                item_id=source.chain_id,
                epistemic_class=EpistemicClass.HYPOTHESIS,
                statement=source.statement,
                source_references=source.source_references,
                payload={
                    **dict(source.payload),
                    "not_an_exploit": True,
                    "not_evidence": True,
                    "not_candidate": True,
                    "not_finding": True,
                    "not_causality_proof": True,
                },
            )
            for source in sorted(chain_hypotheses, key=lambda item: item.chain_id)
        )
        kept_opp, omitted_opp = _sorted_take(
            research_opportunities,
            budget.max_research_opportunity_items,
            lambda item: item.opportunity_id,
        )
        opportunity_items = tuple(
            ContextItem(
                item_id=source.opportunity_id,
                epistemic_class=EpistemicClass.HYPOTHESIS,
                statement=source.statement,
                source_references=source.source_references,
                payload={
                    **dict(redact_secret_keys(source.payload, "opportunity_payload")),
                    "not_hypothesis_truth": True,
                    "not_authorization": True,
                    "not_a_vulnerability": True,
                    "not_evidence": True,
                },
            )
            for source in kept_opp
        )
        kept_eng, omitted_eng = _sorted_take(
            engine_signals,
            budget.max_engine_signal_items,
            lambda item: item.signal_id,
        )
        engine_signal_items = tuple(
            ContextItem(
                item_id=source.signal_id,
                epistemic_class=(
                    EpistemicClass.AUTHORITATIVE_FACT
                    if source.engine in {"AUTHORITY", "TARGET_SCOPE"}
                    else EpistemicClass.DERIVED_FACT
                ),
                statement=source.statement,
                source_references=source.source_references,
                payload={
                    **dict(redact_secret_keys(source.payload, "engine_signal_payload")),
                    "engine": source.engine,
                    "not_a_vulnerability": True,
                    "not_finding": True,
                    "not_model_truth": True,
                },
            )
            for source in kept_eng
        )
        change_items = tuple(
            ContextItem(
                item_id=source.change_event_id,
                epistemic_class=EpistemicClass.DERIVED_FACT,
                statement=source.statement,
                source_references=source.source_references,
                payload={
                    **dict(source.payload),
                    "not_a_vulnerability": True,
                    "not_evidence": True,
                    "not_candidate": True,
                    "not_finding": True,
                },
            )
            for source in sorted(change_events, key=lambda item: item.change_event_id)
        )

        negative_sources = tuple(
            experiment
            for experiment in experiments
            if experiment.execution_state in NEGATIVE_EXPERIMENT_STATES
        )
        kept_neg, omitted_neg = _sorted_take(
            negative_sources,
            budget.max_negative_evidence_items,
            lambda item: item.experiment_id,
        )
        negative_items = tuple(
            ContextItem(
                item_id=f"neg:{source.experiment_id}",
                epistemic_class=EpistemicClass.NEGATIVE_EVIDENCE,
                statement=(
                    f"Experiment {source.experiment_id} ended in "
                    f"{source.execution_state} under this research run. "
                    "This is not a Hypothesis verdict."
                ),
                source_references=(source.experiment_id, source.hypothesis_id),
                payload={
                    "experiment_id": source.experiment_id,
                    "hypothesis_id": source.hypothesis_id,
                    "execution_state": source.execution_state,
                    "context_identity": run_id,
                },
            )
            for source in kept_neg
        )

        # Character budget bounds untrusted text. Extra items are omitted explicitly.
        kept_ext = tuple(sorted(untrusted_external, key=lambda item: item.external_id))
        omitted_ext_ids: list[str] = []
        truncated_ids: list[str] = []
        external_items: list[ContextItem] = []
        remaining_chars = budget.max_external_content_characters
        for source in kept_ext:
            if remaining_chars <= 0:
                omitted_ext_ids.append(source.external_id)
                continue
            text, omitted_chars, truncated = _truncate_text(source.content, remaining_chars)
            remaining_chars -= len(text)
            if truncated:
                truncated_ids.append(source.external_id)
            external_items.append(
                ContextItem(
                    item_id=f"ext:{source.external_id}",
                    epistemic_class=EpistemicClass.UNTRUSTED_EXTERNAL,
                    statement=text,
                    source_references=(source.source_reference,),
                    truncated=truncated,
                    omitted_characters=omitted_chars,
                    payload={
                        "untrusted": True,
                        "instruction_authority": False,
                        "source_reference": source.source_reference,
                    },
                )
            )

        unresolved = tuple(
            _require_text(question_text, "unresolved_questions")
            for question_text in unresolved_questions
        )

        omission = ContextOmission(
            omitted_observation_ids=omitted_obs,
            omitted_hypothesis_ids=omitted_hyp,
            omitted_negative_evidence_ids=tuple(f"neg:{item_id}" for item_id in omitted_neg),
            omitted_external_ids=tuple(omitted_ext_ids),
            truncated_external_ids=tuple(truncated_ids),
            omitted_engine_signal_ids=omitted_eng,
            omitted_opportunity_ids=omitted_opp,
        )
        context_without_fingerprint = {
            "research_run_id": run_id,
            "research_question": question,
            "budget": {
                "max_observation_items": budget.max_observation_items,
                "max_prior_hypothesis_items": budget.max_prior_hypothesis_items,
                "max_negative_evidence_items": budget.max_negative_evidence_items,
                "max_external_content_characters": budget.max_external_content_characters,
                "max_engine_signal_items": budget.max_engine_signal_items,
                "max_research_opportunity_items": budget.max_research_opportunity_items,
            },
            "authoritative_facts": [_item_summary(item) for item in authoritative],
            "observations": [_item_summary(item) for item in observation_items],
            "deterministic_derivations": [_item_summary(item) for item in derivation_items],
            "inferences": [_item_summary(item) for item in inference_items],
            "prior_hypotheses": [_item_summary(item) for item in hypothesis_items],
            "invariant_hypotheses": [_item_summary(item) for item in invariant_items],
            "chain_hypotheses": [_item_summary(item) for item in chain_items],
            "research_opportunities": [_item_summary(item) for item in opportunity_items],
            "change_events": [_item_summary(item) for item in change_items],
            "engine_signals": [_item_summary(item) for item in engine_signal_items],
            "negative_evidence": [_item_summary(item) for item in negative_items],
            "procedural_context": [_item_summary(item) for item in procedural],
            "untrusted_external_content": [_item_summary(item) for item in external_items],
            "unresolved_questions": list(unresolved),
            "omission": {
                "omitted_observation_ids": list(omission.omitted_observation_ids),
                "omitted_hypothesis_ids": list(omission.omitted_hypothesis_ids),
                "omitted_negative_evidence_ids": list(
                    omission.omitted_negative_evidence_ids
                ),
                "omitted_external_ids": list(omission.omitted_external_ids),
                "truncated_external_ids": list(omission.truncated_external_ids),
                "omitted_engine_signal_ids": list(omission.omitted_engine_signal_ids),
                "omitted_opportunity_ids": list(omission.omitted_opportunity_ids),
            },
        }
        return ResearchContext(
            research_run_id=run_id,
            research_question=question,
            fingerprint=_canonical_fingerprint(context_without_fingerprint),
            budget=budget,
            authoritative_facts=authoritative,
            observations=observation_items,
            deterministic_derivations=derivation_items,
            inferences=inference_items,
            prior_hypotheses=hypothesis_items,
            invariant_hypotheses=invariant_items,
            chain_hypotheses=chain_items,
            research_opportunities=opportunity_items,
            change_events=change_items,
            engine_signals=engine_signal_items,
            negative_evidence=negative_items,
            procedural_context=procedural,
            unresolved_questions=unresolved,
            untrusted_external_content=tuple(external_items),
            omission=omission,
        )
