# MR-7 / GATE 21 / GATE 04B Canonical Closure

Document class: Qualification / closure record
Date: 2026-08-26
Status: QUALIFIED
Authoritative tested implementation baseline: 014b1c0d88eeacdcf0e5a26330d9fe2de888f4fe

## Scope

This record closes the technical qualification work required before the
protected Research OS HQ and first controlled MR-8 field campaign.

Closed technical legs:

- GATE 04B paired live ModelRuntime qualification
- GATE 21 real Chromium behavior qualification
- GATE 21 Linux kernel containment qualification
- MR-7 authenticated restart/session behavior
- OAST correlated callback admission
- canonical Hunter/ARC continuity
- current repository-wide regression

This closure does not claim real-world vulnerability yield,
SECURITY_RESEARCH_VALIDATED, or PRODUCTION_READY.

## GATE 04B

Result: PASS

Qualified runtime pair:

- codex-cli-terra / gpt-5.6-terra
- codex-cli-gpt55 / gpt-5.5

Properties:

- three runs per scenario
- comparable paired execution
- authoritative source provenance
- full comparison completed
- contract qualification completed
- scripted baselines and Strix excluded

Evidence SHA-256:

paired report:
e69059c69f26cc74e0920cd86b4d3402c96a51ca35fb6ad670ff764c3d6553a8

full log:
8e0ea1f49e8da3a41b7edcc279f33f208c5c8c9e8d15830c9fcebb601a128d43

## GATE 21

Result: PASS

Real Chromium behavioral qualification:

20 passed

Authoritative delegated Linux cgroup v2 qualification:

7 passed
0 skipped

Qualified containment properties include:

- Worker and Chromium descendants belong to the owned cgroup
- memory.max is kernel-enforced
- pids.max is kernel-enforced
- shutdown removes the owned cgroup
- breach fixture is actually bounded
- execution failure cannot be interpreted as Chromium success

Successful containment evidence SHA-256:

2b6d73daf832c24ceeb05d5652b08c8bf2dc56fce5d6dc35e838a1d5eeeedc77

Earlier failed cgroup-topology attempt, preserved as failure evidence and not
counted as PASS:

ead5e475c293dd80fb93d87451bb5a0f4f1b4da88a86fe30bf58d64f514cbf6f

The ordinary repository E2E invocation later skipped five cgroup cases because
that process did not run inside a delegated cgroup subtree. Those skips are not
the authoritative containment qualification.

## MR-7 session / restart

Result: PASS

Focused qualification:

3 passed

Proved behavior:

- missing session secret after restart is not fabricated
- Browser Worker blocks and requires reauthentication
- durable session metadata does not create durable secret authority
- restart does not manufacture authenticated state

Evidence SHA-256:

faf990bd0c4fb6ba2b1217c312c5c8f33a6c20a39d8c58f8759e7b0093147c70

## Canonical Hunter / ARC

Result: PASS

Current-baseline canonical qualification:

6 passed

Covered properties:

- timeout remains operational failure rather than falsification
- duplicate exploratory work is not minted when a known family later appears
- restart after assessment does not create a Finding
- secure/deceptive false-positive ladder remains intact
- UNKNOWN_OUTCOME does not blind-retry
- provenance and discriminating-plan semantics remain intact

Evidence SHA-256:

c147b2d978d1bed7bba57f10c6ebe91694017c534b7e4903aca7a19fb195e2f8

## OAST

OAST-1 is part of the authoritative implementation baseline.

Qualified chain:

provider callback
-> correlation
-> anti-spoof / expiry / dedup admission
-> PostgreSQL delivery
-> admission
-> Observation
-> Fact

## Final canonical regression

Current-state synchronization and stale-test repair were followed by:

Unit:
1587 passed
4 skipped
106 subtests passed

Integration:
270 passed
53 subtests passed

E2E:
156 passed
5 skipped

Critical current-head qualification:
Hunter/ARC 6/6
Browser 20/20
Session/restart 3/3

Final regression SHA-256:

a91cfb395f88962d6b2c70dc16a9c5d41fabf2c29f7cf3049847135c6ea99492

The five ordinary E2E skips are the non-delegated cgroup cases described above.

## Canonical maturity

GATE_04B_STATUS=PASS
GATE_21_STATUS=PASS
LIVE_MODEL_VALIDATED=True
SECURITY_RESEARCH_VALIDATED=False
PRODUCTION_READY=False

The final two False values are intentional.

## Historical records

Earlier GATE 19, GATE 20, and GATE 22 qualification sections that recorded
GATE 04B as PENDING remain unchanged. They describe the true state at the time
those qualifications were executed and are not rewritten by this closure.

## MR-7 / MR-8 boundary

MR-7 technical reconnection is closed.

MR-8 remains open.

MR-8 requires a controlled authorized field campaign measuring at minimum:

- attack-surface recall
- vulnerability recall
- false findings
- reproducibility
- time-to-validation
- cost per valid finding
- coverage closure
- registry-external hypothesis yield

No field result is fabricated by this closure.

## Next stage

Protected Research OS HQ.

After HQ is qualified end-to-end:

Research OS HQ
-> research-osd
-> ARC
-> Core
-> Worker
-> Evidence pipeline

the first controlled authorized MR-8 field campaign may begin.
