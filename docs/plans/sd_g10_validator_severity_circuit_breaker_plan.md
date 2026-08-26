# SD-G10 Plan — Independent Validator + Severity Engine + Circuit Breaker

**Status:** PASS
**Previous gate:** SD-G9 PASS (`2752df6`)
**Do not confuse with:** old infrastructure `GATE 10 — Runtime / Strix Boundary Integrity`.

## Purpose

SD-G10 adds the first attack-period validator/economy control layer after
HunterScore scheduling. It does not expand active attack surface by itself.

The gate must prove three things:

1. Validator admission is independent and fail-closed.
2. Severity is computed only after in-scope validation and maps internal P0-P3
   to Bugcrowd/HackerOne-style platform labels.
3. Family telemetry can throttle noisy families without deleting or disabling
   them.

## Non-Negotiables

- No severity, confidence, bounty, CVSS, vulnerability, or exploitability fields
  may leak into Hypothesis, Observation, Evidence, Candidate, or early
  FindingProposal rationale.
- V3 `QUEUED` is not a validator PASS.
- V1/V2/V3 admission must fail closed when a required tier is missing.
- Circuit breaker action is throttle only; family deletion/disable is forbidden.
- PASS requires PostgreSQL integration tests plus full suite.

## P1 — Pure Domain Core

Files:

- `src/zest/research/validation/tier_gate.py`
- `src/zest/research/validation/severity.py`
- `src/zest/research/validation/circuit_breaker.py`

Tests:

- missing tier rejects admission;
- V3 `QUEUED` rejects as not passed;
- severity refuses unvalidated or out-of-scope signals;
- account takeover maps to P0 / Bugcrowd P1 / HackerOne Critical;
- noisy families throttle but are not disabled.

## P2 — Application Integration

Add a validator use case that reads append-only tier/audit/proof state and emits
durable validator decisions. FindingProposal admission must reject candidates
without required validator PASS.

Evidence:

- `SubmitFindingProposal` now requires security candidates to have append-only
  validator tier PASS evidence through the required tier before a
  FindingProposal can be created.
- Diagnostic candidates remain exempt because they do not represent a security
  finding.
- Missing V3 evidence and `V3_QUEUED` fail closed with
  `REJECTED_VALIDATION_NOT_PASSED` and an audit event.
- Full suite evidence (2026-08-20): `1465 passed, 9 skipped, 53 subtests passed`.

## P3 — Persistence

Add append-only validator/severity/circuit-breaker records if application
integration requires durable state beyond audit events. No destructive updates.

Evidence:

- No additional mutable tables were required for the current integration.
- Validator, severity, and circuit-breaker decisions are persisted as
  append-only `audit_event` records.
- Severity decisions stay outside Hypothesis, Observation, Evidence, Candidate,
  and early FindingProposal records.

## P4 — Integration Tests

Required PostgreSQL scenarios:

- V1/V2 missing blocks admission;
- V3 queued but not passed blocks admission;
- validated in-scope impact receives deterministic severity;
- out-of-scope/validation-missing severity is not scored;
- family with high rejected+inconclusive rate is throttled and not disabled;
- learning telemetry can explain the throttle decision.

Evidence:

- `tests/integration/test_sd_g10_validator_controls.py` covers all required
  PostgreSQL scenarios.
- Affected SD-G7/SD-G10/Gate14-Gate17 set (2026-08-20): `167 passed`.

## P5 — Seal Standard

`GATE_10_STATUS` is `PASS` after:

- SD-G10 unit tests pass;
- SD-G10 PostgreSQL integration tests pass;
- full pytest suite passes;
- `OPERATIONS.md` contains final evidence;
- `maturity.py` changes to PASS in the same seal commit.

Seal evidence (2026-08-20):

- Focused SD-G10 set: `29 passed`.
- Affected SD-G7/SD-G10/Gate14-Gate17 set: `167 passed`.
- Full suite: `1470 passed, 9 skipped, 53 subtests passed`.
