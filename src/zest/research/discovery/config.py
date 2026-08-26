"""Immutable discovery run configuration. Fingerprint is integrity, not authorization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

from zest.research.discovery.types import SURFACE_DISCOVERY_STRATEGY_VERSION
from zest.research.types import ResearchInputError

IMMUTABLE_DISCOVERY_CONFIG_KEYS = (
    "research_run_id",
    "strategy_version",
    "seed_target_reference",
    "normalized_origin",
    "normalized_path",
    "max_discovery_cycles",
    "max_frontier_items",
    "max_new_facts_per_cycle",
    "max_browser_actions",
    "max_http_transactions",
    "max_per_route_revisit",
    "max_identity_variants",
    "max_transition_depth",
    "max_graph_depth_from_seed",
    "max_template_inference_fanout",
    "max_duplicate_observations",
)


def _require_non_negative(name: str, value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ResearchInputError(f"{name} must be >= 0; 0 means no allowance")
    return value


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class DiscoveryBounds:
    """Hard discovery limits. 0 = no allowance. Negative is invalid."""

    max_discovery_cycles: int
    max_frontier_items: int
    max_new_facts_per_cycle: int
    max_browser_actions: int
    max_http_transactions: int
    max_per_route_revisit: int
    max_identity_variants: int
    max_transition_depth: int
    max_graph_depth_from_seed: int
    max_template_inference_fanout: int
    max_duplicate_observations: int

    def __post_init__(self) -> None:
        _require_non_negative("max_discovery_cycles", self.max_discovery_cycles)
        _require_non_negative("max_frontier_items", self.max_frontier_items)
        _require_non_negative("max_new_facts_per_cycle", self.max_new_facts_per_cycle)
        _require_non_negative("max_browser_actions", self.max_browser_actions)
        _require_non_negative("max_http_transactions", self.max_http_transactions)
        _require_non_negative("max_per_route_revisit", self.max_per_route_revisit)
        _require_non_negative("max_identity_variants", self.max_identity_variants)
        _require_non_negative("max_transition_depth", self.max_transition_depth)
        _require_non_negative("max_graph_depth_from_seed", self.max_graph_depth_from_seed)
        _require_non_negative(
            "max_template_inference_fanout", self.max_template_inference_fanout
        )
        _require_non_negative("max_duplicate_observations", self.max_duplicate_observations)


@dataclass(frozen=True)
class DiscoveryRunConfig:
    research_run_id: str
    seed_target_reference: str
    normalized_origin: str
    normalized_path: str
    bounds: DiscoveryBounds
    strategy_version: str = SURFACE_DISCOVERY_STRATEGY_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        object.__setattr__(
            self,
            "seed_target_reference",
            _require_text(self.seed_target_reference, "seed_target_reference"),
        )
        object.__setattr__(
            self,
            "normalized_origin",
            _require_text(self.normalized_origin, "normalized_origin"),
        )
        object.__setattr__(
            self, "normalized_path", _require_text(self.normalized_path, "normalized_path")
        )
        if not isinstance(self.bounds, DiscoveryBounds):
            raise ResearchInputError("bounds must be DiscoveryBounds")
        object.__setattr__(
            self, "strategy_version", _require_text(self.strategy_version, "strategy_version")
        )
        if self.strategy_version != SURFACE_DISCOVERY_STRATEGY_VERSION:
            raise ResearchInputError("strategy_version must be surface.discovery.v1")

    def config_payload(self) -> dict[str, object]:
        return {
            "research_run_id": self.research_run_id,
            "strategy_version": self.strategy_version,
            "seed_target_reference": self.seed_target_reference,
            "normalized_origin": self.normalized_origin,
            "normalized_path": self.normalized_path,
            "max_discovery_cycles": self.bounds.max_discovery_cycles,
            "max_frontier_items": self.bounds.max_frontier_items,
            "max_new_facts_per_cycle": self.bounds.max_new_facts_per_cycle,
            "max_browser_actions": self.bounds.max_browser_actions,
            "max_http_transactions": self.bounds.max_http_transactions,
            "max_per_route_revisit": self.bounds.max_per_route_revisit,
            "max_identity_variants": self.bounds.max_identity_variants,
            "max_transition_depth": self.bounds.max_transition_depth,
            "max_graph_depth_from_seed": self.bounds.max_graph_depth_from_seed,
            "max_template_inference_fanout": self.bounds.max_template_inference_fanout,
            "max_duplicate_observations": self.bounds.max_duplicate_observations,
        }

    def fingerprint(self) -> str:
        return discovery_config_fingerprint(self.config_payload())


def canonical_discovery_config(payload: Mapping[str, object]) -> dict[str, object]:
    missing = [key for key in IMMUTABLE_DISCOVERY_CONFIG_KEYS if key not in payload]
    if missing:
        raise ResearchInputError(f"discovery config missing keys: {missing}")
    canonical: dict[str, object] = {}
    for key in IMMUTABLE_DISCOVERY_CONFIG_KEYS:
        canonical[key] = payload[key]
    return canonical


def discovery_config_fingerprint(payload: Mapping[str, object]) -> str:
    canonical = canonical_discovery_config(payload)
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
