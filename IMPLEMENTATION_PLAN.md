# Zest — Implementation Plan

Roadmap only. No business logic, products, or frameworks are selected here.

Follow `.cursor/rules/zest.mdc`, `PROJECT_STRUCTURE.md`, `DOMAIN_MODEL.md`, `TECHNICAL_REQUIREMENTS.md`, and `TECHNICAL_DECISIONS.md`.

Decision-driver order remains: correctness → authorization/security → durability → audit/provenance → recoverability → simplicity → testability → …

---

## Locked context (do not reopen in code)

| Decision | Lock |
|---|---|
| 001 | Python control plane; Workers/Integrations may differ; language-neutral contracts |
| 002 | Relational SoR; rebuildable read models; companions non-authoritative |
| 003 | PostgreSQL SoR; not Research Memory; not default artifact bytes |
| 004 | Hybrid orchestration; engine **product deferred** |
| 005 | Mixed topology; Worker contract; Kali/WSL first **tool** Worker; no mandatory broker |
| 006 | Artifact metadata/hash in SoR; bytes behind port; local FS first |
| 007 | No dedicated cache product in v1 |
| 008 | ModelPort; provider **deferred**; no live multi-model router |
| 009 | No vector in v1; Research Memory = structured/text over SoR |
| 010 | Staged deploy; Phase A local Control Plane + local PostgreSQL + Kali/WSL Worker |
| 011 | Staged Interface; no full dashboard required in v1; UI frameworks **deferred** |
| 012 | ObservabilityPort; structured logs first; vendors **deferred** |
| 013 | SecretPort; values never in Domain/prompts/AuditEvent; product **deferred** |
| 014 | Process/OS boundary; in-process = fakes; containers **not** required |
| 015 | Identity classes; operator id for audit/Approval; authn ≠ authz; no IAM product |
| 016 | JSON Schema Draft 2020-12 canonical contracts; Python classes are not contract truth |
| 017 | False-positive suppression; signal ≠ Evidence ≠ Finding; INCONCLUSIVE valid; confidence deferred |
| 018 | Realistic novelty N1/N2/growing N3; N4 not promised; Research Brain in Research, not Core |
| 019 | PEP 621 pyproject.toml; Hatchling; uv installer/lock (replaceable); Python >=3.11 |
| 020 | SQLAlchemy 2 Core + psycopg 3 + Alembic; sync Data adapter; no ORM; no SQLite-as-Postgres |
| 021 | Canonical schemas stay `contracts/`; first local transport = one-shot JSON stdin/stdout; jsonschema runtime validation (local URN, no network); `WorkerInvocationOutcome` ≠ `WorkerResult` |
| 022 | Explicit Application layer owns use-case coordination; not authority; no concrete adapters |
| 023 | Transition A: COMPLETED+valid WorkerResult only; trusted-request normalizer registry; request_id idempotency; WorkerResult+Observation+AuditEvent one UoW |
| 024 | First-class ExecutionAttempt for dispatch coordination; Control Plane owns request_id; AuditEvent is decision provenance not a queue; UNKNOWN_OUTCOME fail-closed; no exactly-once side effects |
| 025 | Typed ResearchContext epistemic boundary; deterministic bounded Context Builder; untrusted external content stays data; no vector/RAG in v1 |
| 026 | Generator → HypothesisProposal → Falsifier → HypothesisChallenge → Research admission; ModelPort provider deferred; reasoning provenance append-only |
| 027 | ExperimentFeedback + context-bound HypothesisAssessment; no SUPPORTED/REJECTED global truth; diagnostic.echo deterministic evaluator only; no numeric belief |
| 028 | Persist reasoning/admission for all completed cycles including rejected; rejected ≠ Hypothesis; N4 claim preserved as model_claimed_novelty, system novelty UNCLASSIFIED |
| 029 | Versioned benchmark scenarios; hard split of model-visible input vs hidden evaluation; leakage is a test failure; development vs holdout |
| 030 | Scorecard of hard-fail events + quality dimensions; no magic aggregate model score; no LLM-as-judge; no vector similarity; provider comparison deferred to GATE 04B |
| 031 | Real-model comparison is a controlled experiment: config/instruction identity, repeated runs, paired scenarios, provider vs research failures, metamorphic variants, no automatic winner |
| 032 | DEVELOPMENT / CALIBRATION / SEALED_HOLDOUT; sealed suite lives outside the Cursor workspace; fingerprint without hidden answers |

---

## Standing invariants (every slice)

- DEFAULT DENY. Core authorizes; Workers execute; Data persists.
- WorkerResult untrusted until Transition A. Evidence only via Transition B.
- Finding only after Human Review and Core-recorded Approval.
- Finding never from model output, scanner signal, WorkerResult, or confidence score alone (Decision 017).
- Interface does not own Approval. Models are not principals.
- PostgreSQL wins vs companions, logs, traces, metrics, caches, vector indexes (none in v1).
- Secret **values** never logged or stored as Domain.

---

## Phase A — first working implementation

Matches Decision 010 Phase A and Decision 011 Phase A.

### Slice A0 — Layout (this commit)

Repository tree, package boundaries, empty ports. **Done when this layout exists.** No runtime.

### Slice A1 — Worker execution contracts

Canonical JSON Schema Draft 2020-12 files under `contracts/v1/` (Decision 016).

**A1 wire contracts (Worker execution boundary only):**

- CorrelationContext
- ExecutionBudget
- SecretReference
- WorkerRequest
- WorkerResult
- ReauthorizationRequest

This slice does **not** define authorization request/decision wire contracts. Those belong in Core (A2) as domain/authority model; a cross-process/wire contract is added later only if that boundary is actually needed.

This slice does **not** define Artifact identity/reference/hash wire contracts. Artifact domain metadata is designed in A3 (Data) and/or A4 (artifact byte port) once that boundary is clear.

**Verify:** `python scripts/check_contracts.py` (contract lint / structural checks, not a Draft 2020-12 semantic validator). `$ref` values are canonical URNs, not filesystem paths. No extra wire contracts “because the old roadmap listed them.”

### Slice A2 — Core (no I/O)

Authorization, scope, budget **policy**, Approval **semantics**, Finding promotion **contract**. In-memory tests only.

Authorization request/decision remain Core domain/authority model in this slice. They become separate `contracts/` wire schemas only if a real cross-process boundary requires them.

**Verify:** missing AuthorizationSource or ambiguous scope → DENY or REQUIRE_HUMAN_REVIEW. LLM-shaped input cannot authorize. No subprocess, no SDKs.

### Slice A3 — Persistence spine (PostgreSQL)

