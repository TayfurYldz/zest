# P4 — External START-to-Terminal Probe

Status: IMPLEMENTED / NOT YET EXECUTED ON VDS

Implementation:

`./scripts/stabilization/start_to_terminal_probe.py`

## Purpose

Drive the existing deployed Zest user path from outside the product and produce one evidence-backed classification. The probe is a test client, not a new scheduler/runtime/research subsystem.

## What it is allowed to do

- launch the repository's existing Gate 22 loopback lab,
- check existing `/health`,
- create Program/ResearchRun through existing dashboard bootstrap,
- execute existing preflight,
- execute existing START,
- poll existing run detail,
- fetch existing run analysis once at the end,
- enforce total/no-progress timeout,
- call existing cancel on timeout or stale supervisor ownership,
- retain target-side Gate22 request hits,
- evaluate the P3 Layer A / Layer B contract,
- write one local JSON record.

## What it does not do

- direct PostgreSQL writes,
- direct ARC/supervisor calls,
- preflight bypass,
- scope/budget override at START,
- fake Worker/model results,
- forced `diagnostic.echo` success,
- finding fabrication,
- hidden retry of UNKNOWN_OUTCOME,
- conversion of incomplete work into `SUCCESS`.

Control URLs are intentionally restricted to loopback addresses because the current Operator API is a local interface.

## Standard first-run mode

The first deployed stabilization run should use:

```bash
python scripts/stabilization/start_to_terminal_probe.py \
  --gate22 \
  --llm-budget-microdollars <EXPLICIT_LIMIT> \
  --output /tmp/zest-start-to-terminal.json
```

The model budget is intentionally not defaulted to a positive number. The operator must choose the bound before execution.

`--gate22` launches the existing lab, uses its exact origin for target/scope bootstrap, and retains `Gate22SurfaceLab.hits` as independent target-side evidence.

## Existing-run diagnostic mode

```bash
python scripts/stabilization/start_to_terminal_probe.py \
  --run-id <RESEARCH_RUN_ID> \
  --output /tmp/zest-run-diagnostic.json
```

This mode can diagnose lifecycle state but cannot by itself prove the target-side hit criterion, because the probe did not own the target process. It therefore must not be used to manufacture a Layer A PASS.

## Default timing bounds

- total runtime: 600 s
- no-progress interval: 240 s
- terminal/supervisor grace: 10 s
- cancel verification: 30 s
- poll interval: 2 s

These values may be changed before execution and are part of the recorded test conditions. A normal long model call must be given enough no-progress allowance.

## Progress detection

The probe treats changes in persisted/effective state, phase, cycle, request/Worker/model counts, hypothesis/experiment/observation counts, or authoritative update timestamp as progress.

It polls run detail during execution. Deep `/analysis` is fetched once after the run instead of every poll, avoiding unnecessary repeated observer/read-model work and keeping the measurement path small.

## Cleanup rule

If the run times out, or reaches a stop-like state while still locally supervised, the probe calls the existing cancel operation and verifies:

```text
stop_reason == OPERATOR_CANCELLED
locally_supervised == false
```

Failure to verify cleanup remains a stabilization failure.

## Result JSON

The local output records:

- timestamps,
- control URLs,
- health,
- bootstrap ids,
- run id,
- preflight,
- START response,
- bounded poll history,
- final run detail,
- final HQ analysis,
- Gate22 target hits,
- Layer A checks,
- Layer B checks,
- cancel attempt/verification when needed,
- final stabilization classification and reason.

The source APIs already redact secrets; the probe does not request raw model prompts, raw Worker diagnostics, credentials, or direct database contents.

## Classification

The implementation emits only the stabilization classes:

- `SUCCESS`
- `COULD_NOT_START`
- `STUCK_OR_CRASHED`
- `FINISHED_WITHOUT_REQUIRED_WORK`
- `CAPABILITY_MISSING`

`SUCCESS` requires both P3 layers and an accepted truthful completion. `diagnostic.echo` alone after discovery is explicitly not Layer B success.

## Source-side validation performed

Before committing, the probe source was compiled with Python's `py_compile` and its argument parser/help path was executed in an isolated local environment. This validates syntax/CLI construction only; it is not a Zest runtime result.

## VDS execution gate

Do not run the probe until S1 proves the deployed release and S2 confirms the actual control endpoints. Once those are known, this file becomes the canonical first real START driver unless VDS evidence proves an interface mismatch.
