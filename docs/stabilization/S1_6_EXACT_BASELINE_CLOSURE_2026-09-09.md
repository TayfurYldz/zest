# S1.6 Exact Deployed Baseline Closure

Date: 2026-09-09
Captured deployed artifact SHA-256: `e37b88e65869205a423913f2307496892f3e0222e373f3e1e84724f67d960de9`
Canonical deployed baseline branch: `baseline/deployed-phase8-rc3`
Canonical deployed baseline commit: `a599b7c58d55d36bfad92ef6b4e455f10d9d8366`
Parent recovery commit: `ae89df540893a972866ce0e7f91765bf7a0244f6`

## Result

S1 deployment provenance is **PASS** again.

The repair was produced directly from the captured deployed artifact after the S1.5 exact object-tree audit proved 22 recovery-import differences. The guarded repair reported:

- pre-repair: 10 content mismatches, 10 mode mismatches, 0 snapshot-only, 2 Git-only, total 22
- post-repair staged tree: 1034 exact matches, 0 content mismatches, 0 mode mismatches, 0 snapshot-only, 0 Git-only, total 0
- staged paths: 22

The repair commit restores the final browser-page fanout hotfix content for ten paths, restores executable mode for ten scripts, and removes two repository-only paths that were absent from the deployed snapshot.

## Independent GitHub verification

GitHub reports `baseline/deployed-phase8-rc3` at `a599b7c58d55d36bfad92ef6b4e455f10d9d8366`, with direct parent `ae89df540893a972866ce0e7f91765bf7a0244f6` and the expected repair commit message.

A GitHub compare of `ae89df5...` → `a599b7c...` reports exactly one commit ahead and exactly 22 changed paths matching the S1.5 repair set.

Critical recovered browser hotfix blobs now match the S1.5 snapshot audit, including:

- `src/zest/resources/contracts/v1/capabilities/browser.page.json` → `c00607bdf7b76ff23cdcf84fdee3f71f87ceefd2`
- `src/zest/tools/browser_page_policy.py` → `0eca9eaee4719e322fed6fd4f9ddc34dcefcfa0b`
- `src/zest/worker_runtime/python/browser_page.py` → `4630c3c31686ed272bb9680a7aa1c357bbbb147f`

## Runtime/source coherence

The deployed release source and installed `site-packages/zest` were reconciled separately before this repair:

- source files: 452
- runtime files: 452
- exact matches: 452
- mismatches: 0
- source-only: 0
- runtime-only: 0

Therefore the deployed release/runtime was coherent; only the first Git recovery importer was incomplete.

## Canonicality

`a599b7c58d55d36bfad92ef6b4e455f10d9d8366` is now the canonical immutable Phase8 deployed-source baseline for stabilization work.

The older `ae89df5...` commit is retained only as historical recovery provenance and must not be used as the source base for product fixes or qualification.

## Next gate

S5 root-cause closure resumes from a fresh stabilization branch rooted directly at `a599b7c...`. Existing S4 evidence remains valid because the deployed VDS release was never changed during provenance repair.
