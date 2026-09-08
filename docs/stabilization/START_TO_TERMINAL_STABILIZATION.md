# Zest START-to-Terminal Stabilization

Status: ACTIVE

## Source candidate

- Base branch: `campaign/canonical-mr5-mr6`
- Base commit: `5b81b3d1ead3ed837ccade4fd17e104389fb1e26`
- Stabilization branch: `stabilization/start-to-terminal`

This source candidate is not yet the deployment baseline. The installed VDS release must first be proven to match a known source revision.

## Scope freeze

Until this stabilization closes:

- no new hunter families,
- no NoScope-derived architecture work,
- no Semantic Application Model,
- no Attack Path Planner / Chain Executor,
- no new product integrations,
- no broad refactor.

Only evidence-backed fixes required for the real START-to-terminal path are permitted.

## Gates

### S0 — Source freeze

PASS when the stabilization branch is pinned to the selected candidate commit.

### S1 — Deployment provenance

PASS when the installed immutable release, package/runtime, PostgreSQL, model runtime, Worker and Chromium are identified and the deployed source is matched to a known revision.

### S2 — Real control path

PASS when the deployed start / observe / cancel path is confirmed from the running services and existing interfaces. No guessed endpoint or phase name is accepted.

### S3 — Single existing scenario

Use the existing Gate 22 loopback surface-discovery lab unless deployment constraints prove it unsuitable. Before execution, record expected evidence for target hit, discovery, model context when used, Worker execution, result processing, and accepted completion reason.

### S4 — First real run

Run one fresh ResearchRun through the real deployed START path. Classify only as:

- SUCCESS
- COULD_NOT_START
- STUCK_OR_CRASHED
- FINISHED_WITHOUT_REQUIRED_WORK
- CAPABILITY_MISSING

Zero findings are allowed. Zero required work is not.

### S5 — Evidence-backed bug closure

For one blocking defect at a time:

1. record expected vs actual behavior,
2. preserve reproduction and run evidence,
3. identify root cause,
4. make the smallest root-cause fix,
5. add the smallest regression check in the existing test structure,
6. rerun the same scenario as a fresh run.

Do not bypass scope, authorization, budgets, or truthful failure states.

### S6 — Repeatability close

After the first success, run the exact scenario ten consecutive times on an unchanged revision with fresh run state. Any code change resets the count.

Then verify:

- cancel actually stops active work,
- after service restart, a new run can be created and started.

Closure statement must remain scoped to the tested revision, environment and scenario.

## Next stage after stabilization

Only after S6 passes: use one known vulnerable case and one secure control to prove detection + non-finding behavior. Feature development resumes only after that vertical security slice is understood.
