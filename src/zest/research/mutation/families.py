"""Deterministic mutation families. No execution, no network, no scope escape."""

from __future__ import annotations

from typing import Any, Mapping

from zest.research.discovery.graph import AttackSurfaceNode
from zest.research.discovery.types import AttackSurfaceNodeKind
from zest.research.mutation.types import MutationRule, MutationVariant
from zest.research.types import ResearchInputError

SUPPORTED_MUTATION_NODE_KINDS = frozenset(
    {
        AttackSurfaceNodeKind.HTTP_OPERATION,
        AttackSurfaceNodeKind.EXACT_PATH,
    }
)


def _require_mutation_node(node: AttackSurfaceNode) -> None:
    if node.kind not in SUPPORTED_MUTATION_NODE_KINDS:
        raise ResearchInputError(
            f"mutation families require HTTP_OPERATION or EXACT_PATH node, got {node.kind}"
        )


def _node_origin(node: AttackSurfaceNode) -> str:
    attrs = node.attributes or {}
    origin = attrs.get("origin") or attrs.get("authorized_origin") or ""
    if not isinstance(origin, str):
        raise ResearchInputError("node origin must be a string")
    return origin


def _node_path(node: AttackSurfaceNode) -> str:
    attrs = node.attributes or {}
    path = attrs.get("path") or "/"
    if not isinstance(path, str):
        raise ResearchInputError("node path must be a string")
    return path


def _node_method(node: AttackSurfaceNode) -> str:
    attrs = node.attributes or {}
    method = attrs.get("method") or "GET"
    if not isinstance(method, str):
        raise ResearchInputError("node method must be a string")
    return method


def _node_query_params(node: AttackSurfaceNode) -> tuple[str, ...]:
    attrs = node.attributes or {}
    params = attrs.get("query_params") or attrs.get("query") or ()
    if isinstance(params, str):
        return (params,)
    if isinstance(params, (list, tuple)):
        return tuple(str(p) for p in params)
    return ()


def _variant_id(prefix: str, family_id: str, rule_id: str, index: int) -> str:
    return f"{prefix}:{family_id}:{rule_id}:{index:03d}"


def _base_arguments(node: AttackSurfaceNode) -> dict[str, Any]:
    return {
        "authorized_origin": _node_origin(node),
        "path": _node_path(node),
        "method": _node_method(node),
    }


def _safe_provenance(node: AttackSurfaceNode, family_id: str, rule_id: str) -> dict[str, Any]:
    return {
        "node_id": node.node_id,
        "family_id": family_id,
        "mutation_rule_id": rule_id,
        "node_canonical_key": node.canonical_key,
    }


class ParamPollutionFamily:
    """Duplicate / array-style query parameters."""

    RULES = (
        MutationRule("duplicate_param", "param_pollution", "duplicate a query parameter"),
        MutationRule("array_param", "param_pollution", "array-style query parameter"),
    )

    @property
    def family_id(self) -> str:
        return "param_pollution"

    def generate(
        self,
        node: AttackSurfaceNode,
        provenance: Mapping[str, Any],
        variant_id_prefix: str,
    ) -> tuple[MutationVariant, ...]:
        _require_mutation_node(node)
        params = _node_query_params(node)
        if not params:
            return ()
        variants: list[MutationVariant] = []
        for index, param in enumerate(params):
            base = _base_arguments(node)
            base["query"] = {param: f"{param}_value", f"{param}": f"{param}_value"}
            variants.append(
                MutationVariant(
                    variant_id=_variant_id(variant_id_prefix, self.family_id, "duplicate_param", index),
                    node_id=node.node_id,
                    family_id=self.family_id,
                    mutation_rule_id="duplicate_param",
                    target_reference=node.canonical_key,
                    scope_classification=node.scope_classification,
                    capability_id="http.transaction",
                    action="read",
                    arguments=base,
                    provenance={**provenance, **_safe_provenance(node, self.family_id, "duplicate_param")},
                )
            )
            base2 = _base_arguments(node)
            base2["query"] = {f"{param}[]": "1"}
            variants.append(
                MutationVariant(
                    variant_id=_variant_id(variant_id_prefix, self.family_id, "array_param", index),
                    node_id=node.node_id,
                    family_id=self.family_id,
                    mutation_rule_id="array_param",
                    target_reference=node.canonical_key,
                    scope_classification=node.scope_classification,
                    capability_id="http.transaction",
                    action="read",
                    arguments=base2,
                    provenance={**provenance, **_safe_provenance(node, self.family_id, "array_param")},
                )
            )
        return tuple(variants)


