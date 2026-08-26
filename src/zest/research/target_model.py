"""Target / causal model projection. Not SoR truth. Not a vulnerability graph."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from zest.research.types import ResearchInputError

TARGET_MODEL_STRATEGY_VERSION = "target.model.diagnostic.echo.v1"
DIAGNOSTIC_ACTOR_KIND = "DIAGNOSTIC_RUNTIME"
FORBIDDEN_TARGET_KEYS = frozenset(
    {
        "token",
        "password",
        "api_key",
        "apiKey",
        "raw_secret",
        "credential",
        "secret_value",
        "secretValue",
        "session_token",
        "cookie",
        "authorization",
        "severity",
        "cvss",
        "cve",
        "vulnerability",
        "confidence",
        "scope",
        "budget_change",
    }
)


class TargetEpistemicStatus(Enum):
    """How a target-model element was established. Not a promotion rank.

    UNTRUSTED_EXTERNAL represents raw data from external sensor sources. It is
    strictly below OBSERVED: it must NEVER be automatically promoted to
    OBSERVED, DERIVED, INFERRED, or HYPOTHESIZED without passing deterministic
    admission control (forbidden-key rejection, scope provenance binding, and
    an admission receipt).

    Türkçe: Bu durum dış sensör kaynaklarından gelen ham veriyi temsil eder;
    OBSERVED'ın ALTINDADIR; admission (kabul) geçmeden ASLA otomatik olarak
    OBSERVED/DERIVED/INFERRED/HYPOTHESIZED'a yükseltilemez; yükseltme yalnızca
    deterministic admission kontrolünden (yasak anahtarlar + scope provenance +
    receipt) geçerse olur.
    """

    OBSERVED = "OBSERVED"
    DERIVED = "DERIVED"
    INFERRED = "INFERRED"
    HYPOTHESIZED = "HYPOTHESIZED"
    UNTRUSTED_EXTERNAL = "UNTRUSTED_EXTERNAL"


class TargetElementKind(Enum):
    ACTOR = "ACTOR"
    ROLE = "ROLE"
    SESSION = "SESSION"
    RESOURCE = "RESOURCE"
    ACTION = "ACTION"
    STATE = "STATE"
    RELATIONSHIP = "RELATIONSHIP"
    STATE_TRANSITION = "STATE_TRANSITION"


class TargetInferenceOutcome(Enum):
    ADMITTED = "ADMITTED"
    REJECTED_EPISTEMIC_UPGRADE = "REJECTED_EPISTEMIC_UPGRADE"
    REJECTED_HALLUCINATED_SOURCE = "REJECTED_HALLUCINATED_SOURCE"
    REJECTED_CROSS_RUN = "REJECTED_CROSS_RUN"
    REJECTED_POLICY_CONFLICT = "REJECTED_POLICY_CONFLICT"


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_ids(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ResearchInputError(f"{field_name} must be a tuple")
    return tuple(_require_text(item, f"{field_name}[{index}]") for index, item in enumerate(value))


def _reject_forbidden(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ResearchInputError(f"{field_name} must be a mapping")
    found = FORBIDDEN_TARGET_KEYS.intersection(payload.keys())
    if found:
        raise ResearchInputError(f"{field_name} must not contain {sorted(found)}")
    return dict(payload)


@dataclass(frozen=True)
class TargetObservationView:
    """Typed observation view for projection. Not Evidence."""

    observation_id: str
    research_run_id: str
    experiment_id: str
    observation_kind: str
    payload: Mapping[str, Any]
    capability: str
    action: str
    actor_handle: str
    resource_handle: str
    submitted_input: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observation_id", _require_text(self.observation_id, "observation_id")
        )
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        object.__setattr__(
            self, "experiment_id", _require_text(self.experiment_id, "experiment_id")
        )
        object.__setattr__(
            self, "observation_kind", _require_text(self.observation_kind, "observation_kind")
        )
        object.__setattr__(self, "payload", _reject_forbidden(self.payload, "payload"))
        object.__setattr__(self, "capability", _require_text(self.capability, "capability"))
        object.__setattr__(self, "action", _require_text(self.action, "action"))
        object.__setattr__(
            self, "actor_handle", _require_text(self.actor_handle, "actor_handle")
        )
        object.__setattr__(
            self, "resource_handle", _require_text(self.resource_handle, "resource_handle")
        )
        if self.submitted_input is not None:
            object.__setattr__(
                self,
                "submitted_input",
                _require_text(self.submitted_input, "submitted_input"),
            )


@dataclass(frozen=True)
class TargetElement:
    """One projected target-model node or edge. Not SoR authority."""

    element_id: str
    kind: TargetElementKind
    epistemic_status: TargetEpistemicStatus
    research_run_id: str
    opaque_ref: str
    statement: str
    source_refs: tuple[str, ...]
    attributes: Mapping[str, Any]
    strategy_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "element_id", _require_text(self.element_id, "element_id"))
        if not isinstance(self.kind, TargetElementKind):
            raise ResearchInputError("kind must be a TargetElementKind")
        if not isinstance(self.epistemic_status, TargetEpistemicStatus):
            raise ResearchInputError("epistemic_status must be a TargetEpistemicStatus")
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        object.__setattr__(self, "opaque_ref", _require_text(self.opaque_ref, "opaque_ref"))
        object.__setattr__(self, "statement", _require_text(self.statement, "statement"))
        object.__setattr__(self, "source_refs", _require_ids(self.source_refs, "source_refs"))
        object.__setattr__(
            self, "attributes", _reject_forbidden(self.attributes, "attributes")
        )
        object.__setattr__(
            self,
            "strategy_version",
            _require_text(self.strategy_version, "strategy_version"),
        )


@dataclass(frozen=True)
class TargetModelProjection:
    """Read model over SoR plus optional inferred records. Not a second truth store."""

    research_run_id: str
    strategy_version: str
    elements: tuple[TargetElement, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        object.__setattr__(
            self,
            "strategy_version",
            _require_text(self.strategy_version, "strategy_version"),
        )

    def elements_with(self, status: TargetEpistemicStatus) -> tuple[TargetElement, ...]:
        return tuple(item for item in self.elements if item.epistemic_status is status)


@dataclass(frozen=True)
class TargetInferenceDraft:
    """Model/Research proposed relation. Must be INFERRED or HYPOTHESIZED."""

    inference_id: str
    research_run_id: str
    kind: TargetElementKind
    epistemic_status: TargetEpistemicStatus
    opaque_ref: str
    statement: str
    source_refs: tuple[str, ...]
    attributes: Mapping[str, Any]
    strategy_version: str = TARGET_MODEL_STRATEGY_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "inference_id", _require_text(self.inference_id, "inference_id")
        )
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        if not isinstance(self.kind, TargetElementKind):
            raise ResearchInputError("kind must be a TargetElementKind")
        if not isinstance(self.epistemic_status, TargetEpistemicStatus):
            raise ResearchInputError("epistemic_status must be a TargetEpistemicStatus")
        object.__setattr__(self, "opaque_ref", _require_text(self.opaque_ref, "opaque_ref"))
        object.__setattr__(self, "statement", _require_text(self.statement, "statement"))
        object.__setattr__(self, "source_refs", _require_ids(self.source_refs, "source_refs"))
        object.__setattr__(
            self, "attributes", _reject_forbidden(self.attributes, "attributes")
        )
        object.__setattr__(
            self,
            "strategy_version",
            _require_text(self.strategy_version, "strategy_version"),
        )


@dataclass(frozen=True)
class TargetInferenceDecision:
    outcome: TargetInferenceOutcome
    reason_codes: tuple[str, ...]
    element: TargetElement | None

    @property
    def admitted(self) -> bool:
        return self.outcome is TargetInferenceOutcome.ADMITTED


def _element(
    *,
    element_id: str,
    kind: TargetElementKind,
    status: TargetEpistemicStatus,
    research_run_id: str,
    opaque_ref: str,
    statement: str,
    source_refs: tuple[str, ...],
    attributes: Mapping[str, Any],
) -> TargetElement:
    return TargetElement(
        element_id=element_id,
        kind=kind,
        epistemic_status=status,
        research_run_id=research_run_id,
        opaque_ref=opaque_ref,
        statement=statement,
        source_refs=source_refs,
        attributes=dict(attributes),
        strategy_version=TARGET_MODEL_STRATEGY_VERSION,
    )


def project_diagnostic_target_model(
    research_run_id: str,
    views: tuple[TargetObservationView, ...],
    *,
    inferences: tuple[TargetElement, ...] = (),
) -> TargetModelProjection:
    """Deterministic diagnostic projection. Rebuildable from SoR. Not ownership fact."""

    run_id = _require_text(research_run_id, "research_run_id")
    elements: list[TargetElement] = []
    seen: set[str] = set()
    for view in views:
        if view.research_run_id != run_id:
            raise ResearchInputError("target observation research_run_id mismatch")
        actor_id = f"actor:{view.actor_handle}"
        action_id = f"action:{view.capability}:{view.action}"
        resource_id = f"resource:{view.resource_handle}"
        for element in (
            _element(
                element_id=actor_id,
                kind=TargetElementKind.ACTOR,
                status=TargetEpistemicStatus.OBSERVED,
                research_run_id=run_id,
                opaque_ref=view.actor_handle,
                statement=f"Diagnostic actor handle {view.actor_handle} executed an action.",
                source_refs=(view.observation_id,),
                attributes={"kind": DIAGNOSTIC_ACTOR_KIND, "not_a_security_principal": True},
            ),
            _element(
                element_id=action_id,
                kind=TargetElementKind.ACTION,
                status=TargetEpistemicStatus.OBSERVED,
                research_run_id=run_id,
                opaque_ref=f"{view.capability}:{view.action}",
                statement=f"Diagnostic action {view.action} was executed.",
                source_refs=(view.observation_id,),
                attributes={"capability": view.capability, "action": view.action},
            ),
            _element(
                element_id=resource_id,
                kind=TargetElementKind.RESOURCE,
                status=TargetEpistemicStatus.OBSERVED,
                research_run_id=run_id,
                opaque_ref=view.resource_handle,
                statement=f"Diagnostic resource handle {view.resource_handle} was targeted.",
                source_refs=(view.observation_id,),
                attributes={"not_ownership": True},
            ),
        ):
            if element.element_id not in seen:
                seen.add(element.element_id)
                elements.append(element)
        elements.append(
            _element(
                element_id=f"rel:executed:{view.observation_id}",
                kind=TargetElementKind.RELATIONSHIP,
                status=TargetEpistemicStatus.OBSERVED,
                research_run_id=run_id,
                opaque_ref=f"{view.actor_handle}->{view.action}",
                statement=(
                    f"Actor handle {view.actor_handle} executed {view.action} "
                    f"against {view.resource_handle}."
                ),
                source_refs=(view.observation_id,),
                attributes={
                    "relation": "EXECUTED",
                    "not_ownership": True,
                    "not_authorization": True,
                },
            )
        )
        echoed = view.payload.get("echoed")
        if isinstance(echoed, str) and echoed.strip():
            elements.append(
                _element(
                    element_id=f"derived:echo:{view.observation_id}",
                    kind=TargetElementKind.STATE,
                    status=TargetEpistemicStatus.DERIVED,
                    research_run_id=run_id,
                    opaque_ref=f"echo:{view.observation_id}",
                    statement=f"Derived echoed value from observation {view.observation_id}.",
                    source_refs=(view.observation_id,),
                    attributes={"echoed": echoed, "not_authorization": True},
                )
            )
            elements.append(
                _element(
                    element_id=f"transition:{view.observation_id}",
                    kind=TargetElementKind.STATE_TRANSITION,
                    status=TargetEpistemicStatus.DERIVED,
                    research_run_id=run_id,
                    opaque_ref=f"transition:{view.observation_id}",
                    statement=(
                        f"Action {view.action} produced a derived postcondition from "
                        f"observation {view.observation_id}. Precondition is unknown."
                    ),
                    source_refs=(view.observation_id,),
                    attributes={
                        "actor_handle": view.actor_handle,
                        "action": view.action,
                        "precondition": "UNKNOWN",
                        "postcondition": "DERIVED_ECHO",
                        "not_a_vulnerability": True,
                    },
                )
            )
    for inference in inferences:
        if inference.research_run_id != run_id:
            raise ResearchInputError("inferred element research_run_id mismatch")
        if inference.epistemic_status not in {
            TargetEpistemicStatus.INFERRED,
            TargetEpistemicStatus.HYPOTHESIZED,
        }:
            raise ResearchInputError("persisted inference must stay INFERRED or HYPOTHESIZED")
        elements.append(inference)
    return TargetModelProjection(
        research_run_id=run_id,
        strategy_version=TARGET_MODEL_STRATEGY_VERSION,
        elements=tuple(elements),
    )


def admit_target_inference(
    draft: TargetInferenceDraft,
    *,
    research_run_id: str,
    resolvable_source_ids: frozenset[str],
) -> TargetInferenceDecision:
    """Admit a model/research inference. Never OBSERVED. Never authorization truth."""

    if draft.research_run_id != research_run_id:
        return TargetInferenceDecision(
            outcome=TargetInferenceOutcome.REJECTED_CROSS_RUN,
            reason_codes=("CROSS_RUN_SOURCE",),
            element=None,
        )
    if draft.epistemic_status in {
        TargetEpistemicStatus.OBSERVED,
        TargetEpistemicStatus.DERIVED,
    }:
        return TargetInferenceDecision(
            outcome=TargetInferenceOutcome.REJECTED_EPISTEMIC_UPGRADE,
            reason_codes=("INFERENCE_CANNOT_BE_OBSERVED_OR_DERIVED",),
            element=None,
        )
    if not draft.source_refs:
        return TargetInferenceDecision(
            outcome=TargetInferenceOutcome.REJECTED_HALLUCINATED_SOURCE,
            reason_codes=("NO_SOURCE_REFERENCES",),
            element=None,
        )
    missing = [ref for ref in draft.source_refs if ref not in resolvable_source_ids]
    if missing:
        return TargetInferenceDecision(
            outcome=TargetInferenceOutcome.REJECTED_HALLUCINATED_SOURCE,
            reason_codes=("HALLUCINATED_SOURCE",),
            element=None,
        )
    lowered = draft.statement.lower()
    if any(
        marker in lowered
        for marker in ("vulnerability", "idor", "authorized", "owns", "change scope")
    ):
        return TargetInferenceDecision(
            outcome=TargetInferenceOutcome.REJECTED_POLICY_CONFLICT,
            reason_codes=("POLICY_OR_VULNERABILITY_CLAIM",),
            element=None,
        )
    return TargetInferenceDecision(
        outcome=TargetInferenceOutcome.ADMITTED,
        reason_codes=("TARGET_INFERENCE_ADMITTED",),
        element=TargetElement(
            element_id=draft.inference_id,
            kind=draft.kind,
            epistemic_status=draft.epistemic_status,
            research_run_id=draft.research_run_id,
            opaque_ref=draft.opaque_ref,
            statement=draft.statement,
            source_refs=draft.source_refs,
            attributes=dict(draft.attributes),
            strategy_version=draft.strategy_version,
        ),
    )
