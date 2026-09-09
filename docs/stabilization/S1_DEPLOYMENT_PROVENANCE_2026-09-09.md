# S1 — Deployment Provenance (2026-09-09)

Status: **PARTIAL PASS — RUNTIME/RELEASE COHERENT, GIT PROVENANCE PENDING**

## Deployed artifact

- Release id: `zest-canonical-phase8-rc3-browser-page-fanout-hotfix`
- Runtime package: `/opt/zest/releases/zest-canonical-phase8-rc3-browser-page-fanout-hotfix/.venv/lib/python3.12/site-packages/zest`
- Package version: `0.1.0`
- Python: `3.12.13`
- PostgreSQL server: `14.24`
- Playwright: `1.62.0`
- Chromium runtime: `chromium-1234`
- `zestd` and dashboard were active/enabled.
- `/health` reported database, Worker, browser/runtime and GPT-5.6 Sol model readiness healthy, with `ready_for_start=true`.

## Packaging integrity

The installed Python distribution reports `direct_url` pointing at the deployed immutable release directory.

For all five checked lifecycle-critical files, the release source and installed `site-packages` copy were byte-identical by Git blob hash. Therefore no source-vs-installed-package drift was observed in this sample.

## Candidate mismatch

The deployed artifact is **not** source-identical to candidate commit `5b81b3d1ead3ed837ccade4fd17e104389fb1e26`.

Matched sampled files:

- `src/zest/application/zestd.py`
- `src/zest/interface/operator_api.py`
- `src/zest/integrations/models/cli_session.py`

Different sampled files:

- `src/zest/application/autonomous_research_controller.py`
- `src/zest/application/local_run_supervisor.py`

This is not treated as random corruption. The deployed ARC/supervisor contain substantial later lifecycle/research work, including persisted discovery-exit handling, global research-work completion guards, research-work planner dispatch, identity/OAST/feedback wiring, and a supervisor rule that keeps surface discovery active until persisted discovery exhaustion.

## Stabilization decision

**Do not replace this release with `5b81b3d` merely to make the fingerprints match.** That would risk downgrading deployed behavior and violating the stabilization rule against weakening Zest.

The deployed artifact is provisionally treated as the stronger field baseline, but S1 is not fully closed until its complete source artifact/tree is captured and assigned reproducible Git provenance.

Next step: capture a full source snapshot + deterministic manifest + candidate comparison before any product-code modification. START remains held until this provenance capture is complete.
