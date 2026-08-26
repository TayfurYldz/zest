# Python Worker (first local runtime)

This directory is **not** part of the `zest` Control Plane package.

It is an execution runtime. It is **not** authority. Same-machine does not authorize work. Worker identity is a configured opaque id (`ZEST_WORKER_ID`), not a PID.

## Protocol (Decision 021)

First local transport — **not** permanent architecture:

1. Control Plane spawns this process (`python -m zest_worker`).
2. stdin: exactly one JSON **WorkerRequest**.
3. Worker performs exactly one capability invocation.
4. stdout: exactly one JSON **WorkerResult**. No logs. No banners.
5. stderr: bounded diagnostics only. Not truth. No secret values.
6. Process exits.

Canonical semantics remain `contracts/v1` JSON Schema. stdin/stdout is only the Phase A adapter. A later remote Worker may use another transport without changing WorkerRequest / WorkerResult.

**Transport / invocation failure is not a WorkerResult.** A crash, timeout, invalid JSON, extra stdout, or correlation mismatch is a Control Plane `WorkerInvocationOutcome` (`PROCESS_FAILED` / `TIMED_OUT` / `PROTOCOL_ERROR` / `CONTRACT_INVALID`). It must not be rewritten as `WorkerResult.status = EXECUTION_FAILED`, because no valid WorkerResult may exist.

WorkerResult remains untrusted until downstream Transition A. This Worker does not create Observation, Evidence, Candidate, or Finding.

## Capability

`diagnostic.echo` / `echo` — deterministic, no network, no side effects. Proves process + contracts. It is not a scanner and not Evidence.

## Rules

- Do not import `zest.core`, Data, or PostgreSQL drivers.
- Do not write the SoR.
- Do not treat WorkerResult as Observation, Evidence, Candidate, or Finding.
- Request bytes arrive on stdin, never as shell command text.
