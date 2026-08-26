"""Admit RouteTemplate inferences from ExactPath facts. Never OBSERVED."""

from __future__ import annotations

from collections import defaultdict

from zest.research.discovery.canonical import (
    admit_route_templates,
    canonical_key,
)
from zest.research.discovery.facts import DiscoveryFact
from zest.research.discovery.inference import (
    DiscoveryInferenceDraft,
    admit_discovery_inference,
)
from zest.research.discovery.types import DiscoveryFactKind, DiscoveryInferenceKind
from zest.research.target_model import TargetEpistemicStatus


def route_template_drafts_from_facts(
    facts: tuple[DiscoveryFact, ...],
    *,
    research_run_id: str,
    identity_id: str,
) -> tuple[DiscoveryInferenceDraft, ...]:
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    fact_ids_by_path: dict[tuple[str, str, str], str] = {}
    for fact in facts:
        if fact.fact_kind is not DiscoveryFactKind.EXACT_PATH:
            continue
        if not fact.normalized_origin or not fact.normalized_path:
            continue
        method = fact.http_method or "GET"
        grouped[(fact.normalized_origin, method)].append(fact.normalized_path)
        fact_ids_by_path[(fact.normalized_origin, method, fact.normalized_path)] = fact.fact_id
    drafts: list[DiscoveryInferenceDraft] = []
    for (origin, method), paths in grouped.items():
        for admission in admit_route_templates(
            origin=origin, http_method=method, exact_paths=tuple(paths)
        ):
            source_ids = tuple(
                fact_ids_by_path[(origin, method, path)] for path in admission.exact_paths
            )
            drafts.append(
                DiscoveryInferenceDraft(
                    research_run_id=research_run_id,
                    inference_kind=DiscoveryInferenceKind.ROUTE_TEMPLATE,
                    canonical_key=canonical_key(
                        "ROUTE_TEMPLATE", origin, method, admission.template_path
                    ),
                    epistemic_status=TargetEpistemicStatus.INFERRED,
                    identity_id=identity_id,
                    source_run_ids=(research_run_id,),
                    source_fact_ids=source_ids,
                    attributes={
                        "template_path": admission.template_path,
                        "http_method": method,
                        "origin": origin,
                        "exact_paths": list(admission.exact_paths),
                        "token_kind": admission.token_kind,
                    },
                )
            )
    return tuple(drafts)


def admit_route_template_inferences(
    facts: tuple[DiscoveryFact, ...],
    *,
    research_run_id: str,
    identity_id: str,
    allocate_id,
) -> tuple:
    admitted = []
    for draft in route_template_drafts_from_facts(
        facts, research_run_id=research_run_id, identity_id=identity_id
    ):
        decision = admit_discovery_inference(draft, inference_id=allocate_id(draft.canonical_key))
        if decision.admitted and decision.inference is not None:
            admitted.append(decision.inference)
    return tuple(admitted)
