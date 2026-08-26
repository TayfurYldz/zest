# Workers

Out-of-process **execution** runtimes. This is the only layer allowed to perform side effects, and only after Core authorization.

Not part of the `zest` Python package. Core and Research must not import this tree.

## First local runtime (A4 / Decision 021)

`python/` is a **one-shot** JSON-over-stdin/stdout Worker. That lifecycle is the first implementation, not permanent topology.

- stdout is the protocol document.
- stderr is diagnostics, not truth.
- `WorkerInvocationOutcome` (Control Plane) ≠ `WorkerResult`.
- WorkerResult remains untrusted until Transition A.

## Rules

- Produce `WorkerResult` only. Do not write PostgreSQL SoR.
- Do not interpret invariant hypotheses or chain hypotheses. Workers are unaware of those Research types.
- Do not authorize, widen scope, or change budget.
- On redirect or newly discovered assets: stop and request Core re-evaluation.
- In-process Workers are test doubles only.
- First tool-execution environment may be Kali/WSL; that is not architecture.
- Strix is not a Worker package and not Research Brain. If used, it is an Integration behind Core authorization.
- Correlation id, timeout, cancellation, and duplicate/retry awareness belong on the Worker contract (`contracts/`).
- PID is diagnostic metadata, not Worker identity.

`python/` is the first runtime slot. Other languages may be added later without changing Core/Research.
