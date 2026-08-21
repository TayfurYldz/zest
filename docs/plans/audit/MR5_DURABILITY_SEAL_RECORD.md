# Canonical MR-5 Durability Seal Record

Date: 2026-08-22.
Branch sealed: `campaign/canonical-mr5-mr6`.
Parent before this seal: `ce2f557534b208bbd5ed61aed352eb160e039d50`.
Checkpoint 9 (do not rewrite): `54b17a1257b5b5307b100e1e163de56915de541f`.

```
CANONICAL_MR5_CLOSED=YES
MR5_DURABILITY_SEAL_QUALIFIED=YES
MR6_FULL_EXPLORATORY_QUALIFIED=NO
```

This record seals the already-qualified PostgreSQL-backed concurrent
idempotency of the canonical promotion path. It does not start MR-6
qualification, research-osd, or Phase J.

## Exact qualified scope

```
Assessment
→ Evidence
→ Candidate
→ Independent Verification
→ FindingProposal
```

Concurrency and restart idempotency are PostgreSQL-backed (Alembic
`a39_001_mr5_durability_uq`). Finding still requires:

```
Human Review
+
Core finalization
```

Finding is not created automatically. Human Review and Core finalization
were not weakened.

## Explicitly NOT closed

- MR-6 exploratory attack capability
- research-osd
- OAST production
- GATE 04B
- field validation
- SECURITY_RESEARCH_VALIDATED
- PRODUCTION_READY

Maturity flags were not changed by this seal:

```
LIVE_MODEL_VALIDATED=False
SECURITY_RESEARCH_VALIDATED=False
PRODUCTION_READY=False
GATE_04B_STATUS=PENDING
GATE_21_STATUS=PENDING
```

## Constraints introduced by a39

`down_revision = a38_001_promotion_run`. No older migration was edited.
Upgrade inspects duplicates and raises; it does not DELETE.

| Name | Semantics |
|---|---|
| `uq_evidence_experiment_supporting` | partial unique on `evidence(experiment_id)` WHERE `polarity='SUPPORTING'` |
| `uq_candidate_evidence_evidence_id` | one Candidate per Evidence |
| `uq_verification_candidate` | one Verification per Candidate |
| `uq_finding_proposal_candidate` | one FindingProposal per Candidate |
| `uq_promotion_run_reproduction_experiment` | partial unique reserved reproduction experiment id |
| `fk_promotion_run_evidence` | nullable FK `promotion_run.evidence_id → evidence` |
| `fk_promotion_run_candidate` | nullable FK `promotion_run.candidate_id → candidate` |
| `fk_promotion_run_verification` | nullable FK `promotion_run.verification_id → verification` |
| `fk_promotion_run_finding_proposal` | nullable FK `promotion_run.finding_proposal_id → finding_proposal.proposal_id` |

No FK on `reproduction_experiment_id` (id is reserved before the experiment
row exists). Downgrade drops the same constraints/indexes/FKs (symmetric,
non-destructive of row data).

## Qualification recheck at seal

| Suite | Result |
|---|---|
| `python -m compileall src tests scripts` | pass |
| `pytest tests/unit -q` | 1374 passed, 4 skipped |
| `pytest tests/integration -q` | 202 passed, **1** known SD-G4 fail |
| MR-5 durability file ×10 (inner 10 concurrent subtests) | 7 passed ×10; 20 subtests ×10 |
| checkpoint-9 Mutation/V3/Protocol targeted | 44 passed |

## Known unrelated repository failures (do not fix in this seal)

- SD-G4 token-economy dual CHECK (`ck_budget_consumption_resource_type` vs v2 / `MODEL_TOKENS_IN`)
- full-E2E `cli_session` isolation when the entire e2e package is collected together

`FULL_REPOSITORY_CLEAN=false` remains expected.

## MR-6

MR-6 exploratory files already exist on this branch (preserved from
`ce2f557`). They were not modified by this seal and are **not** part of
the MR-5 qualification claim.

```
MR6_FULL_EXPLORATORY_QUALIFIED=NO
```
