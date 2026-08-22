# Canonical MR-6 Full Exploratory Qualification Record

Date: 2026-08-22.
Branch: `campaign/canonical-mr5-mr6`.
Baseline HEAD (unchanged code): `7d96f79b8dc1fcb26e5cf764c5626790d9197e12`.

```
CANONICAL_MR6_FULL_QA_PASS  — no
CANONICAL_MR6_PARTIAL       — yes
CANONICAL_MR6_FAIL          — no

MR6_FULL_EXPLORATORY_QUALIFIED=NO
CANONICAL_MR6_CLOSED=NO
CANONICAL_MR5_CLOSED=YES
```

This is a qualification record. It does **not** close Canonical MR-6.
No production code was changed. Checkpoints 9–11 were not rewritten.

## Smallest blocker (next authorized unit)

Connect **one genuine registry-external anomaly source** into ARC
opportunity/hypothesis admission so exploratory research is selected and
compiled inside `AutonomousResearchController.step()`, rather than only via
the operator/test `ExecuteExploratoryResearch` / `DraftExploratoryHypothesis`
side lane.

Until that connection exists, FULL MR-6 cannot be claimed even though
typed compile → Core → Worker → MR-5 plumbing already exists when invoked
manually.

## Why not FAIL

The existing implementation is not a hollow stub:

- Registry-external drafts persist as HYPOTHESIZED hypotheses without writing
  `hunter_family`.
- `compile_exploratory_research` reuses typed compilers and strips model
  payload keys.
- Invoked execution uses Core + WorkerPort + EvaluateExperimentFeedback +
  the sealed PromotionPipeline.
- Permanent family write requires HUMAN_OPERATOR APPROVE.
- Finding remains Human Review gated in the invoked path.
- Checkpoints 9/10/11 remain green.

## Why not PASS

`AutonomousResearchController` contains **zero** references to exploratory
draft/execute/compile. No production sensor/graph/temporal path emits
`ExploratorySignal`. Tests inject `source_refs=("change-1",)` and
`compile_arguments` that already match OBJECT_AUTHORIZATION fields.
That is test composition, not autonomous registry-external research.
