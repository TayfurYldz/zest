# Zest — Repository Layout

This document describes the **actual repository tree**, **package boundaries**, and **import rules**.

It does not replace `.cursor/rules/zest.mdc`, `PROJECT_STRUCTURE.md`, `DOMAIN_MODEL.md`, `TECHNICAL_REQUIREMENTS.md`, or `TECHNICAL_DECISIONS.md`.

It does not choose frameworks, ORM, API stack, workflow product, secrets product, observability vendor, container runtime, or companion stores.

---

## Tree

```
zest/
├── src/zest/          # Python control-plane package (Decision 001)
│   ├── core/
│   ├── research/
│   ├── application/
│   ├── data/
│   ├── tools/
│   ├── platform/
│   ├── interface/
│   └── benchmark/            # Evaluation harness (not SoR, not a provider)
├── benchmarks/research/      # Versioned scenarios; hidden evaluation is not model-visible
├── contracts/                # Language-neutral contracts (not Python classes)
├── workers/                  # Side-effect runtimes (out of the control-plane package)
│   └── python/
├── integrations/             # Replaceable adapters (not imported by Core/Research)
│   └── strix/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   └── e2e/
├── scripts/
├── docs/
├── var/artifacts/            # Local artifact *byte* adapter (Decision 006); not SoR
└── var/benchmark-results/    # Optional generated reports; gitignored; not SoR
```

Constitutional design docs stay at the **repository root**. Do not move them into `docs/`.

---

## Why this split

| Location | Why |
|---|---|
| `src/zest/` | Control plane in Python: Core, Research, Application, Data access, Tools contracts, Platform **ports**, Interface |
| `contracts/` | Language-neutral schemas/contracts so Workers/Integrations are not Python-locked |
| `workers/` | Out-of-process execution (Decisions 005, 014). May be Python now; other languages later |
| `integrations/` | Concrete adapters. Core and Research **must not** import these |
| `benchmarks/research/` | Engineering evaluation fixtures (Decision 029). Not Domain SoR or Research Memory |
| `src/zest/benchmark/` | Provider-neutral harness (Decision 030). Imports Research. Must not import postgres/Application/Workers/provider SDKs |
| `var/artifacts/` | First artifact **byte** store adapter (local filesystem). Identity/hash stay in PostgreSQL |

Workers and Integrations are **not** subpackages of `zest`. That keeps “do not import concrete Integrations from Core/Research” visible in the tree.

---

## Package boundaries (`src/zest`)

### `core`

Highest-trust control logic: `request → policy → scope → budget → execution`, Approval semantics, Finding promotion **contract**.

Must not: side effects, tool/provider SDKs, Integration/Platform **implementations**, model-specific logic, declaring targets authorized from LLM output.

Must not depend on `research`.

### `research`

Proposals only: Hypothesis, Experiment plans, Evidence **proposals**, Candidate/Verification/FindingProposal logic.

Must not: execute, change Core decisions, import Integrations or concrete Platform adapters, treat model output as fact/Evidence/Finding.

May depend on Core **contracts**, not on Interface or Application.

### `application`

Use-case coordination (Decision 022). Use cases: Transition A ingestion (Decision 023); `ExecutePlannedExperiment` control-loop skeleton (Decision 024); `ProposeResearchHypothesis` bounded reasoning cycle (Decisions 025–026); `PreparePlannedExperiment` / `EvaluateExperimentFeedback` closed learning cycle (Decisions 027–028).

Must not: own Core policy, import `data.postgres` or `local_process_worker`, create Evidence/Finding. Must not hold a database transaction open while a Worker runs.

### `data`

Persistence of domain records and Artifact **metadata/reference**. PostgreSQL is the SoR (Decision 003). Research Memory is a **read model**, not truth (Decision 009).

Must not: promotion decisions, leaking ORM/SQL dialect into Core/Research domain contracts, storing secret **values** (Decision 013).

Artifact **bytes** are not this package’s default store (Decision 006).

### `tools`

Capability **contracts** (HTTP, browser, shell, recon, …). No side effects. No vendor implementations.

### `platform`

Ports for orchestration coordination (Decision 004), secrets (013), observability (012), artifact bytes (006), isolation primitives (014).

