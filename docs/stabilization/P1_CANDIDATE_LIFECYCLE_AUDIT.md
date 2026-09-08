# P1 — Candidate Lifecycle Audit

Status: SOURCE AUDIT COMPLETE / FIELD IMPACT NOT YET VALIDATED

Candidate source: `5b81b3d1ead3ed837ccade4fd17e104389fb1e26`

Stabilization branch: `stabilization/start-to-terminal`

## Purpose

Map the current START-to-terminal path before changing product behavior. This document separates source-confirmed behavior from field impact that still requires the VDS.

No production behavior was changed by this audit.

## Six post-master candidate commits

| Commit | Purpose | Stabilization interpretation |
| --- | --- | --- |
| `b1e5cf8` | Preserve ProgramPolicy through dispatch/discovery and fail closed when a historical network authorization cannot safely reconstruct its original network envelope | Security-strengthening recovery fix; prevents unsafe redispatch |
| `26ae120` | Add strict START→STOP QA manifest and expand lifecycle/browser/operator qualification | QA coverage only; does not prove the deployed user flow |
| `610dbf3` | Require a live Codex diagnostic qualification before production model readiness and retain redacted failure provenance | Fixes model-readiness false positives |
| `07deb1e` | Make reauthorization a durable control obligation, reject unhandled loop states, guard natural completion | Fixes false-completion/fallthrough paths |
| `874d6d2` | Report only live supervisor threads as locally owned | Fixes stale liveness projection |
| `5b81b3d` | Resolve operator DENY of reauthorization without granting new authority and resume only when obligations are cleared | Closes a durable WAITING_HUMAN branch without weakening policy |

These changes are directly relevant to stabilization. None is treated as field-proven merely because its tests pass.

## Current production lifecycle

```text
Dashboard bootstrap
  -> Program / Scope / ProgramPolicy / AuthorizationSource / ResearchRun / Budget in PostgreSQL

Operator API START
  -> ZestdRuntime.start_run(run_id)
  -> allocate daily model budget when required
  -> reconstruct_start_command(..., recovery=False) from PostgreSQL SoR
  -> preflight
       DB / schema / run config / authorization / scope / target / budget
       reconciliation / lease / Worker capability / Worker health
       browser containment / model readiness
  -> AutonomousResearchController.start(command)
  -> attach LocalRunSupervisor with lease-fenced controller + Worker

Supervisor tick
  -> reload persisted orchestration state
  -> renew/check lease
  -> AutonomousResearchController.step(command)

First normal production tick for a URL target
  -> command.surface_discovery is present
  -> ARC._step_surface_discovery(...)
  -> SurfaceDiscoveryRunner.run_cycle(...)
  -> Prepare Experiment
  -> Core/dispatch authorization
  -> real Worker
  -> WorkerResult / Observation / discovery projection
  -> one cycle checkpoint

Subsequent production ticks
  -> LocalRunSupervisor removes `surface_discovery` after the first nonterminal discovery tick
  -> ARC generic research path
  -> SelectResearchOpportunities
  -> Generator
  -> Falsifier
  -> admission
  -> ExperimentPlan
  -> Core authorization
  -> Worker
  -> WorkerResult
  -> Observation
  -> Assessment
  -> PromotionPipeline when eligible
  -> next bounded cycle or truthful stop
```

## Truthful stop / wait boundaries already present

The candidate explicitly preserves these as non-success conditions instead of forcing progress:

- preflight NOT_READY,
- scope/Core deny,
- model auth/rate/content/runtime failure,
- Worker start/invocation failure,
- UNKNOWN_OUTCOME,
- reauthorization required / human review,
- unresolved control obligations,
- exhausted budget/bounds,
- supervisor fault,
- lease loss/fencing,
- unsupported or untestable model proposal.

Stabilization must preserve these boundaries.

## Source-confirmed gaps to observe in the first field run

### GAP-01 — Production discovery cadence differs from the Gate 22 E2E path

Classification: **SOURCE-CONFIRMED BEHAVIOR DIFFERENCE; FIELD IMPACT UNPROVEN**

`reconstruct_start_command(..., recovery=False)` gives the new production run a `SurfaceDiscoveryStart`.

`AutonomousResearchController.step()` routes to surface discovery while that field is present, and one call to `_step_surface_discovery()` performs one `SurfaceDiscoveryRunner.run_cycle()`.

`LocalRunSupervisor.tick()` then replaces its command with `surface_discovery=None` after the first discovery tick remains READY/RUNNING. Therefore normal daemon supervision transitions to generic research after one discovery cycle.

