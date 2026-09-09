"""Deterministic ExperimentCompiler registry (MR-2).

Known HunterFamily work bypasses `plan_admitted_hypothesis`. Compilation binds a
real Worker capability (version + fingerprint + authoritative side-effect) via
`compile_experiment_intent`. This module does not authorize, dispatch, or
invent HTTP payloads.

Planning aliases stored on `hunt_v3_queue` (`mutation.matrix`, `protocol.parser`,
`http.authorization_differential`, …) are not Worker capabilities and must never
be passed through the generic planner.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol

from zest.research.compiler import ExperimentCompileError, ExperimentIntent, compile_experiment_intent
from zest.research.http_transaction import (
    HTTP_TRANSACTION_DISCONFIRMING_OBSERVATION,
    HTTP_TRANSACTION_EVALUATION_STRATEGY,
    HTTP_TRANSACTION_EXPECTED_OBSERVATION,
)
from zest.research.mutation.cell_contract import (
    FAMILY_REQUIRED_DIMENSIONS,
    MutationCellContractError,
    bind_mutation_matrix_cell,
)
from zest.research.mutation.identity import (
    MutationCellIdentityError,
    lookup_authoritative_mutation_cell,
)
from zest.research.planning import (
    HTTP_AUTHORIZATION_DISCONFIRMING_OBSERVATION,
    HTTP_AUTHORIZATION_EXPECTED_OBSERVATION,
    HTTP_STATE_TRANSITION_DISCONFIRMING_OBSERVATION,
    HTTP_STATE_TRANSITION_EXPECTED_OBSERVATION,
    plan_admitted_hypothesis,
    plan_authorization_differential,
    plan_state_transition,
)
from zest.research.proposals import HypothesisChallenge, HypothesisProposal
from zest.research.protocol.step_compile import (
    HTTP_RAW_EXCHANGE_ACTION,
    HTTP_RAW_EXCHANGE_CAPABILITY,
    ProtocolStepContractError,
    bind_protocol_step,
)
from zest.research.types import ExperimentPlan
from zest.tools.capabilities import (
    HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY,
    HTTP_STATE_TRANSITION_CAPABILITY,
    HTTP_TRANSACTION_CAPABILITY,
)
from zest.tools.registry import WORKER_EXECUTOR_CLASS, load_capability_registry

COMPILER_AUTHORIZATION_DIFFERENTIAL = "authorization_differential.v1"
COMPILER_STATE_TRANSITION = "state_transition.v1"
COMPILER_MUTATION_MATRIX_CELL = "mutation_matrix_cell.v1"
COMPILER_MUTATION_VARIANT = "mutation_variant.v1"
COMPILER_PROTOCOL_STEP = "protocol_step.v1"
COMPILER_OAST_INTERACTION = "oast.interaction.v1"
COMPILER_GENERIC_PLANNER = "generic_planner.v1"

FAMILY_OBJECT_AUTHORIZATION = "OBJECT_AUTHORIZATION"
FAMILY_WORKFLOW_STATE_TRANSITION = "WORKFLOW_STATE_TRANSITION"

MUTATION_MATRIX_FAMILIES = frozenset(
    {
        "SQL_INJECTION",
        "SERVER_SIDE_TEMPLATE_INJECTION",
        "FILE_INCLUDE_AND_PATH_TRAVERSAL",
        "MASS_ASSIGNMENT",
        "JWT_CRYPTO_AND_CLAIM_CONFUSION",
        "CORS_CREDENTIAL_EXFILTRATION_CHAIN",
        "GRAPHQL_AUTHORIZATION_AND_INJECTION",
        "DOM_TAINT_AND_CLIENT_SIDE_EXECUTION",
        "AI_LLM_PROMPT_INJECTION_AND_TOOL_ABUSE",
    }
)
PROTOCOL_FAMILIES = frozenset(
    {
        "HTTP_REQUEST_SMUGGLING_DESYNC",
        "HTTP_CACHE_POISONING_DECEPTION",
    }
)

# Queue-row planning aliases. Not entries in the Worker capability registry.
PLANNING_ALIAS_CAPABILITIES = frozenset(
    {
        "http.authorization_differential",
        "http.state_transition_authorization",
        "mutation.matrix",
        "protocol.parser",
    }
)

HTTP_TRANSACTION_ARG_KEYS = (
    "authorized_origin",
    "method",
    "path",
    "query",
    "headers",
    "body",
    "content_type",
    "session_context_reference",
    "max_response_bytes",
    "timeout_ms",
)


class CompilerOutcome(Enum):
    COMPILED = "COMPILED"
    BLOCKED_UNSUPPORTED_CAPABILITY = "BLOCKED_UNSUPPORTED_CAPABILITY"
    BLOCKED_MISSING_SEMANTICS = "BLOCKED_MISSING_SEMANTICS"
    BLOCKED_UNKNOWN_CELL = "BLOCKED_UNKNOWN_CELL"
    BLOCKED_INVALID_INPUT = "BLOCKED_INVALID_INPUT"


@dataclass(frozen=True)
class CompilerRequest:
    """Family-scoped compile input. Not an ExperimentPlan and not Core ALLOW."""

    hypothesis_id: str
    budget_id: str
    target_reference: str
    family_id: str | None = None
    family_name: str | None = None
    arguments: Mapping[str, Any] | None = None
    requested_side_effect: int | None = None
    proposal: HypothesisProposal | None = None
    challenge: HypothesisChallenge | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", dict(self.arguments or {}))


@dataclass(frozen=True)
class CompilerResult:
    """Compile decision. COMPILED is not authorization and not coverage."""

    outcome: CompilerOutcome
    reason_code: str
    compiler_id: str
    plan: ExperimentPlan | None = None
    family_name: str | None = None

    @property
    def compiled(self) -> bool:
        return self.outcome is CompilerOutcome.COMPILED and self.plan is not None


class ExperimentCompiler(Protocol):
    compiler_id: str

    def compile(self, request: CompilerRequest) -> CompilerResult: ...


def _blocked(
    compiler_id: str,
    outcome: CompilerOutcome,
    reason_code: str,
    *,
    family_name: str | None = None,
) -> CompilerResult:
    return CompilerResult(
        outcome=outcome,
        reason_code=reason_code,
        compiler_id=compiler_id,
        family_name=family_name,
    )


def _compiled(
    compiler_id: str,
    plan: ExperimentPlan,
    *,
    family_name: str | None = None,
    reason_code: str = "COMPILED",
) -> CompilerResult:
    return CompilerResult(
        outcome=CompilerOutcome.COMPILED,
        reason_code=reason_code,
        compiler_id=compiler_id,
        plan=plan,
        family_name=family_name,
    )


def _text_arg(arguments: Mapping[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


class AuthorizationDifferentialCompiler:
    """Known-family compiler for OBJECT_AUTHORIZATION. Bypasses the generic planner."""

    compiler_id = COMPILER_AUTHORIZATION_DIFFERENTIAL

    def compile(self, request: CompilerRequest) -> CompilerResult:
        arguments = request.arguments
        authorized_origin = _text_arg(arguments, "authorized_origin") or _text_arg(
            arguments, "origin"
        )
        actor = _text_arg(arguments, "actor")
        own_object = _text_arg(arguments, "own_object")
        cross_object = _text_arg(arguments, "cross_object")
        mode = _text_arg(arguments, "mode") or "vulnerable"
        if not all((authorized_origin, actor, own_object, cross_object)):
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_MISSING_SEMANTICS,
                "AUTHORIZATION_DIFFERENTIAL_FIELDS_REQUIRED",
                family_name=request.family_name,
            )
        try:
            plan = plan_authorization_differential(
                request.hypothesis_id,
                budget_id=request.budget_id,
                target_reference=request.target_reference,
                authorized_origin=authorized_origin,
                actor=actor,
                own_object=own_object,
                cross_object=cross_object,
                mode=mode,
            )
        except ExperimentCompileError as exc:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_INVALID_INPUT,
                exc.reason_code,
                family_name=request.family_name,
            )
        except Exception:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_INVALID_INPUT,
                "AUTHORIZATION_DIFFERENTIAL_COMPILE_REJECTED",
                family_name=request.family_name,
            )
        return _compiled(self.compiler_id, plan, family_name=request.family_name)


class StateTransitionCompiler:
    """Known-family compiler for WORKFLOW_STATE_TRANSITION. Bypasses the generic planner."""

    compiler_id = COMPILER_STATE_TRANSITION

    def compile(self, request: CompilerRequest) -> CompilerResult:
        arguments = request.arguments
        authorized_origin = _text_arg(arguments, "authorized_origin") or _text_arg(
            arguments, "origin"
        )
        actor = _text_arg(arguments, "actor")
        resource_id = _text_arg(arguments, "resource_id")
        transition = _text_arg(arguments, "transition")
        area = _text_arg(arguments, "area") or "workflow"
        if not all((authorized_origin, actor, resource_id, transition)):
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_MISSING_SEMANTICS,
                "STATE_TRANSITION_FIELDS_REQUIRED",
                family_name=request.family_name,
            )
        try:
            plan = plan_state_transition(
                request.hypothesis_id,
                budget_id=request.budget_id,
                target_reference=request.target_reference,
                authorized_origin=authorized_origin,
                actor=actor,
                resource_id=resource_id,
                transition=transition,
                area=area,
            )
        except ExperimentCompileError as exc:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_INVALID_INPUT,
                exc.reason_code,
                family_name=request.family_name,
            )
        except Exception:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_INVALID_INPUT,
                "STATE_TRANSITION_COMPILE_REJECTED",
                family_name=request.family_name,
            )
        return _compiled(self.compiler_id, plan, family_name=request.family_name)


class MutationMatrixCellCompiler:
    """Bind a selected MutationMatrixCell onto http.transaction.

    The compiler owns the payload catalog. Incoming query/body/headers are not
    Worker authority and are ignored. AI may select a cell_id only. The selected
    id must exist in the rebuilt authoritative MutationMatrix; caller dimensions
    cannot mint or rewrite cell identity.
    """

    compiler_id = COMPILER_MUTATION_MATRIX_CELL

    def compile(self, request: CompilerRequest) -> CompilerResult:
        arguments = request.arguments
        family_name = request.family_name
        if family_name is None:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_INVALID_INPUT,
                "MUTATION_FAMILY_REQUIRED",
                family_name=family_name,
            )
        cell_id = _text_arg(arguments, "cell_id") or _text_arg(arguments, "selected_cell_id")
        origin = _text_arg(arguments, "authorized_origin") or _text_arg(arguments, "origin")
        path = _text_arg(arguments, "path") or "/"
        family_id = request.family_id.strip() if isinstance(request.family_id, str) and request.family_id.strip() else None
        matrix_hash = _text_arg(arguments, "matrix_hash")
        if cell_id is None:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_MISSING_SEMANTICS,
                "MUTATION_MATRIX_CELL_TARGET_REQUIRED",
                family_name=family_name,
            )
        try:
            cell = lookup_authoritative_mutation_cell(
                family_name=family_name,
                cell_id=cell_id,
                family_id=family_id,
                matrix_hash=matrix_hash,
            )
        except MutationCellIdentityError as exc:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_UNKNOWN_CELL,
                exc.reason_code,
                family_name=family_name,
            )
        if origin is None:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_MISSING_SEMANTICS,
                "MUTATION_MATRIX_CELL_TARGET_REQUIRED",
                family_name=family_name,
            )
        required = FAMILY_REQUIRED_DIMENSIONS.get(family_name, ())
        missing = [name for name in required if not str(cell.dimension_values.get(name) or "").strip()]
        if missing:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_MISSING_SEMANTICS,
                "MUTATION_MATRIX_CELL_DIMENSIONS_INCOMPLETE",
                family_name=family_name,
            )
        try:
            binding = bind_mutation_matrix_cell(
                family_name=family_name,
                cell_id=cell.cell_id,
                dimension_values=cell.dimension_values,
                control=cell.control,
                authorized_origin=origin,
                path=path,
            )
            plan = compile_experiment_intent(
                ExperimentIntent(
                    hypothesis_id=request.hypothesis_id,
                    capability_id=HTTP_TRANSACTION_CAPABILITY,
                    action=binding.action,
                    target_reference=request.target_reference,
                    arguments=binding.template.arguments(),
                    requested_budget_id=request.budget_id,
                    expected_observation=binding.expected_observation,
                    disconfirming_observation=binding.disconfirming_observation,
                    evaluation_strategy=binding.evaluation_strategy,
                    requested_side_effect=request.requested_side_effect,
                )
            )
        except MutationCellContractError as exc:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_MISSING_SEMANTICS,
                exc.reason_code,
                family_name=family_name,
            )
        except ExperimentCompileError as exc:
            outcome = (
                CompilerOutcome.BLOCKED_UNSUPPORTED_CAPABILITY
                if exc.reason_code == "UNKNOWN_CAPABILITY"
                else CompilerOutcome.BLOCKED_INVALID_INPUT
            )
            return _blocked(
                self.compiler_id, outcome, exc.reason_code, family_name=family_name
            )
        return _compiled(self.compiler_id, plan, family_name=family_name)


class MutationVariantCompiler:
    """Compile an already-concrete MutationEngine variant onto http.transaction.

    Input is a MutationVariant-shaped argument map (capability/action/HTTP fields
    produced by `research.mutation.families`), not a HunterFamily matrix cell.
    """

    compiler_id = COMPILER_MUTATION_VARIANT

    def compile(self, request: CompilerRequest) -> CompilerResult:
        arguments = request.arguments
        capability = _text_arg(arguments, "capability_id")
        action = _text_arg(arguments, "action")
        if capability != HTTP_TRANSACTION_CAPABILITY or action is None:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_UNSUPPORTED_CAPABILITY,
                "MUTATION_VARIANT_REQUIRES_HTTP_TRANSACTION",
                family_name=request.family_name,
            )
        http_arguments = {
            key: arguments[key] for key in HTTP_TRANSACTION_ARG_KEYS if key in arguments
        }
        try:
            plan = compile_experiment_intent(
                ExperimentIntent(
                    hypothesis_id=request.hypothesis_id,
                    capability_id=HTTP_TRANSACTION_CAPABILITY,
                    action=action,
                    target_reference=request.target_reference,
                    arguments=http_arguments,
                    requested_budget_id=request.budget_id,
                    expected_observation=(
                        _text_arg(arguments, "expected_observation")
                        or HTTP_TRANSACTION_EXPECTED_OBSERVATION
                    ),
                    disconfirming_observation=(
                        _text_arg(arguments, "disconfirming_observation")
                        or HTTP_TRANSACTION_DISCONFIRMING_OBSERVATION
                    ),
                    evaluation_strategy=(
                        _text_arg(arguments, "evaluation_strategy")
                        or HTTP_TRANSACTION_EVALUATION_STRATEGY
                    ),
                    requested_side_effect=request.requested_side_effect,
                )
            )
        except ExperimentCompileError as exc:
            outcome = (
                CompilerOutcome.BLOCKED_UNSUPPORTED_CAPABILITY
                if exc.reason_code == "UNKNOWN_CAPABILITY"
                else CompilerOutcome.BLOCKED_INVALID_INPUT
            )
            return _blocked(
                self.compiler_id, outcome, exc.reason_code, family_name=request.family_name
            )
        return _compiled(self.compiler_id, plan, family_name=request.family_name)


class ProtocolStepCompiler:
    """Bind one ProtocolParserPlanStep onto http.raw_exchange.

    Does not treat an approved ProtocolPlan as an execution token. Does not
    impersonate wire semantics through http.transaction. Incoming method/body
    fields are ignored; framing_profile is compiler-owned.
    """

    compiler_id = COMPILER_PROTOCOL_STEP

    def compile(self, request: CompilerRequest) -> CompilerResult:
        arguments = request.arguments
        step_id = _text_arg(arguments, "step_id") or _text_arg(arguments, "selected_step_id")
        control = _text_arg(arguments, "control")
        origin = _text_arg(arguments, "authorized_origin") or _text_arg(arguments, "origin")
        path = _text_arg(arguments, "path") or "/"
        lane = _text_arg(arguments, "protocol_lane") or _text_arg(arguments, "lane")
        dimensions = arguments.get("dimension_values")
        if not isinstance(dimensions, Mapping):
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_MISSING_SEMANTICS,
                "PROTOCOL_STEP_DIMENSIONS_INCOMPLETE",
                family_name=request.family_name,
            )
        if step_id is None or control is None or origin is None or lane is None:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_MISSING_SEMANTICS,
                "PROTOCOL_STEP_TARGET_REQUIRED",
                family_name=request.family_name,
            )
        try:
            binding = bind_protocol_step(
                family_name=request.family_name or "",
                step_id=step_id,
                dimension_values=dimensions,
                control=control,
                authorized_origin=origin,
                path=path,
                protocol_lane=lane,
            )
            plan = compile_experiment_intent(
                ExperimentIntent(
                    hypothesis_id=request.hypothesis_id,
                    capability_id=HTTP_RAW_EXCHANGE_CAPABILITY,
                    action=HTTP_RAW_EXCHANGE_ACTION,
                    target_reference=request.target_reference,
                    arguments=dict(binding.arguments),
                    requested_budget_id=request.budget_id,
                    expected_observation=binding.expected_observation,
                    disconfirming_observation=binding.disconfirming_observation,
                    evaluation_strategy=binding.evaluation_strategy,
                    requested_side_effect=request.requested_side_effect,
                )
            )
        except ProtocolStepContractError as exc:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_MISSING_SEMANTICS,
                exc.reason_code,
                family_name=request.family_name,
            )
        except ExperimentCompileError as exc:
            outcome = (
                CompilerOutcome.BLOCKED_UNSUPPORTED_CAPABILITY
                if exc.reason_code == "UNKNOWN_CAPABILITY"
                else CompilerOutcome.BLOCKED_INVALID_INPUT
            )
            return _blocked(
                self.compiler_id, outcome, exc.reason_code, family_name=request.family_name
            )
        return _compiled(self.compiler_id, plan, family_name=request.family_name)


class OastInteractionCompiler:
    """Compile an armed OAST delivery onto http.transaction. Does not fake callbacks."""

    compiler_id = COMPILER_OAST_INTERACTION

    def compile(self, request: CompilerRequest) -> CompilerResult:
        from zest.application.oast_source import OAST_CALLBACK_EVALUATION_STRATEGY

        arguments = request.arguments
        action = _text_arg(arguments, "action")
        if action not in {"read", "mutate"}:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_MISSING_SEMANTICS,
                "OAST_REQUIRES_HTTP_TRANSACTION_ACTION",
                family_name=request.family_name,
            )
        http_arguments = {
            key: arguments[key] for key in HTTP_TRANSACTION_ARG_KEYS if key in arguments
        }
        try:
            plan = compile_experiment_intent(
                ExperimentIntent(
                    hypothesis_id=request.hypothesis_id,
                    capability_id=HTTP_TRANSACTION_CAPABILITY,
                    action=action,
                    target_reference=request.target_reference,
                    arguments=http_arguments,
                    requested_budget_id=request.budget_id,
                    expected_observation=(
                        _text_arg(arguments, "expected_observation")
                        or "correlated callback observed for the armed sink"
                    ),
                    disconfirming_observation=(
                        _text_arg(arguments, "disconfirming_observation")
                        or "no correlated callback before deadline"
                    ),
                    evaluation_strategy=OAST_CALLBACK_EVALUATION_STRATEGY,
                    requested_side_effect=request.requested_side_effect,
                )
            )
        except ExperimentCompileError as exc:
            outcome = (
                CompilerOutcome.BLOCKED_UNSUPPORTED_CAPABILITY
                if exc.reason_code == "UNKNOWN_CAPABILITY"
                else CompilerOutcome.BLOCKED_INVALID_INPUT
            )
            return _blocked(
                self.compiler_id, outcome, exc.reason_code, family_name=request.family_name
            )
        return _compiled(self.compiler_id, plan, family_name=request.family_name)


class GenericPlannerCompiler:
    """Fallback for registry-external / exploratory work only.

    Known families must never reach this compiler. Planning aliases are rejected
    as unsupported capabilities rather than silently compiled.
    """

    compiler_id = COMPILER_GENERIC_PLANNER

    def compile(self, request: CompilerRequest) -> CompilerResult:
        arguments = request.arguments
        capability = _text_arg(arguments, "capability_id") or _text_arg(
            arguments, "suggested_capability"
        )
        if capability in PLANNING_ALIAS_CAPABILITIES:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_UNSUPPORTED_CAPABILITY,
                "PLANNING_ALIAS_IS_NOT_A_WORKER_CAPABILITY",
                family_name=request.family_name,
            )
        if request.proposal is not None and request.challenge is not None:
            return self._from_proposal(request)
        if capability is None:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_MISSING_SEMANTICS,
                "GENERIC_PLANNER_REQUIRES_CAPABILITY",
                family_name=request.family_name,
            )
        catalog = load_capability_registry()
        definition = catalog.get(capability)
        if definition is None or definition.executor_class != WORKER_EXECUTOR_CLASS:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_UNSUPPORTED_CAPABILITY,
                "UNKNOWN_CAPABILITY",
                family_name=request.family_name,
            )
        action = _text_arg(arguments, "action")
        if action is None:
            if len(definition.actions) != 1:
                return _blocked(
                    self.compiler_id,
                    CompilerOutcome.BLOCKED_MISSING_SEMANTICS,
                    "GENERIC_PLANNER_ACTION_AMBIGUOUS",
                    family_name=request.family_name,
                )
            action = next(iter(definition.actions))
        expected = _text_arg(arguments, "expected_observation")
        disconfirming = _text_arg(arguments, "disconfirming_observation")
        strategy = _text_arg(arguments, "evaluation_strategy") or f"{capability}.v1"
        if expected is None or disconfirming is None:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_MISSING_SEMANTICS,
                "GENERIC_PLANNER_REQUIRES_OBSERVATION_CONTRACT",
                family_name=request.family_name,
            )
        excluded = {
            "capability_id",
            "suggested_capability",
            "action",
            "expected_observation",
            "disconfirming_observation",
            "evaluation_strategy",
            "family_name",
            "family_id",
            "claim",
            "node_id",
            "identity_id",
            "compiler_id",
            "worker_dispatch",
            "mutation_rule_id",
        }
        intent_arguments = {key: value for key, value in arguments.items() if key not in excluded}
        try:
            plan = compile_experiment_intent(
                ExperimentIntent(
                    hypothesis_id=request.hypothesis_id,
                    capability_id=capability,
                    action=action,
                    target_reference=request.target_reference,
                    arguments=intent_arguments,
                    requested_budget_id=request.budget_id,
                    expected_observation=expected,
                    disconfirming_observation=disconfirming,
                    evaluation_strategy=strategy,
                    requested_side_effect=request.requested_side_effect,
                )
            )
        except ExperimentCompileError as exc:
            outcome = (
                CompilerOutcome.BLOCKED_UNSUPPORTED_CAPABILITY
                if exc.reason_code == "UNKNOWN_CAPABILITY"
                else CompilerOutcome.BLOCKED_INVALID_INPUT
            )
            return _blocked(
                self.compiler_id, outcome, exc.reason_code, family_name=request.family_name
            )
        return _compiled(self.compiler_id, plan, family_name=request.family_name)

    def _from_proposal(self, request: CompilerRequest) -> CompilerResult:
        assert request.proposal is not None
        assert request.challenge is not None
        capability = request.proposal.suggested_capability
        if capability in PLANNING_ALIAS_CAPABILITIES:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_UNSUPPORTED_CAPABILITY,
                "PLANNING_ALIAS_IS_NOT_A_WORKER_CAPABILITY",
                family_name=request.family_name,
            )
        try:
            plan = plan_admitted_hypothesis(
                request.hypothesis_id,
                request.proposal,
                request.challenge,
                budget_id=request.budget_id,
                target_reference=request.target_reference,
            )
        except ExperimentCompileError as exc:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_INVALID_INPUT,
                exc.reason_code,
                family_name=request.family_name,
            )
        except Exception:
            return _blocked(
                self.compiler_id,
                CompilerOutcome.BLOCKED_INVALID_INPUT,
                "GENERIC_PLANNER_PROPOSAL_REJECTED",
                family_name=request.family_name,
            )
        return _compiled(self.compiler_id, plan, family_name=request.family_name)


def default_family_compilers() -> dict[str, ExperimentCompiler]:
    compilers: dict[str, ExperimentCompiler] = {
        FAMILY_OBJECT_AUTHORIZATION: AuthorizationDifferentialCompiler(),
        FAMILY_WORKFLOW_STATE_TRANSITION: StateTransitionCompiler(),
    }
    mutation = MutationMatrixCellCompiler()
    for name in MUTATION_MATRIX_FAMILIES:
        compilers[name] = mutation
    protocol = ProtocolStepCompiler()
    for name in PROTOCOL_FAMILIES:
        compilers[name] = protocol
    from zest.application.oast_source import OAST_EXECUTABLE_FAMILIES

    oast = OastInteractionCompiler()
    for name in OAST_EXECUTABLE_FAMILIES:
        compilers[name] = oast
    return compilers


class ExperimentCompilerRegistry:
    """Keyed by HunterFamily.name. Unknown names fall back to the generic planner."""

    def __init__(
        self,
        compilers: Mapping[str, ExperimentCompiler] | None = None,
        *,
        generic: ExperimentCompiler | None = None,
        mutation_variant: ExperimentCompiler | None = None,
    ) -> None:
        self._by_family_name = dict(compilers if compilers is not None else default_family_compilers())
        self._generic = generic or GenericPlannerCompiler()
        self._mutation_variant = mutation_variant or MutationVariantCompiler()

    def compiler_for(self, family_name: str | None) -> ExperimentCompiler:
        if family_name and family_name in self._by_family_name:
            return self._by_family_name[family_name]
        return self._generic

    def compile(self, request: CompilerRequest) -> CompilerResult:
        family_name = request.family_name
        if family_name and family_name in self._by_family_name:
            return self._by_family_name[family_name].compile(request)
        if request.arguments.get("mutation_rule_id"):
            return self._mutation_variant.compile(request)
        from zest.application.oast_source import OAST_EXECUTABLE_FAMILIES

        if family_name in OAST_EXECUTABLE_FAMILIES or request.arguments.get("oast_family"):
            return OastInteractionCompiler().compile(request)
        return self._generic.compile(request)


def assert_plan_not_understated(plan: ExperimentPlan) -> None:
    """Side-effect on a compiled plan must match the capability registry minimum."""

    catalog = load_capability_registry()
    definition = catalog.get(plan.required_capability)
    if definition is None:
        raise ExperimentCompileError("UNKNOWN_CAPABILITY", "unknown capability")
    action = definition.action(plan.action)
    if action is None:
        raise ExperimentCompileError("UNKNOWN_ACTION", "unknown action")
    if plan.side_effect_level < action.minimum_side_effect_level:
        raise ExperimentCompileError(
            "RISK_UNDERSTATEMENT", "compiled side effect is below action minimum"
        )
    if plan.side_effect_level > action.maximum_side_effect_level:
        raise ExperimentCompileError(
            "RISK_EXCEEDS_CAPABILITY", "compiled side effect exceeds action maximum"
        )


# Re-export observation constants so application dispatch does not re-derive them.
AUTHORIZATION_EXPECTED_OBSERVATION = HTTP_AUTHORIZATION_EXPECTED_OBSERVATION
STATE_TRANSITION_EXPECTED_OBSERVATION = HTTP_STATE_TRANSITION_EXPECTED_OBSERVATION
AUTHORIZATION_CAPABILITY = HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY
STATE_TRANSITION_CAPABILITY = HTTP_STATE_TRANSITION_CAPABILITY
