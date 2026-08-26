from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.impact.capability_map import (
    DEMONSTRATED_CAPABILITY_TO_IMPACT_KIND,
    validate_chain_impact_scope,
    validate_impact_scope,
)
from zest.research.impact.chain import ImpactChain, ImpactNode, ImpactScopeRef
from zest.research.impact.types import ImpactKind, ProofRecord, ProofResolver


class _CapabilityResolver(ProofResolver):
    def __init__(self, capabilities_by_proof: dict[str, frozenset[str]]) -> None:
        self.capabilities_by_proof = capabilities_by_proof

    def resolve(self, proof_id: str, expected_run_id: str) -> ProofRecord | None:
        capabilities = self.capabilities_by_proof.get(proof_id)
        if capabilities is None:
            return None
        return ProofRecord(
            proof_id=proof_id,
            research_run_id=expected_run_id,
            target_reference="https://example.com",
            demonstrated_capabilities=capabilities,
        )


def _node(node_id: str, kind: ImpactKind, proof_refs: tuple[str, ...]) -> ImpactNode:
    return ImpactNode(
        node_id=node_id,
        proof_refs=proof_refs,
        impact_kind=kind,
        claim_text="claim",
        scope_ref=ImpactScopeRef(
            research_run_id="run-1",
            program_id="prog-1",
            target_reference="https://example.com",
        ),
        provenance={},
    )


class CapabilityMapTests(unittest.TestCase):
    def test_data_read_allowed_by_read_capability(self) -> None:
        resolver = _CapabilityResolver({"p1": frozenset({"READ_OTHER_OBJECT"})})
        node = _node("n1", ImpactKind.DATA_READ, ("p1",))
        result = validate_impact_scope(node, resolver, "run-1")
        self.assertTrue(result.valid)

    def test_data_write_rejected_by_read_only_proof(self) -> None:
        resolver = _CapabilityResolver({"p1": frozenset({"READ_OTHER_OBJECT"})})
        node = _node("n1", ImpactKind.DATA_WRITE, ("p1",))
        result = validate_impact_scope(node, resolver, "run-1")
        self.assertFalse(result.valid)
        self.assertIn("IMPACT_EXCEEDS_DEMONSTRATED_CAPABILITY", result.reason_codes)

    def test_account_takeover_requires_both_capabilities(self) -> None:
        resolver = _CapabilityResolver(
            {
                "p1": frozenset({"AUTHENTICATED_AS_USER"}),
                "p2": frozenset({"PRIVILEGE_ESCALATION_EVIDENCE"}),
            }
        )
        node = _node("n1", ImpactKind.ACCOUNT_TAKEOVER_PATH, ("p1", "p2"))
        result = validate_impact_scope(node, resolver, "run-1")
        self.assertTrue(result.valid)

    def test_account_takeover_rejected_with_only_auth_capability(self) -> None:
        resolver = _CapabilityResolver({"p1": frozenset({"AUTHENTICATED_AS_USER"})})
        node = _node("n1", ImpactKind.ACCOUNT_TAKEOVER_PATH, ("p1",))
        result = validate_impact_scope(node, resolver, "run-1")
        self.assertFalse(result.valid)
        self.assertIn("IMPACT_EXCEEDS_DEMONSTRATED_CAPABILITY", result.reason_codes)

    def test_unknown_capability_fails_closed(self) -> None:
        resolver = _CapabilityResolver({"p1": frozenset({"UNKNOWN_CAPABILITY_X"})})
        node = _node("n1", ImpactKind.DATA_READ, ("p1",))
        result = validate_impact_scope(node, resolver, "run-1")
        self.assertFalse(result.valid)
        self.assertIn("IMPACT_EXCEEDS_DEMONSTRATED_CAPABILITY", result.reason_codes)

    def test_chain_scope_validation_reports_all_failures(self) -> None:
        resolver = _CapabilityResolver(
            {
                "p1": frozenset({"READ_OTHER_OBJECT"}),
                "p2": frozenset({"READ_OTHER_OBJECT"}),
            }
        )
        chain = ImpactChain(
            chain_id="c1",
            nodes=(
                _node("n1", ImpactKind.DATA_READ, ("p1",)),
                _node("n2", ImpactKind.DATA_WRITE, ("p2",)),
            ),
            edges=(),
        )
        result = validate_chain_impact_scope(chain, resolver, "run-1")
        self.assertFalse(result.valid)
        self.assertIn("IMPACT_EXCEEDS_DEMONSTRATED_CAPABILITY", result.reason_codes)

    def test_account_takeover_rejected_with_only_escalation_capability(self) -> None:
        resolver = _CapabilityResolver(
            {"p1": frozenset({"PRIVILEGE_ESCALATION_EVIDENCE"})}
        )
        node = _node("n1", ImpactKind.ACCOUNT_TAKEOVER_PATH, ("p1",))
        result = validate_impact_scope(node, resolver, "run-1")
        self.assertFalse(result.valid)
        self.assertIn("IMPACT_EXCEEDS_DEMONSTRATED_CAPABILITY", result.reason_codes)

    def test_cross_run_proof_rejected(self) -> None:
        class _RunMismatchResolver(ProofResolver):
            def resolve(self, proof_id: str, expected_run_id: str) -> ProofRecord | None:
                return ProofRecord(
                    proof_id=proof_id,
                    research_run_id="other-run",
                    target_reference="https://example.com",
                    demonstrated_capabilities=frozenset({"READ_OTHER_OBJECT"}),
                )

        resolver = _RunMismatchResolver()
        node = _node("n1", ImpactKind.DATA_READ, ("p1",))
        result = validate_impact_scope(node, resolver, "run-1")
        self.assertFalse(result.valid)
        self.assertIn("CROSS_RUN_PROOF", result.reason_codes)

    def test_capability_map_is_shelf_data(self) -> None:
        self.assertIn("READ_OTHER_OBJECT", DEMONSTRATED_CAPABILITY_TO_IMPACT_KIND)
        self.assertIn("DATA_READ", DEMONSTRATED_CAPABILITY_TO_IMPACT_KIND["READ_OTHER_OBJECT"])
