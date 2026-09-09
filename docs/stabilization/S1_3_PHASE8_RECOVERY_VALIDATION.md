# S1.3 — Phase8 Recovery Validation

Status: READY_FOR_IMPORT

## Artifact identity

Deployed artifact:

`zest-canonical-phase8-rc3-browser-page-fanout-hotfix`

Expected archive SHA-256:

`e37b88e65869205a423913f2307496892f3e0222e373f3e1e84724f67d960de9`

Recorded base:

`5b81b3d1ead3ed837ccade4fd17e104389fb1e26`

Recorded Alembic head:

`a51_001_phase66_dic`

Reserved recovery branch:

`baseline/deployed-phase8-rc3`

## Validation performed on recovered artifact

- Archive member paths were checked for absolute/path-traversal entries before extraction.
- 1,157 tar members were inspected; zero unsafe members were found.
- `deployed-manifest.sha256` contains 1,037 file entries.
- Every one of the 1,037 extracted files matched its SHA-256 entry.
- No manifest-declared file was missing.
- No extracted file existed outside the capture manifest.
- Release manifest records zero deleted files.
- Release manifest records 66 tracked modified files and 65 untracked files relative to base `5b81b3d1...`.
- All 801 Python files byte-compiled successfully. Compatibility shims containing UTF-8 BOM were validated as bytes rather than incorrectly treated as syntax failures from decoded text.
- All 119 JSON files parsed successfully.
- 26 shared Worker mirror files under `src/zest/worker_runtime/python` and `workers/python/zest_worker` were byte-identical.

## Focused recovered-source tests

Run directly from the recovered artifact source with `PYTHONPATH=src`:

- research admission/cycle/orchestration/fairness smoke: **36 passed**
- Phase 6.2–6.9 / Phase 7 / Phase 8 / discovery / browser-boundary focused suite: **184 passed**
- lifecycle contract: **14 passed**

Total focused tests executed during recovery validation: **234 passed**.

These tests are source-recovery validation only. They do not substitute for the deployed START-to-terminal field run.

## Recovery rule

The recovered Git baseline must be reconstructed from the exact recorded base plus only the release-manifest-declared modified and untracked files. It must not silently drop Phase8 capabilities or overwrite the VDS.

Importer:

`scripts/stabilization/import_deployed_phase8_baseline.sh`

The importer verifies artifact SHA-256, capture manifest, release-manifest contract, exact staged path set, and Python byte compilation before creating the recovery commit. Pushing is optional and explicit.

## Gate consequence

S1 remains open until the recovered source commit is present in Git and can be matched back to the deployed artifact. The VDS is not modified during this recovery step.
