# P3 — Phase8 START-to-Terminal Acceptance Contract

Status: **LOCKED BEFORE THE FIRST S4 START-TO-TERMINAL RUN**

Date locked: 2026-09-09
Product baseline: `ae89df540893a972866ce0e7f91765bf7a0244f6`
Scenario: existing `Gate22SurfaceLab`

This file supersedes the candidate-era interpretation of `P3_SCENARIO_ACCEPTANCE.md` for the recovered Phase8 deployment. It does not weaken the stabilization objective. It corrects one criterion that would measure the wrong Phase8 architecture: Phase8 has a first-class deterministic Research Work Fabric that may compile selected research work directly to a target-coupled `ExperimentPlan` without invoking the Generator/Falsifier model path.

The correction is source-backed and is locked **before** the first S4 START-to-terminal acceptance run, not after a field failure.

## Product behavior that defines this contract

Recovered Phase8 source establishes two legitimate research planning lanes after selection:

1. **Research Work Fabric lane** — `ResearchWorkPlannerRegistry` may return `EXECUTE_PLAN` for discovery handoff, hunter, authentication, authorization, workflow, mutation, protocol, OAST and some invariant work. ARC creates a separate hypothesis with origin `research-work-fabric.v1:<opportunity_id>`, persists the compile decision, and owns preparation/authorization/dispatch/evaluation.
2. **Model lane** — a planner may return `USE_MODEL`; ARC then invokes the normal budget-enforced model-driven hypothesis path.

Therefore "model_count > 0" is not a universal definition of research coupling in Phase8. Requiring Generator/Falsifier for a deterministic `EXECUTE_PLAN` lane would reject intended product behavior. Conversely, a model lane is not allowed to claim success without real model reasoning.

## Non-dilution invariant

The following remain mandatory:

- no scope or authority bypass,
- no fake Worker/model path,
- no `diagnostic.echo` substitution for target-coupled research,
- no terminal-state relabeling,
- no hidden fatal fault or unresolved control obligation,
- no scenario shrink after failure,
- no acceptance merely because discovery produced traffic.

## Layer A — Runtime / discovery

Layer A passes only when all are true:

1. Gate22 independently records a real `GET /`.
2. persisted discovery state contains at least one reachable surface beyond `/` (`surface.facts` or `surface.frontier_items`).
3. at least one real ExecutionAttempt and WorkerResult exist.
4. at least one persisted Observation exists.
5. no unresolved fatal run fault remains.
6. no unresolved control obligation remains.
7. after terminal completion, `locally_supervised == false`.

Layer A alone is not research success.

## Layer B — Phase8 discovery → research coupling

Layer B passes only when all are true:

1. a discovery Observation is persisted before the later target-coupled research action;
2. a non-discovery research hypothesis exists;
3. a separate Experiment exists for that hypothesis;
4. its plan is target-coupled to the Gate22 origin and does not use `diagnostic.echo`;
5. that Experiment reaches a real ExecutionAttempt and WorkerResult;
6. that same Experiment reaches a HypothesisAssessment;
7. the run closes or advances truthfully with no orphaned control obligation;
8. the selected planning lane is proven as one of:
   - **WORK_FABRIC**: the hypothesis origin begins `research-work-fabric.v1:` and the target-coupled plan/attempt/result/assessment chain exists; or
   - **MODEL**: the target-coupled research experiment exists and the standard real model reasoning path contains Generator + Falsifier reasoning after discovery context became available.

A Research Work Fabric path is not a model substitute added by the stabilization campaign; it is deployed Phase8 product code. A model-selected path must still prove real model reasoning.

## `diagnostic.echo`

`diagnostic.echo` remains plumbing-only and never satisfies Layer B target coupling.

## Capability-missing classification

If Layer B is absent and persisted records explicitly identify an unsupported/not-connected capability, classify `CAPABILITY_MISSING`. Evidence may include model admission reason codes or Phase8 research-work compile audits such as `DEFERRED_ENGINE_WIRING`, `CAPABILITY_NOT_YET_CONNECTED`, or an explicit unsupported-capability reason.

Policy/scope/side-effect blocks are not automatically renamed `CAPABILITY_MISSING`; their actual reason remains evidence.

## Accepted terminal reasons

`SUCCESS` requires Layer A + Layer B, no unresolved fatal fault/control obligation, no live supervisor, and one of:

- `COMPLETED_NO_MORE_OPPORTUNITIES`
- `MAX_CYCLES_REACHED` only when Layer A + Layer B already passed
- `COMPLETED_UNDER_CURRENT_AUTHORITY_WITH_BLOCKED_WORK` only when Layer A + Layer B already passed **and** the final global research-work audit records both `completion_allowed=true` and `protocol_authority_blocked=true`

The third reason is accepted because Phase8 explicitly represents a truthful safe completion under current authority; accepting it does not grant blocked authority or weaken scope.

Not accepted as success:

- `OPERATOR_CANCELLED`
- `BUDGET_EXHAUSTED`
- `MAX_DURATION_REACHED`
- `REQUIRE_HUMAN_REVIEW`
- `UNKNOWN_OUTCOME_REQUIRES_REVIEW`
- `CORE_BLOCKED`
- `NO_COMPATIBLE_RUNTIME`
- `OPERATIONAL_FAILURE`
- `CONTENT_POLICY_BLOCKED`
- `AUTH_REQUIRED`
- `RATE_LIMITED`

## Result classes

Only:

- `SUCCESS`
- `COULD_NOT_START`
- `STUCK_OR_CRASHED`
- `FINISHED_WITHOUT_REQUIRED_WORK`
- `CAPABILITY_MISSING`

are used.

A zero-finding run may pass. A zero-required-work run may not.

## First-run bounds

The external Phase8 acceptance probe uses a fresh Gate22 ResearchRun with bounded authority and resources. It keeps side-effect ceiling at `1`, uses an explicit positive model budget, and allows enough cycles for the recovered Phase8 discovery/research scheduler to operate. Bounds are test limits, not permission to bypass Core or program policy.

## Freeze rule

This contract is now frozen for S4. A failed S4 run triggers evidence-backed diagnosis under S5. Acceptance is not relaxed because the run fails. A criterion may change only if new source/runtime evidence proves that the criterion measures the wrong deployed product behavior, and such a change must be recorded explicitly before rerun.