class TypeJugglingFamily:
    """Type-confusion candidates for numeric-looking parameters."""

    RULES = (
        MutationRule("numeric_string", "type_juggling", "numeric parameter as string variant"),
        MutationRule("boolean_string", "type_juggling", "boolean-like parameter value"),
        MutationRule("float_string", "type_juggling", "float-like parameter value"),
    )

    TYPE_VARIANTS = ("1abc", "true", "1.0", "0", "-0", "null")

    @property
    def family_id(self) -> str:
        return "type_juggling"

    def generate(
        self,
        node: AttackSurfaceNode,
        provenance: Mapping[str, Any],
        variant_id_prefix: str,
    ) -> tuple[MutationVariant, ...]:
        _require_mutation_node(node)
        params = _node_query_params(node)
        if not params:
            return ()
        variants: list[MutationVariant] = []
        for param_index, param in enumerate(params):
            for value_index, value in enumerate(self.TYPE_VARIANTS):
                base = _base_arguments(node)
                base["query"] = {param: value}
                variants.append(
                    MutationVariant(
                        variant_id=_variant_id(
                            variant_id_prefix, self.family_id, "type_juggling", param_index * 10 + value_index
                        ),
                        node_id=node.node_id,
                        family_id=self.family_id,
                        mutation_rule_id="type_juggling",
                        target_reference=node.canonical_key,
                        scope_classification=node.scope_classification,
                        capability_id="http.transaction",
                        action="read",
                        arguments=base,
                        provenance={**provenance, **_safe_provenance(node, self.family_id, "type_juggling")},
                    )
                )
        return tuple(variants)


class BoundaryValueFamily:
    """Boundary values for numeric / identifier parameters."""

    BOUNDARY_VALUES = ("-1", "0", "1", "2147483647", "99999999999999999999")

    @property
    def family_id(self) -> str:
        return "boundary_value"

    def generate(
        self,
        node: AttackSurfaceNode,
        provenance: Mapping[str, Any],
        variant_id_prefix: str,
    ) -> tuple[MutationVariant, ...]:
        _require_mutation_node(node)
        params = _node_query_params(node)
        if not params:
            return ()
        variants: list[MutationVariant] = []
        for param_index, param in enumerate(params):
            for value_index, value in enumerate(self.BOUNDARY_VALUES):
                base = _base_arguments(node)
                base["query"] = {param: value}
                variants.append(
                    MutationVariant(
                        variant_id=_variant_id(
                            variant_id_prefix, self.family_id, "boundary", param_index * 10 + value_index
                        ),
                        node_id=node.node_id,
                        family_id=self.family_id,
                        mutation_rule_id="boundary",
                        target_reference=node.canonical_key,
                        scope_classification=node.scope_classification,
                        capability_id="http.transaction",
                        action="read",
                        arguments=base,
                        provenance={**provenance, **_safe_provenance(node, self.family_id, "boundary")},
                    )
                )
        return tuple(variants)


class AuthHeaderVariationFamily:
    """Authorization-related header variations."""

    HEADER_VARIANTS = (
        {"Authorization": "Bearer null"},
        {"Authorization": "Bearer "},
        {"X-Forwarded-For": "127.0.0.1"},
        {"X-Original-Url": "/admin"},
    )

    @property
    def family_id(self) -> str:
        return "auth_header_variation"

    def generate(
        self,
        node: AttackSurfaceNode,
        provenance: Mapping[str, Any],
        variant_id_prefix: str,
    ) -> tuple[MutationVariant, ...]:
        _require_mutation_node(node)
        variants: list[MutationVariant] = []
        for index, headers in enumerate(self.HEADER_VARIANTS):
            base = _base_arguments(node)
            base["headers"] = headers
            variants.append(
                MutationVariant(
                    variant_id=_variant_id(variant_id_prefix, self.family_id, "auth_header", index),
                    node_id=node.node_id,
                    family_id=self.family_id,
                    mutation_rule_id="auth_header",
                    target_reference=node.canonical_key,
                    scope_classification=node.scope_classification,
                    capability_id="http.transaction",
                    action="read",
                    arguments=base,
                    provenance={**provenance, **_safe_provenance(node, self.family_id, "auth_header")},
                )
            )
        return tuple(variants)


