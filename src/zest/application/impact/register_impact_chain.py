"""Register an ImpactChain in the ledger. Separate from FindingProposal admission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.data.records import (
    ImpactChainEdgeRecord,
    ImpactChainNodeRecord,
    ImpactChainRecord,
)
from zest.research.impact.chain import ImpactChain
from zest.research.impact.types import ProofResolver
from zest.research.impact.validator import validate_chain


@dataclass(frozen=True)
class RegisterImpactChainCommand:
    chain_id: str
    research_run_id: str
    program_id: str
    chain: ImpactChain
    graph_hash: str | None = None


@dataclass(frozen=True)
class RegisterImpactChainResult:
    chain_id: str
    registered_at: datetime


class RegisterImpactChain:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(
        self,
        command: RegisterImpactChainCommand,
        resolver: ProofResolver,
    ) -> RegisterImpactChainResult:
        structural = validate_chain(command.chain, resolver, command.research_run_id)
        if not structural.valid:
            raise ApplicationError(
                f"impact chain validation failed: {structural.reason_codes}"
            )
        if command.graph_hash is not None and len(command.graph_hash) != 64:
            raise ApplicationError("graph_hash must be a SHA-256 hex digest")

        now = self._clock.now()
        nodes = tuple(
            ImpactChainNodeRecord(
                node_id=node.node_id,
                chain_id=command.chain.chain_id,
                impact_kind=node.impact_kind.value,
                claim_text=node.claim_text,
                scope_ref=dict(node.scope_ref.__dict__),
                proof_refs=node.proof_refs,
                ordering=index,
                created_at=now,
            )
            for index, node in enumerate(command.chain.nodes)
        )
        edges = tuple(
            ImpactChainEdgeRecord(
                edge_id=new_opaque_id(),
                chain_id=command.chain.chain_id,
                from_node_id=edge.from_node_id,
                to_node_id=edge.to_node_id,
                relation=edge.relation.value,
                proof_refs=edge.proof_refs,
                created_at=now,
            )
            for edge in command.chain.edges
        )
        record = ImpactChainRecord(
            chain_id=command.chain.chain_id,
            research_run_id=command.research_run_id,
            program_id=command.program_id,
            graph_hash=command.graph_hash,
            created_at=now,
        )
        with self._uow_factory.open() as uow:
            uow.impact_chains.insert(record, nodes, edges)
            uow.commit()
        return RegisterImpactChainResult(
            chain_id=command.chain.chain_id,
            registered_at=now,
        )
