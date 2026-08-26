# Core

Core owns execution **authorization semantics**:

- authorization source eligibility
- scope decision semantics over pre-evaluated rule matches
- budget authority / eligibility (IssuedBudget envelope; consumption ledger is Data)
- human Approval eligibility, including evaluation of a recorded Approval for an explicit subject
- final `ExecutionDecision`

Core does **not** own:

- target parsing, wildcard/CIDR/DNS/redirect/URL normalization
- tool execution
- Worker runtime
- database implementation
- model execution
- Strix
- transport
- model routing / runtime selection (Research policy; not authorization truth)
- Target Model / differential reasoning / invariant mining / chain composition / exploration selection / temporal snapshots (Research; not authorization truth)
- model runtime adapters, CLI processes, OAuth sessions, or Strix internals

`ExecutionDecision` is not an execution result. It is not `WorkerResult`, Observation, Evidence, or Finding.

Python classes in this package are **not** language-neutral architectural contracts. Worker wire truth remains `contracts/`.

Models are not authorization principals. Level 3 is denied by default, including when a human Approval is present. Core Approval is not a vulnerability verdict and does not create a Finding.