By contrast, Gate 22's direct `run_bounded(command)` keeps the same command and can execute repeated discovery cycles. A green Gate 22 E2E therefore does not prove that deployed START performs the same breadth of discovery.

This is the highest-priority field observation. Do not change it before the real run shows its consequence.

### GAP-02 — Generic model-driven planning cannot currently compile major multi-action network capabilities

Classification: **SOURCE-CONFIRMED CAPABILITY LIMITATION; FIELD IMPACT UNPROVEN**

`plan_admitted_hypothesis()` accepts the model's `suggested_capability`, but rejects a capability when its registry definition contains more than one action. It also only automatically supplies a required `message` argument.

Current important network capabilities do not fit that generic compiler:

- `browser.page` exposes `observe`, `navigate`, and `interact`, and requires target-specific arguments such as `authorized_origin` and `path`.
- `http.transaction` exposes `read` and `mutate`, and requires `authorized_origin`, `method`, and `path`.

If the Generator proposes either through the generic path, the plan is rejected as untestable rather than being executed. That is safe, but it may prevent Layer B from producing a real target-coupled experiment.

Do not work around this by forcing `diagnostic.echo`; a diagnostic echo would prove plumbing, not target research.

### GAP-03 — The bridge from discovery to generic opportunity selection is diagnostic, not surface-native

Classification: **SOURCE-CONFIRMED COUPLING LIMITATION; FIELD IMPACT UNPROVEN**

Surface discovery creates a durable discovery hypothesis with origin `surface-discovery-v1`.

`SelectResearchOpportunities` does not directly consume discovery facts/frontier/AttackSurfaceGraph. Its generated diagnostic sources are differentials, invariants, chains, change events, hypotheses, negative knowledge, plus separately-produced pending opportunity candidates.

Because the discovery hypothesis exists, `propose_diagnostic_opportunities()` can create a `HYPOTHESIS_FOLLOWUP`, whose configured direction is explicitly to continue the existing diagnostic hypothesis with a control echo.

Therefore a discovered route is not, by this path alone, transformed into a target-specific executable research opportunity.

### GAP-04 — Model research context contains Observations but not the structured discovery graph/facts directly

Classification: **SOURCE-CONFIRMED CONTEXT LIMITATION; FIELD IMPACT UNPROVEN**

`ProposeResearchHypothesis` loads run Observations into `ResearchContext`, so target data observed before the model call can reach the model.

However the same generic context construction does not directly load `discovery_facts`, frontier state, or the AttackSurfaceGraph. Those records remain available elsewhere in the SoR/HQ analysis, but are not direct generic Generator context inputs in this path.

For the first field run we will therefore distinguish:

- observation-to-model coupling, which the current code can provide,
- structured surface-model-to-planner coupling, which this generic path does not directly provide.

### GAP-05 — Generator suggests a capability without receiving an executable action/argument contract in the canonical research prompt

Classification: **SOURCE-CONFIRMED PLANNING RISK; FIELD IMPACT UNPROVEN**

The Generator output contract requires one free-string `suggested_capability`. The generic research prompt carries structured research context and output-contract instructions, but does not provide the capability registry's actions and required argument schemas as execution authority.

The deterministic planner correctly refuses unsupported/ambiguous output. This protects execution authority, but it also means a valid security idea can fail to become an executable plan.

## Important non-bugs

The following are not defects merely because they stop a run:

- Core refuses an out-of-scope or disallowed action.
- Reauthorization requires a human decision.
- A model proposes a capability Zest cannot deterministically compile.
- A crash leaves an external execution outcome unknown and Zest fails closed.
- A run ends with zero findings after performing the required work.

The defect is a false state transition, missing intended capability, broken coupling, crash, hang, or incorrect completion—not the existence of safety boundaries.

## First VDS run observation priorities

1. Does the first discovery tick execute a real browser request and persist its Observation?
2. How many discovery facts/frontier items exist when the supervisor switches out of discovery mode?
3. Does the first generic selection come from the discovery hypothesis rather than a target-specific surface opportunity?
4. Does Generator reasoning occur after the discovery Observation exists?
5. What `suggested_capability` survives admission/planning?
6. Is a second, non-discovery Experiment created and actually dispatched?
7. Does that Experiment reach a HypothesisAssessment?
8. If the run completes, are there unresolved control obligations, fatal faults, or required work still absent?

## P1 decision

P1 is complete as a source audit.

No source-confirmed gap above is automatically converted into a product fix before the first deployed reproduction. The VDS run will tell us which gap is the first blocking root cause on the real START path.