**Data access (Decision 020):** SQLAlchemy 2 Core + psycopg 3 + Alembic, synchronous Data adapter, explicit Unit of Work. **No ORM. No SQLModel.** SQLite is not a PostgreSQL substitute.

A3 implements a **minimum authoritative persistence spine**, not the complete domain schema:

Program → AuthorizationSource → ResearchRun → IssuedBudget → Hypothesis → Experiment → WorkerResult → Observation, plus AuditEvent.

**Deliberately deferred** until their domain semantics are ready (do not invent them as JSON authority):

- ScopeRule matcher definitions (grammar/normalization not locked)
- Evidence (Evidence admission authority is still an open domain question)
- Candidate, Verification, FindingProposal, Finding, Approval
- Snapshot, ChangeEvent, graphs, vectors, model routing

Artifact identity/reference/hash as a **wire** contract is not part of A1. Decide that boundary here and/or in A4 (byte port) when the cross-process need is real.

**Verify:** Alembic upgrade is the schema path (`create_all` is not startup). Core/Research import no SQLAlchemy/psycopg/Alembic. WorkerResult insert does not create Observation or Evidence. IssuedBudget is immutable after insert; 0 is no allowance. AuditEvent is append-only. Integration tests run only when `ZEST_TEST_DATABASE_URL` is set.

**A3 PostgreSQL validation:** GATE 01 runs `tests/integration` against an explicit `ZEST_TEST_DATABASE_URL`. Skipped tests remain **PENDING, not PASS**. Do not install Docker automatically. If the URL is absent, report PENDING.

Preferred local sources, in order: existing local PostgreSQL; existing WSL PostgreSQL; an explicit local install; a container only if the developer chooses it later. Docker/Kubernetes are not architecture.

### Slice A4 — Minimal out-of-process Worker runtime (Decision 021)

Prove the execution boundary, not a scanner:

```
canonical WorkerRequest
  → WorkerPort
  → LocalProcessWorkerAdapter (argv, no shell)
  → one-shot child process
  → isolated diagnostic Worker
  → WorkerInvocationOutcome (optional canonical WorkerResult)
```

- First local transport: JSON over stdin/stdout; stderr diagnostics. **Not** architecture. Not HTTP/broker/RPC.
- One-request-per-process is the **first implementation**, not permanent topology.
- Runtime Draft 2020-12 validation via `jsonschema` over local URN `$id`. No network schema fetch. `scripts/check_contracts.py` remains structural lint.
- `WorkerInvocationOutcome` ≠ `WorkerResult`. Crash/timeout/invalid JSON do not fabricate a WorkerResult.
- Production capability: `diagnostic.echo` only. Test-only failure modes live in fixture programs, not the production registry.
- Worker lives in `workers/python/`. It does not import Core/Data/PostgreSQL and does not write the SoR.
- This slice does **not** persist WorkerResult, run Transition A, or produce Observation/Evidence/Candidate/Finding.

Original roadmap items that remain later Platform work (not this A4): SecretPort product, ObservabilityPort vendor, artifact byte adapter, orchestration engine.

**Verify:** unit + `tests/contract` + `scripts/check_contracts.py`. Core/Research import neither `subprocess` nor `local_process_worker`. Worker imports neither `zest` nor SQLAlchemy/psycopg. Correlation mismatch is fail-closed. Bounded stdout/stderr.

### Slice A6-lite — Transition-A spine (Decisions 022–023)

valid COMPLETED Worker invocation → Application `IngestCompletedWorkerInvocation` → deterministic `diagnostic.echo` normalizer → Observation persistence.

- Application layer owns the use case. Core/Research/Platform/Interface do not.
- Only COMPLETED + valid WorkerResult may ingest. Transport failures are not WorkerResult rows.
- BLOCKED/EXECUTION_FAILED diagnostic results may persist WorkerResult with `NO_OBSERVATION`.
- Idempotency identity is `request_id` (unique). Payload-equal distinct requests are not collapsed.
- Alembic revision `a6_001_transition_a_provenance` adds envelope columns. Do not rewrite `a3_001`.
- No Evidence, Candidate, Finding, scanner, or Research Brain.

**Verify:** unit + contract + architecture. PostgreSQL integration when `ZEST_TEST_DATABASE_URL` is set; otherwise PENDING.

### Slice A7-lite — Minimal research control-loop skeleton (Decision 024)

Prove plumbing, not a Research Brain:

```
Human-seeded Hypothesis
  → ExperimentPlan (Research)
  → ExecutePlannedExperiment (Application)
  → Core evaluate_execution
  → durable AuditEvent + ExecutionAttempt
  → WorkerPort
  → WorkerInvocationOutcome
  → Transition A
  → Observation feedback (no Hypothesis truth update)
```

- No model, no autonomous hypothesis generation, no Evidence/Candidate/Finding, no Strix.
- Research produces `HypothesisDraft` / `ExperimentPlan` only. It does not execute, authorize, or persist.
- Application constructs `WorkerRequest` only after Core ALLOW. `request_id` is Control Plane–generated.
- Staged transactions: TX1 AUTHORIZED intent, TX1b DISPATCHING, Worker outside the database transaction, TX2 outcome, then Transition A.
- Alembic revision `a7_001_execution_attempt`. Do not rewrite `a3_001` or `a6_001`.
- Persistent budget consumption ledger is **deferred**. Level 0 diagnostic may proceed.
- Zest does not claim exactly-once side effects.

**Verify:** unit + architecture. PostgreSQL integration when `ZEST_TEST_DATABASE_URL` is set; otherwise PENDING.

### Slice A7 — Research Brain v1 foundation (Decisions 025–026)

Prove one bounded reasoning cycle, not autonomous bug bounty:

```
Authoritative / admitted state
  → typed ResearchContext
  → Generator HypothesisProposal (untrusted)
  → Falsifier HypothesisChallenge
  → Research admission
  → persisted Hypothesis + ResearchReasoningRecord
  → ExperimentPlan (no Worker dispatch)
```

- No provider SDK. Tests use a deterministic fake ModelPort.
- No Evidence, Candidate, Finding, Strix, vector retrieval, chain engine, or autonomous loop.
- Generator output is not a Hypothesis until Research admission.
- Alembic revision `a8_001_research_reasoning`. Do not rewrite `a3_001`, `a6_001`, or `a7_001`.

**Verify:** unit + architecture. PostgreSQL GATE 02 when `ZEST_TEST_DATABASE_URL` is set; skipped tests are PENDING, not PASS.

### Slice A7 — Closed learning cycle (Decisions 027–028)

Close the research loop after execution:

```
admitted Hypothesis
  → durable ExperimentPlan
  → Core-authorized execution
  → Observation
  → ExperimentFeedback
  → context-bound HypothesisAssessment
```

