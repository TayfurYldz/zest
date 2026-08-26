"""Deterministic admission of SensorObservation into DiscoveryFact.

This is not model inference. It enforces the epistemic boundary:
- FORBIDDEN_DISCOVERY_KEYS are rejected.
- Epistemic status is capped at OBSERVED.
- Source is marked UNTRUSTED_EXTERNAL.
- Each admitted fact carries an admission receipt provenance record.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from zest.application.identity import new_opaque_id
from zest.application.ports import UnitOfWorkFactory
from zest.core.enums import ScopeClassification
from zest.data.records import DiscoveryFactRecord, DiscoveryFactSourceRecord
from zest.research.discovery.facts import DiscoveryFact, DiscoveryFactSourceView
from zest.research.discovery.types import (
    FORBIDDEN_DISCOVERY_KEYS,
    DiscoveryFactKind,
    DiscoverySourcePlane,
)
from zest.research.sensor import SensorObservation
from zest.research.target_model import TargetEpistemicStatus


class SensorAdmissionError(Exception):
    """Observation rejected from fact admission."""


@dataclass(frozen=True)
class SensorAdmissionResult:
    fact_id: str
    observation_id: str


class AdmitSensorObservations:
    """Admit one SensorObservation as one DiscoveryFact."""

    _SENSOR_FACT_KIND: dict[str, DiscoveryFactKind] = {
        "sensor.dns": DiscoveryFactKind.HOSTNAME,
        "sensor.ctlog": DiscoveryFactKind.HOSTNAME,
        "sensor.archive": DiscoveryFactKind.EXACT_PATH,
        "sensor.cert": DiscoveryFactKind.CERT,
        "sensor.techfp": DiscoveryFactKind.TECH,
    }

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(
        self,
        observation: SensorObservation,
        *,
        research_run_id: str,
        identity_id: str = "ANONYMOUS",
        scope_classification: str,
    ) -> SensorAdmissionResult:
        try:
            ScopeClassification(scope_classification)
        except ValueError as exc:
            raise SensorAdmissionError(
                f"scope_classification must be one of {', '.join(item.value for item in ScopeClassification)}"
            ) from exc

        with self._uow_factory.open() as uow:
            result = self.admit_in_uow(
                uow,
                observation,
                research_run_id=research_run_id,
                identity_id=identity_id,
                scope_classification=scope_classification,
            )
            uow.commit()

        return result

    def admit_in_uow(
        self,
        uow: Any,
        observation: SensorObservation,
        *,
        research_run_id: str,
        identity_id: str,
        scope_classification: str,
        fact_id: str | None = None,
        source_row_id: str | None = None,
        canonical_key: str | None = None,
        extra_attributes: Mapping[str, Any] | None = None,
    ) -> SensorAdmissionResult:
        """Persist a deterministic sensor admission in a caller UoW."""
        try:
            ScopeClassification(scope_classification)
        except ValueError as exc:
            raise SensorAdmissionError(
                f"scope_classification must be one of {', '.join(item.value for item in ScopeClassification)}"
            ) from exc
        payload = dict(observation.payload)
        source_metadata = dict(observation.source_metadata)
        self._reject_forbidden(payload, "payload")
        self._reject_forbidden(source_metadata, "source_metadata")

        fact_kind = self._SENSOR_FACT_KIND.get(
            observation.sensor_id, DiscoveryFactKind.HOSTNAME
        )
        attributes: dict[str, Any] = {
            "sensor_id": observation.sensor_id,
            "scope_classification": scope_classification,
            "source_status": TargetEpistemicStatus.UNTRUSTED_EXTERNAL.value,
            "admitted_at": observation.collected_at.isoformat(),
        }
        if fact_kind is DiscoveryFactKind.TECH:
            technologies = payload.get("technologies")
            if isinstance(technologies, list) and technologies:
                first = technologies[0]
                if isinstance(first, dict) and isinstance(first.get("name"), str):
                    attributes["technology"] = first["name"]
        if extra_attributes:
            self._reject_forbidden(extra_attributes, "extra_attributes")
            attributes.update(dict(extra_attributes))

        domain_fact = DiscoveryFact(
            fact_id=fact_id or new_opaque_id(),
            research_run_id=research_run_id,
            fact_kind=fact_kind,
            canonical_key=canonical_key or self._canonical_key(observation, fact_kind),
            epistemic_status=TargetEpistemicStatus.OBSERVED,
            identity_id=identity_id,
            target_reference=observation.target_reference,
            sources=(
                DiscoveryFactSourceView(
                    source_plane=DiscoverySourcePlane.OBSERVATION,
                    sensor_observation_id=observation.observation_id,
                ),
            ),
            normalized_origin=observation.target_reference,
            attributes=attributes,
        )
        uow.discovery_facts.insert(
            DiscoveryFactRecord(
                fact_id=domain_fact.fact_id,
                research_run_id=domain_fact.research_run_id,
                fact_kind=domain_fact.fact_kind.value,
                canonical_key=domain_fact.canonical_key,
                epistemic_status=domain_fact.epistemic_status.value,
                identity_id=domain_fact.identity_id,
                target_reference=domain_fact.target_reference,
                session_context_id=domain_fact.session_context_id,
                normalized_origin=domain_fact.normalized_origin,
                normalized_path=domain_fact.normalized_path,
                http_method=domain_fact.http_method,
                attributes=dict(domain_fact.attributes),
                created_at=observation.collected_at,
            )
        )
        uow.discovery_fact_sources.insert(
            DiscoveryFactSourceRecord(
                source_row_id=source_row_id or new_opaque_id(),
                research_run_id=research_run_id,
                fact_id=domain_fact.fact_id,
                sensor_observation_id=observation.observation_id,
                created_at=observation.collected_at,
            )
        )
        return SensorAdmissionResult(
            fact_id=domain_fact.fact_id,
            observation_id=observation.observation_id,
        )

    def _reject_forbidden(
        self, mapping: Mapping[str, Any], field_name: str
    ) -> None:
        found = FORBIDDEN_DISCOVERY_KEYS.intersection(mapping.keys())
        if found:
            raise SensorAdmissionError(
                f"{field_name} contains forbidden discovery keys: {sorted(found)}"
            )

    def _canonical_key(self, observation: SensorObservation, fact_kind: DiscoveryFactKind) -> str:
        return f"{observation.sensor_id}:{fact_kind.value}:{observation.target_reference}"
