"""Resolve a selected MutationMatrix cell from the authoritative rebuilt matrix.

The model may select a cell_id. It may not mint cell identity by supplying
complete dimension_values. Caller copies of dimensions are not authoritative.

The matrix is deterministic from this research-layer catalog (family_id,
dimensions, controls). Tests compare it to the HunterFamily seed so the two
cannot silently drift.
"""

from __future__ import annotations

from dataclasses import dataclass

from zest.research.mutation.cell_contract import FAMILY_REQUIRED_DIMENSIONS
from zest.research.mutation.matrix import (
    MutationMatrixCell,
    MutationMatrixPlan,
    build_mutation_matrix,
)
from zest.research.selection import HunterFamilyView
from zest.research.types import ResearchInputError


class MutationCellIdentityError(ResearchInputError):
    """Selected cell_id is not an authoritative matrix member. Not missing semantics."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True)
class MutationMatrixFamilyCatalogEntry:
    family_id: str
    family_name: str
    dimensions: tuple[str, ...]
    controls: tuple[str, ...]


MUTATION_MATRIX_FAMILY_CATALOG: tuple[MutationMatrixFamilyCatalogEntry, ...] = (
    MutationMatrixFamilyCatalogEntry(
        "hf-sqli",
        "SQL_INJECTION",
        ("input_vector", "encoding", "parser_delta"),
        ("secure_fixture", "deceptive_fixture", "read_back"),
    ),
    MutationMatrixFamilyCatalogEntry(
        "hf-ssti",
        "SERVER_SIDE_TEMPLATE_INJECTION",
        ("template_engine_probe", "encoding"),
        ("secure_fixture", "deceptive_fixture"),
    ),
    MutationMatrixFamilyCatalogEntry(
        "hf-lfi-rfi",
        "FILE_INCLUDE_AND_PATH_TRAVERSAL",
        ("path_vector", "encoding", "normalization"),
        ("safe_path_control", "deceptive_status_control"),
    ),
    MutationMatrixFamilyCatalogEntry(
        "hf-mass-assignment",
        "MASS_ASSIGNMENT",
        ("field_family", "role", "state_change"),
        ("read_back", "role_boundary_control"),
    ),
    MutationMatrixFamilyCatalogEntry(
        "hf-jwt-crypto",
        "JWT_CRYPTO_AND_CLAIM_CONFUSION",
        ("algorithm", "key_source", "claim"),
        ("valid_token_control", "invalid_token_control"),
    ),
    MutationMatrixFamilyCatalogEntry(
        "hf-cors",
        "CORS_CREDENTIAL_EXFILTRATION_CHAIN",
        ("origin_variant", "credentials", "data_sink"),
        ("non_credentialed_control", "sensitive_endpoint_read_back"),
    ),
    MutationMatrixFamilyCatalogEntry(
        "hf-graphql",
        "GRAPHQL_AUTHORIZATION_AND_INJECTION",
        ("operation_kind", "resolver", "identity"),
        ("introspection_control", "role_boundary_control"),
    ),
    MutationMatrixFamilyCatalogEntry(
        "hf-dom-taint",
        "DOM_TAINT_AND_CLIENT_SIDE_EXECUTION",
        ("source", "sink", "execution_token"),
        ("dom_marker_control", "detection_rotation"),
    ),
    MutationMatrixFamilyCatalogEntry(
        "hf-ai-llm-target",
        "AI_LLM_PROMPT_INJECTION_AND_TOOL_ABUSE",
        ("instruction_channel", "retrieval_context", "tool_boundary"),
        ("benign_prompt_control", "metamorphic_variant", "tool_denial_control"),
    ),
)

_CATALOG_BY_NAME = {entry.family_name: entry for entry in MUTATION_MATRIX_FAMILY_CATALOG}


def mutation_family_view(
    *,
    family_name: str,
    family_id: str | None = None,
) -> HunterFamilyView:
    """Minimal HunterFamilyView used to rebuild the deterministic matrix."""

    entry = _CATALOG_BY_NAME.get(family_name)
    if entry is None:
        raise MutationCellIdentityError(
            "MUTATION_MATRIX_UNKNOWN_FAMILY",
            "family_name is not a mutation matrix family",
        )
    if family_id is not None and family_id != entry.family_id:
        raise MutationCellIdentityError(
            "MUTATION_MATRIX_FAMILY_MISMATCH",
            "family_id does not match the catalog family for this name",
        )
    return HunterFamilyView(
        family_id=entry.family_id,
        name=entry.family_name,
        target_node_kinds=("HTTP_OPERATION",),
        preconditions={"scope_classification": "IN_SCOPE"},
        claim_template="",
        evidence_requirements={
            "required_matrix_dimensions": list(entry.dimensions),
            "required_controls": list(entry.controls),
        },
        validation_tier="V3",
        enabled=True,
        version=1,
    )


def rebuild_authoritative_mutation_matrix(
    *,
    family_name: str,
    family_id: str | None = None,
) -> MutationMatrixPlan:
    if family_name not in FAMILY_REQUIRED_DIMENSIONS:
        raise MutationCellIdentityError(
            "MUTATION_MATRIX_UNKNOWN_FAMILY",
            "family_name is not a mutation matrix family",
        )
    return build_mutation_matrix(mutation_family_view(family_name=family_name, family_id=family_id))


def lookup_authoritative_mutation_cell(
    *,
    family_name: str,
    cell_id: str,
    family_id: str | None = None,
    matrix_hash: str | None = None,
) -> MutationMatrixCell:
    """Return the matrix cell for cell_id, or raise MutationCellIdentityError."""

    if not isinstance(cell_id, str) or not cell_id.strip():
        raise MutationCellIdentityError("CELL_ID_REQUIRED", "cell_id is required")
    matrix = rebuild_authoritative_mutation_matrix(
        family_name=family_name, family_id=family_id
    )
    if matrix_hash is not None and matrix_hash != matrix.matrix_hash:
        raise MutationCellIdentityError(
            "MUTATION_MATRIX_CONTEXT_MISMATCH",
            "matrix_hash does not match the rebuilt authoritative matrix",
        )
    wanted = cell_id.strip()
    for cell in matrix.cells:
        if cell.cell_id == wanted:
            if family_id is not None and cell.family_id != family_id:
                raise MutationCellIdentityError(
                    "MUTATION_MATRIX_FAMILY_MISMATCH",
                    "cell family_id does not match the requested family",
                )
            return cell
    raise MutationCellIdentityError(
        "MUTATION_MATRIX_UNKNOWN_CELL",
        "selected_cell_id is not a member of the authoritative MutationMatrix",
    )
