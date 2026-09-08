# P3 — Gate 22 Scenario Acceptance Contract

Status: SOURCE CONTRACT COMPLETE / DEPLOYED EXECUTION PENDING

The first stabilization scenario uses the existing `Gate22SurfaceLab`. The scenario is intentionally not a vulnerability benchmark. It asks whether deployed Zest can perform real target work and connect discovery to research without manual intervention.

The acceptance contract is locked before the first VDS run. A failure does not permit weakening it merely to obtain green output.

## Target

Use the existing loopback Gate 22 HTTP application.

The seed page `/` exposes multiple reachable surfaces, including normal links, SPA behavior, forms, browser-generated API requests, order paths, a workflow route, a dead path, and a cross-origin redirect.

The external stabilization runner should launch the existing lab when practical so it can retain the lab's own request-hit list as independent target-side proof. If the lab is launched separately, equivalent target-side access evidence must be retained.

## Layer A — Runtime / discovery

Layer A answers only:

> Can deployed START reach the target, execute a real Worker path, persist what happened, and terminate truthfully?

| Condition | Required evidence | Authoritative observable |
| --- | --- | --- |
| Target received traffic | At least one real request to `/` | `Gate22SurfaceLab.hits` or equivalent target-side access record |
| Seed produced additional surface knowledge | At least one discovered fact/frontier/path beyond the seed `/` | `analysis.surface.facts` / `analysis.surface.frontier_items` |
| Real execution occurred | At least one real browser/HTTP execution attempt and WorkerResult | `analysis.execution.attempts`, `analysis.execution.worker_results`, `engine_summary.browser_attempts` |
| Result was processed | At least one persisted Observation from that WorkerResult and discovery projection/fact | `analysis.execution.observations`, `analysis.surface.facts` |
| Lifecycle is truthful | No unresolved fatal fault/control obligation is hidden by terminal state | run detail `run_faults`, `control_obligations`, operational state |
| Work is no longer owned after terminal | No live local supervisor remains | run detail `locally_supervised == false` |

Layer A PASS does **not** prove that the research engine is connected.

## Layer B — Discovery / research coupling

Layer B answers:

> Did observed target information actually lead through the research system to a separate, target-coupled executable experiment and assessment?

All of the following are required for Layer B PASS:

1. At least one discovery Observation exists before the later model reasoning/admission activity.
2. Real model reasoning occurs on the standard model-driven path. For the normal Generator/Falsifier path this should produce model/reasoning activity, not a fabricated test model.
3. A non-discovery research Hypothesis is admitted. The seed discovery hypothesis whose `origin_reference` is `surface-discovery-v1` does not satisfy this condition by itself.
4. A separate Experiment exists for that non-discovery Hypothesis. The initial surface-discovery Experiment does not count.
5. That Experiment is **target-coupled**. `diagnostic.echo` alone does not satisfy this stabilization requirement because it can prove plumbing without testing observed application behavior.
6. The target-coupled Experiment produces a real ExecutionAttempt and WorkerResult through the deployed Worker path.
7. Its result reaches a `HypothesisAssessment` referencing the same Experiment.
8. The research state changes or closes truthfully after that assessment; it must not remain silently orphaned.

No finding, Evidence, Candidate, or FindingProposal is required for this first stabilization scenario.

## Why `diagnostic.echo` is not Layer B success

A control echo remains useful for internal plumbing tests, but the stabilization objective is stronger: discovery must influence a real target operation.

Counting `diagnostic.echo` as Layer B would allow this false success:

```text
browser discovers target
  -> model ignores usable target action
  -> diagnostic.echo succeeds
  -> run completes
  -> reported as research success
```

That would dilute the product objective and is explicitly forbidden.

## Model-context evidence rule

The current read model intentionally does not expose raw model prompts or target Observation payloads. Therefore the first test uses a combined proof:

- persisted discovery Observation exists before reasoning,
- persisted Generator/Falsifier reasoning records carry a context fingerprint,
- current `ProposeResearchHypothesis` source deterministically builds ResearchContext from run Observations.

This is sufficient to establish that the normal context-building path was used without exposing private raw prompts. It does not claim that structured discovery facts/AttackSurfaceGraph were directly provided to the model; P1 records that limitation separately.

## Accepted main-run completion reasons

For this bounded stabilization scenario, main-run SUCCESS may use only:

- `COMPLETED_NO_MORE_OPPORTUNITIES`, or
- `MAX_CYCLES_REACHED` **only if both Layer A and Layer B already passed and there are no unresolved control obligations/fatal faults**.

The following are not accepted as main-run success:

- `OPERATIONAL_FAILURE`
- `REQUIRE_HUMAN_REVIEW`
- `CORE_BLOCKED`
- `AUTH_REQUIRED`
- `RATE_LIMITED`
- `CONTENT_POLICY_BLOCKED`
- `NO_COMPATIBLE_RUNTIME`
- `BUDGET_EXHAUSTED`
- `MAX_DURATION_REACHED`
- operator cancellation

These may be truthful outcomes, but they mean the selected acceptance scenario did not complete autonomously.

## Classification

### `SUCCESS`

All Layer A conditions pass, all Layer B conditions pass, terminal reason is accepted, no unresolved fatal fault/control obligation remains, and no supervisor is left live.

### `COULD_NOT_START`

The run never enters real supervised work because deployment, health, preflight, configuration, model, Worker, schema, authorization, scope, budget, or lease readiness blocks START.

### `STUCK_OR_CRASHED`

START begins but the run hits a fatal operational state, loses progress for the configured no-progress interval, supervisor ownership becomes inconsistent, or timeout cancellation cannot cleanly stop the work.

### `FINISHED_WITHOUT_REQUIRED_WORK`

The run reaches a clean terminal state but one or more required acceptance conditions were never performed, and there is no specific persisted proof that the missing step is an unsupported product capability.

Examples:

- only the seed page is touched,
- only discovery runs and generic research never executes,
- model reasoning occurs but no separate target-coupled Experiment is dispatched,
- only `diagnostic.echo` is executed after discovery.

### `CAPABILITY_MISSING`

The missing Layer B step is explicitly explained by current product capability/planning records, for example a persisted admission/planning rejection such as `UNSUPPORTED_CAPABILITY` / `PLANNING_INPUT_REJECTED`, and no supported target-coupled equivalent executes.

This is not converted to SUCCESS by changing the scenario or forcing a weaker capability.

## Progress fingerprint for no-progress detection

The runner should treat a poll as progress when any of these change:

- persisted/effective state,
- current phase,
- cycle number,
- request/Worker/model count,
- hypothesis/experiment/observation count,
- latest authoritative update timestamp,
- analysis counts for reasoning/assessment/surface facts/attempts/WorkerResults.

A long model call must be allowed enough time before declaring a stall. The exact no-progress timeout is an operator input to the runner and is recorded with the result.

## P3 decision

The scenario contract is now frozen for the first VDS run.

A field failure may lead to a root-cause fix, but not to weaker acceptance criteria unless new evidence proves that a criterion was measuring the wrong product behavior rather than exposing a missing or broken capability.