- Deterministic `diagnostic.echo` evaluator only. WorkerResult does not choose the evaluator.
- Persist reasoning and admission for rejected proposals; they still do not become a Hypothesis.
- Alembic revision `a9_001_learning_cycle`. Do not rewrite `a3_001`, `a6_001`, `a7_001`, or `a8_001`.

**Verify:** GATE 01 and GATE 02 remain PASS. GATE 03 requires real PostgreSQL.

### FIRST VERTICAL RESEARCH LOOP — INFRASTRUCTURE/CONTROL LOOP GATE

**Status: PASS** (GATE 01, 2026-08-16) against explicit `ZEST_TEST_DATABASE_URL` on real PostgreSQL 18.

This gate proves durable:

```
research state → authority → execution → observation feedback
```

It does **not** mean Research Brain complete, vulnerability discovery complete, or an autonomous bug bounty system complete.

Skipped PostgreSQL-required tests are not used to claim this PASS.

### Next planned slices (not implemented here)

### Slice A5 — Research (proposals)

Hypothesis / Experiment **plan** / Evidence **proposal** / Candidate / FindingProposal. ModelPort **fake** adapter in tests.

**Verify:** Research cannot create Finding. Model output stays UNTRUSTED STRUCTURED PROPOSAL. Full A5 does not block proving the minimal control loop.

### Slice A6 — Interface Phase A

Application/API **boundary** (no API framework chosen until a later decision) + minimal CLI + minimal Human Review that submits Approval to Core.

**Verify:** UI/CLI cannot write Findings into PostgreSQL. Operator identity on Approval (Decision 015).

### Slice A7 — Kali/WSL tool-Worker adapter slot (after A4)

First **local diagnostic** Worker runtime is delivered in A4. Remaining A7 work is the Kali/WSL **tool** environment adapter when a real (still replaceable) tool Worker is justified — not a recon framework, not Strix-as-architecture.

**Verify:** out-of-process; crash does not kill Core; no SoR writes from Worker; redirect → stop → Core re-eval; correlation id present.

### Slice A8 — Transition B (after A6-lite Observation ingest)

A6-lite persists Observation. Remaining: Artifact metadata/bytes via port, then **separate** Evidence admission (Transition B).

**Verify:** no Evidence at Observation ingest. Artifact attachment ≠ Evidence.

### Slice A9 — First Human Review loop

FindingProposal → Interface review → Core Approval → Finding. AuditEvent for the decision.

**Verify:** no Finding without Approval. Logs are not the audit authority (Decision 012).

---

## Phase B — replaceable Workers and dashboard

- Same Worker contract → authenticated remote Worker **when needed** (not now).
- Interface Phase B: web dashboard (**framework still a later decision**).
- ModelPort: one real adapter when a provider is chosen (Decision 008 product still deferred).
- `integrations/strix/` only if explicitly adopted as a replaceable adapter.

---

## Phase C — production topology if justified

Distributed Control Plane/Workers, object-store artifact adapter, external secrets manager, observability backend, stronger isolation — **each requires a decision revisit**. No Kubernetes-by-default.

---

## Explicitly later / not this roadmap’s job

- API framework, web framework
- Temporal/Celery/Prefect, brokers, Redis
- Vector/graph/search products
- Embedding pipeline
- Full observability platform
- Docker/Kubernetes as architecture
- Multi-model live routing

---

## Suggested test order

1. `tests/unit/` — Core deny/allow, promotion contract, immutability rules
2. `tests/contract/` — WorkerResult and authorization message shapes
3. `tests/integration/` — PostgreSQL transactions; filesystem artifact adapter
4. `tests/e2e/` — one authorized run through Human Review

---

## Definition of “first working implementation”

A single operator can:

1. Record Program + AuthorizationSource + ScopeRules
2. Start a ResearchRun under Core
3. Dispatch an authorized Worker job
4. Ingest WorkerResult (Transition A)
5. Admit Evidence (Transition B) only as a separate step
6. Open a FindingProposal
7. Record Human Review as Core Approval
8. Persist a Finding only after that Approval

All of that uses this repository layout, PostgreSQL as SoR, and no extra product mandated by this plan.

Research Brain (target/state model, invariant mining, differential engine, exploration policy) is **not** required for this first working implementation. It is later Research work (Decisions 017–018), not Core.

---

## Vertical-slice gate (after A3 spine exists)

After the minimum persistence, Worker, orchestration, and Research pieces exist, prove an **end-to-end control loop** before broad horizontal schema expansion:

```
Program
→ AuthorizationSource
→ ResearchRun
→ Hypothesis
→ Experiment
→ Core ExecutionDecision
→ WorkerRequest
→ Worker
→ WorkerResult
→ Transition A
→ Observation
→ Research feedback
```

This gate does **not** claim vulnerability discovery. It proves the research control loop.

**GATE 01 status: PASS** on real PostgreSQL via explicit `ZEST_TEST_DATABASE_URL`. Skipped tests are not PASS.

## GATE 02 — Bounded Research Reasoning Cycle

```
structured ResearchContext
→ Generator proposal
→ Falsifier challenge
→ Research admission
→ persisted Hypothesis
→ ExperimentPlan
```

Uses a deterministic fake ModelPort and real PostgreSQL. Does **not** prove vulnerability discovery.

**GATE 02 status: PASS** (2026-08-16) against explicit `ZEST_TEST_DATABASE_URL` on real PostgreSQL 18. Skipped tests are not PASS.

**Verify:** unit + contract + `scripts/check_contracts.py` + integration (no PostgreSQL-required skips).

## GATE 03 — Closed Research Learning Cycle

```
ResearchContext
→ Generator
→ Falsifier
→ Admission
→ Hypothesis
→ durable ExperimentPlan
→ Core authorization
→ ExecutionAttempt
→ Worker
→ WorkerResult
→ Observation
→ ExperimentFeedback
→ HypothesisAssessment
→ restart/reload
```

Uses a deterministic fake ModelPort, `diagnostic.echo`, a real local Worker, and real PostgreSQL. Does **not** prove vulnerability discovery. Does not create Evidence, Candidate, or Finding.

Also proves the rejected-proposal ledger path: invalid/unsupported proposal → persisted admission/reasoning → no Hypothesis → reconstructable after reload.

**GATE 03 status: PASS** (2026-08-17) against explicit `ZEST_TEST_DATABASE_URL` on real PostgreSQL 18. Skipped tests are not PASS.

Alembic revision `a9_001_learning_cycle`. Do not rewrite `a3_001`, `a6_001`, `a7_001`, or `a8_001`.

**Verify:** unit + contract + `scripts/check_contracts.py` + integration (no PostgreSQL-required skips).

