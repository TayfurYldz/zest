# S2.1 Deployed Control Interface — PASS

Date: 2026-09-09
Environment: staging VDS
Deployed release: `zest-canonical-phase8-rc3-browser-page-fanout-hotfix`
Recovered source baseline: `ae89df540893a972866ce0e7f91765bf7a0244f6`

## Result

`S2_1_RESULT=PASS`

The running deployment was observed without creating or starting a new ResearchRun.

## Runtime interface evidence

- `zest-dashboard` listens on `127.0.0.1:8765`.
- `zestd` listens on `127.0.0.1:8766`.
- dashboard `/healthz` returned `{"ok": true}`.
- zestd `/health` returned `ok=true`, `ready_for_start=true`, database/Worker/model all `HEALTHY`.
- model runtime reported authenticated structured-output-compatible GPT-5.6 Sol availability.
- `/api/programs`, `/api/runs`, and `/api/console` returned successful bounded read models.
- dashboard `/api/dashboard` reported `client_only=true`, `database.operator_source=zestd`, database `HEALTHY`, and the operator projection saw `ready_for_start=true`.
- nonexistent run reads for detail, analysis, and latest preflight returned HTTP 404 with `RUN_NOT_FOUND` and `not_research_truth=true`, confirming the deployed read-route error contract.

Observed existing database counts during this read-only check:

- programs: 17
- research runs: 21

These existing rows are not stabilization success evidence.

## Source contract cross-check

Recovered Phase8 source confirms:

1. dashboard bootstrap is the create boundary (`POST /api/programs/bootstrap`),
2. dashboard run controls are clients of the local zestd Operator API,
3. zestd owns `preflight/start/pause/resume/cancel`,
4. START reconstructs authoritative run configuration from PostgreSQL before preflight and ARC/supervisor attachment,
5. Operator API does not accept client-supplied scope/budget authority overrides for START.

## Gate status

S2 is not fully closed yet. S2.1 proves the deployed read/control surfaces and dashboard→zestd client topology. The remaining S2 evidence (`create → preflight → start → observe → cancel`) will be collected on one fresh stabilization run so we do not create disposable authoritative state merely for route probing.

No product behavior was modified by this check.
