# Contracts

These files are **architectural boundaries**.

They are **not**:

- Python domain classes
- database models
- a transport implementation (HTTP, gRPC, queue, IPC framing)
- Core/Research business logic
- Evidence, Finding, Approval, or authorization decisions

Canonical representation: **JSON Schema Draft 2020-12** (Decision 016).

`canonical representation ≠ transport protocol.` A message that validates against these schemas may later move over local IPC or a remote transport without changing contract semantics.

`scripts/check_contracts.py` performs **contract lint / structural checks** only. It is **not** a Draft 2020-12 semantic validator. Runtime instance validation is Decision 021 (`jsonschema`, local URN registry, no network fetch) in the Control Plane Worker adapter. The lint script does not fetch `$schema` or `$ref` over the network.

---

## A1 scope

Worker **execution** boundary only:

- CorrelationContext
- ExecutionBudget
- SecretReference
- WorkerRequest
- WorkerResult
- ReauthorizationRequest

Not in A1 (do not invent extra wire contracts for the old roadmap):

- authorization request/decision — Core A2 domain/authority model; wire contract later only if a real cross-process boundary appears
- Artifact identity/reference/hash — A3 Data and/or a later artifact byte port when that boundary is clear

---

## Layout

```
contracts/
  v1/
    common/
      correlation-context.schema.json
      execution-budget.schema.json
      secret-reference.schema.json
    worker/
      worker-request.schema.json
      worker-result.schema.json
      reauthorization-request.schema.json
```

`$id` (URN) is the **contract id**. `contract_version` on Worker-facing messages is `"v1"` for this major.

---

## `$ref` model

Contract identity is the canonical `$id` URN, not a filesystem path.

Allowed `$ref` values:

- a canonical URN that exists in this schema set, e.g. `urn:zest:contracts:v1:correlation-context`
- a same-document JSON Pointer fragment, e.g. `#/$defs/...`

Filesystem-relative refs (`../common/...`) and network/external refs are not allowed.

This repo implements a runtime validator `$id` registry in the Control Plane (`zest.platform.contract_validation`). Lint still only checks that `$ref` URNs match local `$id` values. Structural lint ≠ runtime semantic validation.

---

## Versioning

- Breaking semantic or schema change → new major (`contracts/v2/`).
- Backward-compatible additive fields may stay in `v1`.
- Field removal or meaning change is breaking.
- Transport version, HTTP API version, and domain lifecycle version are **not** this contract major.
- Producer/consumer compatibility is tested under `tests/contract/` against the runtime validator and local Worker. No SemVer product is selected.

---

## ExecutionBudget zero semantics

`minimum` is 0. **0 is not unlimited.**

| Field | `0` means |
|---|---|
| `max_requests` | no requests may be made |
| `max_tool_calls` | no tool calls may be made |
| `max_runtime_ms` | execution must not start / is immediately exhausted |
| `max_concurrency` | no execution slot |

Workers cannot raise these limits.

---

## WorkerResult timestamps

`started_at` / `completed_at` are transport-neutral strings with RFC 3339 / ISO 8601 **timezone-aware** timestamp semantics.

Schemas may set `"format": "date-time"`. `scripts/check_contracts.py` still does **not** enforce `format`. Control Plane runtime validation (Decision 021, `jsonschema` format checker) does. These fields are not PostgreSQL `timestamptz` (or any other database type).

---

## Authority rules

First-class fields and references carry authorization, budget, and targeting. Extensible objects (`arguments`, `raw_result`, `diagnostics`, `discovery_context`, `control_signal`) **must not** be used as a second place for:

- authorization / scope / approval
- budget authority
- Evidence / Finding / promotion

Unknown properties on authority-bearing objects are **rejected** (`additionalProperties: false`).

`SecretReference` never contains secret **values**.

`WorkerResult` is untrusted execution output. It is not Observation, Artifact, Evidence, Candidate, or Finding.

`ReauthorizationRequest` asks Core to evaluate again. It does **not** grant authorization.

`side_effect_level` 3 is representable and **denied by default** in Core (Decision 014). The field is not policy.

Identifiers are **opaque strings**, not PostgreSQL UUID types.