## GATE 04A — Provider-Neutral Research Benchmark Harness

```
versioned scenario
→ visible_input → ResearchContext
→ ModelPort (scripted doubles)
→ Generator / Falsifier / admission
→ hidden_evaluation scorecard
```

Proves the harness can measure research *behavior* under the same ResearchContext. Does **not** select a provider. Does **not** prove real LLM quality, vulnerability discovery, N4, or novel finding ability.

Scripted doubles (`GOOD_BASELINE`, `BAD_HALLUCINATOR`, `BAD_POLICY_FOLLOWER`, `OVERCAUTIOUS_BASELINE`) are test fixtures, not models.

Benchmark reports are evaluation artifacts. They are not Evidence, Finding, Candidate, or SoR truth. No PostgreSQL benchmark schema.

**GATE 04A status: PASS** (2026-08-17). Hidden evaluation never enters ModelRequest. Gate 01–03 remain PASS on real PostgreSQL.

**Verify:** unit + contract + `scripts/check_contracts.py` + integration (no PostgreSQL-required skips) + `uv run python scripts/run_research_benchmark.py`.

GATE 04B (deferred): real provider adapters compared on this same harness.

## GATE 04B-PREP — Real Model Evaluation Readiness

Makes real-model comparison scientifically defensible **before** any provider SDK is installed.

- `BenchmarkExperimentConfig` + `ModelConfigurationIdentity` + instruction fingerprints
- repeated runs with hard-fail occurrence fractions (not hidden averages)
- paired comparison with incomparable-suite detection
- provider/runtime failure ≠ research-quality failure
- metamorphic development variants
- context-utilization / scenario-specificity observations
- external sealed holdout loader (`ZEST_BENCHMARK_HOLDOUT_PATH`)
- suite fingerprint/manifest without hidden answers
- immutable JSON reports under `var/benchmark-results/`

Does **not** select OpenAI, Anthropic, Gemini, or any other provider. Does not print `WINNER`.

**GATE 04B-PREP status: PASS** (2026-08-17). GATE 01–04A remain PASS on real PostgreSQL.

**Verify:** unit + contract + `scripts/check_contracts.py` + integration (0 skipped) + `uv run python scripts/run_research_benchmark.py --runs-per-scenario 1`.

Live GATE 04B (deferred): attach real provider adapters to this experiment protocol.

## GATE 04B — Live Model Adapter / Empirical Benchmark

Runs the GATE 04A/04B-PREP harness through replaceable ModelPort adapters under `integrations/models/`.

- Research/Core/Application/benchmark do not import provider SDKs
- OpenAI, Anthropic, and Gemini adapters exist; missing SDK/credential/model id = **UNAVAILABLE**
- UNAVAILABLE is not a research-quality failure and not a fake PASS
- Comparative PASS requires ≥2 real model configurations actually executed on the same comparable suite
- Secrets stay in composition-root env references
- Failure classes distinguish provider auth/rate-limit/timeout/runtime from research quality
- No `WINNER` line; sealed holdout remains external or UNAVAILABLE

**GATE 04B status: PENDING** (2026-08-17) for comparative live execution: adapters and harness are implemented, but this environment has no installed provider SDKs and no API keys, so 0 live configurations ran. Scripted baselines are not live providers. GATE 01–04A and GATE 04B-PREP remain PASS on real PostgreSQL. Transition B implementation is PASS.

**Verify:** unit + contract + `scripts/check_contracts.py` + integration (0 skipped) + `uv run python scripts/run_research_benchmark.py --runs-per-scenario 1`. Live comparison requires `--adapter/--model` with credentials and ≥2 real configurations.

## Transition B — Evidence admission foundation

Observation/Artifact → Research evaluation → EvidenceProposal → auditable admission → Evidence.

Research owns admission semantics. Application coordinates persistence. Data persists. Core remains authorization authority, not Evidence truth. The model cannot admit Evidence.

First path is deterministic diagnostic.echo plumbing only. It is not vulnerability Evidence, Candidate, Finding, or Verification.

**Verify:** unit + contract + `scripts/check_contracts.py` + integration on real PostgreSQL (0 skipped).

## GATE 05 — Verification / Candidate Integrity

Evidence → Candidate OPEN → VERIFYING → independent reproduction/control → Verification → VALIDATED / REJECTED / INCONCLUSIVE.

- Research owns Candidate admission and transition rules
- Deterministic diagnostic verifier; no second provider required
- Reproduction uses a new Experiment / request_id; original Evidence cannot self-validate
- Timeout / unusable execution → INCONCLUSIVE, not REJECTED
- VALIDATED Candidate is not a Finding
- New Alembic `a11_001_candidate_verification` only

**GATE 05 status: PASS** (2026-08-17) on real PostgreSQL. Deterministic diagnostic path only. VALIDATED Candidate is not a Finding. GATE 04B remains PENDING (missing credentials is not a GATE 05 regression). GATE 01–04A, GATE 04B-PREP, and Transition B remain PASS.

**Verify:** unit + contract + `scripts/check_contracts.py` + integration (0 skipped).

## GATE 06 — Human Finding Acceptance Integrity

VALIDATED Candidate → FindingProposal → Human Review → Core Approval → Finding.

- Research owns FindingProposal and Finding creation-gate semantics
- Core owns Approval semantics only; it does not decide vulnerability truth
- Application coordinates and cannot self-approve
- Human REJECT leaves Candidate VALIDATED and creates no Finding
- Diagnostic plumbing Finding is not a security vulnerability
- New Alembic `a12_001_finding_acceptance` only; a3–a11 are not edited

**GATE 06 status: PASS** (2026-08-17) on real PostgreSQL. Deterministic diagnostic path only. No automatic Finding path. GATE 04B remains PENDING (missing credentials is not a GATE 06 regression). GATE 01–05 remain PASS.

**Verify:** unit + contract + `scripts/check_contracts.py` + integration (0 skipped).

## GATE 07 — Target Model / Differential Integrity

Observation set → Target Model projection → controlled DifferentialCase → DifferentialObservation → bounded Generator/Falsifier cycle → admitted/rejected Hypothesis.

- Target Model is a Research projection, not a second SoR
- OBSERVED / DERIVED / INFERRED / HYPOTHESIZED remain distinct
- Difference is not a vulnerability and does not create Evidence/Candidate/Finding
- New Alembic `a13_001_target_differential` only; a3–a12 are not edited

**GATE 07 status: PASS** (2026-08-17) on real PostgreSQL. Deterministic diagnostic path only. GATE 04B remains PENDING (missing credentials is not a GATE 07 regression). GATE 01–06 remain PASS.

