# S4 First Real Field Run — Root Cause

Date: 2026-09-09
Environment: staging VDS
Deployed product baseline: `ae89df540893a972866ce0e7f91765bf7a0244f6`
Acceptance probe revision: `442cc0ae01332881cdb3e82cdbe07233f43eb067`
ResearchRun: `918e6194-bc38-4ed3-8b18-f001e7824661`
Gate22 origin: `http://127.0.0.1:36917`

## Decision

S4 does **not** pass.

The authoritative pre-cleanup state was `WAITING_HUMAN / REQUIRE_HUMAN_REVIEW` at `WORKER_RESULT_RECORDED`, cycle 10. The probe subsequently issued its safety cleanup cancel and therefore its final post-cleanup classification (`COMPLETED / OPERATOR_CANCELLED`) must not be interpreted as the product's natural terminal outcome.

The first evidence-backed product root cause is a discovery boundary-classification/disposition defect. Browser containment behaved correctly and must not be weakened.

## What worked

Before the failure, deployed Zest performed substantial real discovery work:

- bootstrap/preflight/START succeeded,
- the controlled target received traffic,
- discovery expanded beyond `/`,
- multiple `browser.page` and `http.transaction` executions succeeded,
- observations/facts/frontier state were persisted,
- no fatal runtime fault was present.

The run had reached 103 attempted network requests, 10 Worker invocations, 10 experiments and 9 observations when the blocking transition occurred.

## Exact blocking branch

The blocking frontier item was:

- frontier id: `cfad95c1-7575-4118-b030-c5b4e695b10d`
- goal: `INSPECT_CONTROL`
- element: anchor named `oos`
- observed href destination: `http://example.com/`
- candidate execution origin/path: local Gate22 page (`http://127.0.0.1:36917/`)
- scope hint: `in_scope_observed_control`

The corresponding browser interaction produced:

- experiment: `472b234f-fae5-462d-bb15-cb46015dd1e6`
- WorkerResult: `wr:34d82e39-fd6d-41e0-845b-3b91cbdb707a`
- status: `REAUTHORIZATION_REQUIRED`
- resulting ControlEvent: `NEW_ORIGIN_BOUNDARY`
- boundary location: `http://example.com/`

The Worker did not silently cross the boundary. It correctly returned an authority-transition result. This is desired containment behavior.

The unresolved control obligations were then:

1. `REAUTHORIZATION_REQUIRED` for the WorkerResult, and
2. `OPEN_EXPERIMENT_CONTROL` because the experiment remained `AUTHORIZATION_CHECK`.

ARC therefore truthfully moved the run to human review before any normal research lane executed.

## Source-level root cause

`research.discovery.projection` correctly records an off-origin control as a `SCOPE_BOUNDARY_CANDIDATE`, but still creates an executable `INSPECT_CONTROL` frontier whose `candidate_origin`/`candidate_path` point at the current in-scope page and whose href destination survives only in attributes.

`SurfaceDiscoveryRunner` therefore sees the candidate page itself as in-scope, compiles a local `browser.page interact` plan, and dispatches it. The Worker later discovers that the interaction wants to cross authority and correctly returns `REAUTHORIZATION_REQUIRED`.

This creates avoidable human-review work for a boundary that the frozen compiled scope can already classify definitively as out-of-scope.

## Required smallest coherent fix

The fix must preserve Core/Worker authority and handle both known and runtime-discovered boundaries.

### 1. Pre-dispatch known control boundary

For an `INSPECT_CONTROL` frontier containing an HTTP(S) `href_origin`/`href_path`, evaluate that destination against the frozen `CompiledScope` before dispatch.

If the result is definitively `DENY + OUT_OF_SCOPE`:

- do not click,
- terminally disposition that discovery frontier as `BLOCKED_SCOPE`,
- preserve the existing boundary fact,
- continue discovery.

Unknown/ambiguous scope classification must retain the existing safe behavior; it must not be auto-allowed.

### 2. Runtime-discovered authority transition

A local control may still reveal an out-of-scope redirect only after execution (for example Gate22 `/redirect-cross`). When the Worker returns `REAUTHORIZATION_REQUIRED`:

- preserve the WorkerResult and ControlEvent,
- evaluate the typed reauthorization target against the same frozen `CompiledScope`,
- if it is definitively `DENY + OUT_OF_SCOPE`, terminally block that experiment/frontier without granting authority and continue discovery,
- if scope is unknown/ambiguous, retain the existing `AWAITING_REAUTHORIZATION / WAITING_HUMAN` behavior.

No auto-allow is permitted.

Existing control-obligation semantics already treat a `BLOCKED` experiment as terminal, so a truthful deny closes this branch without erasing the reauthorization evidence.

## Regression requirements

Before another S4 field run, focused tests must prove:

- a known off-origin anchor is not clicked and becomes `BLOCKED_SCOPE`,
- a same-origin route that redirects off-origin still produces Worker boundary evidence but is automatically denied only when compiled scope classifies the new destination `OUT_OF_SCOPE`,
- an unknown/ambiguous authority transition still requires human review,
- no external target receives the blocked request,
- in-scope controls continue to execute normally.

## Probe correctness follow-up

The acceptance probe currently classifies after performing cleanup cancel. That can obscure the original failure as `COMPLETED / OPERATOR_CANCELLED`.

The probe must retain and classify against the pre-cleanup final detail, then record cleanup cancellation separately. This is a measurement correction only; acceptance criteria remain unchanged.

## Non-dilution statement

Do not modify Worker/browser containment to make this scenario pass. The Worker correctly prevented cross-boundary egress. The product defect is that discovery promoted a known boundary into executable work and then treated a definitively out-of-scope authority transition as unresolved human work instead of a terminal denied branch.

No second S4 run should be started until the focused regression tests and an immutable deployment of the root-cause fix exist.
