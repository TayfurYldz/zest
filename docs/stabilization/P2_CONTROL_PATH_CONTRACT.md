# P2 — START Control-Path Contract

Status: SOURCE CONTRACT COMPLETE / DEPLOYED CONFIRMATION PENDING

This contract defines the existing interfaces the stabilization probe is allowed to use. It does not add lifecycle authority and it does not bypass PostgreSQL, preflight, Core, or the daemon supervisor.

## Services

Default local endpoints from current source:

- Zest dashboard: `http://127.0.0.1:8765`
- zestd Operator API: `http://127.0.0.1:8766`

The deployed environment may override ports through existing settings. VDS values must be read from the running service configuration before execution.

## Authority model

Program bootstrap persists configuration into PostgreSQL.

START does not accept caller-supplied scope, budget, target, or runtime overrides. `zestd` reconstructs the command from PostgreSQL SoR and performs a fresh preflight. Every Worker dispatch remains independently subject to Core authorization.

The external stabilization runner is therefore an operator client only.

## 1. Health/readiness

```http
GET {ZEST_URL}/health
```

For a new acceptance run, the important field is:

```text
ready_for_start == true
```

`ok == true` alone is insufficient. Current health semantics can report the daemon/database process alive while Worker/model/schema readiness still prevents START.

Record at minimum:

- `engine_version`
- `environment_name`
- `ready_for_start`
- database availability/schema head
- Worker health and capability list
- model configured/authenticated/structured-output/availability/health
- runtime instance id

## 2. Program + ResearchRun creation

Existing dashboard endpoint:

```http
POST {DASHBOARD_URL}/api/programs/bootstrap
Content-Type: application/json
```

Required fields:

- `program_name`
- `target_reference`
- `authorization_reference`
- `in_scope` with at least one entry

Useful explicit stabilization fields:

```json
{
  "program_name": "Zest Stabilization Gate22",
  "program_handle": "zest-stabilization-gate22-<unique>",
  "platform": "manual",
  "target_reference": "<EXACT_LOCAL_TARGET_URL>",
  "authorization_reference": "controlled-local-stabilization-target",
  "operator_id": "stabilization-operator",
  "research_question": "Map the authorized application surface and derive one concrete testable research step from observed target behavior.",
  "in_scope": ["<EXACT_LOCAL_ORIGIN>"],
  "out_of_scope": [],
  "forbidden_actions": [],
  "max_response_bytes": 1048576,
  "timeout_ms": 10000,
  "max_requests_per_window": 60,
  "window_seconds": 60,
  "max_requests": 500,
  "max_tool_calls": 200,
  "max_runtime_ms": 600000,
  "max_concurrency": 1,
  "max_cycles": 20,
  "max_experiments": 50,
  "max_model_calls": 50,
  "max_worker_invocations": 100,
  "max_elapsed_ms": 600000,
  "max_selected_opportunities": 4,
  "max_runtime_fallback": 1,
  "daily_llm_budget_microdollars": "<EXPLICIT_TEST_BUDGET>",
  "side_effect_ceiling": 1
}
```

The model budget must be explicitly chosen before the VDS test. Do not silently use the dashboard default `0` for a Layer B test that requires real model calls.

The bootstrap response must provide:

- `program_id`
- `authorization_source_id`
- `research_run_id`
- `budget_id`
- `configuration_fingerprint`
- `state == STARTABLE`

Bootstrap persists setup only; it does not start active testing.

## 3. Explicit preflight

```http
POST {ZEST_URL}/api/runs/{research_run_id}/preflight
Content-Type: application/json

{}
```

Required result:

```text
status == READY_TO_START
```

Record every check and reason. Current checks cover:

- database reachability,
- schema head,
- run configuration,
- authorization source,
- scope compilation,
- target scope decision,
- budget,
- orchestration recoverability,
- lease conflict,
- Worker capabilities,
- Worker health,
- browser containment when surface discovery is required,
- model readiness.

`READY_TO_START` is readiness only. It is not execution authorization.

## 4. START

```http
POST {ZEST_URL}/api/runs/{research_run_id}/start
Content-Type: application/json

{}
```

No test runner field may override target/scope/budget/config here.

The expected start response includes:

- `research_run_id`
- `state`
- `cycle_number`
- `outcome`
- `stop_reason`
- `last_phase`
- `hypothesis_id`
- `experiment_id`

The runner must immediately begin observation; a successful HTTP response is not itself a successful run.

## 5. Observe

Primary truth-oriented polling:

```http
GET {ZEST_URL}/api/runs/{research_run_id}
```

This gives state/phase, effective operational state, faults, lease/liveness, request/Worker/model counts, hypothesis/experiment/observation counts, control obligations, preflight and timeline.

Deep read-only analysis:

```http
GET {ZEST_URL}/api/runs/{research_run_id}/analysis
```

This exposes bounded projections of:

- model reasoning/admissions,
- hypotheses and assessments,
- experiments/plans/attempts/WorkerResults/Observations,
- discovery facts/inferences/frontier,
- opportunities/selections,
- evidence/promotion/verification,
- faults and consistency warnings.

Optional live stream:

```http
GET {ZEST_URL}/api/runs/{research_run_id}/events
Accept: text/event-stream
```

SSE is optional for the stabilization probe. REST remains authoritative enough for pass/fail classification.

## 6. Cancel

On total-time or no-progress timeout:

```http
POST {ZEST_URL}/api/runs/{research_run_id}/cancel
Content-Type: application/json

{}
```

Then poll run detail until both conditions hold:

- persisted state reflects operator cancellation/terminal completion,
- `locally_supervised == false`.

Failure to stop is a stabilization failure; the runner must not simply exit and leave work behind.

## 7. Result states used by stabilization

The runner reports only:

- `SUCCESS`
- `COULD_NOT_START`
- `STUCK_OR_CRASHED`
- `FINISHED_WITHOUT_REQUIRED_WORK`
- `CAPABILITY_MISSING`

`COMPLETED` is a persisted lifecycle state, not the stabilization result by itself.

## VDS confirmation required before S2 PASS

When the VDS returns, confirm the actual:

1. dashboard URL/port,
2. zestd URL/port,
3. deployed release identity,
4. health response schema,
5. bootstrap response,
6. preflight response,
7. START response,
8. run-detail/analysis accessibility,
9. cancel behavior.

Until then P2 is source-complete but S2 remains open.
