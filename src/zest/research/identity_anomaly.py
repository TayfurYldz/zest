"""Identity-differential anomaly admission. Not Evidence, Finding, or authorization.

MR-6A source class: durable identity-dependent response divergence that is
either unmatched by enabled HunterFamily rows (registry-external) or already
owned by a permanent family (normal known-family path). This module does not
dispatch a Worker, write the hunter registry, or create a Finding.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from zest.research.compiler_registry import AuthorizationDifferentialCompiler, CompilerRequest
from zest.research.differential import DifferentialDimension, DifferentialInterpretation
from zest.research.discovery.graph import AttackSurfaceGraph
from zest.research.exploration import OpportunityKind
from zest.research.planning import (
    HTTP_AUTHORIZATION_DISCONFIRMING_OBSERVATION,
    HTTP_AUTHORIZATION_EXPECTED_OBSERVATION,
)
from zest.research.proposals import HypothesisChallenge, HypothesisProposal, NoveltyBasis
from zest.research.selection import HunterFamilyView, families_for_node
from zest.research.types import ExperimentPlan, ResearchInputError
from zest.tools.capabilities import HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY

IDENTITY_ANOMALY_STRATEGY_VERSION = "identity_anomaly.registry_external.v1"
EXPLORATORY_HYPOTHESIS_ORIGIN_PREFIX = "exh:"
HTTP_AUTHORIZATION_DIFFERENTIAL_KIND = "HTTP_AUTHORIZATION_DIFFERENTIAL"
IDENTITY_CHANGED_DIMENSIONS = frozenset(
    {
        DifferentialDimension.ACTOR.value,
        DifferentialDimension.ROLE.value,
        DifferentialDimension.SESSION.value,
    }
)
IDENTITY_ANOMALY_CLAIM = (
    "Observed identity-dependent response divergence is not explained by "
    "current enabled HunterFamily matching and may indicate an unmodelled "
    "access or state invariant."
)
IDENTITY_ANOMALY_ALTERNATIVE = (
    "The divergence is benign representation or session variance rather than "
    "an unmodelled access invariant."
)
IDENTITY_ANOMALY_RATIONALE = (
    "A durable identity differential exists, no enabled HunterFamily claims "
    "this exact anomaly, and AuthorizationDifferentialCompiler can discriminate "
    "the access-invariant explanation from benign variance using identities and "
    "resources already present in the source record."
)
IDENTITY_ANOMALY_DIRECTION = (
    "Discriminate unexplained identity-dependent response divergence with a "
    "bounded authorization-differential control experiment."
)
IDENTITY_ANOMALY_QUESTION = (
    "Is the identity-dependent divergence explained by an unmodelled access "
    "invariant, or by benign representation or session variance?"
)
ALLOWED_LAB_MODES = frozenset({"vulnerable", "secure_only", "redirect"})


class IdentityAnomalyClass(Enum):
    """Admission class. Not a vulnerability class."""

    REGISTRY_EXTERNAL = "REGISTRY_EXTERNAL"
    KNOWN_FAMILY = "KNOWN_FAMILY"
    REJECT_UNRESOLVED_SOURCE = "REJECT_UNRESOLVED_SOURCE"
    REJECT_CROSS_RUN = "REJECT_CROSS_RUN"
    REJECT_NOT_IDENTITY = "REJECT_NOT_IDENTITY"
    REJECT_NO_ANOMALY = "REJECT_NO_ANOMALY"


@dataclass(frozen=True)
class IdentityAnomalyContext:
    """Bounded source facts for one identity anomaly. Not truth and not scope."""

    source_id: str
    source_kind: str
    research_run_id: str
    observation_ids: tuple[str, ...]
    classification: IdentityAnomalyClass
    claim: str
    alternative_explanation: str
    owning_family_ids: tuple[str, ...] = ()
    authorized_origin: str | None = None
    actor: str | None = None
    own_object: str | None = None
    cross_object: str | None = None
    mode: str | None = None
    reason_code: str | None = None

    @property
    def registry_external(self) -> bool:
        return self.classification is IdentityAnomalyClass.REGISTRY_EXTERNAL

    def structural_identity(self) -> str:
        return identity_anomaly_structural_identity(
            research_run_id=self.research_run_id,
            authorized_origin=self.authorized_origin,
            actor=self.actor,
            own_object=self.own_object,
            cross_object=self.cross_object,
        )

    def context_signature(self) -> str:
        return f"identity_anomaly:{self.structural_identity()}"


def identity_anomaly_structural_identity(
    *,
    research_run_id: str,
    authorized_origin: str | None,
    actor: str | None,
    own_object: str | None,
    cross_object: str | None,
) -> str:
    payload = "|".join(
        (
            research_run_id.strip(),
            (authorized_origin or "").strip(),
            (actor or "").strip(),
            (own_object or "").strip(),
            (cross_object or "").strip(),
            IDENTITY_ANOMALY_STRATEGY_VERSION,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def exploratory_hypothesis_origin(structural_identity: str) -> str:
    if not isinstance(structural_identity, str) or not structural_identity.strip():
        raise ResearchInputError("structural_identity must be a non-empty string")
    return f"{EXPLORATORY_HYPOTHESIS_ORIGIN_PREFIX}{structural_identity.strip()}"


def is_exploratory_hypothesis_origin(origin_reference: str | None) -> bool:
    return isinstance(origin_reference, str) and origin_reference.startswith(
        EXPLORATORY_HYPOTHESIS_ORIGIN_PREFIX
    )


def exploratory_experiment_id(research_run_id: str, hypothesis_id: str) -> str:
    payload = f"{research_run_id}:{hypothesis_id}:exploratory-identity-v1"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_identity_changed_dimensions(changed_dimensions: tuple[str, ...] | object) -> bool:
    if not isinstance(changed_dimensions, tuple):
        return False
    return any(item in IDENTITY_CHANGED_DIMENSIONS for item in changed_dimensions)


def classify_identity_differential(
    *,
    research_run_id: str,
    differential_id: str,
    differential_run_id: str,
    interpretation: str,
    changed_dimensions: tuple[str, ...],
    observation_ids: tuple[str, ...],
    unresolved_observation_ids: tuple[str, ...],
    cross_run_observation_ids: tuple[str, ...],
    owning_family_ids: tuple[str, ...],
    authorized_origin: str | None,
    actor: str | None,
    own_object: str | None,
    cross_object: str | None,
    mode: str | None,
) -> IdentityAnomalyContext:
    """Classify a durable DifferentialObservation as an identity anomaly source."""

    if differential_run_id != research_run_id:
        return _reject(
            source_id=differential_id,
            source_kind="DIFFERENTIAL",
            research_run_id=research_run_id,
            observation_ids=observation_ids,
            classification=IdentityAnomalyClass.REJECT_CROSS_RUN,
            reason_code="CROSS_RUN_SOURCE",
        )
    if unresolved_observation_ids:
        return _reject(
            source_id=differential_id,
            source_kind="DIFFERENTIAL",
            research_run_id=research_run_id,
            observation_ids=observation_ids,
            classification=IdentityAnomalyClass.REJECT_UNRESOLVED_SOURCE,
            reason_code="UNRESOLVED_SOURCE",
        )
    if cross_run_observation_ids:
        return _reject(
            source_id=differential_id,
            source_kind="DIFFERENTIAL",
            research_run_id=research_run_id,
            observation_ids=observation_ids,
            classification=IdentityAnomalyClass.REJECT_CROSS_RUN,
            reason_code="CROSS_RUN_OBSERVATION",
        )
    if not is_identity_changed_dimensions(changed_dimensions):
        return _reject(
            source_id=differential_id,
            source_kind="DIFFERENTIAL",
            research_run_id=research_run_id,
            observation_ids=observation_ids,
            classification=IdentityAnomalyClass.REJECT_NOT_IDENTITY,
            reason_code="NOT_IDENTITY_DIMENSION",
        )
    if interpretation != DifferentialInterpretation.CONTROLLED_DIFFERENCE.value:
        return _reject(
            source_id=differential_id,
            source_kind="DIFFERENTIAL",
            research_run_id=research_run_id,
            observation_ids=observation_ids,
            classification=IdentityAnomalyClass.REJECT_NO_ANOMALY,
            reason_code="NOT_CONTROLLED_DIFFERENCE",
        )
    if owning_family_ids:
        return IdentityAnomalyContext(
            source_id=differential_id,
            source_kind="DIFFERENTIAL",
            research_run_id=research_run_id,
            observation_ids=observation_ids,
            classification=IdentityAnomalyClass.KNOWN_FAMILY,
            claim=IDENTITY_ANOMALY_CLAIM,
            alternative_explanation=IDENTITY_ANOMALY_ALTERNATIVE,
            owning_family_ids=owning_family_ids,
            authorized_origin=authorized_origin,
            actor=actor,
            own_object=own_object,
            cross_object=cross_object,
            mode=mode,
            reason_code="OWNED_BY_HUNTER_FAMILY",
        )
    return IdentityAnomalyContext(
        source_id=differential_id,
        source_kind="DIFFERENTIAL",
        research_run_id=research_run_id,
        observation_ids=observation_ids,
        classification=IdentityAnomalyClass.REGISTRY_EXTERNAL,
        claim=IDENTITY_ANOMALY_CLAIM,
        alternative_explanation=IDENTITY_ANOMALY_ALTERNATIVE,
        authorized_origin=authorized_origin,
        actor=actor,
        own_object=own_object,
        cross_object=cross_object,
        mode=mode,
        reason_code="REGISTRY_EXTERNAL_IDENTITY_ANOMALY",
    )


def classify_http_authorization_observation(
    *,
    research_run_id: str,
    observation_id: str,
    observation_kind: str,
    observation_run_id: str | None,
    payload: Mapping[str, Any],
    owning_family_ids: tuple[str, ...],
) -> IdentityAnomalyContext:
    """Classify a durable HTTP_AUTHORIZATION_DIFFERENTIAL Observation."""

    if observation_run_id is None:
        return _reject(
            source_id=observation_id,
            source_kind=HTTP_AUTHORIZATION_DIFFERENTIAL_KIND,
            research_run_id=research_run_id,
            observation_ids=(observation_id,),
            classification=IdentityAnomalyClass.REJECT_UNRESOLVED_SOURCE,
            reason_code="UNRESOLVED_SOURCE",
        )
    if observation_run_id != research_run_id:
        return _reject(
            source_id=observation_id,
            source_kind=HTTP_AUTHORIZATION_DIFFERENTIAL_KIND,
            research_run_id=research_run_id,
            observation_ids=(observation_id,),
            classification=IdentityAnomalyClass.REJECT_CROSS_RUN,
            reason_code="CROSS_RUN_SOURCE",
        )
    if observation_kind != HTTP_AUTHORIZATION_DIFFERENTIAL_KIND:
        return _reject(
            source_id=observation_id,
            source_kind=observation_kind,
            research_run_id=research_run_id,
            observation_ids=(observation_id,),
            classification=IdentityAnomalyClass.REJECT_NOT_IDENTITY,
            reason_code="NOT_IDENTITY_OBSERVATION",
        )
    fields = compiler_fields_from_authz_payload(payload)
    cross_status = payload.get("cross_object_request_status")
    if cross_status != 200:
        return _reject(
            source_id=observation_id,
            source_kind=HTTP_AUTHORIZATION_DIFFERENTIAL_KIND,
            research_run_id=research_run_id,
            observation_ids=(observation_id,),
            classification=IdentityAnomalyClass.REJECT_NO_ANOMALY,
            reason_code="NO_CROSS_IDENTITY_GRANT",
            **fields,
        )
    if owning_family_ids:
        return IdentityAnomalyContext(
            source_id=observation_id,
            source_kind=HTTP_AUTHORIZATION_DIFFERENTIAL_KIND,
            research_run_id=research_run_id,
            observation_ids=(observation_id,),
            classification=IdentityAnomalyClass.KNOWN_FAMILY,
            claim=IDENTITY_ANOMALY_CLAIM,
            alternative_explanation=IDENTITY_ANOMALY_ALTERNATIVE,
            owning_family_ids=owning_family_ids,
            reason_code="OWNED_BY_HUNTER_FAMILY",
            **fields,
        )
    return IdentityAnomalyContext(
        source_id=observation_id,
        source_kind=HTTP_AUTHORIZATION_DIFFERENTIAL_KIND,
        research_run_id=research_run_id,
        observation_ids=(observation_id,),
        classification=IdentityAnomalyClass.REGISTRY_EXTERNAL,
        claim=IDENTITY_ANOMALY_CLAIM,
        alternative_explanation=IDENTITY_ANOMALY_ALTERNATIVE,
        reason_code="REGISTRY_EXTERNAL_IDENTITY_ANOMALY",
        **fields,
    )


def owning_identity_families(
    graph: AttackSurfaceGraph,
    registry: tuple[HunterFamilyView, ...],
) -> tuple[str, ...]:
    """Families that legitimately own an identity-differential research claim.

    Matching is node/precondition based. An HTTP_OPERATION or
    RESOURCE_INSTANCE_CANDIDATE node that OBJECT_AUTHORIZATION (or another
    enabled family) claims routes the anomaly to the known-family path.
    """

    owned: list[str] = []
    seen: set[str] = set()
    for node in graph.nodes:
        for family in families_for_node(node, graph, registry):
            if family.family_id in seen:
                continue
            seen.add(family.family_id)
            owned.append(family.family_id)
    return tuple(owned)


def compiler_fields_from_authz_payload(payload: Mapping[str, Any]) -> dict[str, str | None]:
    origin = _text(payload.get("authorized_origin")) or _text(payload.get("origin"))
    actor = _text(payload.get("actor"))
    own_object = _text(payload.get("own_object"))
    cross_object = _text(payload.get("cross_object"))
    mode = _text(payload.get("mode"))
    if mode not in ALLOWED_LAB_MODES:
        mode = "vulnerable"
    return {
        "authorized_origin": origin,
        "actor": actor,
        "own_object": own_object,
        "cross_object": cross_object,
        "mode": mode,
    }


def compiler_arguments_from_context(context: IdentityAnomalyContext) -> dict[str, str] | None:
    origin = context.authorized_origin
    actor = context.actor
    own_object = context.own_object
    cross_object = context.cross_object
    if not all((origin, actor, own_object, cross_object)):
        return None
    mode = context.mode if context.mode in ALLOWED_LAB_MODES else "vulnerable"
    return {
        "authorized_origin": origin,
        "actor": actor,
        "own_object": own_object,
        "cross_object": cross_object,
        "mode": mode,
    }


def identity_anomaly_proposal_and_challenge(
    context: IdentityAnomalyContext,
) -> tuple[HypothesisProposal, HypothesisChallenge]:
    if not context.registry_external:
        raise ResearchInputError("only registry-external identity anomalies may draft a proposal")
    source_refs = (context.source_id,)
    proposal = HypothesisProposal(
        proposed_claim=context.claim,
        rationale=IDENTITY_ANOMALY_RATIONALE,
        source_references=source_refs,
        assumptions=(
            "The source record is a durable same-run observation or differential.",
            "HunterFamily matching did not claim this identity anomaly.",
            "Opportunity selection is not Core authorization.",
        ),
        unresolved_questions=(IDENTITY_ANOMALY_QUESTION,),
        suggested_disconfirming_test=HTTP_AUTHORIZATION_DISCONFIRMING_OBSERVATION,
        suggested_capability=HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY,
        expected_security_relevance=None,
        novelty_basis=NoveltyBasis.TARGET_SPECIFIC_BEHAVIOR,
    )
    challenge = HypothesisChallenge(
        alternative_explanations=(context.alternative_explanation,),
        missing_preconditions=(),
        contradictory_source_references=(),
        required_negative_controls=(
            "secure control must deny cross-identity access when the invariant holds"
        ),
        reasons_not_to_test=(),
        proposed_disconfirming_observation=HTTP_AUTHORIZATION_DISCONFIRMING_OBSERVATION,
        ambiguity=(
            "Identity divergence is not Evidence and does not declare a Finding."
        ),
    )
    return proposal, challenge


def compile_identity_anomaly_experiment(
    context: IdentityAnomalyContext,
    *,
    hypothesis_id: str,
    budget_id: str,
    target_reference: str,
) -> ExperimentPlan:
    arguments = compiler_arguments_from_context(context)
    if arguments is None:
        raise ResearchInputError("identity anomaly source is missing compiler semantics")
    proposal, challenge = identity_anomaly_proposal_and_challenge(context)
    result = AuthorizationDifferentialCompiler().compile(
        CompilerRequest(
            hypothesis_id=hypothesis_id,
            budget_id=budget_id,
            target_reference=target_reference,
            family_id=None,
            family_name=None,
            proposal=proposal,
            challenge=challenge,
            arguments=arguments,
        )
    )
    if not result.compiled or result.plan is None:
        raise ResearchInputError(result.reason_code)
    return result.plan


def opportunity_kind() -> OpportunityKind:
    return OpportunityKind.REGISTRY_EXTERNAL_EXPLORATORY


def _reject(
    *,
    source_id: str,
    source_kind: str,
    research_run_id: str,
    observation_ids: tuple[str, ...],
    classification: IdentityAnomalyClass,
    reason_code: str,
    authorized_origin: str | None = None,
    actor: str | None = None,
    own_object: str | None = None,
    cross_object: str | None = None,
    mode: str | None = None,
) -> IdentityAnomalyContext:
    return IdentityAnomalyContext(
        source_id=source_id,
        source_kind=source_kind,
        research_run_id=research_run_id,
        observation_ids=observation_ids,
        classification=classification,
        claim=IDENTITY_ANOMALY_CLAIM,
        alternative_explanation=IDENTITY_ANOMALY_ALTERNATIVE,
        authorized_origin=authorized_origin,
        actor=actor,
        own_object=own_object,
        cross_object=cross_object,
        mode=mode,
        reason_code=reason_code,
    )


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
