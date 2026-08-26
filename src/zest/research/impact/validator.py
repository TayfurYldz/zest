"""ImpactChain validation against a ProofResolver."""

from __future__ import annotations

from zest.research.impact.chain import ImpactChain
from zest.research.impact.types import ChainValidation, ProofResolver


def validate_chain(
    chain: ImpactChain,
    resolver: ProofResolver,
    expected_run_id: str,
) -> ChainValidation:
    """Validate that every node and edge references at least one existing proof.

    The resolver is a port implemented in the application layer; the research
    layer remains database-agnostic. Proofs must belong to the same research
    run as the chain (K4 cross-run confinement). Node proof requirements are
    not relaxed by adding edge proofs.
    """

    reason_codes: list[str] = []
    for node in chain.nodes:
        if not node.proof_refs:
            reason_codes.append("EMPTY_PROOF_REFS")
            continue
        _collect_proof_failures(node.proof_refs, resolver, expected_run_id, reason_codes)
    for edge in chain.edges:
        if not edge.proof_refs:
            reason_codes.append("EMPTY_EDGE_PROOF_REFS")
            continue
        _collect_proof_failures(edge.proof_refs, resolver, expected_run_id, reason_codes)
    if reason_codes:
        return ChainValidation(valid=False, reason_codes=tuple(reason_codes))
    return ChainValidation(valid=True, reason_codes=("IMPACT_CHAIN_PROOFS_RESOLVED",))


def _collect_proof_failures(
    proof_refs: tuple[str, ...],
    resolver: ProofResolver,
    expected_run_id: str,
    reason_codes: list[str],
) -> None:
    for proof_id in proof_refs:
        record = resolver.resolve(proof_id, expected_run_id)
        if record is None:
            reason_codes.append("HALLUCINATED_OR_ABSENT_PROOF")
        elif record.research_run_id != expected_run_id:
            reason_codes.append("CROSS_RUN_PROOF")