**Verify:** unit + contract + `scripts/check_contracts.py` + integration (0 skipped).

## GATE 08 — Invariant / Chain Integrity

Target Model + DifferentialObservation → InvariantProposal → invariant admission → Hypothesis/Experiment direction; and Target/Research state → bounded ChainHypothesis construction → provenance reload → controlled multi-step experiment planning.

- Invariant stays a hypothesis; it is not a fact, ScopeRule, Evidence, Candidate, Finding, or vulnerability
- Counterexamples remain context-bound
- Unsupported causal edges are rejected; inferred intermediate state stays inferred
- Chain Engine does not dispatch Workers and cannot bypass Core
- New Alembic `a14_001_invariant_chain` only; a3–a13 are not edited

**GATE 08 status: PASS** (2026-08-17) on real PostgreSQL. Deterministic diagnostic path only. No security vulnerability required. GATE 04B remains PENDING (missing credentials is not a GATE 08 regression). GATE 01–07 remain PASS.

**Verify:** unit + contract + `scripts/check_contracts.py` + integration (0 skipped).

## GATE 09 — Exploration / Temporal Integrity

Research state → ResearchOpportunity set → bounded exploration/exploitation selection; and Snapshot t1 → Snapshot t2 → deterministic ChangeEvent → TIME DifferentialCase → ResearchOpportunity → optional HypothesisProposal.

- Selection is not Core authorization and does not dispatch a Worker
- Opportunity is not Hypothesis truth, Evidence, Candidate, or Finding
- No magic weighted priority score
- Negative knowledge remains context-bound; historical assessments are not rewritten
- Change is not a vulnerability; TIME requires snapshot provenance
- Snapshot is immutable; ChangeEvent provenance survives reload
- New Alembic `a15_001_exploration_temporal` only; a3–a14 are not edited
- Snapshot retention/compaction is deferred and must never delete Evidence/Verification/Finding provenance

**GATE 09 status: PASS** (2026-08-17) on real PostgreSQL. Deterministic diagnostic path only. No security vulnerability required. GATE 04B remains PENDING (missing credentials is not a GATE 09 regression). GATE 01–08 remain PASS.

**Verify:** unit + contract + `scripts/check_contracts.py` + integration (0 skipped).

## GATE 10 — Runtime / Strix Boundary Integrity

Replaceable ModelRuntime below ModelPort; Strix as Integration only.

- API is not the only runtime type; CLI/session is first-class
- Runtime identity is separate from provider API identity; inference vs agent runtime is explicit
- Secret/session material is not ResearchContext, SoR, Evidence, logs, or benchmark reports
- Runtime failure taxonomy includes `CONTENT_POLICY_BLOCKED` as an operational outcome, not Hypothesis rejection
- Research imports no subprocess/provider SDK; argv execution stays in Platform/Integrations
- Strix remains Integration; Core ALLOW is required; denied requests never reach Strix
- Strix outputs remain untrusted; runtime failure creates no Observation/Evidence
- External-agent/MCP requires an explicit capability allowlist
- No new Alembic; head remains `a15_001_exploration_temporal`
- Runtime availability is reported separately and must not be fabricated

**GATE 10 status: PASS** for architecture (2026-08-17) on real PostgreSQL. Deterministic diagnostic path only. No security scanning workflow. GATE 04B remains PENDING unless >=2 real comparable runtime configurations actually execute. GATE 01–09 remain PASS.

**Verify:** unit + contract + `scripts/check_contracts.py` + integration (0 skipped).

## GATE 11 — Runtime Routing Integrity

Research `select_runtime` → Application audit provenance; live discovery may execute GATE 04B if ≥2 ModelRuntime configurations are actually available.

- Hard filters before quality preference; no magic aggregate score
- Role-specific Generator/Falsifier selection is permitted
- Bounded fallback; 0 means none; `CONTENT_POLICY_BLOCKED` does not hop
- Agent runtime is not auto-selected for inference-only roles; unrestricted capabilities rejected
- Routing provenance is audited; no secrets
- Unavailable runtime is never selected
- Strix is not counted as a ModelRuntime
- GATE 04B PASS still requires ≥2 executed comparable live configurations; discovery alone is not PASS
- GATE 11 can PASS while GATE 04B remains PENDING
- No new Alembic; head remains `a15_001_exploration_temporal`

**GATE 11 status: PASS** for architecture (2026-08-17) on real PostgreSQL. GATE 04B remains PENDING unless ≥2 real comparable runtime configurations actually execute. GATE 01–10 remain PASS.

**Verify:** unit + contract + `scripts/check_contracts.py` + `uv run python scripts/run_research_benchmark.py --discover` + integration (0 skipped).

## GATE 12 — Autonomous Orchestration Integrity

`AutonomousResearchController` coordinates existing use cases for one ResearchRun. Autonomous != unbounded.

- Durable `research_orchestration` checkpoint + append-only `research_cycle`
- Explicit states: READY / RUNNING / PAUSED / WAITING_HUMAN / BLOCKED / BUDGET_EXHAUSTED / COMPLETED / FAILED_OPERATIONAL
- Hard bounds; 0 = no allowance; `max_cycles=0` executes none
- PAUSE / RESUME / CANCEL; cancel does not fabricate completed Worker execution
- Restart reloads durable state; DISPATCHING/UNKNOWN_OUTCOME is not blindly retried
- Core still gates execution; Research does not execute; no recursive child-agent spawn
- Finding created is not an orchestration state and does not auto-stop
- New Alembic `a16_001_orchestration_operations` only; a3–a15 are not edited

**GATE 12 status: PASS** (2026-08-17) on real PostgreSQL 18, including process-kill/restart crash matrix at opportunity selected, hypothesis, experiment, authorization requested, AUTHORIZED, DISPATCHING, WorkerResult, Transition A, and Assessment. DISPATCHING remains UNKNOWN_OUTCOME / human-safe reconciliation. Bounds cannot widen after reload. GATE 04B remains PENDING.

**Verify:** unit + contract + integration including `tests/integration/test_gate12.py` and `tests/integration/test_qa_remediation.py` (0 skipped). Do not mark PASS merely because code was edited.

## GATE 13 — Operational Readiness

Hardening from “correct in integration tests” toward operationally survivable diagnostic architecture.

