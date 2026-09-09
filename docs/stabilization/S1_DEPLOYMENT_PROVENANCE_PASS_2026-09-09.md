# S1 Deployment Provenance — PASS

Date: 2026-09-09

## Decision

S1 is closed as **PASS** for the currently deployed staging release:

- deployed release: `zest-canonical-phase8-rc3-browser-page-fanout-hotfix`
- recovered canonical Git commit: `ae89df540893a972866ce0e7f91765bf7a0244f6`
- recovery branch: `baseline/deployed-phase8-rc3`
- recovery parent/base: `5b81b3d1ead3ed837ccade4fd17e104389fb1e26`
- deployed source archive SHA-256: `e37b88e65869205a423913f2307496892f3e0222e373f3e1e84724f67d960de9`
- deployed capture manifest SHA-256: `88f88de95a4770f3c5bf947288bbf39dd29672730ec2ab3a273f491e42541aaf`
- release-vs-candidate inventory SHA-256: `51b7c06fb631157431cb114640feb06f064cbd0fc5b217b164d396ce4b6bba62`
- Alembic head recorded by the release: `a51_001_phase66_dic (head)`

## Evidence chain

1. VDS baseline capture identified the running immutable release and showed database, Worker, Chromium/Playwright and model runtime healthy and `ready_for_start=true`.
2. Critical runtime-package files were compared to release source; release source and installed package matched. The deployed source differed from the older `5b81...` candidate in ARC and local supervisor.
3. A full deployed source snapshot and deterministic SHA-256 manifest were captured read-only from `/opt/zest/current`.
4. The archive transferred to the operator workstation with the same SHA-256 values as the VDS-produced artifacts.
5. Recovery import verified the exact archive digest, archive safety, all 1,037 capture-manifest entries, the release manifest contract, 66 tracked modified paths, 65 untracked paths, zero deleted paths, the exact 131 staged path set, and byte-compiled 801 Python files.
6. The recovered commit was created directly on parent `5b81b3d1...` and pushed to `baseline/deployed-phase8-rc3`.
7. GitHub confirms branch HEAD `ae89df5...` has parent `5b81b3d1...`; compare status is one commit ahead and zero behind the base.

## Important interpretation

This PASS proves **source/deployment provenance for the captured deployed release**. It does not prove START-to-terminal correctness, research quality, vulnerability-detection quality, concurrency safety, or broad-target reliability.

The older `5b81...` candidate must not be redeployed over the Phase8 release merely to regain Git provenance. The recovered Phase8 source is now the canonical product baseline for stabilization because it preserves the deployed discovery lifecycle, research-work fabric/planners, identity/auth workflow, mutation/protocol, OAST, DIC, scheduler, evidence/evaluator, lifecycle and browser/runtime work that is absent from the older candidate.

## Next gate

Proceed to **S2 — Real Control Path** against the deployed services while keeping product behavior unchanged. Confirm the deployed create/bootstrap → preflight → START → observe/detail/analysis/events → cancel path before the first S4 field run.
