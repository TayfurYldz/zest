from __future__ import annotations

from datetime import datetime, timezone
import unittest

import pathsetup  # noqa: F401

from zest.application.draft_exploratory_hypothesis import (
    DraftExploratoryHypothesis,
    DraftExploratoryHypothesisCommand,
    ExploratorySignalInput,
)
from zest.data.records import HunterFamilyRecord
from zest.research.exploratory import ExploratorySignalKind
from zest.research.types import ResearchInputError
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run


NOW = datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc)


class FixedClock:
    def now(self):
        return NOW


def _command(**overrides) -> DraftExploratoryHypothesisCommand:
    values = dict(
        research_run_id="run-1",
        proposed_family_name="Unmapped Response Shape Coupling",
        proposed_family_rationale=(
            "Temporal drift and lab-only response-shape behavior need a human "
            "reviewed family draft before registry admission."
        ),
        signals=(
            ExploratorySignalInput(
                signal_id="sig-1",
                kind=ExploratorySignalKind.LAB_ZERO_DAY_STYLE_ANOMALY.value,
                description="A lab-only zero-day-style behavior changed the response shape.",
                source_refs=("change-1",),
                target_node_kind="ACTION",
                attributes={"lab_fixture": "zero_day_style"},
            ),
            ExploratorySignalInput(
                signal_id="sig-2",
                kind=ExploratorySignalKind.COVERAGE_DEBT_INCREASE.value,
                description="Coverage debt increased after the same route drift.",
                source_refs=("coverage-snap-1",),
                target_node_kind="ACTION",
            ),
        ),
        correlation_id="corr-sd-g16",
        model_claimed_novelty="N4_ZERO_DAY",
    )
    values.update(overrides)
    return DraftExploratoryHypothesisCommand(**values)


def _service(store: _Store) -> DraftExploratoryHypothesis:
    return DraftExploratoryHypothesis(FakeUnitOfWorkFactory(store), clock=FixedClock())


def _seed_family(store: _Store, *, name: str = "Existing Boundary Drift") -> None:
    store.hunter_families["hf-existing:1"] = HunterFamilyRecord(
        family_id="hf-existing",
        name=name,
        target_node_kinds=("ACTION",),
        preconditions={},
        claim_template="existing template",
        evidence_requirements={"v1": "required"},
        validation_tier="V3",
        enabled=True,
        version=1,
        created_at=CREATED_AT,
    )


class SDG16DraftExploratoryHypothesisTests(unittest.TestCase):
    def test_persists_hypothesis_and_audit_without_registry_or_finding_state(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        _seed_family(store)

        result = _service(store).execute(_command())

        self.assertIn(result.hypothesis_id, store.hypotheses)
        hypothesis = store.hypotheses[result.hypothesis_id]
        self.assertEqual(hypothesis.research_run_id, "run-1")
        self.assertEqual(hypothesis.origin_reference, result.audit_event_id)
        self.assertIn("before any registry admission", hypothesis.claim)
        self.assertEqual(set(store.hunter_families), {"hf-existing:1"})
        self.assertEqual(store.hunt_v3_queue, {})
        self.assertEqual(store.evidence, {})
        self.assertEqual(store.candidates, {})
        self.assertEqual(store.finding_proposals, {})
        self.assertEqual(store.human_reviews, {})
        self.assertEqual(store.findings, {})

        audit = store.audit_events[result.audit_event_id]
        self.assertEqual(audit.event_type, "EXPLORATORY_HYPOTHESIS_DRAFTED")
        self.assertEqual(audit.subject_type, "exploratory_family_draft")
        self.assertEqual(audit.subject_id, result.draft.draft_id)
        self.assertEqual(audit.correlation_id, "corr-sd-g16")
        self.assertEqual(audit.payload["hypothesis_id"], result.hypothesis_id)
        self.assertEqual(audit.payload["status"], "HYPOTHESIZED")
        self.assertTrue(audit.payload["registry_external"])
        self.assertTrue(audit.payload["requires_human_family_approval"])
        self.assertFalse(audit.payload["may_write_hunter_registry"])
        self.assertTrue(audit.payload["not_evidence"])
        self.assertTrue(audit.payload["not_candidate"])
        self.assertTrue(audit.payload["not_finding"])
        self.assertTrue(audit.payload["not_impact_graph_edge"])
        self.assertTrue(audit.payload["false_finding_required_zero"])
        self.assertEqual(audit.payload["model_claimed_novelty"], "N4_ZERO_DAY")

    def test_rejects_registry_overlap_without_partial_persistence(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        _seed_family(store)

        with self.assertRaises(ResearchInputError):
            _service(store).execute(
                _command(proposed_family_name="Existing Boundary Drift")
            )

        self.assertEqual(store.hypotheses, {})
        self.assertEqual(store.audit_events, {})
        self.assertEqual(set(store.hunter_families), {"hf-existing:1"})

    def test_requires_existing_research_run(self) -> None:
        store = _Store()

        with self.assertRaisesRegex(Exception, "research run not found"):
            _service(store).execute(_command())

        self.assertEqual(store.hypotheses, {})
        self.assertEqual(store.audit_events, {})


if __name__ == "__main__":
    unittest.main()