- Append-only `budget_consumption` ledger; IssuedBudget remains the immutable envelope
- SecretPort (`ENV_REFERENCE` / `LOCAL_DEV`); values never SoR/Evidence/ResearchContext/AuditEvent
- Runtime health registry; Strix/Codex supervision without auto-install or token scraping
- `SUBSCRIPTION_OAUTH = NOT_IMPLEMENTED`
- Structured observability (not AuditEvent, not Evidence, not domain truth)
- Bounded reconciliation classifier; side-effectful UNKNOWN remains fail-closed
- Artifact store path/hash/size/atomic write; evidence-linked artifacts are not silently deleted
- DB ops: `scripts/zest_db.py` migrate/version/ping; no SQLite fallback
- Operator view: `zest status` / `scripts/zest_status.py`
- Bounded endurance test with restart midway
- Maturity flags: LIVE_MODEL_VALIDATED=no, SECURITY_RESEARCH_VALIDATED=no, PRODUCTION_READY=no while GATE 04B is PENDING

**GATE 13 status: PASS** (2026-08-17) for diagnostic operational readiness: real PostgreSQL MODEL_CALL budget (including concurrent reservation), clean wheel install from an empty CWD, Windows process-tree timeout cleanup, secret redaction, status DB separation, live dirty-tree provenance, worker diagnostic HEALTHY, missing Codex/Strix UNAVAILABLE, `SUBSCRIPTION_OAUTH=NOT_IMPLEMENTED`, Alembic head `a17_001_qa_remediation`. This does **not** mean production-ready autonomous security research. GATE 04B remains PENDING.

**Verify:** unit + contract + `scripts/zest_status.py status` + `scripts/clean_install_smoke.py` + integration including `tests/integration/test_gate13.py` and `tests/integration/test_endurance.py` (0 skipped). Do not mark PASS merely because code was edited.

## GATE 14 — Authorized Local Security Research E2E

First controlled security-research validation beyond `diagnostic.echo`. Prove the existing authority/evidence/verification/finding pipeline can process a real HTTP security behavior in a deliberately vulnerable **local** lab.

This is **not**:

- a live bug bounty scan
- an internet target
- a Codex/LLM benchmark
- a Strix validation
- proof of autonomous vulnerability discovery
- permission to set `SECURITY_RESEARCH_VALIDATED=true`
- permission to set `PRODUCTION_READY=true`

GATE 04B remains PENDING. GATE 01–13 statuses are unchanged.

Required proof:

- Intentionally vulnerable loopback HTTP lab (`127.0.0.1`, ephemeral port)
- Out-of-process Worker capability `http.authorization.differential` (GET-only, no redirect follow)
- Transition A Observation `HTTP_AUTHORIZATION_DIFFERENTIAL` (behavior only; not Evidence/Candidate/Finding)
- Deterministic Research evaluator (no LLM): admit Evidence only for a full owner/cross-object/control differential
- Independent verification with a fresh Experiment/`request_id`
- Finding only after Human Review + Core Approval
- Secure-control false-positive path admits no security Evidence and no Finding
- Out-of-scope target never reaches the Worker
- Real PostgreSQL via `ZEST_TEST_DATABASE_URL` (skip/PENDING if unset)

**GATE 14 status: PASS** (2026-08-17) on Kali Linux against dedicated real PostgreSQL (`ZEST_TEST_DATABASE_URL`), Alembic head `a18_001_http_auth_class`, `tests.e2e.test_gate14_security_lab` **19 OK / 0 skipped**. Controlled localhost HTTP lab only. No Codex / LLM / Strix.

GATE 14 proves: controlled authorized local security-research pipeline E2E for HTTP authorization differential / BOLA semantics (Worker probe → Transition A Observation → deterministic Evidence admission → Candidate → independent verification → Human Review / Core Approval → Finding, plus secure-control and out-of-scope negatives).

GATE 14 does **not** prove:

- autonomous vulnerability discovery quality
- real-world bug bounty performance
- multi-model live validation
- production readiness
- broad security-research validation

`LIVE_MODEL_VALIDATED=no`, `SECURITY_RESEARCH_VALIDATED=no`, `PRODUCTION_READY=no`. GATE 04B remains PENDING. GATE 01–13 statuses are unchanged.

**Verify:** `python -m unittest tests.e2e.test_gate14_security_lab` on Kali with `ZEST_TEST_DATABASE_URL` set. Do not set `SECURITY_RESEARCH_VALIDATED` or `PRODUCTION_READY` because this gate passed.

## GATE 15 — Security Ground-Truth / False-Positive Benchmark

GATE 14 proved one controlled BOLA/IDOR path end-to-end. GATE 15 tests whether that same pipeline can distinguish true unauthorized access from secure, public, delegated, shared, deceptive, contradictory, timed-out, redirected, and out-of-scope behavior.

This is **not** GATE 04B (live model comparison) and **not** a new security capability. It reuses `http.authorization.differential` and the existing ExperimentPlan → Core → Worker → Transition A → Evidence → Candidate → Verification → Human Review / Finding path.

**GATE 15 status: PASS** (2026-08-17) on Kali Linux against dedicated real PostgreSQL (`ZEST_TEST_DATABASE_URL`), Alembic head `a18_001_http_auth_class`. GATE 14 regression **19 OK / 0 skipped**. GATE 15 ground-truth benchmark `tests.e2e.test_gate15_security_ground_truth` **21 OK / 0 skipped**. Localhost-only security ground-truth lab. No Codex / LLM / Strix.

GATE 15 proves only: controlled multi-scenario ground-truth / false-positive security benchmark passed for HTTP authorization differential semantics.

Preserved benchmark guarantees:

- true BOLA validated
- independent verification required
- `false_finding = 0`
- secure / public / delegated / shared cases produced no Finding
- deceptive 200 / insufficient evidence produced no Finding
- contradictory verification did not VALIDATE
- timeout became INCONCLUSIVE
- redirect boundary was not crossed
- out-of-scope target did not reach Worker
- Human/Core approval remained mandatory
- no ground-truth leakage

GATE 15 does **not** prove autonomous vulnerability discovery quality, real-world bug bounty performance, multi-model live validation, production readiness, or broad security-research validation.

`LIVE_MODEL_VALIDATED=no`, `SECURITY_RESEARCH_VALIDATED=no`, `PRODUCTION_READY=no`. GATE 04B remains PENDING. GATE 14 remains PASS. GATE 01–13 statuses are unchanged.

Primary mission: few correct reproducible findings; **zero false Findings on negative ground truth**.

Hidden evaluation (`expected_class`, canaries, expected promotion) must never enter WorkerRequest, Observation, Evidence evaluator input, Candidate, or Verification.

**Verify:** `python -m unittest tests.e2e.test_gate15_security_ground_truth` on Kali with `ZEST_TEST_DATABASE_URL` set. Do not set `SECURITY_RESEARCH_VALIDATED` or `PRODUCTION_READY` because this gate passed.

## GATE 16 — Workflow / State-Transition Authorization

