# SD-G11 Plan — Production Executor Fabric

**Status:** PASS
**Previous gate:** SD-G10 PASS (`f5b08c0`)
**Do not confuse with:** old infrastructure `GATE 11 — Runtime Routing Integrity`.
**Also not:** `GATE 21 — browser / application state`.

## Purpose

SD-G11 starts the production executor fabric muscle. The goal is to make
authorized Worker execution more field-replayable and evidence-rich without
expanding scope, bypassing Core, or weakening any in-scope capability.

The first slice is deterministic replay manifesting. It does not redispatch a
Worker and does not create Evidence or Findings.

## Non-Negotiables

- Core remains the only authority for scope, budget, side-effect level, and
  capability authorization.
- Replay tooling must never rerun a side-effectful action automatically.
- Secrets and raw session material must not appear in replay manifests.
- Browser/stateful results must be marked environment-sensitive unless a later
  slice proves deterministic replay.
- This work must not modify old infrastructure `GATE 11` status or G21/G22
  maturity.

## P1 — Executor Replay Manifest

Files:

- `src/zest/application/executor_replay_manifest.py`

Behavior:

- reads persisted Experiment, ExecutionAttempt, WorkerResult, and Observation
  rows;
- produces a deterministic canonical manifest and SHA-256 hash;
- includes correlation, authorization reference, capability/action, side-effect
  level, WorkerResult status, artifact descriptor count, and observation
  payload digests;
- stores raw result, diagnostics, artifacts, and observations as redacted
  digests only;
- classifies replay as:
  - `DETERMINISTIC_REPLAY`;
  - `ENVIRONMENT_SENSITIVE`;
  - `HUMAN_REVIEW_REQUIRED`;
  - `NOT_REPLAYABLE`.

Evidence:

- Unit: deterministic manifest, secret-free output, missing WorkerResult
  fail-closed, browser/stateful environment-sensitive.
- PostgreSQL: G19 authorized HTTP execution now proves deterministic replay
  manifest generation from real ledger rows.
- Focused checks (2026-08-20): `6 passed`.
- Affected checks (2026-08-20): `35 passed, 5 skipped`.
- Full suite (2026-08-20): `1474 passed, 9 skipped, 53 subtests passed`.

## P2 — Replay Bundle Artifacts

Add controlled artifact bundle assembly after the manifest model is stable:
request templates, response digests, screenshot/trace descriptors where present,
and privacy-preserving redaction metadata.

Files:

- `src/zest/application/executor_replay_bundle.py`

Behavior:

- wraps the replay manifest with a deterministic replay bundle and bundle hash;
- reads the durable ExperimentPlan as a request-template fingerprint without
  exposing raw arguments;
- emits response digests from WorkerResult rows without response bodies,
  diagnostics, or control-signal content;
- keeps screenshot/trace/response artifact presence visible while storing
  artifact descriptors only as digests;
- sets replay controls to fail closed: no automatic redispatch, Core
  authorization required, redirect reauthorization required, and human review
  required for high side-effect replay classes.

Evidence:

- Unit: deterministic bundle hash, secret-free output, missing plan explicit,
  and high side-effect bundles requiring human review.
- PostgreSQL: G19 authorized HTTP execution now proves manifest + replay bundle
  generation from the same persisted ledger rows.
- Focused checks (2026-08-20): `9 passed`.

## P3 — Production Executor Vertical Slice

Extend the manifest contract to HTTPS/browser/API workers with secure,
vulnerable, deceptive, and scope-escape fixtures. No worker can reach outside
the Core-issued envelope; every redirect still requires reauthorization.

Files:

- `src/zest/application/executor_fabric_assessment.py`
- `src/zest/worker_runtime/python/http_transaction.py`
- `workers/python/zest_worker/http_transaction.py`
- `tests/e2e/lab/https_transaction_lab.py`
- `tests/unit/application/test_executor_fabric_assessment.py`
- `tests/unit/worker_runtime/test_http_transaction.py`
- `tests/integration/test_sd_g11_executor_fabric_vertical_slice.py`

Behavior:

- reads persisted WorkerResult rows plus replay manifest/bundle hashes without
  redispatching Workers;
- extends `http.transaction` to HTTPS loopback transport while preserving
  Core-issued network envelope enforcement and redirect STOP behavior;
- emits a deterministic fabric assessment hash with derived invariant metadata
  only, not raw response bodies, diagnostics, screenshots, traces, or session
  material;
- verifies replay controls stay fail closed: automatic redispatch forbidden,
  Core authorization required, and redirect reauthorization required;
- treats browser ledger rows as environment-sensitive without claiming G21
  browser/application-state maturity;
- proves HTTPS/API/state-transition worker behavior across vulnerable, secure,
  deceptive, scope-escape, and redirect fixtures;
- confirms scope-escape attempts stop at the Core-issued network envelope and
  redirect results stop with `REAUTHORIZATION_REQUIRED`.

Evidence:

- Focused checks (2026-08-20): `36 passed`.
- SD-G11 affected checks (2026-08-20): `104 passed`.
- Full suite (2026-08-20): `1484 passed, 9 skipped, 53 subtests passed`.

## G21 Boundary Note

Current local check (2026-08-20):

- real Chromium G21 browser lab ran: `22 passed, 5 skipped`;
- required cgroup mode failed closed because `/sys/fs/cgroup/init.scope` is not
  a writable delegated subtree.

Therefore G21 remains `PENDING`. SD-G11 P1 does not claim or depend on G21 PASS.
