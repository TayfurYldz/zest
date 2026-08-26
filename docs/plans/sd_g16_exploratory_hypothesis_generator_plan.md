# SD-G16 Plan — Exploratory Hypothesis Generator

**Status:** PASS
**Previous gate:** SD-G15 PASS (`dde38c5`)

## Purpose

SD-G16 adds a registry-external exploratory lane for anomaly-backed hypothesis
drafts. The lane can notice a behavior family that is not represented by enabled
`HunterFamily` rows, but it cannot write to the registry or promote creative
ideas into Evidence, Candidate, Finding, or ImpactGraph edges.

## Non-Negotiables

- Generated drafts start as `HYPOTHESIZED`.
- Every draft carries the same `V1`/`V2`/`V3` plus `FALSE_FINDING_ZERO` gate list.
- `HunterFamily` registry writes require a separate human-approved path; the
  exploratory generator never inserts or updates registry rows.
- Model novelty claims are advisory only. `N4_ZERO_DAY` remains model-claimed
  metadata, not product truth.
- A draft cannot create Evidence, Candidate, Finding, HumanReview, Approval, V3
  queue, or ImpactGraph edge state.
- No SD-G16 work updates old infra gates or `maturity.py`.

## P1 — Registry-External Draft Domain

Files:

- `src/zest/research/exploratory.py`
- `tests/unit/research/test_sd_g16_exploratory.py`

Behavior:

- accepts sourced anomaly signals from temporal/coverage/scope-boundary/response
  drift/lab zero-day-style contexts;
- rejects direct vulnerability/exploit/finding/evidence truth claims;
- rejects a draft whose proposed family name/id or signal mapping overlaps an
  enabled `HunterFamily` row;
- emits `ExploratoryHypothesisDraft` with `registry_external`,
  `requires_human_family_approval`, `may_write_hunter_registry=false`,
  `not_evidence`, `not_candidate`, `not_finding`, and
  `not_impact_graph_edge`.

## P2 — Application Persistence Lane

Files:

- `src/zest/application/draft_exploratory_hypothesis.py`
- `tests/unit/application/test_sd_g16_exploratory_hypothesis.py`

Behavior:

- loads the current enabled HunterFamily registry read-only;
- persists one `HypothesisRecord` whose claim remains exploratory and
  `HYPOTHESIZED`;
- writes an `EXPLORATORY_HYPOTHESIS_DRAFTED` audit event containing the human
  approval and validation-gate markers;
- does not write HunterFamily, Evidence, Candidate, FindingProposal,
  HumanReview, Finding, or V3 queue rows.

## Seal Checklist

- [x] Focused SD-G16 tests green: `8 passed`.
- [x] Affected suite green: `84 passed`.
- [x] Full suite green: `1542 passed, 9 skipped, 53 subtests passed`.
- [x] Operations notes updated.
- [x] Commit and push SD-G16 seal.
