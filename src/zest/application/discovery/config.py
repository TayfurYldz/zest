"""Persist and resume DiscoveryRunConfig. Mismatch fails closed. No widening."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import OrchestrationIntegrityError
from zest.data.records import DiscoveryRunConfigRecord
from zest.research.discovery.config import DiscoveryBounds, DiscoveryRunConfig


@dataclass(frozen=True)
class EffectiveDiscoveryConfiguration:
    config: DiscoveryRunConfig
    fingerprint: str


def record_from_config(config: DiscoveryRunConfig, *, created_at) -> DiscoveryRunConfigRecord:
    bounds = config.bounds
    return DiscoveryRunConfigRecord(
        research_run_id=config.research_run_id,
        strategy_version=config.strategy_version,
        seed_target_reference=config.seed_target_reference,
        normalized_origin=config.normalized_origin,
        normalized_path=config.normalized_path,
        max_discovery_cycles=bounds.max_discovery_cycles,
        max_frontier_items=bounds.max_frontier_items,
        max_new_facts_per_cycle=bounds.max_new_facts_per_cycle,
        max_browser_actions=bounds.max_browser_actions,
        max_http_transactions=bounds.max_http_transactions,
        max_per_route_revisit=bounds.max_per_route_revisit,
        max_identity_variants=bounds.max_identity_variants,
        max_transition_depth=bounds.max_transition_depth,
        max_graph_depth_from_seed=bounds.max_graph_depth_from_seed,
        max_template_inference_fanout=bounds.max_template_inference_fanout,
        max_duplicate_observations=bounds.max_duplicate_observations,
        configuration_fingerprint=config.fingerprint(),
        created_at=created_at,
    )


def config_from_record(record: DiscoveryRunConfigRecord) -> DiscoveryRunConfig:
    config = DiscoveryRunConfig(
        research_run_id=record.research_run_id,
        seed_target_reference=record.seed_target_reference,
        normalized_origin=record.normalized_origin,
        normalized_path=record.normalized_path,
        bounds=DiscoveryBounds(
            max_discovery_cycles=record.max_discovery_cycles,
            max_frontier_items=record.max_frontier_items,
            max_new_facts_per_cycle=record.max_new_facts_per_cycle,
            max_browser_actions=record.max_browser_actions,
            max_http_transactions=record.max_http_transactions,
            max_per_route_revisit=record.max_per_route_revisit,
            max_identity_variants=record.max_identity_variants,
            max_transition_depth=record.max_transition_depth,
            max_graph_depth_from_seed=record.max_graph_depth_from_seed,
            max_template_inference_fanout=record.max_template_inference_fanout,
            max_duplicate_observations=record.max_duplicate_observations,
        ),
        strategy_version=record.strategy_version,
    )
    if config.fingerprint() != record.configuration_fingerprint:
        raise OrchestrationIntegrityError("discovery configuration fingerprint mismatch")
    return config


def assert_runtime_matches_persisted(
    persisted: DiscoveryRunConfig, runtime: DiscoveryRunConfig
) -> None:
    if persisted.fingerprint() != runtime.fingerprint():
        raise OrchestrationIntegrityError(
            "runtime discovery config does not match persisted config; widening is denied"
        )
    if persisted.bounds != runtime.bounds:
        raise OrchestrationIntegrityError("runtime discovery bounds do not match persisted bounds")