A4 adds the first **local process Worker adapter** (`local_process_worker`) behind `WorkerPort`. That adapter is not architecture. Core and Research must not import it or `subprocess`.

The ModelPort **protocol** currently lives in Research (`zest.research.model_port`). Concrete adapters belong in Integrations (or a later Platform adapter). No provider is selected.

Must not: own policy, own Evidence/Finding, select Temporal/Redis/Vault/Docker here.

### `interface`

Operator/API/CLI/Human Review **surfaces**. Phase A: application/API boundary + minimal CLI + minimal Human Review (Decision 011). No framework chosen.

Must not: own Approval semantics, bypass Core, write PostgreSQL authority directly, collapse AI recommendation into judgment.

### `benchmark`

Provider-neutral evaluation of Research Brain behavior (Decisions 029–030 / GATE 04A). Consumes `ModelPort`. Must not persist to PostgreSQL, import provider SDKs, or treat reports as Evidence/Finding.

Core, Research, and Application must not import `benchmark`.

---

## Allowed dependency direction

```
interface → application → research → core → (tools | data | platform) *contracts*
```

Concrete implementations:

- `workers/`
- `integrations/`
- later Platform adapters

These implement contracts; they are not imported by `core`, `research`, or `application`.

Trust (not the same as imports): Core > Research > Interface/orchestration callers. Workers and Integrations are lower trust. Model output and WorkerResult remain untrusted until the documented transitions.

---

## `contracts/`

Language-neutral. Canonical schemas are JSON Schema Draft 2020-12 (Decision 016). Runtime instance validation is Control Plane `jsonschema` over a local URN registry (Decision 021). Structural lint (`scripts/check_contracts.py`) is not that validator.

Intended contents later: Worker job/result, ModelPort, SecretReference, authorization request/decision, Artifact locator (opaque), observability correlation fields.

Not Python modules. Not vendor SDKs.

---

## `workers/`

Only layer that may perform side effects, and only after Core authorization.

- In-process Workers: **test doubles only** (Decision 005).
- First tool-execution environment: Kali/WSL **adapter location**, not architecture.
- Must not write SoR, self-authorize, widen scope, or change budget.
- WorkerResult is untrusted until Transition A.

`workers/python/` is the first **runtime** location, not a commitment that all Workers stay Python.

---

## `integrations/`

Replaceable connectors. `integrations/strix/` is a **reserved adapter slot**. Strix is optional, not Zest, not Core, not ModelPort owner (Decisions 005, 008, 015).

Core/Research must not import `integrations/`.

---

## `tests/`

| Tree | Intent |
|---|---|
| `unit/` | Layer-local tests (especially Core invariants) |
| `contract/` | Language-neutral contract fixtures vs Core/Research/Worker boundaries |
| `integration/` | PostgreSQL, local Worker process, filesystem artifact adapter — when those exist |
| `e2e/` | Operator → authorization → WorkerResult → Transition A → Human Review → Approval — later |

No test framework is chosen in this document.

---

## `var/artifacts/`

Decision 006 first adapter: local filesystem bytes. Opaque locators only in SoR. Not Evidence. Not committed as domain truth. Windows paths must not enter Domain/Core contracts.

---

## Explicitly not in this layout

- API framework, web framework
- workflow/queue/broker product
- cache/vector/graph/search product
- secrets or observability vendor
- container/Kubernetes manifests as architecture

Packaging (`pyproject.toml`, `uv.lock`) and the PostgreSQL adapter (SQLAlchemy 2 Core, Alembic) now exist. They are not Domain/Core architecture. ORM is not used.

---

## Import rules (enforced later by tests, not by this file)

1. `core` does not import `research`, `application`, `interface`, `integrations`, `workers`, or `benchmark`.
2. `research` does not import `application`, `interface`, `integrations`, `workers`, or `benchmark`.
3. `application` does not import concrete Platform adapters, `data.postgres`, or `benchmark`.
4. `core` and `research` do not import concrete Platform adapters.
5. Secret **values**, provider SDKs, and Strix clients do not appear in `core` or `research`.
6. Models are not authorization principals.
7. `benchmark` does not import `data.postgres`, Application, Workers, or provider SDKs.
 