"""MR-1 (Slice 3) real-PostgreSQL proof: opportunity_selection_candidate persists,
enforces its uniqueness/check constraints, and mark_decided is a one-way CAS.

Skipped when ZEST_TEST_DATABASE_URL is absent (PENDING, not PASS).
SQLite is not a substitute.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from zest.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import OpportunitySelectionCandidateRecord
from zest.data.errors import PersistenceConflictError, PersistenceInputError
from integration.harness import NOW, alembic_upgrade, seed_authorized_spine, truncate_spine

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("ZEST_DATABASE_URL")
    )


def _candidate(candidate_id: str = "cand-1", **overrides) -> OpportunitySelectionCandidateRecord:
    values = dict(
        candidate_id=candidate_id,
        research_run_id="run-1",
        source_system="HUNTER_COVERAGE",
        opportunity_kind="HUNTER_COVERAGE_GAP",
        mode="EXPLORATION",
        source_refs=("family-1", "node-1", "identity-1"),
        proposed_direction="Investigate HunterFamily family-1 against node-1 for identity-1.",
        unresolved_question="Does family-1 reveal new information here?",
        expected_information_value_description="claim_recorded",
        assumptions=("hunter_coverage_gap is plumbing, not authorization",),
        dimensions={
            "expected_information_value": "HIGH",
            "security_relevance_potential": "LOW",
            "novelty_composition": "LOW",
            "unresolved_uncertainty": "HIGH",
            "chain_potential": "LOW",
            "evidence_coverage": "LOW",
            "execution_cost": "LOW",
            "side_effect_requirement": 0,
            "duplicate_risk": "LOW",
            "previous_failed_attempts": 0,
        },
        context_signature="hunter_coverage:family-1:node-1:identity-1",
        structural_identity="0" * 64,
        strategy_version="hunter_coverage_opportunity_source.v1",
        created_at=NOW,
    )
    values.update(overrides)
    return OpportunitySelectionCandidateRecord(**values)


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class OpportunitySelectionCandidateIntegrationTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(
            "DESTRUCTIVE PostgreSQL integration tests: TRUNCATE CASCADE against "
            f"{redacted_database_url(TEST_URL)}",
            flush=True,
        )
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.commit()

    def test_insert_and_get_round_trip(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            uow.opportunity_selection_candidates.insert(_candidate())
            uow.commit()
        with PostgresUnitOfWork(self.engine) as uow:
            fetched = uow.opportunity_selection_candidates.get("cand-1")
        assert fetched is not None
        self.assertEqual(fetched.outcome, "PENDING")
        self.assertEqual(fetched.source_system, "HUNTER_COVERAGE")
        self.assertIsNone(fetched.resulting_opportunity_id)
        self.assertIsNone(fetched.decided_at)

    def test_list_for_research_run_only_returns_that_run(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            uow.opportunity_selection_candidates.insert(_candidate("cand-1"))
            uow.commit()
        with PostgresUnitOfWork(self.engine) as uow:
            items = uow.opportunity_selection_candidates.list_for_research_run("run-1")
            other = uow.opportunity_selection_candidates.list_for_research_run("does-not-exist")
        self.assertEqual([item.candidate_id for item in items], ["cand-1"])
        self.assertEqual(other, [])

    def test_duplicate_structural_identity_in_same_run_is_rejected(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            uow.opportunity_selection_candidates.insert(_candidate("cand-1"))
            uow.commit()
        with PostgresUnitOfWork(self.engine) as uow:
            with self.assertRaises(PersistenceConflictError):
                uow.opportunity_selection_candidates.insert(_candidate("cand-2"))
            uow.rollback()

    def test_mark_decided_admits_exactly_once(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            uow.opportunity_selection_candidates.insert(_candidate())
            uow.commit()
        with PostgresUnitOfWork(self.engine) as uow:
            first = uow.opportunity_selection_candidates.mark_decided(
                "cand-1",
                outcome="ADMITTED",
                resulting_opportunity_id="cand-1",
                decided_at=NOW,
            )
            uow.commit()
        self.assertTrue(first)
        with PostgresUnitOfWork(self.engine) as uow:
            second = uow.opportunity_selection_candidates.mark_decided(
                "cand-1",
                outcome="NOT_ADMITTED",
                resulting_opportunity_id=None,
                decided_at=NOW,
            )
            uow.commit()
        self.assertFalse(second)
        with PostgresUnitOfWork(self.engine) as uow:
            fetched = uow.opportunity_selection_candidates.get("cand-1")
        assert fetched is not None
        self.assertEqual(fetched.outcome, "ADMITTED")
        self.assertEqual(fetched.resulting_opportunity_id, "cand-1")

    def test_invalid_source_system_is_rejected_before_reaching_the_database(self) -> None:
        with self.assertRaises(PersistenceInputError):
            _candidate(source_system="MODEL_INVENTED")

    def test_invalid_outcome_transition_is_rejected_before_reaching_the_database(self) -> None:
        with self.assertRaises(PersistenceInputError):
            _candidate(outcome="ADMITTED")  # missing resulting_opportunity_id/decided_at


if __name__ == "__main__":
    unittest.main()