GATE 14 proved one controlled BOLA/IDOR E2E. GATE 15 proved multi-scenario false-positive discipline for HTTP authorization differential. GATE 16 adds a **second distinct vulnerability class**: workflow / state-transition authorization, plus cross-class discrimination against BOLA.

Classification: `HTTP_STATE_TRANSITION_AUTHORIZATION`. Capability: `http.state_transition`. This class must not be stored as `HTTP_AUTHORIZATION_DIFFERENTIAL`.

Alembic revision `a19_001_http_state_class` extends Candidate/Finding CHECK constraints only. It does not rewrite a3–a18.

**GATE 16 status: PASS** (2026-08-17) on Kali Linux against dedicated real PostgreSQL (`ZEST_TEST_DATABASE_URL`), Alembic head `a19_001_http_state_class`. GATE 14 regression **19 OK / 0 skipped**. GATE 15 regression **21 OK / 0 skipped**. GATE 16 workflow/state-transition benchmark `tests.e2e.test_gate16_state_transition_security` **34 OK / 0 skipped**. Localhost-only synthetic workflow lab. No Codex / LLM / Strix.

GATE 16 proves only: controlled workflow/state-transition authorization semantics plus cross-class discrimination against `HTTP_AUTHORIZATION_DIFFERENTIAL`.

GATE 16 does **not** prove autonomous vulnerability discovery quality, real-world bug bounty performance, multi-model live validation, production readiness, or broad security-research validation.

`LIVE_MODEL_VALIDATED=no`, `SECURITY_RESEARCH_VALIDATED=no`, `PRODUCTION_READY=no`. GATE 04B remains PENDING. GATE 14 remains PASS. GATE 15 remains PASS. GATE 01–13 statuses are unchanged.

**Verify:** `python -m unittest tests.e2e.test_gate16_state_transition_security` on Kali with `ZEST_TEST_DATABASE_URL` set. Do not set `SECURITY_RESEARCH_VALIDATED` or `PRODUCTION_READY` because this gate passed.

## GATE 17 — Autonomous Multi-Hypothesis Research Selection

GATE 14 proved one controlled BOLA/IDOR E2E. GATE 15 proved false-positive / ground-truth discipline for `HTTP_AUTHORIZATION_DIFFERENTIAL`. GATE 16 proved a second class, `HTTP_STATE_TRANSITION_AUTHORIZATION`, plus cross-class discrimination.

GATE 17 is the first explicit validation that Zest can connect observations and decide what to investigate next: competing hypotheses, experiment options, lexicographic selection, Core authorization, ingestion, assessment append, and a changed next decision. It reuses only `http.authorization.differential` and `http.state_transition`. It does not add a third vulnerability class, scanners, crawlers, fuzzers, shell, or arbitrary HTTP.

**GATE 17 status: PASS** (2026-08-17), authoritative tested commit `48d807d`, on Kali Linux against dedicated real PostgreSQL (`ZEST_TEST_DATABASE_URL`). GATE 14 regression **19 OK / 0 skipped**. GATE 15 regression **21 OK / 0 skipped**. GATE 16 regression **34 OK / 0 skipped**. GATE 17 autonomous research-selection benchmark `tests.e2e.test_gate17_autonomous_research_selection` **57 OK / 0 skipped**. Repo-wide: unit **586 OK**, contract **2 OK**, architecture **15 OK**, integration **112 OK**. No Codex / LLM / Strix. No Alembic migration. 0 `psycopg.Connection` ResourceWarnings.

GATE 17 proves only: Controlled local multi-hypothesis closed-loop research selection and adaptive experiment choice were validated against the dedicated real PostgreSQL test database with truth-blind benchmark execution.

GATE 17 does **not** prove general autonomous vulnerability discovery, real-world bug bounty performance, live model quality, or production readiness.

No Alembic migration is expected or added. Selection traces reuse `research_opportunity`, `research_selection`, `hypothesis_assessment`, `research_cycle`, `research_orchestration`, and audit events.

`LIVE_MODEL_VALIDATED=no`, `SECURITY_RESEARCH_VALIDATED=no`, `PRODUCTION_READY=no`. GATE 04B remains PENDING. GATE 14/15/16/17 remain PASS.

**Verify:** `python -m unittest tests.e2e.test_gate17_autonomous_research_selection` on Kali with `ZEST_TEST_DATABASE_URL` set. Do not set `SECURITY_RESEARCH_VALIDATED` or `PRODUCTION_READY` because this gate passed.

## GATE 18 — Offensive Substrate Foundation

GATE 14–17 proved controlled local security-research pipelines, ground-truth / false-positive discipline, a second authorization class, and adaptive multi-hypothesis selection. GATE 18 does not add offensive breadth. It makes capability, risk, scope, and execution **authoritative, typed, durable, and fail-closed** so later HTTP/identity/browser/recon capabilities cannot copy an authorization model or understate risk.

Canonical Worker capability registry contains only:

- `diagnostic.echo`
- `http.authorization.differential`
- `http.state_transition`

Codex remains ModelPort. Strix remains Integration. Neither compiles into a Worker `ExperimentPlan`. Core independently checks capability/action/version/fingerprint/risk. The Worker independently rejects mismatched definitions. Legacy NULL plans are never silently fingerprint-backfilled. Scope remains exact-host / default-deny / fail-closed for path ambiguity. Redirects require reauthorization.

Alembic revision `a20_001_capability_plan_binding` adds nullable `capability_version` and `capability_definition_fingerprint` on `experiment_plan` only. No silent backfill.

**GATE 18 status: PASS** (2026-08-17), authoritative tested commit `241e901fb2c6730ee293cca71942de45d3796282`, on Kali Linux against dedicated real PostgreSQL (`ZEST_TEST_DATABASE_URL`), Alembic head `a20_001_capability_plan_binding`. Migration round-trip `a19 → a20`, `a20 → a19`, `a19 → a20` validated. GATE 14 regression **19 OK / 0 skipped**. GATE 15 regression **21 OK / 0 skipped**. GATE 16 regression **34 OK / 0 skipped**. GATE 17 regression **57 OK / 0 skipped**. Repo-wide: unit **627 OK**, contract **2 OK**, architecture **20 OK**, integration **117 OK**. No Codex / LLM. No external-network research. No `psycopg` ResourceWarning.

GATE 18 PASS means Zest can transform an admitted research intent into a typed, per-action capability-bound and scope-evaluated experiment whose risk level and capability definition are independently verified by Core, durably bound across restart, and independently rejected by the Worker if its executable definition does not match.

GATE 18 does **not** prove autonomous vulnerability discovery, broad security-research capability, real-world bug bounty performance, live model quality, production readiness, crawler/browser/recon capability, or XBOW/Edra parity.

