# S1.5 Exact Snapshot → Recovered Git Audit

Date: 2026-09-09
Captured deployed artifact SHA-256: `e37b88e65869205a423913f2307496892f3e0222e373f3e1e84724f67d960de9`
Recovered baseline audited: `ae89df540893a972866ce0e7f91765bf7a0244f6`

## Result

The recovered Git baseline is not yet an exact representation of the deployed source snapshot.

Exact object-tree audit, excluding release/build provenance metadata and cache artifacts:

- snapshot files: 1034
- Git files: 1036
- exact matches: 1014
- content mismatches: 10
- mode mismatches: 10
- snapshot-only: 0
- Git-only: 2
- total non-matches: 22

The deployed runtime package itself is coherent with the release source tree: the full `src/zest` ↔ installed `site-packages/zest` reconciliation produced 452/452 exact file matches, with zero mismatches, source-only files, or runtime-only files. Therefore the defect is in the recovery import, not in the deployed runtime/release.

## Content mismatches

The recovery importer missed the final browser-page fanout hotfix for these ten paths:

1. `src/zest/resources/contracts/v1/capabilities/browser.page.json`
2. `src/zest/tools/browser_page_policy.py`
3. `src/zest/worker_runtime/python/browser_page.py`
4. `src/zest/worker_runtime/python/packaged_registry.py`
5. `src/zest/worker_runtime/python/resources/capabilities/browser.page.json`
6. `tests/unit/application/test_browser_page.py`
7. `tests/unit/tools/test_browser_page_capability.py`
8. `workers/python/zest_worker/browser_page.py`
9. `workers/python/zest_worker/packaged_registry.py`
10. `workers/python/zest_worker/resources/capabilities/browser.page.json`

The exact snapshot blobs are authoritative and must be restored from the captured artifact, not reconstructed by hand.

## Mode mismatches

The following scripts have identical content but lost their executable bit in the recovered Git baseline:

- `scripts/check_contracts.py`
- `scripts/clean_install_smoke.py`
- `scripts/export_source.py`
- `scripts/run_research_benchmark.py`
- `scripts/start_wsl_test_postgres.py`
- `scripts/start_wsl_test_postgres.sh`
- `scripts/vds_checkpoint16_fixture.py`
- `scripts/verify_zest_release.py`
- `scripts/zest_db.py`
- `scripts/zest_status.py`

Each must be represented as Git mode `100755`.

## Git-only paths

Two repository-only paths are absent from the deployed snapshot and must not remain on the exact deployed-baseline branch:

- `.cursor/rules/zest.mdc`
- `var/artifacts/.gitkeep`

These may exist on development branches later if desired, but `baseline/deployed-phase8-rc3` is an exact deployed-source baseline and therefore must match the captured source set.

## Root cause

The original recovery importer copied only paths declared by the embedded release manifest (66 tracked modifications + 65 untracked paths). The final release source tree contained later browser-page fanout hotfix changes that were present in the captured snapshot but not included in that dirty-tree manifest. Thus the importer proved manifest fidelity, not full snapshot fidelity.

## Closure criterion

S1 remains open until a repair commit on `baseline/deployed-phase8-rc3` is produced directly from the captured artifact and a second snapshot-vs-Git object audit reports:

- `CONTENT_MISMATCH=0`
- `MODE_MISMATCH=0`
- `SNAPSHOT_ONLY=0`
- `GIT_ONLY=0`
- `TOTAL_NON_MATCH=0`

No S5 product fix qualification or deployment should resume before that closure.
