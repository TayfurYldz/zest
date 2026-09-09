# S1.4 Runtime/Source Drift — Provenance Reopened

Date: 2026-09-09
Deployed release: `zest-canonical-phase8-rc3-browser-page-fanout-hotfix`
Recovered Git baseline: `ae89df540893a972866ce0e7f91765bf7a0244f6`

## Decision

S1 deployment provenance is temporarily **reopened**. S5 root-cause work is paused until the deployed Python package is fully reconciled against the release source tree and recovered Git baseline.

## Evidence

A focused S5 import test exposed this baseline inconsistency:

- `src/zest/application/execute_planned_experiment.py` imports and calls `browser_page_max_network_requests(action_id)`.
- recovered `src/zest/tools/browser_page_policy.py` does not define that helper; it contains only `BROWSER_PAGE_MAX_NETWORK_REQUESTS = 16` for this concern.
- the deployed installed package at `/opt/zest/current/.venv/lib/python3.12/site-packages/zest/tools/browser_page_policy.py` **does** define `browser_page_max_network_requests(action_id)`.
- deployed `zest.application.execute_planned_experiment` imports successfully and calls that helper.

Therefore the deployed runtime package contains at least one source change not represented by the recovered release source snapshot/Git baseline.

## Important correction

Earlier S1.1 proved release-source == installed-runtime only for five sampled lifecycle-critical files. It did not prove whole-package equality. The later full source snapshot proved the release source tree itself was captured correctly, but it did not compare every installed `site-packages/zest/*.py` file against `src/zest/*.py`.

The correct status is now:

- release source snapshot integrity: PASS
- recovered Git source integrity: PASS relative to that snapshot
- whole installed runtime package vs release source: **UNKNOWN / at least one confirmed mismatch**

## Next gate

Run a read-only full Python package reconciliation between:

- `/opt/zest/current/src/zest`
- `/opt/zest/current/.venv/lib/python3.12/site-packages/zest`

Classify every `.py` path as MATCH, MISMATCH, SOURCE_ONLY or RUNTIME_ONLY and capture SHA-256s for mismatches.

No product change, deployment, S4 rerun or S5 qualification should proceed until this reconciliation is complete.

## Non-dilution

Do not replace the runtime helper with the old constant merely to make Git importable. The deployed release name and behavior indicate a browser-page fanout hotfix; runtime-only behavior must be recovered exactly before deciding whether any code should be changed.