class MethodOverrideFamily:
    """Method override via header or query parameter."""

    OVERRIDES = (
        {"headers": {"X-HTTP-Method-Override": "DELETE"}},
        {"headers": {"X-HTTP-Method-Override": "PUT"}},
        {"query": {"_method": "DELETE"}},
        {"query": {"_method": "PUT"}},
    )

    @property
    def family_id(self) -> str:
        return "method_override"

    def generate(
        self,
        node: AttackSurfaceNode,
        provenance: Mapping[str, Any],
        variant_id_prefix: str,
    ) -> tuple[MutationVariant, ...]:
        _require_mutation_node(node)
        variants: list[MutationVariant] = []
        for index, override in enumerate(self.OVERRIDES):
            base = _base_arguments(node)
            base.update(override)
            variants.append(
                MutationVariant(
                    variant_id=_variant_id(variant_id_prefix, self.family_id, "override", index),
                    node_id=node.node_id,
                    family_id=self.family_id,
                    mutation_rule_id="override",
                    target_reference=node.canonical_key,
                    scope_classification=node.scope_classification,
                    capability_id="http.transaction",
                    action="read",
                    arguments=base,
                    provenance={**provenance, **_safe_provenance(node, self.family_id, "override")},
                )
            )
        return tuple(variants)


class ContentTypeConfusionFamily:
    """Content-Type confusion for mutating requests."""

    CONTENT_TYPES = (
        "application/xml",
        "text/plain",
        "application/x-www-form-urlencoded",
    )

    @property
    def family_id(self) -> str:
        return "content_type_confusion"

    def generate(
        self,
        node: AttackSurfaceNode,
        provenance: Mapping[str, Any],
        variant_id_prefix: str,
    ) -> tuple[MutationVariant, ...]:
        _require_mutation_node(node)
        variants: list[MutationVariant] = []
        for index, content_type in enumerate(self.CONTENT_TYPES):
            base = _base_arguments(node)
            base["method"] = "POST"
            base["content_type"] = content_type
            base["body"] = "<test/>"
            variants.append(
                MutationVariant(
                    variant_id=_variant_id(variant_id_prefix, self.family_id, "content_type", index),
                    node_id=node.node_id,
                    family_id=self.family_id,
                    mutation_rule_id="content_type",
                    target_reference=node.canonical_key,
                    scope_classification=node.scope_classification,
                    capability_id="http.transaction",
                    action="mutate",
                    arguments=base,
                    provenance={**provenance, **_safe_provenance(node, self.family_id, "content_type")},
                )
            )
        return tuple(variants)


class IdOrTraversalCandidateFamily:
    """Path traversal / identifier manipulation candidates."""

    TRAVERSAL_VALUES = ("../", "..%2F", "..%252F", "%2e%2e%2f", "....//")

    @property
    def family_id(self) -> str:
        return "id_or_traversal"

    def generate(
        self,
        node: AttackSurfaceNode,
        provenance: Mapping[str, Any],
        variant_id_prefix: str,
    ) -> tuple[MutationVariant, ...]:
        _require_mutation_node(node)
        params = _node_query_params(node)
        if not params:
            return ()
        variants: list[MutationVariant] = []
        for param_index, param in enumerate(params):
            for value_index, value in enumerate(self.TRAVERSAL_VALUES):
                base = _base_arguments(node)
                base["query"] = {param: value}
                variants.append(
                    MutationVariant(
                        variant_id=_variant_id(
                            variant_id_prefix, self.family_id, "traversal", param_index * 10 + value_index
                        ),
                        node_id=node.node_id,
                        family_id=self.family_id,
                        mutation_rule_id="traversal",
                        target_reference=node.canonical_key,
                        scope_classification=node.scope_classification,
                        capability_id="http.transaction",
                        action="read",
                        arguments=base,
                        provenance={**provenance, **_safe_provenance(node, self.family_id, "traversal")},
                    )
                )
        return tuple(variants)
