# Zest START-to-Terminal Stabilization

Status: ACTIVE

## Objective

Restore the existing Zest to a state where the real deployed START path performs verifiable work and reaches an accepted terminal state without manual intervention.

This campaign is not a feature-development phase and does not claim vulnerability-detection quality. It proves the current product can execute its intended runtime path reliably enough to become a stable base for later security-quality work.

## Non-dilution invariant

Stabilization must not make Zest weaker merely to obtain a green run.

The following are forbidden as stabilization shortcuts:

- disabling or weakening scope enforcement,
- bypassing Core authorization or approval boundaries,
- lowering security-relevant evidence requirements,
- converting failed or incomplete work into `COMPLETED`,
- swallowing Worker, model, browser, persistence or orchestration errors,
- replacing a required real Worker/model path with a fake path in the deployed acceptance run,
- removing a capability because it is currently difficult to execute,
- shrinking the scenario after a failure merely to make the result pass,
- reducing the intended research loop to discovery-only while claiming research success,
- broad refactors whose primary purpose is to avoid diagnosing the current failure.

A simplification is allowed only when it removes test noise without changing product semantics, authority boundaries, required capabilities, or the acceptance contract. If the current product cannot perform a required step, record `CAPABILITY_MISSING`; do not hide the gap.

## Source candidate

- Base branch: `campaign/canonical-mr5-mr6`
- Base commit: `5b81b3d1ead3ed837ccade4fd17e104389fb1e26`
- Stabilization branch: `stabilization/start-to-terminal`

The source candidate is not yet the deployment baseline. The installed VDS release must first be proven to match a known source revision.

## Scope freeze

Until stabilization closes:

- no new hunter families,
- no NoScope-derived architecture work,
- no Semantic Application Model,
- no Attack Path Planner / Chain Executor,
- no new product integrations,
- no broad refactor.

Only evidence-backed changes that directly restore the chosen scenario, make its result measurable, or prevent recurrence of a proven defect are in scope.

## Execution rule

Gate acceptance is sequential, but preparation work may proceed in parallel when it does not depend on unavailable VDS evidence.

VDS-offline work may include:

- source and branch verification,
- inspection of current lifecycle tests and runtime composition,
- confirmation of API/control surfaces from source,
- preparation of the external observer/runner,
- confirmation of the existing local target and expected evidence,
- static review of already-known planning/discovery blockers in the selected candidate.

No local or source-only result may be reported as VDS-validated.

## Current execution queue while VDS is offline

Work is performed in this order. Later preparation may begin only when it does not require changing product behavior before evidence exists.

### P1 — Candidate lifecycle audit

Goal: establish exactly what the selected candidate already does before writing fixes.

Tasks:

1. Review the six post-master stabilization commits and map each change to the runtime failure class it addresses.
2. Trace `START` from Operator API through `ZestdRuntime`, supervisor, ARC, discovery/research, Core authorization, Worker result, observation, assessment, promotion and terminal-state handling.
3. Mark all points where an expected real run can stop, wait, fail or silently complete without required work.
4. Do not change product code during this audit.

Exit artifact: one source-backed lifecycle map plus a list of suspected blockers labelled `UNPROVEN` until reproduced.

### P2 — Control-path contract

Goal: define the exact external operations the VDS test will use, with no guessed endpoint or payload.

Tasks:

1. Confirm program/run creation path and required bootstrap fields.
2. Confirm preflight, start, run-detail, analysis/events and cancel operations.
3. Define the minimal safe bootstrap payload for the selected existing local target without lowering normal policy checks.
4. Define what data the external runner may read and how timeout/cancel is handled.

Exit artifact: a short create → preflight → start → observe → cancel runbook derived from current source.

### P3 — Scenario acceptance contract

Goal: decide before execution what counts as real work.

Use the existing Gate 22 loopback target unless a deployment constraint later proves it unsuitable.

Layer A — runtime/discovery evidence:

- target receives a real request,
- at least one reachable surface beyond the seed is discovered,
- real Worker invocation and WorkerResult exist,
- result processing reaches persisted observation/state transition,
- run reaches an accepted end state with no orphaned active work.

Layer B — research-coupling evidence:

- discovered/observed target information is present in the research/model context when the chosen path requires a model,
- a concrete supported experiment or equivalent actionable research step is produced,
- that step is executed through the real Worker path,
- the result reaches assessment/feedback and changes or closes research state.

Layer A and Layer B are reported separately. Layer A success never substitutes for Layer B.

Exit artifact: an evidence matrix mapping every acceptance condition to its authoritative observable source.

