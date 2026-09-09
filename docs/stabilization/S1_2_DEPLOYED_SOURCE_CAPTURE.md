# S1.2 — Deployed Source Capture

Status: CAPTURE COMPLETE / GIT IMPORT PENDING

Observed release: `zest-canonical-phase8-rc3-browser-page-fanout-hotfix`

Observed release path: `/opt/zest/releases/zest-canonical-phase8-rc3-browser-page-fanout-hotfix`

Candidate previously used for stabilization comparison: `5b81b3d1ead3ed837ccade4fd17e104389fb1e26`

## Decision

The deployed VDS release is not the `5b81b3d` candidate and is materially more advanced. It must not be replaced by the older candidate merely to obtain source provenance.

The currently running release source matches its installed Python package for the critical runtime files checked in S1.1, so this is not a source-vs-site-packages packaging drift. The problem is missing Git provenance for the deployed source snapshot.

Until the exact deployed source is imported into Git and pinned to a reproducible commit, S1 remains open and no START-to-terminal acceptance run should be classified against a Git revision.

## Captured artifacts

The VDS generated these immutable evidence artifacts:

- `/tmp/zest-provenance-20260909/deployed-source.tar.gz`
  - SHA-256: `e37b88e65869205a423913f2307496892f3e0222e373f3e1e84724f67d960de9`
- `/tmp/zest-provenance-20260909/deployed-manifest.sha256`
  - SHA-256: `88f88de95a4770f3c5bf947288bbf39dd29672730ec2ab3a273f491e42541aaf`
- `/tmp/zest-provenance-20260909/release-vs-candidate.txt`
  - SHA-256: `51b7c06fb631157431cb114640feb06f064cbd0fc5b217b164d396ce4b6bba62`

The comparison inventory contains 108 lines. Some entries are runtime `__pycache__` noise from comparing the live release tree directly, but many are substantive source differences.

## Material differences confirmed by inventory

The deployed release contains substantial post-candidate work, including but not limited to:

- persistent discovery lifecycle and exit semantics,
- research work planners and sources,
- global research-work completion audit,
- identity catalog / identity-auth workflow sources,
- Hunter execution feedback and exhaustion state,
- mutation/protocol source and feedback paths,
- OAST source / feedback / timeout handling,
- DIC source / evaluation / feedback,
- model context census / reasoning context packaging,
- Phase 8 acceptance census and trace helpers,
- additional scheduler eligibility/fairness logic,
- additional research evaluators,
- browser route-policy and browser capability changes,
- Alembic migrations `a46` through `a51`.

This is therefore not treated as a two-file hotfix or an accidental local edit set. It is a coherent later product phase whose exact history is currently absent from visible Git branches.

## Stabilization rule

Do not deploy `5b81b3d` over this release.

Do not delete or mutate the current VDS release while provenance is unresolved.

Next gate:

1. obtain the three captured artifacts off the VDS,
2. verify their SHA-256 hashes after transfer,
3. inspect the complete deployed source snapshot,
4. import that exact source as a reproducible Git baseline,
5. rebase/reapply only the stabilization docs and external probes on top of that baseline,
6. then continue with S2 control-path verification and the first real START acceptance run.

No product weakening is allowed during this import. The deployed capabilities are the minimum baseline to preserve.
