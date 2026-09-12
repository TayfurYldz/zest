"""Control-flow hypotheses must never become research truth."""

from __future__ import annotations

from dataclasses import replace
import unittest

import pathsetup  # noqa: F401

from application.test_promotion_pipeline import (
    FixedClock,
    _allow_scope,
    _run_echo,
)
from support.fake_unit_of_work import (
    FakeUnitOfWorkFactory,
    _Store,
)
from support.recording_worker import (
    RecordingWorkerPort,
)
from support.spine import seed_spine

from zest.application.admit_diagnostic_evidence import (
    AdmitDiagnosticEvidence,
    AdmitDiagnosticEvidenceCommand,
)
from zest.application.promotion_pipeline import (
    AdvancePromotionCommand,
    PromotionOutcome,
    PromotionPipeline,
)
from zest.application.propose_candidate import (
    ProposeCandidateFromEvidence,
    ProposeCandidateFromEvidenceCommand,
)
from zest.application.research_truth_provenance import (
    NonPromotableHypothesisOrigin,
    RESEARCH_WORK_FABRIC_HYPOTHESIS_ORIGIN_PREFIX,
)
from zest.application.submit_finding_proposal import (
    SubmitFindingProposal,
    SubmitFindingProposalCommand,
)


def _mark_synthetic(store: _Store) -> None:
    original = store.hypotheses["hyp-1"]

    store.hypotheses["hyp-1"] = replace(
        original,
        origin_reference=(
            RESEARCH_WORK_FABRIC_HYPOTHESIS_ORIGIN_PREFIX
            + "test-anchor"
        ),
    )


class ResearchTruthPromotionProvenanceTests(
    unittest.TestCase
):
    def test_pipeline_stops_before_evidence(
        self,
    ) -> None:
        store = _Store()
        seed_spine(store)

        feedback = _run_echo(store)
        _mark_synthetic(store)

        result = PromotionPipeline(
            FakeUnitOfWorkFactory(store),
            clock=FixedClock(),
        ).on_assessment(feedback)

        self.assertEqual(
            result.outcome,
            (
                PromotionOutcome
                .SKIPPED_NON_PROMOTABLE_HYPOTHESIS
            ),
        )

        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(len(store.candidates), 0)
        self.assertEqual(
            len(store.promotion_runs),
            0,
        )
        self.assertEqual(
            len(store.finding_proposals),
            0,
        )
        self.assertEqual(len(store.findings), 0)

    def test_direct_evidence_boundary_blocks_synthetic(
        self,
    ) -> None:
        store = _Store()
        seed_spine(store)

        feedback = _run_echo(store)
        _mark_synthetic(store)

        with self.assertRaises(
            NonPromotableHypothesisOrigin
        ):
            AdmitDiagnosticEvidence(
                FakeUnitOfWorkFactory(store),
                clock=FixedClock(),
            ).execute(
                AdmitDiagnosticEvidenceCommand(
                    experiment_id=(
                        feedback.experiment_id
                    ),
                    assessment_id=(
                        feedback.assessment_id
                    ),
                )
            )

        self.assertEqual(len(store.evidence), 0)

    def test_existing_candidate_cannot_be_reaffirmed_after_contamination(
        self,
    ) -> None:
        store = _Store()
        seed_spine(store)
        factory = FakeUnitOfWorkFactory(store)

        feedback = _run_echo(store)

        started = PromotionPipeline(
            factory,
            clock=FixedClock(),
        ).on_assessment(feedback)

        self.assertIsNotNone(
            started.evidence_id
        )
        self.assertIsNotNone(
            started.candidate_id
        )

        _mark_synthetic(store)

        with self.assertRaises(
            NonPromotableHypothesisOrigin
        ):
            ProposeCandidateFromEvidence(
                factory,
                clock=FixedClock(),
            ).execute(
                ProposeCandidateFromEvidenceCommand(
                    evidence_id=started.evidence_id
                )
            )

        self.assertEqual(
            len(store.candidates),
            1,
        )

    def test_pending_contaminated_run_stops_before_worker(
        self,
    ) -> None:
        store = _Store()
        seed_spine(store)
        factory = FakeUnitOfWorkFactory(store)

        feedback = _run_echo(store)

        started = PromotionPipeline(
            factory,
            clock=FixedClock(),
        ).on_assessment(feedback)

        self.assertEqual(
            started.outcome,
            PromotionOutcome.VERIFICATION_STARTED,
        )

        _mark_synthetic(store)

        worker = RecordingWorkerPort(
            store=store
        )

        results = PromotionPipeline(
            factory,
            clock=FixedClock(),
            worker=worker,
        ).advance(
            AdvancePromotionCommand(
                research_run_id="run-1",
                scope=_allow_scope(),
            )
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0].outcome,
            (
                PromotionOutcome
                .SKIPPED_NON_PROMOTABLE_HYPOTHESIS
            ),
        )

        self.assertEqual(len(worker.calls), 0)
        self.assertEqual(
            len(store.verifications),
            0,
        )
        self.assertEqual(
            len(store.finding_proposals),
            0,
        )
        self.assertEqual(len(store.findings), 0)

        promotion = next(
            iter(store.promotion_runs.values())
        )

        self.assertEqual(
            promotion.stage,
            "STOPPED",
        )

    def test_finding_proposal_boundary_blocks_contaminated_candidate(
        self,
    ) -> None:
        store = _Store()
        seed_spine(store)
        factory = FakeUnitOfWorkFactory(store)

        feedback = _run_echo(store)

        started = PromotionPipeline(
            factory,
            clock=FixedClock(),
        ).on_assessment(feedback)

        self.assertIsNotNone(
            started.candidate_id
        )

        candidate = store.candidates[
            started.candidate_id
        ]

        store.candidates[
            started.candidate_id
        ] = replace(
            candidate,
            state="VALIDATED",
        )

        _mark_synthetic(store)

        with self.assertRaises(
            NonPromotableHypothesisOrigin
        ):
            SubmitFindingProposal(
                factory,
                clock=FixedClock(),
            ).execute(
                SubmitFindingProposalCommand(
                    candidate_id=(
                        started.candidate_id
                    )
                )
            )

        self.assertEqual(
            len(store.finding_proposals),
            0,
        )
        self.assertEqual(len(store.findings), 0)


if __name__ == "__main__":
    unittest.main()
