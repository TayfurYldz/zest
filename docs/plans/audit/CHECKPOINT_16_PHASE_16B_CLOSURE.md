# Checkpoint 16 — Phase 16B Closure Record

**Status:** SEALED for native Ubuntu VDS operational qualification.

This closure is evidence-backed by the sanitized operator evidence file:

```text
dist/checkpoint16-evidence.txt
size=21980
sha256=8d2fe617d98bb5430cd06b5d9ffc967a3fa76565e0a243c96bb31fef09f3cba6
sidecar=dist/checkpoint16-evidence.txt.sha256
secret_scan=one masked PostgreSQL URI only; no unmasked credential pattern
```

The raw evidence file remains an operational artifact under `dist/` and is
not source doctrine. Do not paste raw terminal transcripts into repository
docs. This record stores the sanitized operator summary and immutable hashes.

## Scope

Checkpoint 16 seals process-supervision and recovery qualification for the
local native-Ubuntu deployment:

- immutable release install and packaging completeness
- PostgreSQL-backed runtime identity, heartbeat, recovery, and fail-closed API
- systemd startup, SIGTERM, SIGKILL restart, and repeated restarts
- real machine reboot and reboot from unsafe recovery state
- J8-J11 recovery and lease/fencing behavior
- file permissions and secret-surface checks

This does **not** validate live model readiness, real-target field research,
remote operator exposure, broad autonomous vulnerability discovery, or
production readiness.

## Release Evidence

Installed VDS release:

```text
checkpoint16a-j11-fencing
```

Installed immutable root:

```text
/opt/research-os/releases/checkpoint16a-j11-fencing
/opt/research-os/current -> /opt/research-os/releases/checkpoint16a-j11-fencing
```

Artifact evidence:

```text
dist/research-os-checkpoint16a-j11-fencing.tar.gz
sha256=9cdc8287de4ea317ea3d42c07c023fc86be44c6f393b03d483c6f96ed8263982
manifest_sha256=75a74b40d44e347ebf1d1113e87de33b2f28687a4776ec2838751e66919b3bef
```

Local and VDS archive path safety, entry type safety, manifest coverage, and
file hash verification passed before install.

## J8-J16 Matrix

| Unit | Result | Evidence summary |
|---|---:|---|
| J8 fixture recovery | PASS | `AUTHORIZED` but never dispatched classified `SAFE_RETRY_AFTER_REAUTHORIZATION`; no stale AuthorizationDecision reused; no Worker dispatch. |
| J9 dispatching recovery | PASS | `DISPATCHING`, side-effect level `1`; classified `RECONCILIATION_REQUIRED`; restart held `WAITING_HUMAN`; no replay; audit event persisted. |
| J10 unknown outcome recovery | PASS | `UNKNOWN_OUTCOME`, side-effect level `2`; classified `HUMAN_REQUIRED`; restart held `WAITING_HUMAN`; no auto retry; audit reason persisted. |
| J11 two-process fencing | PASS | Real PostgreSQL, two real OS child processes, stale epoch proof, five-race epochs `3,4,5,6,7`, exactly one owner, loser Worker blocked, inner dispatch count `0`. |
| J12 machine reboot | PASS | Actual reboot created new runtime/PID, PostgreSQL and research-osd returned automatically, lease epoch `7` survived, owner stayed `NULL`, Worker result count stayed `0`. |
| J13 unsafe-state reboot | PASS | Reboot from `UNKNOWN_OUTCOME` level `2` returned to `WAITING_HUMAN / HUMAN_REQUIRED`, owner `NULL`, epoch `7`, no replay. |
| J14 repeated restart | PASS | Five sequential systemd restarts produced distinct runtime IDs/PIDs, exactly one RUNNING runtime each time, fail-closed state preserved. |
| J15 permissions | PASS | `/etc` and release tree denied to `research-os`; state/log writable; sudo denied; expected ownership and modes observed. |
| J16 secret surfaces | PASS | journal, APIs, process args, systemd status, and collected evidence clean; evidence contained masked URI only. |

J11 cleanup ergonomics were added after the VDS PASS evidence: the fixture now
has `j11-cleanup`, which uses the production repository `release_lease` CAS to
clear only an expired STOPPED `checkpoint16-j11-fencing` qualification owner.
It refuses live owners, owner/epoch mismatch, missing owner rows, and
non-qualification owners. It preserves the reached lease epoch.

## Local QA

Focused Phase 16B regression:

```text
compileall focused J11/fixture/verifier: PASS
unit qualification/deploy/packaged resources: 42 passed, 5 subtests passed
integration fixture/J11 cleanup: 17 passed, 3 subtests passed
orchestration lease + daemon recovery focused: 55 passed, 3 subtests passed
release asset verifier: PASS
```

Full local QA after J11 cleanup:

```text
compileall src scripts tests: PASS
unit: 1509 passed, 4 skipped, 72 subtests passed
integration: 260 passed, 1 failed, 53 subtests passed
e2e: 156 passed, 5 skipped
```

The integration failure is the known unrelated SD-G4 token-economy schema
debt:

```text
tests/integration/test_sd_g4_token_economy.py
MODEL_TOKENS_IN violates ck_budget_consumption_resource_type
```

Do not fix that failure opportunistically in Checkpoint 16. It does not touch
research-osd runtime ownership, systemd deployment, lease fencing, recovery,
evidence admission, human review, or VDS qualification.

## Seal Decision

Checkpoint 16 is sealed for its stated operational scope because:

- all J8-J16 real-VDS qualification units passed with sanitized evidence;
- J11 cleanup now has a bounded, tested CAS-based operator path;
- fixture operations still dispatch zero Workers unless fenced diagnostic
  probes are intentionally blocked before inner invocation;
- PostgreSQL remains the sole authoritative SoR;
- production `research-osd` does not import qualification helpers;
- global maturity flags remain unchanged.

Maturity flags remain:

```text
GATE_04B_STATUS=PENDING
LIVE_MODEL_VALIDATED=False
SECURITY_RESEARCH_VALIDATED=False
PRODUCTION_READY=False
```

Checkpoint 16 does not start OAST, real target testing, live model
qualification, or a controlled field campaign.
