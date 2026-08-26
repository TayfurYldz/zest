# Platform

Ports and first adapters for Control Plane infrastructure.

## WorkerPort

`zest.platform.worker.WorkerPort` is the invocation contract.

`LocalProcessWorkerAdapter` is the **first local transport**:

- one-shot child process
- JSON WorkerRequest on stdin
- JSON WorkerResult on stdout
- stderr diagnostics
- `shell=False`

This adapter is **not** architecture. Remote Workers may use another transport with the same canonical schemas.

Core and Research must not import `local_process_worker` or `subprocess`.

## ModelPort

The provider-neutral completion protocol currently lives in Research (`zest.research.model_port.ModelPort`) because Research is the consumer. Concrete adapters belong in Integrations. No provider SDK is selected. Tests use a deterministic fake that returns structured mappings, not natural-language magic.

Core must not call ModelPort. Research must not import provider SDKs.

## Invocation vs WorkerResult

Development defaults (configurable): stdout protocol payload 1 MiB; stderr diagnostics 64 KiB; timeout 30s. Overflow of stdout is a protocol failure, not a truncated WorkerResult.

Cancellation: first implementation is local process termination (timeout/kill). Distributed cancellation is a later runtime slice, not a protocol product now.

Child environment is constructed explicitly. Database URLs, model API keys, and application credentials are not forwarded. `ZEST_WORKER_ID` is the configured opaque identity. PID is diagnostic only.

## Argv / CLI session runner

`zest.platform.argv_process` is the first argv transport for authenticated CLI runtimes. It is **not** architecture and is **not** re-exported from `platform/__init__.py`.

- `shell=False`
- bounded stdout/stderr
- timeout / kill
- constructed environment (no DB URLs or provider secrets)

Research and Application must not import this module. Integrations may.

## StrixIntegration port

`zest.platform.strix` is the typed Strix envelope. Strix is not Research Brain, Core, Memory, or Finding authority. Concrete adapters live in Integrations. Application injects the port after Core ALLOW. Denied requests must not reach the adapter.

## Invocation vs WorkerResult

`WorkerInvocationOutcome.invocation_status` is a Control Plane runtime outcome.

It is **not** `WorkerResult.status`.

A crash, timeout, invalid JSON, or correlation mismatch does **not** produce a WorkerResult for Transition A.

## SecretPort / artifacts / health / observability

GATE 13 operational ports live here:

- `secrets.SecretPort` — SecretReference only; values never enter SoR
- `artifacts.LocalArtifactStore` — bounded paths, hash, atomic write
- `health.ComponentHealth` — operational health, not research truth
- `observability.ObservabilityPort` — structured events/metrics, not AuditEvent or Evidence

ENV_REFERENCE is not a production secret manager. Do not scrape CLI/OAuth tokens.

## Contract validation

Runtime Draft 2020-12 validation loads `contracts/v1` and resolves URN `$id` locally. It does not fetch the network. `scripts/check_contracts.py` remains structural lint only.
