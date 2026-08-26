from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.impact.chain import ImpactChain, ImpactEdge, ImpactNode, ImpactScopeRef
from zest.research.impact.types import ImpactKind, ImpactRelation, ProofRecord, ProofResolver
from zest.research.impact.validator import validate_chain


class _FakeResolver(ProofResolver):
    def __init__(self, existing: set[str]) -> None:
        self.existing = existing

    def resolve(self, proof_id: str, expected_run_id: str) -> ProofRecord | None:
        if proof_id not in self.existing:
            return None
        return ProofRecord(
            proof_id=proof_id,
            research_run_id=expected_run_id,
            target_reference="https://example.com",
            demonstrated_capabilities=frozenset(),
        )


def _node(node_id: str, proof_refs: tuple[str, ...]) -> ImpactNode:
    return ImpactNode(
        node_id=node_id,
        proof_refs=proof_refs,
        impact_kind=ImpactKind.DATA_READ,
        claim_text="claim",
        scope_ref=ImpactScopeRef(
            research_run_id="run-1",
            program_id="prog-1",
            target_reference="https://example.com",
        ),
        provenance={},
    )


class ValidateChainTests(unittest.TestCase):
    def test_valid_chain_resolves(self) -> None:
        resolver = _FakeResolver({"proof-a"})
        chain = ImpactChain(
            chain_id="c1",
            nodes=(_node("n1", ("proof-a",)),),
            edges=(),
        )
        result = validate_chain(chain, resolver, "run-1")
        self.assertTrue(result.valid)
        self.assertIn("IMPACT_CHAIN_PROOFS_RESOLVED", result.reason_codes)

    def test_missing_proof_rejected(self) -> None:
        resolver = _FakeResolver(set())
        chain = ImpactChain(
            chain_id="c1",
            nodes=(_node("n1", ("missing-proof",)),),
            edges=(),
        )
        result = validate_chain(chain, resolver, "run-1")
        self.assertFalse(result.valid)
        self.assertIn("HALLUCINATED_OR_ABSENT_PROOF", result.reason_codes)

    def test_one_missing_proof_rejects_chain(self) -> None:
        resolver = _FakeResolver({"proof-a"})
        chain = ImpactChain(
            chain_id="c1",
            nodes=(
                _node("n1", ("proof-a",)),
                _node("n2", ("missing-proof",)),
            ),
            edges=(),
        )
        result = validate_chain(chain, resolver, "run-1")
        self.assertFalse(result.valid)
        self.assertIn("HALLUCINATED_OR_ABSENT_PROOF", result.reason_codes)

    def test_cross_run_proof_rejected(self) -> None:
        class _CrossRunResolver(ProofResolver):
            def resolve(self, proof_id: str, expected_run_id: str) -> ProofRecord | None:
                return ProofRecord(
                    proof_id=proof_id,
                    research_run_id="other-run",
                    target_reference="https://example.com",
                    demonstrated_capabilities=frozenset(),
                )

        resolver = _CrossRunResolver()
        chain = ImpactChain(
            chain_id="c1",
            nodes=(_node("n1", ("proof-a",)),),
            edges=(),
        )
        result = validate_chain(chain, resolver, "run-1")
        self.assertFalse(result.valid)
        self.assertIn("CROSS_RUN_PROOF", result.reason_codes)

    def test_missing_edge_proof_rejected(self) -> None:
        resolver = _FakeResolver({"proof-a"})
        chain = ImpactChain(
            chain_id="c1",
            nodes=(_node("n1", ("proof-a",)), _node("n2", ("proof-a",))),
            edges=(
                ImpactEdge(
                    from_node_id="n1",
                    to_node_id="n2",
                    relation=ImpactRelation.ENABLES,
                    proof_refs=("missing-edge-proof",),
                ),
            ),
        )
        result = validate_chain(chain, resolver, "run-1")
        self.assertFalse(result.valid)
        self.assertIn("HALLUCINATED_OR_ABSENT_PROOF", result.reason_codes)

    def test_proven_edge_resolves(self) -> None:
        resolver = _FakeResolver({"proof-a", "proof-edge"})
        chain = ImpactChain(
            chain_id="c1",
            nodes=(_node("n1", ("proof-a",)), _node("n2", ("proof-a",))),
            edges=(
                ImpactEdge(
                    from_node_id="n1",
                    to_node_id="n2",
                    relation=ImpactRelation.ENABLES,
                    proof_refs=("proof-edge",),
                ),
            ),
        )
        result = validate_chain(chain, resolver, "run-1")
        self.assertTrue(result.valid)
