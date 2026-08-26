from __future__ import annotations

from datetime import datetime, timezone
import unittest

import pathsetup  # noqa: F401

from zest.data.records import HunterFamilyRecord
from zest.research.exploratory import (
    ExploratorySignal,
    ExploratorySignalKind,
    VALIDATION_GATES,
    draft_registry_external_hypothesis,
)
from zest.research.selection import HunterFamilyView
from zest.research.types import ResearchInputError


NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _signal(**overrides) -> ExploratorySignal:
    values = dict(
        signal_id="sig-1",
        research_run_id="run-1",
        kind=ExploratorySignalKind.LAB_ZERO_DAY_STYLE_ANOMALY,
        description="A lab-only zero-day-style behavior changed the response shape.",
        source_refs=("change-1",),
        target_node_kind="ACTION",
        attributes={"lab_fixture": "zero_day_style"},
    )
    values.update(overrides)
    return ExploratorySignal(**values)


def _family(**overrides) -> HunterFamilyView:
    record = HunterFamilyRecord(
        family_id="hf-existing",
        name="Existing Boundary Drift",
        target_node_kinds=("ACTION",),
        preconditions={},
        claim_template="existing template",
        evidence_requirements={"v1": "required"},
        validation_tier="V3",
        enabled=True,
        version=1,
        created_at=NOW,
    )
    values = {
        "family_id": record.family_id,
        "name": record.name,
        "target_node_kinds": record.target_node_kinds,
        "preconditions": record.preconditions,
        "claim_template": record.claim_template,
        "evidence_requirements": record.evidence_requirements,
        "validation_tier": record.validation_tier,
        "enabled": record.enabled,
        "version": record.version,
    }
    values.update(overrides)
    return HunterFamilyView(**values)


class SDG16ExploratoryDomainTests(unittest.TestCase):
    def test_drafts_registry_external_hypothesis_with_hard_gates(self) -> None:
        draft = draft_registry_external_hypothesis(
            draft_id="draft-1",
            research_run_id="run-1",
            proposed_family_name="Unmapped Response Shape Coupling",
            proposed_family_rationale=(
                "The temporal and lab-only signals point to a behavior family "
                "that is not represented by enabled HunterFamily rows."
            ),
            signals=(
                _signal(),
                _signal(
                    signal_id="sig-2",
                    kind=ExploratorySignalKind.COVERAGE_DEBT_INCREASE,
                    description="Coverage debt increased after the same route drift.",
                    source_refs=("coverage-snap-1",),
                ),
            ),
            registry=(_family(),),
            model_claimed_novelty="N4_ZERO_DAY",
        )

        self.assertEqual(draft.status, "HYPOTHESIZED")
        self.assertEqual(draft.validation_gates, VALIDATION_GATES)
        self.assertTrue(draft.registry_external)
        self.assertTrue(draft.requires_human_family_approval)
        self.assertFalse(draft.may_write_hunter_registry)
        self.assertTrue(draft.not_evidence)
        self.assertTrue(draft.not_candidate)
        self.assertTrue(draft.not_finding)
        self.assertTrue(draft.not_impact_graph_edge)
        self.assertTrue(draft.false_finding_required_zero)
        self.assertEqual(draft.model_claimed_novelty, "N4_ZERO_DAY")
        self.assertIn("before any registry admission", draft.hypothesis_claim)

    def test_rejects_direct_vulnerability_truth_claims(self) -> None:
        with self.assertRaises(ResearchInputError):
            _signal(description="This is a vulnerability in the lab route.")

    def test_rejects_nested_secret_material_in_signal_attributes(self) -> None:
        with self.assertRaises(ResearchInputError):
            _signal(attributes={"headers": {"authorization": "Bearer example"}})

    def test_rejects_existing_registry_family_name(self) -> None:
        with self.assertRaises(ResearchInputError):
            draft_registry_external_hypothesis(
                draft_id="draft-1",
                research_run_id="run-1",
                proposed_family_name="Existing Boundary Drift",
                proposed_family_rationale="same family",
                signals=(_signal(),),
                registry=(_family(),),
            )

    def test_rejects_signal_already_mapped_to_registry_family(self) -> None:
        with self.assertRaises(ResearchInputError):
            draft_registry_external_hypothesis(
                draft_id="draft-1",
                research_run_id="run-1",
                proposed_family_name="Unmapped Response Shape Coupling",
                proposed_family_rationale="new family",
                signals=(_signal(attributes={"matched_family_id": "hf-existing"}),),
                registry=(_family(),),
            )


if __name__ == "__main__":
    unittest.main()
