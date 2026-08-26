"""Ledger-backed ProofResolver. No raw secrets or full payloads exposed."""

from __future__ import annotations

from zest.data.records import EvidenceRecord, ImpactChainRecord
from zest.data.unit_of_work import UnitOfWork
from zest.research.impact.chain import ImpactChain, ImpactEdge, ImpactNode, ImpactScopeRef
from zest.research.impact.types import ImpactKind, ImpactRelation, ProofRecord, ProofResolver


# Mapping from admitted evidence claim_scope to demonstrated capabilities.
# Evidence must already be admitted; this resolver only reads metadata.
_CLAIM_SCOPE_TO_CAPABILITIES: dict[str, frozenset[str]] = {
    "Authenticated actor can read another actor's account object because object "
    "authorization is missing on the vulnerable endpoint.": frozenset({"READ_OTHER_OBJECT"}),
    "Authenticated requester performed an unauthorized workflow state transition "
    "because role or sequence authorization is missing on the workflow endpoint.": frozenset(
        {"WORKFLOW_TRANSITION_WITHOUT_AUTH"}
    ),
}


class UnitOfWorkProofResolver(ProofResolver):
    """Resolve proof_ids against the append-only ledger."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def resolve(self, proof_id: str, expected_run_id: str) -> ProofRecord | None:
        evidence = self._uow.evidence.get(proof_id)
        if evidence is not None:
            # Evidence carries its own run. The validator compares against the
            # chain's expected run and emits CROSS_RUN_PROOF if they differ.
            return self._resolve_evidence(evidence)
        observation = self._uow.observations.get(proof_id)
        if observation is not None:
            worker_result = self._uow.worker_results.get(observation.worker_result_id)
            if worker_result is None:
                # Run cannot be determined: fail-closed.
                return None
            return ProofRecord(
                proof_id=proof_id,
                research_run_id=worker_result.research_run_id,
                target_reference=observation.observation_kind,
                demonstrated_capabilities=frozenset(),
            )
        experiment = self._uow.experiments.get(proof_id)
        if experiment is not None:
            return ProofRecord(
                proof_id=proof_id,
                research_run_id=experiment.research_run_id,
                target_reference=experiment.experiment_id,
                demonstrated_capabilities=frozenset(),
            )
        return None

    def _resolve_evidence(self, evidence: EvidenceRecord) -> ProofRecord:
        capabilities = _CLAIM_SCOPE_TO_CAPABILITIES.get(evidence.claim_scope, frozenset())
        return ProofRecord(
            proof_id=evidence.evidence_id,
            research_run_id=evidence.research_run_id,
            target_reference=evidence.experiment_id,
            demonstrated_capabilities=capabilities,
        )


def rebuild_impact_chain(uow: UnitOfWork, record: ImpactChainRecord) -> ImpactChain:
    """Rebuild an ImpactChain from its persisted records."""

    node_records = uow.impact_chains.get_nodes(record.chain_id)
    edge_records = uow.impact_chains.get_edges(record.chain_id)
    nodes = tuple(
        ImpactNode(
            node_id=node.node_id,
            proof_refs=node.proof_refs,
            impact_kind=ImpactKind(node.impact_kind),
            claim_text=node.claim_text,
            scope_ref=ImpactScopeRef(**node.scope_ref),
            provenance={"source": "impact_chain.rebuild", "chain_id": record.chain_id},
        )
        for node in node_records
    )
    edges = tuple(
        ImpactEdge(
            from_node_id=edge.from_node_id,
            to_node_id=edge.to_node_id,
            relation=ImpactRelation(edge.relation),
            proof_refs=edge.proof_refs,
        )
        for edge in edge_records
    )
    return ImpactChain(chain_id=record.chain_id, nodes=nodes, edges=edges)