### P4 — External runner specification

Goal: prepare a small observer/driver without adding a new product subsystem.

The runner may:

- call existing bootstrap/control APIs,
- poll existing run detail/analysis,
- collect existing semantic events and errors,
- enforce total runtime and no-progress limits,
- call existing cancel operation on timeout,
- classify the result using the stabilization result classes.

The runner must not:

- create alternate lifecycle logic,
- mutate database state directly to advance a run,
- bypass preflight or authorization,
- fake model/Worker success in the deployed acceptance run,
- reinterpret failure as success.

Exit artifact: runner interface, inputs, stop conditions and result schema. Implementation may be prepared before VDS returns, but deployed execution waits for S1/S2 validation.

## Gates

### S0 — Source freeze

PASS when the stabilization branch is pinned to the selected candidate and unrelated feature work is frozen.

### S1 — Deployment provenance

PASS when the installed immutable VDS release, package/runtime, PostgreSQL, model runtime, Worker and Chromium are identified and the deployed source is matched to a known revision.

S1 blocks interpretation of VDS field results, but does not block source-side preparation for later gates.

### S2 — Real control path

PASS when the deployed create/preflight/start/observe/cancel path is confirmed from running services and existing interfaces. No guessed endpoint, phase name, or authority path is accepted.

Source-level confirmation may be prepared before S1; deployed confirmation closes the gate.

### S3 — Single existing scenario and acceptance contract

Prefer the existing Gate 22 loopback surface-discovery lab unless deployment constraints prove it unsuitable. Do not build a new lab merely to make the test easier.

Before execution, record the expected evidence and evaluate two independent layers:

#### Layer A — Runtime/discovery path

- target receives a real request,
- reachable surface is discovered,
- a real Worker request and WorkerResult are recorded,
- result processing produces the expected persisted observation/state transition,
- the run reaches an accepted end state without orphaned active work.

Passing Layer A proves the basic runtime/discovery path only.

#### Layer B — Research coupling

- discovered/observed target information reaches the model/research context when the selected path requires a model,
- the research layer produces a concrete supported experiment or equivalent actionable research step,
- that step executes through the real Worker path,
- its result reaches assessment/feedback and changes or closes research state.

No specific model wording is required. Evidence is judged by persisted inputs, actions and outcomes.

Layer A must not be reported as proof that Layer B works.

If the current product cannot perform the selected required step, classify it as `CAPABILITY_MISSING`; do not weaken the scenario to hide the gap.

### S4 — First real deployed run

Run one fresh ResearchRun through the real deployed START path. Collect run detail, analysis, available events/errors, counts and stop reason.

Classify the run only as:

- `SUCCESS`
- `COULD_NOT_START`
- `STUCK_OR_CRASHED`
- `FINISHED_WITHOUT_REQUIRED_WORK`
- `CAPABILITY_MISSING`

Zero findings are allowed. Zero required work is not.

A terminal state alone is never sufficient for success.

### S5 — Evidence-backed root-cause closure

The unit of work is one proven root cause, not necessarily one visible symptom.

For each blocking root cause:

1. record expected vs actual behavior,
2. preserve reproduction and run evidence,
3. distinguish symptom from root cause,
4. make the smallest coherent root-cause fix,
5. add the smallest regression check within the existing test structure,
6. run the directly relevant existing tests locally/where available,
7. deploy a new immutable VDS release only after the focused checks pass,
8. rerun the same scenario as a fresh run.

Multiple symptoms caused by the same root cause may be fixed together. Independent changes remain separate.

Do not bypass scope, authorization, budgets, approval boundaries, truthful failure states, or error reporting merely to make the run advance.

### S6 — Repeatability close

Repeatability begins only after the chosen scenario performs the required real work, including the research-coupling layer when that layer is part of the stabilization acceptance contract.

Run the exact scenario ten consecutive times on an unchanged revision/release with fresh run state. Any product-code change resets the count.

Then verify:

- cancel actually stops active work,
- after service restart, a new run can be created and started.

Closure statement must remain scoped to the tested revision, environment and scenario:

> The selected revision and release completed the selected scenario ten consecutive times in the specified VDS environment, and the cancel plus post-restart new-run checks passed.

This does not claim concurrency safety, interrupted-run recovery, broad target reliability, or security-detection quality unless separately tested.

## Next stage after stabilization

Only after S6 passes, run one known vulnerable case and one secure control to prove detection + verification and non-finding behavior.

NoScope-derived work, Semantic Application Model, Attack Path Planner, Chain Executor, new hunter families and other product expansion remain deferred until that vertical security slice is understood.