`LIVE_MODEL_VALIDATED=no`, `SECURITY_RESEARCH_VALIDATED=no`, `PRODUCTION_READY=no`. GATE 04B remains PENDING. GATE 14/15/16/17/18 remain PASS.

**Verify:** Kali + dedicated PostgreSQL + `ZEST_TEST_DATABASE_URL`. Do not set `SECURITY_RESEARCH_VALIDATED` or `PRODUCTION_READY` because this gate passed.

## GATE 19 — Authorized HTTP Substrate

GATE 18 made capability, risk, scope, and execution authoritative. GATE 19 adds the first general HTTP transaction capability on that substrate: typed, capability-bound, Core-authorized experiments with bounded methods, paths, queries, headers, and bodies. Exact-host scope, redirect reauthorization, capability fingerprint enforcement, and Worker execution bounds remain unchanged.

**GATE 19 status: PASS** (2026-08-17). Implementation commit `95c88bc`. Authoritative tested HEAD `b442a672a7df86482d0f5a60eb156483b691d44c`. Kali Linux against dedicated real PostgreSQL (`ZEST_TEST_DATABASE_URL`). Alembic head `a21_001_session_context`. Repo-wide: unit **676 OK**, contract **2 OK**, architecture **22 OK**, integration **120 OK**. Explicit G18 plan binding **5 OK**. Explicit G19 HTTP transaction **2 OK**. GATE 14 regression **19 OK / 0 skipped**. GATE 15 regression **21 OK / 0 skipped**. GATE 16 regression **34 OK / 0 skipped**. GATE 17 regression **57 OK / 0 skipped**. No Codex / LLM. No G21. No skipped tests treated as PASS.

GATE 19 PASS means Zest can construct and execute typed, capability-bound, Core-authorized general HTTP experiments using bounded request methods, paths, queries, headers and bodies, while preserving exact scope evaluation, redirect reauthorization, capability fingerprint enforcement and Worker execution bounds.

GATE 19 does **not** prove autonomous endpoint discovery, crawler/recon capability, browser automation, arbitrary internet HTTP, broad vulnerability discovery, real-world bug bounty performance, or production readiness.

Current limitations: loopback HTTP substrate only; no HTTPS breadth claim; no crawler; no automatic endpoint discovery; no browser state; HTTP transaction does not by itself imply vulnerability semantics.

`LIVE_MODEL_VALIDATED=no`, `SECURITY_RESEARCH_VALIDATED=no`, `PRODUCTION_READY=no`. GATE 04B remains PENDING. GATE 20 remains PENDING at this closure. GATE 14/15/16/17/18/19 remain PASS.

**Verify:** Kali + dedicated PostgreSQL + `ZEST_TEST_DATABASE_URL`. Do not set `SECURITY_RESEARCH_VALIDATED` or `PRODUCTION_READY` because this gate passed.

## GATE 20 — Identity Authentication Session

GATE 19 added bounded authorized HTTP transactions. GATE 20 adds identity/session isolation on that substrate: explicitly configured identities, one bounded auth profile (`HTTP_FORM_LOGIN`), session metadata in the research state, and process-local session material that is never stored as raw credential, cookie, or token in the system of record. Session ID alone is not authority. Missing session secret after restart requires reauthentication.

**GATE 20 status: PASS** (2026-08-17). Implementation commit `e574306`. Authoritative tested HEAD `b442a672a7df86482d0f5a60eb156483b691d44c`. Kali Linux against dedicated real PostgreSQL (`ZEST_TEST_DATABASE_URL`). Alembic head `a21_001_session_context`. Repo-wide: unit **676 OK**, contract **2 OK**, architecture **22 OK**, integration **120 OK**. Explicit G18 plan binding **5 OK**. Explicit G19 HTTP transaction **2 OK**. Explicit G20 identity/session **1 OK**. GATE 14 regression **19 OK / 0 skipped**. GATE 15 regression **21 OK / 0 skipped**. GATE 16 regression **34 OK / 0 skipped**. GATE 17 regression **57 OK / 0 skipped**. No Codex / LLM. No G21. No skipped tests treated as PASS.

GATE 20 PASS means Zest can establish and isolate authenticated sessions for explicitly configured identities and execute authorized HTTP experiments under the correct identity/session context without storing raw credential or session material in the authoritative research state.

GATE 20 does **not** prove autonomous account discovery, browser authentication, arbitrary authentication mechanisms, durable session-secret recovery after restart, autonomous vulnerability discovery, real-world bug bounty performance, or production readiness.

Current limitations: `HTTP_FORM_LOGIN` is the bounded supported auth profile; session material is process-local/non-durable; missing session secret after restart requires reauthentication; session ID alone is not authority; raw password/cookie/token is not stored in SoR; no browser login; no G21 behavior.

`LIVE_MODEL_VALIDATED=no`, `SECURITY_RESEARCH_VALIDATED=no`, `PRODUCTION_READY=no`. GATE 04B remains PENDING. GATE 14/15/16/17/18/19/20 remain PASS.

**Verify:** Kali + dedicated PostgreSQL + `ZEST_TEST_DATABASE_URL`. Do not set `SECURITY_RESEARCH_VALIDATED` or `PRODUCTION_READY` because this gate passed.

---

## Research Brain (Research capability — not Core)

A7 v1 delivers the bounded reasoning cycle (Decisions 025–026). It is not a graph/database product and not required to declare the first working implementation (Human Review loop) done.

GATE 08 delivered diagnostic invariant mining and bounded diagnostic chain composition. GATE 09 delivered bounded exploration/exploitation selection and diagnostic Temporal Intelligence. Later Research Brain work remains architecturally capable of:

- non-diagnostic invariant kinds (hypothesis ≠ fact)
- broader chain search (N2) beyond diagnostic plumbing
- model-proposed opportunities (still untrusted; policy still selects)
- independent verification (Decision 017; not a required second model vendor in v1)
- counter-hypothesis / disconfirming evidence
- negative evidence that stays **context-bound**

It must not reduce to “ask the LLM for vulnerability ideas” or `LLM → tool → LLM`.

Exploration remains Core-authorized: no scope, budget, or side-effect bypass. There is no autonomous infinite loop.

---

## Advanced Research (later than first working implementation)

After the first Human Review loop exists and metrics can be read from the SoR:

- chain search (N2)
- temporal prioritization beyond diagnostic snapshots
- persistent exploration-specific budget ledger beyond IssuedBudget consumption
- novelty / information-gain factors (conceptual; no fake formula)
- duplicate reduction (duplicate semantics still an open domain question)
- empirical calibration of claims (Decision 018 anti-hype metrics)

These stay in **Research** (plus Data records and ObservabilityPort aggregates). Do not move them into Core.
