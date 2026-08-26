# SD-G15 Plan — Continuous Change, Live Coverage Debt, Recall Consolidation

**Status:** PASS
**Previous gate:** SD-G14 PASS (`1558411`)

## Purpose

SD-G15 turns temporal change into live research steering without allowing a
ChangeEvent to become vulnerability truth. It also consolidates existing
family-level benchmark recall into one hard-gate report, without weighted
averages or false-positive dilution.

## Non-Negotiables

- A `ChangeEvent` is not Evidence, Candidate, Finding, severity, or confidence.
- Coverage debt is a live operator metric; it does not authorize exploitation.
- Recall consolidation is hard-gated per family. No weighted average can hide a
  missed positive or false finding.
- PostgreSQL-backed tests are required for vertical slices. Without PostgreSQL,
  integration tests are SKIP/PENDING, not PASS.
- No SD-G15 work updates old infra gates or `maturity.py`.

## P1 — Live Coverage Debt Change Impact

Files:

- `src/zest/research/coverage/live.py`
- `src/zest/application/coverage/live_debt.py`
- `src/zest/data/ports.py`
- `src/zest/data/unit_of_work.py`
- `tests/support/fake_unit_of_work.py`
- `tests/unit/research/coverage/test_sd_g15_live_coverage.py`
- `tests/unit/application/test_sd_g15_live_coverage_debt.py`
- `tests/integration/test_sd_g15_live_coverage.py`
- `src/zest/worker_runtime/python/browser_engine.py`
- `src/zest/worker_runtime/python/playwright_chromium_engine.py`
- `workers/python/zest_worker/browser_engine.py`
- `workers/python/zest_worker/playwright_chromium_engine.py`
- `src/zest/application/transition_a/browser_page.py`
- `src/zest/application/discovery/project.py`
- `src/zest/research/discovery/projection.py`

Behavior:

- compares durable coverage-debt snapshots by count and total-debt delta;
- attaches in-window `ChangeEvent` ids as temporal context only;
- rejects cross-run change events and vulnerability-labelled change statements;
- writes `LIVE_COVERAGE_DEBT_REFRESHED` audit events with
  `not_a_vulnerability`, `not_evidence`, `not_candidate`, and `not_finding`;
- does not create Evidence, Candidate, Finding, HumanReview, Approval, or V3
  queue rows.
- preserves browser-observed link boundary metadata as safe
  `href_scheme`/`href_origin`/`href_path` only, never raw href query/fragment;
- derives `SCOPE_BOUNDARY_CANDIDATE` from observed out-of-origin controls as
  DERIVED facts, not observed scope grants and not exploitation authorization.

Evidence:

- Focused checks (2026-08-20): `10 passed`.
- Discovery regression checks (2026-08-20): `37 passed`.
- PostgreSQL vertical slice (2026-08-20): included in focused checks with real
  PostgreSQL when `ZEST_TEST_DATABASE_URL` is configured.

## P2 — Recall Consolidation Report

Files:

- `src/zest/security_benchmark/recall.py`
- `tests/unit/security_benchmark/test_sd_g15_recall.py`

Behavior:

- consolidates object authorization, workflow authorization, and research
  selection scorecards into `recall.consolidated.v1`;
- reports recall as per-family fractions, not a weighted aggregate;
- fails the gate when any family has a miss, false finding, or hard failure;
- keeps the report as benchmark evidence, not a Finding.

Evidence:

- Focused checks (2026-08-20): included in `10 passed`.

## Seal Checklist

- [x] Affected suite green: `44 passed`.
- [x] Full suite green: `1534 passed, 9 skipped, 53 subtests passed`.
- [x] Operations notes updated.
- [x] Commit and push SD-G15 seal.
