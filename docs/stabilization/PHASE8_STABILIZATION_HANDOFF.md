# Phase8 START-to-Terminal Stabilization Handoff

Status: ACTIVE
Date: 2026-09-09

## Canonical source truth

The stabilization campaign is now anchored on the exact recovered deployed Phase8 RC3 source, not the older candidate.

- immutable recovery baseline branch: `baseline/deployed-phase8-rc3`
- immutable recovery baseline commit: `ae89df540893a972866ce0e7f91765bf7a0244f6`
- parent/base commit: `5b81b3d1ead3ed837ccade4fd17e104389fb1e26`
- active stabilization branch: `stabilization/phase8-start-to-terminal`
- deployed release: `zest-canonical-phase8-rc3-browser-page-fanout-hotfix`
- deployed archive SHA-256: `e37b88e65869205a423913f2307496892f3e0222e373f3e1e84724f67d960de9`
- Alembic head: `a51_001_phase66_dic`

The old `stabilization/start-to-terminal` branch remains historical planning/provenance material. Product fixes must not be based on it because its product parent predates the recovered Phase8 source.

## Non-dilution invariant

Stabilization must not weaken Zest to obtain a green run. Do not bypass or relax scope, Core authorization, approval, budgets, evidence semantics, Worker/model reality, truthful failure states, or required research coupling. Do not delete a capability merely because it blocks the run. A missing executable capability is reported as `CAPABILITY_MISSING`.

## Gate state

- S0 Source freeze: PASS
- S1 Deployment provenance: PASS
- S2 Real deployed control path: ACTIVE
- S3 Scenario acceptance contract: PREPARED, must be rechecked against recovered Phase8 semantics before execution
- S4 First deployed START-to-terminal run: NOT STARTED
- S5 Root-cause closure: BLOCKED ON S4
- S6 10/10 repeatability: BLOCKED
- S7 security vertical slice: BLOCKED

## S1 evidence summary

The deployed release and installed Python package were shown to be the same source for the critical runtime files. A full read-only VDS source capture was transferred with matching SHA-256 values. Recovery import verified 1,037 capture-manifest files, 66 tracked modifications, 65 untracked files, zero deletions, the exact 131 staged-path set, and byte-compiled 801 Python files before creating the recovery commit. GitHub confirms the recovery branch points to `ae89df5...` whose parent is exactly `5b81b3d1...`.

## Current S2 source contract

Recovered Phase8 exposes the deployed control path through existing interfaces:

1. Dashboard `POST /api/programs/bootstrap` creates Program, scope rules, ProgramPolicy, rate-limit profile, active AuthorizationSource, ResearchRun and IssuedBudget in PostgreSQL, but does not start active testing.
2. zestd Operator API owns `POST /api/runs/{id}/preflight`, `/start`, `/pause`, `/resume`, `/cancel` and read surfaces for run detail, analysis, events, program/run lists and health.
3. Dashboard is a client of zestd for run control; it does not own supervisors. zestd reconstructs authoritative run configuration from PostgreSQL by run ID.
4. `start_run` performs reconstruction and a real preflight before ARC start/supervisor attachment. Client-supplied scope/budget/config overrides are rejected/ignored at the command boundary.

S2 closes only after these interfaces are confirmed against the running VDS without guessing endpoints or bypassing authority.

## Next action

Run the S2 deployed-interface probe read-only first. Do not create/start a research run until the running dashboard→zestd path is confirmed. Then create one fresh authorized stabilization run, preflight it, and preserve its IDs for S4 rather than creating disposable active runs.
