# S1.4 Recovery Import Drift — Provenance Reopened

Date: 2026-09-09
Deployed release: `zest-canonical-phase8-rc3-browser-page-fanout-hotfix`
Recovered Git baseline: `ae89df540893a972866ce0e7f91765bf7a0244f6`

## Decision

S1 deployment provenance remains temporarily **reopened**, but the previously suspected installed-runtime/source drift is disproved.

S5 root-cause work remains paused only until the recovered Git baseline is reconciled against the exact captured deployed source snapshot.

## Runtime/source reconciliation result

A full read-only comparison of the deployed release source package and installed `site-packages/zest` returned:

- source files: 452
- runtime files: 452
- MATCH: 452
- MISMATCH: 0
- SOURCE_ONLY: 0
- RUNTIME_ONLY: 0

Therefore the installed Python package is byte-coherent with the release `src/zest` tree. The browser fanout helper is **not** a runtime-only modification.

## Correct root cause

The original deployed-source capture already contains the browser-page fanout hotfix. In particular, captured `src/zest/tools/browser_page_policy.py` has SHA-256:

`81873234e093d40f61f80b42b58e9db37c72f4593dd9f24c7fbd560b4b2058ba`

and defines `browser_page_max_network_requests(action_id)`.

The independent release-vs-candidate comparison also records that this file differs from candidate `5b81b3d1...`.

However the recovered Git baseline still contains the old candidate blob for that path. The recovery importer staged only the 131 paths declared by `release/release-manifest.json` (`66 tracked modified + 65 untracked`). That manifest path set was stale/incomplete relative to the final release tree and omitted late browser-page fanout hotfix files.

The importer therefore reproduced the manifest-declared dirty tree, **not the complete final deployed source snapshot**.

## Confirmed omitted hotfix surface so far

Direct comparison evidence already confirms omissions in the browser-page fanout surface, including canonical policy/capability/worker files and mirrored Worker/test files. A whole-snapshot-vs-Git audit is required before repairing the baseline so the omission set is exhaustive rather than guessed.

## Required next gate

Compare every non-release, non-cache file in the exact captured `deployed-source.tar.gz` against Git objects in recovered baseline `ae89df540893a972866ce0e7f91765bf7a0244f6`.

Classify paths as:

- MATCH
- CONTENT_MISMATCH
- MODE_MISMATCH
- SNAPSHOT_ONLY
- GIT_ONLY

Only after this exhaustive audit may the baseline be repaired from the captured artifact.

## Importer correction requirement

The recovery importer must no longer trust only `tracked_modified_files + untracked_files` from the embedded release manifest as the authoritative copy set.

A corrected recovery procedure must verify the final snapshot tree itself against the resulting Git tree and fail if any captured product/source/test file remains different.

## Non-dilution

Do not revert the deployed browser fanout behavior to the candidate constant merely to make the recovered branch importable. The deployed runtime and deployed release source agree; Git recovery is the stale side. Recover the final deployed source exactly first, then resume the S5 boundary fix qualification.