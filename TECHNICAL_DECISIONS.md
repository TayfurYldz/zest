# Zest — Technical Decisions

This file records explicit technical decisions.

It does not replace `.cursor/rules/zest.mdc`, `PROJECT_STRUCTURE.md`, `DOMAIN_MODEL.md`, or `TECHNICAL_REQUIREMENTS.md`.

Decisions must stay inside those documents. A language choice does not choose a framework, database, orchestrator, broker, or provider.

---

# Decision 001 — Primary Programming Language

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Confidence:** MEDIUM

Primary language for the first implementation of Core, Research, Data access, Platform control-plane code, and Interface/application logic:

**Python**

Python is the primary first-implementation language.

That does **not** mean:

- every Worker must be Python
- every Integration must be Python
- performance-critical components must stay Python

Core/Research contract boundaries stay language-independent.

No web framework, CLI framework, ORM, database, orchestrator, or serialization format is chosen here.

Windows + Cursor and Kali/WSL are current development context. They are used only to check portability/integration. They are not the reason Python is selected.

This decision must remain valid even if deployment later moves entirely to Linux or remote workers.

---

## Gate then differentiate

`TECHNICAL_REQUIREMENTS.md` decision-driver order is not changed. Correctness and authorization/security boundaries are not ranked below simplicity.

### Stage 1 — Mandatory gates

A language is eliminated if it cannot support:

- correctness
- authorization/security boundaries
- durable state integrity
- auditability
- explicit contracts

Python, TypeScript/Node.js, Go, and Rust can all pass Stage 1. None is eliminated for lack of capability.

Go provides stronger compile-time enforcement for some correctness/security properties.

Python meets the same gates when the constraints in this decision are enforced:

- explicit domain boundaries
- language-neutral schemas
- runtime validation
- tests
- static analysis where practical

### Stage 2 — Differentiators among viable options

Among languages that pass Stage 1:

- developer simplicity
- iteration speed
- ecosystem fit
- maintainability
- operational simplicity

The Python vs Go difference is a Stage 2 difference, not a Stage 1 failure of Go.

---

## Language-neutral contracts

Cross-boundary contracts are not Python classes as architectural truth.

Contracts must be:

- language-neutral
- explicit
- versionable
- machine-validatable
- serializable/transport-neutral in principle

Python types/classes may be one implementation representation of those contracts.

A later Go Worker, Rust Worker, or TypeScript integration must be able to implement the same boundaries.

No serialization format is chosen here.

---

## Python

### Strengths

- Faster Research/domain iteration on a provenance-heavy model (Program, Evidence, Candidate, FindingProposal, lifecycles).
- Richer LLM tooling ecosystem for untrusted structured proposals.
- More convenient experimentation for a single developer.
- Lower first-implementation friction.
- Less initial glue for common security/AI workflows (wrappers and examples around CLI tools and model clients).
- Workers in another language remain possible because Core must talk to contracts, not in-process tools.

### Weaknesses

- Fewer compiler-enforced guarantees than Go, TypeScript, or Rust.
- Weaker default concurrency for CPU-bound work (GIL); mixed I/O/CPU in the control plane needs care.
- Long-running process hygiene (memory growth, dependency drift) needs explicit operational care.
- Easy to bury Core policy in scripts or tool helpers if constraints are not enforced.
- Runtime errors that a compiler would reject can reach execution if schemas/tests are thin.

### Project-specific fit

- Passes Stage 1 if documented Core constraints are enforced.
- Wins Stage 2 for this project’s first implementation: Research/domain iteration, LLM experimentation convenience, and lower single-developer glue.
- Python’s advantage is **not** unique capability. Go can call model/provider APIs, exec tools, and run the control plane without Python.

### Risks

- Core invariants (DEFAULT DENY, budget immutability, Transition A vs B) weaken if boundaries are informal.
- Packaging/deployment:
  - interpreter/version consistency
  - dependency pinning
  - host vs WSL environment drift
  - native dependency complications
  - reproducible environment creation
  - deployment artifact consistency
- These packaging concerns are later technical decisions; no package manager is chosen here.
- LLM client libraries can pull Core/Research toward vendor-shaped APIs unless providers stay behind replaceable ports.
- If deterministic normalization/ingestion (Transition A) becomes CPU-heavy or throughput-critical, that component may move behind a language-neutral contract to Go, Rust, or another runtime. This primary-language decision does not block that.

---

## TypeScript / Node.js

### Strengths

- Excellent static contract ergonomics.
- Strong application/dashboard ecosystem.
- Good async I/O model for a control plane that waits on model calls and worker results.
- Cross-platform on Windows and Linux is solid.
- Capable of calling model APIs and executing subprocess tools; it is not “weak for backend.”

### Weaknesses

- Security/research Python ecosystem is a weaker match for first-phase Worker/tool adapters.
- External CLI/security-tool integration has less operator ergonomics than Python.
- Research/AI experimentation libraries are capable but less convenient than Python for this domain.
- Runtime and packaging (Node version, native addons) add operational surface.

### Project-specific fit

- Passes Stage 1, with especially strong contract ergonomics.
- Not primary because Stage 2 research/security-tool and AI-experimentation fit is weaker than Python for this project’s first loop.
- A later dashboard/Interface in TypeScript remains possible and is not decided here.

### Risks

- Using TypeScript as primary does not require a Python sidecar. It would cost more glue for common security/AI workflows than Python.

---

## Go

### Strengths

- Stronger compile-time guarantees.
- Simpler deployment artifact (static binary).
- Predictable long-running service behavior.
- Strong concurrency primitives.
- Lower runtime operational complexity.
- Good fit for Core/policy/control-plane components.
- Good subprocess and network execution support.
- Can call model/provider APIs directly.
- Can execute subprocess-based tools.
- Can integrate with Strix through contracts/adapters.
- Can implement the entire control plane without Python.

### Weaknesses

- Slower first-loop Research/domain iteration for this project’s current team/context.
- Less convenient AI/research experimentation ecosystem.
- More implementation ceremony for a rapidly evolving Research domain.
- Security-tool wrapping is mostly “exec the binary,” with fewer high-level examples than Python.

### Project-specific fit

- Passes Stage 1. Strongest of the four as a pure control-plane language.
- Not selected as primary because of Stage 2: slower Research/domain iteration and less convenient AI/security-workflow experimentation.
- Go is not missing capability. Python is chosen because it also satisfies mandatory gates **if the documented constraints are enforced**, and it is more productive for the first Zest implementation.

### Risks

- If chosen as primary, delivery of the hypothesis → experiment → WorkerResult → Evidence admission loop would likely be slower, not impossible.
- Ad-hoc helper scripts in another language would still be a discipline problem, as with Python.

---

## Rust

### Strengths

- Strongest memory and type safety among the candidates.
- Excellent isolation/performance potential.
- Useful future Worker/runtime candidate.
- Explicit types and enums express authorization/state machines well.

### Weaknesses

- Highest iteration cost and complexity for one developer on a large domain model.
- Development velocity is the lowest of the four for v1.
- Ecosystem friction for Research/LLM experimentation.
- Everyday control-plane iteration on mixed Windows/Linux hosts is heavier.

### Project-specific fit

- Passes Stage 1.
- Not primary for first implementation because of iteration cost, complexity, velocity, and Research/LLM ecosystem friction.
- Appropriate later for a Worker/runtime if isolation or throughput triggers appear.

### Risks

- Delivery stall; temptation to over-abstract.
- Control-plane memory safety does not replace Worker isolation or Core policy.

---

## Comparison Matrix

Scores are 1 (worse project fit) to 5 (better project fit) for this project’s first implementation. Higher is always better.

| Criterion | Python | TypeScript / Node.js | Go | Rust | Why |
| --- | --- | --- | --- | --- | --- |
| 1. correctness | 3 | 4 | 4 | 5 | All four can implement invariants. Go/TS/Rust get more compile-time help. Python relies on schemas, tests, and analysis. |
| 2. security boundary implementation | 3 | 3 | 4 | 5 | Boundaries are architectural. Go/Rust make accidental in-process sharing harder. Python/TS rely more on process isolation plus constraints. |
| 3. explicit / machine-validatable contracts | 3 | 5 | 4 | 5 | TS has the best contract ergonomics. All four can validate language-neutral schemas. Python’s compiler will not. |
| 4. domain model expressiveness | 5 | 4 | 3 | 3 | Nested lifecycles and optional provenance iterate fastest in Python. |
| 5. testability | 4 | 4 | 4 | 4 | All four can unit-test Core/Research. |
| 6. AI / LLM ecosystem convenience | 5 | 4 | 3 | 2 | Convenience, not capability. Go/TS can call model APIs. Python has richer experimentation libraries. |
| 7. security-tool wrapping convenience | 5 | 3 | 4 | 3 | All can exec CLI tools. Python has more existing wrappers/examples. Go’s subprocess support is strong. |
| 8. local/remote worker portability | 4 | 4 | 5 | 3 | Any language can talk to replaceable workers. Go binaries travel cleanly. This row is not a Kali-mandates-Python score. |
| 9. subprocess / external tool integration | 4 | 3 | 5 | 4 | Go and Python are both strong at subprocess control. |
| 10. async / concurrency model | 3 | 4 | 5 | 4 | Go is simplest for mixed concurrency. Python is weaker for CPU-bound control-plane work. |
| 11. long-running control-plane suitability | 3 | 4 | 5 | 4 | Go is built for this. Python works with operational care. |
| 12. cross-platform Windows/Linux development | 4 | 4 | 5 | 3 | Portability check only. Go is simplest to ship across hosts. |
| 13. library ecosystem convenience | 5 | 4 | 4 | 3 | Python reduces first-loop glue for research/AI workflows. |
| 14. observability support | 4 | 4 | 5 | 4 | All can emit structured telemetry. Go’s long-running service culture is strongest. |
| 15. developer simplicity | 5 | 4 | 4 | 2 | Stage 2. One developer, local first: Python has the lowest coordination cost. |
| 16. implementation speed | 5 | 4 | 3 | 1 | Stage 2. First Evidence/FindingProposal loop is fastest in Python. |
| 17. maintainability | 3 | 4 | 5 | 4 | Unchecked Python degrades. Go stays readable. TS stays maintainable if types stay strict. |
| 18. performance | 3 | 3 | 4 | 5 | Not a Stage 1 differentiator. Workers may move later. |
| 19. operational simplicity | 3 | 3 | 5 | 4 | Higher is simpler operations. Go’s single binary scores highest. Python/TS need a managed runtime. |
| 20. future move of hot components | 5 | 4 | 4 | 3 | Language-neutral contracts make Python-primary + later Go/Rust workers straightforward. |

No additional language was added.

---

## Decision

**ACCEPT WITH CONSTRAINTS**

**Primary language: Python**

Python is selected because it satisfies the mandatory architecture/security gates and provides the best current first-implementation productivity for Zest.

Go remains a credible alternative, especially for Core/control-plane/runtime components.

Go is not rejected for missing capability. It can call model/provider APIs, execute subprocess tools, integrate Strix through adapters, and implement the entire control plane without Python.

Python’s v1 advantage is Stage 2:

- faster Research/domain iteration
- richer LLM tooling ecosystem
- more convenient experimentation
- lower single-developer implementation friction
- less initial glue for common security/AI workflows

The decision is ACCEPT WITH CONSTRAINTS rather than unconditional ACCEPT because Python provides fewer compiler-enforced guarantees and carries packaging/concurrency risks.

This is not “Python because AI,” “Python because Windows/Kali,” or “simplicity outranks security.”

---

## Constraints

Python is the primary first-implementation language for Core, Research, Data access, Platform control-plane logic, and application/Interface code.

Workers and Integrations may be another language. Performance-critical or isolation-critical components may move behind language-neutral contracts (Transition A / WorkerResult). They must not be inlined into Core.

Until a later recorded decision splits them, Core and Research stay in the primary language **and** must obey:

- public boundaries validated
- no raw dict-shaped domain contracts at critical boundaries
- explicit state transitions
- no hidden mutable global state
- no policy logic inside scripts
- no subprocess calls from Core/Research
- no provider/tool SDK dependency in Core/Research
- tests for domain invariants
- static analysis/type checking where practical
- runtime validation at trust boundaries

No library or framework is named here.

Python types are not architectural contracts. Contracts remain language-neutral.

Python does not create vendor lock-in by itself. Lock-in appears only if Core/Research depend on a specific provider SDK or tool. That remains forbidden.

---

## Rejected Alternatives

- **Go:** Credible Stage 1 option and often stronger for Core/control-plane compile-time guarantees, deployment artifact, concurrency, and long-running behavior. Not primary today because of slower first-loop Research/domain iteration, less convenient AI/research experimentation, and more ceremony for a rapidly evolving Research domain—while Python still meets mandatory gates under the constraints above.
- **TypeScript / Node.js:** Excellent static contract ergonomics, strong application/dashboard ecosystem, good async I/O, and a capable backend. Not primary because security/research-tool and Research/AI experimentation ergonomics are a weaker Stage 2 fit than Python. Not rejected as “weak for backend.”
- **Rust:** Strongest memory/type safety and a strong future Worker/runtime candidate. Not primary because of iteration cost, complexity, velocity, and Research/LLM ecosystem friction for first implementation.

---

## Revisit Triggers

Reopen this decision only with measured evidence in these categories (no numeric thresholds invented):

- repeated Core invariant bugs despite validation/tests
- control-plane concurrency bottleneck
- CPU-bound deterministic ingestion / Transition A becoming throughput-critical
- memory pressure in the long-running control process
- deployment/packaging instability (interpreter drift, pinning, host vs WSL, native deps, irreproducible environments)
- worker throughput constrained by the control plane rather than by tool/network budget
- isolation requirements that favor another runtime
- cross-platform runtime drift
- operational burden exceeding expected simplicity

If Transition A becomes CPU-heavy or throughput-critical, that component may move behind a language-neutral contract to Go/Rust/another runtime without reversing Python as the primary control-plane language unless other triggers also fire.

---

## Confidence

**MEDIUM**

Python satisfies Stage 1 with constraints and wins Stage 2 for first-implementation productivity. Confidence is not HIGH because Python provides fewer compiler-enforced guarantees than Go and carries packaging/concurrency risks. Go remains the leading alternative if revisit triggers appear.

---

# Decision 002 — Primary Data Strategy / Database Paradigm

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Confidence:** MEDIUM

Primary data strategy for the first implementation:

**Relational primary system of record, with rebuildable read models and optional companion stores later.**

This is a paradigm decision. It does not choose a database product, ORM, cache, vector store, graph product, object store, or search product.

It does **not** mean:

- AssetRelation exists → a graph database is required
- flexible metadata → a document database is required
- AuditEvent / immutable Evidence exist → the whole domain must be event-sourced
- AI exists → a vector database is required
- artifacts exist → an object store is required immediately
- relational primary → all data lives in one relational product forever
- a companion store is a second source of truth
- a companion index may override the System of Record
- flexible JSON/schemaless payload may hold authority truth
- Evidence, AuditEvent, or recorded Approval may be edited or rewritten in place
- relational paradigm → a specific database product

---

## System of Record vs read models vs companions

### System of Record

The single authoritative strategy for domain truth.

Authoritative here: Program, AuthorizationSource, ScopeRule, ResearchRun, Budget, Asset, AssetRelation, Observation, Hypothesis, Experiment, WorkerResult (durable untrusted capture), Artifact identity/reference and integrity/lifecycle metadata, Evidence, Candidate, Verification records, FindingProposal, Finding, Approval, Snapshot, ChangeEvent, AuditEvent.

WorkerResult is durable capture, not Observation/Evidence/Finding truth. Research Memory is not in this set.

### Read models

Rebuildable views over the System of Record: Research Memory retrieval, dashboard views, temporal slices, search projections.

A read model that cannot be rebuilt from the System of Record is a design error.

### Companion stores

Later, optional capabilities for full-text search, semantic/vector retrieval, graph acceleration, analytics, or artifact bytes.

Every derived companion index/read model must be rebuildable from the authoritative System of Record. This includes:

- search indexes
- semantic/vector indexes
- graph projections
- dashboard projections
- Research Memory projections
- temporal/materialized read models

A companion store never becomes authoritative truth automatically. If it disagrees with the System of Record, the companion is corrected or rebuilt. The System of Record is not changed to match the companion. The companion is never the authoritative conflict winner.

**Exception:** Artifact bytes may sit outside this rebuild-from-SoR rule because artifact byte-storage topology is a separate technical decision. Artifact metadata, reference, and provenance remain authoritative in the System of Record.

Artifact byte-storage topology/product is **not** decided here. Semantic/vector, search, graph companion, and analytics strategies are **not** decided here.

---

## Gate then differentiate

`TECHNICAL_REQUIREMENTS.md` decision-driver order is not changed.

### Stage 1 — Mandatory gates

A strategy is eliminated if it cannot support:

- domain integrity
- authorization/security consistency
- durable authoritative truth
- auditability/provenance
- explicit lifecycle semantics

Especially: Approval and Finding creation must not contradict; Evidence provenance must hold; Audit history must be reconstructable; Transition A and Transition B must remain distinct.

### Stage 2 — Differentiators among viable options

- developer simplicity
- operational simplicity
- query ergonomics
- evolution
- future extensibility
- performance profile

---

## Immutability model

Not every record is immutable.

**Mutable lifecycle records** (current state, with history via AuditEvent / new rows where needed):

- ResearchRun
- Experiment
- Hypothesis belief/status
- Candidate lifecycle
- FindingProposal lifecycle

**Append / immutable-oriented records** (do not rewrite in place as truth):

- WorkerResult raw capture
- Artifact integrity metadata
- Evidence
- AuditEvent
- historical Snapshot
- recorded Approval decision

Evidence, AuditEvent, and recorded Approval decision:

- must not rewrite history via in-place mutation
- must add a new record/event when a correction is required
- must keep the old record as history

In-place mutation of Evidence, AuditEvent, or recorded Approval is a failed invariant.

This invariant must later be enforced by store-level constraints, application tests, and persistence rules. How it is enforced, and which database feature is used, is not chosen here.

The primary paradigm must make both kinds natural: constrained updates for lifecycles, and insert-only new records for Evidence, AuditEvent, recorded Approval, and historical Snapshots. A strategy that can only do one of these poorly fails Stage 1 or pays a large Stage 2 tax.

### Flexible metadata

Flexible/schemaless metadata fields may be used only for secondary/extensible attributes.

They must not authoritatively embed:

- authorization state
- scope authority
- approval state
- lifecycle state
- promotion state
- budget authority
- finding acceptance state
- evidence identity/provenance
- core policy decisions

Those fields must be modeled as explicit first-class domain fields/records.

Flexible metadata must never become a second hidden domain schema.

---

## Consistency boundaries

These transitions must be representable as one authoritative decision, or as an explicit, auditable two-phase protocol with a single source of truth. Product-level isolation levels are not chosen here.

1. **AuthorizationSource + ScopeRule** — effective scope is Core-evaluated; stored rules and source state must not silently diverge.
2. **ResearchRun + Budget consumption** — remaining budget and run state must not disagree after an authorized execution.
3. **Experiment lifecycle** — execution states (EXECUTION_SUCCEEDED / FAILED / BLOCKED / CANCELLED / BUDGET_EXHAUSTED) must not be collapsed into Hypothesis outcomes.
4. **Transition A** — Observation and/or Artifact metadata created from a WorkerResult without Evidence promotion.
5. **Evidence admission (Transition B)** — Observation/Artifact exist first; Evidence is a separate admitted record.
6. **Candidate lifecycle** — only Candidate state is authoritative; Verification records are episodic proposals.
7. **Verification process records** — append/episodic; they do not commit Candidate state by themselves.
8. **Candidate VALIDATED → FindingProposal** — proposal cannot exist without VALIDATED.
9. **Human Review → Core Approval** — Approval is the decision record; FindingProposal APPROVED is the same event’s domain view, not a second authority.
10. **Approval → Finding creation** — Finding exists only after Core Approval APPROVE. If Approval is recorded and Finding insert fails, the System of Record must not present an approved proposal as an accepted Finding. Prefer one atomic decision that writes Approval + Finding together, or a recorded incomplete-promotion state that is retryable and auditable. Silent split-brain is forbidden.
11. **AuditEvent** — append-oriented; corrections are new events.
12. **Snapshot / ChangeEvent** — historical; ChangeEvent is derived fact only when the comparator is deterministic.

Partial failure of (10) is the sharpest test: transactional multi-record update where one decision touches Approval and Finding, or an explicit recovery record. This is why “one System of Record that can update multiple related records together” is a Stage 1 concern.

---

## Relational primary

### Strengths

- Natural fit for distinct, linked domain records and integrity constraints.
- Transactional consistency for authority transitions (Approval + Finding, run + budget, VALIDATED + FindingProposal).
- Lifecycle/state columns plus constraints can encode legal transitions without making every change an event.
- Provenance as explicit links (run, source, artifact reference, evidence set), not only nested blobs.
- Joins and indexing support historical Snapshot/ChangeEvent queries and audit reconstruction.
- Mature schema-migration culture at the paradigm level (not a product choice).
- Flexible metadata can exist as constrained structured fields plus limited schemaless payload for **secondary** attributes only, without switching the System of Record to document-primary. Authority, lifecycle, promotion, budget, approval, finding acceptance, evidence identity/provenance, and policy decisions stay first-class fields/records.
- Research Memory can read this store; it need not copy truth.

### Weaknesses

- Deep/recursive graph traversal (AssetRelation, attack-surface walks) can become awkward if it dominates the workload.
- Semantic retrieval is not native; that is a companion trigger, not a reason to abandon the SoR.
- Large analytics or event/snapshot volumes may later need companions.
- Poor modeling of sparse/evolving fields can make tables cumbersome; that is a modeling failure, not an automatic document-DB requirement.

### Project-specific fit

- Passes Stage 1 for this domain’s many distinct concepts and multi-record authority transitions.
- Graph-shaped AssetRelation can be stored as relation records in a relational SoR; graph-shaped domain ≠ graph-primary SoR.
- Does not, by itself, choose PostgreSQL, SQLite, or any other product.

### Risks

- Treating JSON/flexible columns as an unbounded second schema, or embedding authorization, lifecycle, approval, promotion, budget, or evidence identity in schemaless payload.
- Putting Research Memory or search indexes in the SoR as if they were truth.

---

## Document primary

### Strengths

- Flexible evolving records and natural aggregates (for example a Candidate document with nested notes).
- Easy local variation of metadata shape.
- Some implementations can transact; this evaluation does not claim “document stores cannot transact.”

### Weaknesses

- Authority transitions cross aggregates: Approval, FindingProposal, Candidate, Evidence, ResearchRun. Document-primary modeling pushes either huge documents or multi-document transactions that recreate relational integrity by hand.
- Provenance is relational (many-to-many Evidence ↔ Candidate, Artifact ↔ Observation). Nested copies duplicate truth or lose links.
- Invariant enforcement (VALIDATED before FindingProposal; Evidence required for Finding) is easier to weaken when documents are the unit of write.
- Duplication and divergent nested copies are a real paradigm risk, not a product insult.

### Project-specific fit

- Useful for some aggregates; weak as the *only* System of Record for this authority-heavy domain.
- Flexible metadata is a real need; it does not require document-primary SoR.

### Risks

- Candidate/Finding/Approval consistency becomes an application-level distributed transaction across documents.
- Research Memory projections drift from nested copies that cannot be rebuilt cleanly.

---

## Graph primary

### Strengths

- AssetRelation, provenance walks, attack-surface exploration, and relationship-heavy authorization questions map well to graph traversal.
- Explicit edges can make “inferred vs observed vs derived” visible as edge types.

### Weaknesses

- System-of-record lifecycle transactions, structured audit rows, and state-machine-heavy entities are not what graph-primary stores optimize as the *only* SoR.
- Schema/evolution, local simplicity, and operational overhead are typically worse for a single developer than a relational SoR plus later graph acceleration.
- Append-oriented Evidence/Audit and Approval→Finding atomicity are less natural as graph-only writes.

### Project-specific fit

- Strong as a *companion* if relationship traversal dominates.
- Not Stage 1–best as the sole System of Record. Graph-shaped queries can be answered from relational relation records until a trigger says otherwise.

### Risks

- Confusing traversal convenience with authoritative lifecycle semantics.
- Dual-writing graph + tables from day one without a single SoR.

---

## Event-sourced primary

### Strengths

- Append-only history, explicit transitions, temporal reconstruction, and strong audit narratives.

### Weaknesses

- Projections become the working model; debugging and event-schema evolution are expensive for one developer.
- Ad-hoc querying of “current Candidate state” depends on projections that can lag or drift.
- Event/model drift: forgotten events, poisoned replays, versioned payloads.
- Eventual consistency of views fights “Approval recorded iff Finding exists” unless the SoR *is* the event log *and* projections are strictly derived—which is a large operational bet.

### Project-specific fit

- **AuditEvent and immutable Evidence do not require event-sourcing the whole domain.** They require append/immutable *records* inside the SoR.
- Audit trail ≠ event sourcing. Zest needs reconstructable audit and immutable Evidence, not a mandatory event log as the only truth.

### Risks

- Building Research Memory as a projection farm that is mistaken for truth.
- Single-developer burden of replay, upcasters, and “which projection is current.”

---

## Polyglot-from-start

Relational + graph + vector + object + event stores from day one.

### Strengths

- Each workload could use a specialized engine later.

### Weaknesses

- Synchronization, which store is truth, operational and local-dev burden, and migration cost are high before any measured trigger.
- Source-of-truth ambiguity is a Stage 1 hazard: companions start being treated as authoritative.
- Conflicts with preferred first-implementation simplicity and “do not introduce a distributed plane until topology requires it.”

### Project-specific fit

- Fails Stage 2 badly; risks failing Stage 1 via split-brain (search/graph/vector vs SoR).
- Not selected. Companions remain allowed *later*, when triggered.

### Risks

- Dual writes; Research Memory or vector index becoming a shadow database.

---

## Relational primary + optional companions later

### Strengths

- One Stage 1 System of Record for integrity, transactions, provenance links, lifecycles, and audit rows.
- Read models (including Research Memory) stay rebuildable.
- Companions can be added for graph acceleration, search, semantic retrieval, analytics, or artifact bytes without promoting them to truth.
- Avoids premature polyglot cost while not forbidding later stores.

### Weaknesses

- Until companions exist, some graph walks, full-text, or semantic queries may be slower or clumsier.
- Requires discipline: do not sneak a second SoR into a “cache,” and do not treat companion indexes as conflict winners.

### Project-specific fit

- Matches mandatory gates and single-developer first implementation.
- Matches “search/vector/graph may later exist as companion capabilities.”
- Artifact bytes remain a separate open decision; only Artifact metadata/reference is in this SoR.

### Risks

- Delaying a companion too long after a real trigger (traversal, search, byte volume).
- Overloading the relational SoR with blobs or search indexes that should have become companions.

---

## Comparison Matrix

Scores are 1 (worse project fit) to 5 (better project fit). Higher is always better. Stage 1 weighs more than Stage 2.

| Criterion | Rel. primary | Doc. primary | Graph primary | Event-sourced primary | Polyglot-from-start | Rel. + companions later | Why |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1. domain integrity | 5 | 3 | 3 | 3 | 2 | 5 | Many distinct concepts need constrained records. Documents/graphs/events can, but integrity is easier to scatter. |
| 2. transactional consistency | 5 | 3 | 3 | 3 | 2 | 5 | Approval+Finding and run+budget need one SoR transaction (or explicit recovery). Polyglot splits writes. |
| 3. authority-transition correctness | 5 | 2 | 3 | 3 | 2 | 5 | Cross-entity promotions are the hard case. Document aggregates and multi-store writes fight this. |
| 4. provenance representation | 5 | 3 | 4 | 4 | 3 | 5 | Links as first-class records. Graph/events can represent provenance; documents tend to nest/copy. |
| 5. lifecycle/state-machine representation | 5 | 3 | 3 | 4 | 3 | 5 | Current state + legal transitions. Events encode transitions well but need projections for “now.” |
| 6. append-oriented history support | 4 | 3 | 3 | 5 | 4 | 4 | Event-sourced wins append-only. Relational can insert-only Evidence/Audit without event-sourcing everything. |
| 7. immutable Evidence/Audit semantics | 4 | 3 | 3 | 5 | 3 | 4 | Same: events excel at immutability; relational insert-only rows are enough for this domain. |
| 8. relationship modeling | 4 | 2 | 5 | 3 | 4 | 4 | Graph is strongest for edges. Relational relation tables are enough until traversal dominates. |
| 9. graph-like traversal capability | 3 | 2 | 5 | 3 | 4 | 4 | Graph primary / later companion. Relational-only is weaker if walks dominate; “later” raises this. |
| 10. historical/snapshot querying | 4 | 3 | 3 | 5 | 3 | 4 | Snapshots/ChangeEvents as records. Event replay can reconstruct; ad-hoc relational querying is simpler on a record SoR. |
| 11. flexible metadata support | 3 | 5 | 3 | 3 | 4 | 4 | Document-primary is most flexible. Relational+companions can add constrained flexible fields without changing SoR. |
| 12. schema evolution | 4 | 4 | 3 | 2 | 2 | 4 | Event schemas and multi-store schemas are the hardest to evolve. |
| 13. query expressiveness | 5 | 3 | 4 | 2 | 3 | 5 | Ad-hoc joins and filters over current + historical records. Event logs need projections first. |
| 14. indexing | 4 | 4 | 3 | 2 | 3 | 4 | Record stores index well. Event-primary indexes usually sit on projections. |
| 15. analytical querying | 4 | 3 | 3 | 2 | 4 | 4 | Analytics may later leave the SoR; starting polyglot is not required. |
| 16. single-developer simplicity | 5 | 4 | 2 | 2 | 1 | 5 | One SoR. Event-sourcing and polyglot are the heaviest. |
| 17. operational simplicity | 5 | 4 | 3 | 2 | 1 | 5 | One primary process/store class until a trigger. |
| 18. local development simplicity | 5 | 4 | 2 | 2 | 1 | 5 | One paradigm locally; companions optional. |
| 19. testability | 5 | 3 | 3 | 2 | 2 | 5 | Invariants and transitions testable against one SoR. Projections/multi-store tests explode. |
| 20. migration/evolution safety | 4 | 3 | 3 | 2 | 2 | 4 | One schema story. Event upcasters and multi-store migrations are costlier. |
| 21. future companion-store interoperability | 4 | 3 | 3 | 3 | 5 | 5 | Polyglot-from-start “has” companions already but without a clean SoR. Rel+later is the intended path. |
| 22. avoidance of vendor lock-in | 4 | 3 | 3 | 3 | 2 | 4 | Paradigm ≠ product. More stores → more product coupling. |
| 23. avoidance of premature polyglot cost | 5 | 4 | 3 | 3 | 1 | 5 | Inverse of starting with many stores. Higher = less premature cost. |
| 24. truth independent of Research Memory | 5 | 3 | 3 | 2 | 2 | 5 | Memory must remain a read model. Event/polyglot/document copies easily become shadow truth. |

No extra paradigm was added.

---

## Decision

**ACCEPT WITH CONSTRAINTS**

**Primary data strategy:** Relational primary System of Record, with rebuildable read models, and optional companion stores later.

This passes Stage 1: distinct domain records, multi-record authority transitions (especially Approval → Finding), provenance links, mixed mutable lifecycles and append-oriented Evidence/Audit, without treating Research Memory as truth.

It wins Stage 2 for a single developer: one authoritative paradigm, local/operational simplicity, query ergonomics, and a clear extension path.

It is not selected because “relational means Postgres,” nor because graph/document/events are incapable. Document-primary and graph-primary fail as *sole* SoR on authority-transition and lifecycle/audit shape. Event-sourced primary overfits audit/Evidence immutability. Polyglot-from-start creates source-of-truth ambiguity before any trigger.

The decision is ACCEPT WITH CONSTRAINTS because graph traversal, semantic retrieval, full-text, analytics, and large artifact bytes are real possible later workloads, and because flexible metadata must not dissolve relational invariants.

---

## Constraints

- **System of Record:** relational-paradigm store of authoritative domain records. Not a product name.
- **Authoritative data:** the domain concepts listed above, including WorkerResult as untrusted durable capture and Artifact *metadata/reference*, not automatically artifact bytes.
- **Read model:** rebuildable projection (Research Memory, UI, search views). Not a second truth.
- **Companion store:** may not gain authority. Every derived companion index/read model must be rebuildable from the System of Record (search, semantic/vector, graph, dashboard, Research Memory, temporal/materialized views). On drift, the companion is corrected or rebuilt; the SoR is not rewritten to match it. The companion is never the authoritative conflict winner. Artifact bytes may be exempt from rebuild-from-SoR because their topology is a separate decision; Artifact metadata/reference/provenance stay in the SoR.
- **Evidence/Audit/Approval immutability:** Evidence, AuditEvent, and recorded Approval decision must not rewrite history in place. Corrections are new records; old records remain. In-place mutation of Evidence/Audit/recorded Approval is a failed invariant. Enforcement mechanism (store constraints, tests, persistence rules) is not chosen here.
- **Flexible metadata:** secondary/extensible attributes only. Must not authoritatively hold authorization, scope, approval, lifecycle, promotion, budget, finding acceptance, evidence identity/provenance, or core policy decisions. Those are first-class domain fields/records. Flexible metadata is not a hidden second schema.
- **Cross-store consistency:** if a companion exists, the System of Record is source of truth; companions are derived. No dual-write as two SoRs.
- **Artifact bytes:** out of scope for this decision (open: topology/product).
- **Graph/vector/search:** out of scope as products; allowed later as non-authoritative companions when revisit triggers fire.
- Do not map one domain concept to one table as a requirement; preserve distinct records and links.
- Do not put Research Memory, vector indexes, or search indexes in the role of System of Record.
- FindingProposal APPROVED remains the domain view of Core Approval, not a separate store of authority.

---

## Rejected alternatives

- **Document primary:** Flexible aggregates help evolving metadata, but Candidate/Finding/Approval/Evidence provenance crosses aggregates. Integrity and multi-record authority would be reimplemented by hand; duplication risk is high.
- **Graph primary:** Strong for AssetRelation and provenance walks. Weak as the only SoR for state machines, structured audit, Approval→Finding atomicity, and single-developer operations. Graph-shaped domain ≠ graph-primary SoR.
- **Event-sourced primary:** Strong append/audit story. AuditEvent + immutable Evidence do **not** require event-sourcing the whole domain. Projection, replay, and query cost fail Stage 2; views can drift from authority.
- **Polyglot-from-start:** Specialized engines help later workloads. Starting with several stores splits truth, sync, and local operations before a measured need.
- **Relational-only forever (no companions allowed):** Not chosen as a hard ban. Companions remain optional later. The selected strategy is relational primary **plus** companions when triggered—not a freeze on all future stores.

---

## Revisit Triggers

Reopen or extend (usually by adding a companion, not by replacing the SoR) with measured evidence in these categories. No numeric thresholds invented.

- relationship traversal dominates workload
- semantic retrieval becomes a core retrieval path
- full-text search demand exceeds primary-store capability
- analytics volume becomes operationally expensive on the SoR
- event volume or snapshot volume becomes a specialized workload
- primary-store query bottlenecks despite indexing/modeling
- write throughput limits authority transitions
- storage growth (especially bytes vs metadata)
- read-model rebuild cost
- cross-program analysis that the SoR cannot answer without harming operational simplicity
- schema migration pain that a different *product* might ease without changing paradigm
- operational complexity of the primary store itself

Adding a companion under these triggers does not by itself reverse relational primary as System of Record.

---

## Open questions after this decision

Still unresolved; not answered here:

- specific database product
- artifact byte-storage topology/product
- semantic/vector strategy
- search strategy
- graph companion strategy
- analytics strategy
- retention policy
- backup/restore strategy
- schema migration tooling
- data encryption implementation

---

## Confidence

**MEDIUM**

Relational primary plus later companions is the only candidate that clearly passes Stage 1 for this authority/provenance domain and Stage 2 for one developer, without forbidding graph/search/vector/bytes later.

Confidence is not HIGH because graph-shaped traversal and flexible metadata are real, product choice is still open, and a badly modeled relational SoR could recreate document-shaped invariant loss inside “flexible” columns. Revisit triggers exist so companions can be added without pretending the SoR must never change shape.

---

# Decision 003 — Specific Primary Database Product

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Depends on:** Decision 002 — Primary Data Strategy (relational primary System of Record; rebuildable read models; optional companions later)

This decision selects the **concrete primary database product** for that System of Record.

It does **not** select:

- ORM / data-access library
- schema migration tooling
- connection pooling
- cache / ephemeral store
- vector, graph, or search products
- artifact **byte** storage product
- CDC / event-projection product
- orchestrator
- replication / HA topology
- backup/restore **implementation**
- encryption implementation

Those remain later decisions.

Python is the locked first-implementation language (Decision 001). The database product must expose **language-neutral client protocols/interfaces**. A mature Python client ecosystem exists for that primary language. This database decision is **not** Python-locked. No driver or client library is selected here. Contracts remain language-neutral.

---

## Decision

**ACCEPT WITH CONSTRAINTS**

The primary System of Record for Zest v1 is **PostgreSQL**.

PostgreSQL is the durable store for Program, AuthorizationSource, ScopeRule, ResearchRun, Budget, Asset, AssetRelation, Observation, Hypothesis, Experiment, WorkerResult metadata/reference, Artifact metadata/reference, Evidence, Candidate, Verification records, FindingProposal, Finding, Approval, Snapshot, ChangeEvent, and AuditEvent.

It is **not** Research Memory.  
It is **not** the default artifact byte store.  
It is **not** a graph, search, or vector product.  
JSONB / document-shaped columns, if used at all, are **secondary/extensible attributes only**. They do not replace first-class fields for authorization, scope, lifecycle, promotion, budget, approval, finding acceptance, evidence identity/provenance, or core policy state (Decision 002).

PostgreSQL is **not** selected because other candidates are incapable of being a relational System of Record. Multiple products can satisfy the mandatory Stage 1 gates. PostgreSQL is selected because it offers the strongest **combined project fit** after those gates are satisfied.

---

## Why this decision exists

Decision 002 locked the **paradigm**: relational primary SoR.

This decision locks the **product** that must actually:

- commit Approval + Finding together, or neither
- commit Budget reservation/decrement with ResearchRun/execution start, or neither
- keep Evidence, AuditEvent, and recorded Approval from being rewritten in place
- hold first-class authority and lifecycle fields under constraints
- feed later rebuildable companions without becoming those companions

Choosing “relational” without a product would leave the first implementation without a durable store. Choosing a product because it is popular, distributed, embedded, or JSON-capable would violate the domain.

---

## Candidates considered

Serious relational candidates only:

1. **PostgreSQL**
2. **MySQL / MariaDB** (related family; not treated as identical products)
3. **SQLite**
4. **CockroachDB**

No additional candidate was added. Document databases, wide-column stores, and “Postgres-compatible” cloud forks were not evaluated as separate products; they would either reopen Decision 002 or add vendor lock-in without changing the relational question.

---

## Project facts that drive product choice

- Authority and lifecycle fields are first-class.
- Flexible metadata is secondary only.
- Evidence / AuditEvent / recorded Approval must not mutate in place.
- Approval → Finding and ResearchRun + Budget consumption must be consistent.
- Transition A and Transition B stay separate.
- Provenance is relationship-dense.
- AssetRelation may be graph-shaped; a graph companion remains optional later (Decision 002). Recursive SQL does **not** make a graph store unnecessary, and does **not** make recursive SQL a domain dependency.
- Historical / snapshot querying matters.
- Companions later are rebuildable from this SoR.
- First implementation is one developer.
- Local development matters.
- Production topology is **not** chosen.
- Current context is Windows + Cursor; Kali/WSL is the initial tool-integration environment. Windows is **not** the production architecture driver.
- Database product must remain replaceable in principle; Core/Research must not embed product-specific types or SQL dialects in domain contracts.

---

## Stage 1 — Mandatory gates

A product that fails a Stage 1 gate is **not** a viable **production** System of Record candidate.

Multiple candidates can satisfy these mandatory gates.

PostgreSQL, MySQL/MariaDB, and CockroachDB can all be viable production relational systems of record **in principle**.

SQLite may satisfy durability and transactional correctness in an **embedded/local** role, but is weaker for the expected shared/networked multi-process production control-plane role.

PostgreSQL is selected primarily because it gives the best combined project fit **after** the mandatory gates are satisfied — not because it is the only product that can pass them.

| Gate | Meaning for this project |
|---|---|
| Transactional correctness | Multi-row, multi-table commits with rollback on partial failure |
| Authority consistency | AuthorizationSource / ScopeRule / ResearchRun / Budget can be updated without split-brain vs companions |
| Durable SoR suitability | Survives process restart as the source of truth for control-plane records |
| Provenance / audit support | Append-oriented Evidence / AuditEvent / Approval history can be stored and queried |
| Integrity constraints | PK / FK / UNIQUE / NOT NULL / CHECK (or equivalent) on first-class fields |
| Linux production viability | Can run as a Linux service later without rewriting the SoR |
| Language-neutral client interoperability | Product exposes language-neutral client protocols/interfaces; it is not a Python library or Python-locked store |

Python client maturity is a **Stage 2** concern, not a Stage 1 gate. No driver is selected here.

### Gate results

| Product | Stage 1 | Note |
|---|---|---|
| **PostgreSQL** | **Pass** | Viable production relational SoR: transactions, constraints, durable shared server, Linux service, language-neutral clients |
| **MySQL 8+ / recent MariaDB** | **Pass** | Viable production relational SoR family. InnoDB transactions and foreign keys are real. Integrity **can** be preserved. Constraint/query **ergonomics** are Stage 2, not a Stage 1 failure |
| **SQLite** | **Pass as embedded/local; not the production SoR role** | Durable ACID in-process. Weaker for shared/networked multi-process production control plane, remote workers, and HA/replication as a service. Not eliminated for lack of correctness |
| **CockroachDB** | **Pass** | Serious production candidate at Stage 1. Distributed operational cost, latency, and single-developer burden are Stage 2, not a correctness failure |

Stage 1 does **not** pick the winner. It only establishes which products are capable enough to compare.

---

## Consistency boundaries (conceptual)

These are **not** schema or API designs. They test whether the product’s transactional model can hold the domain.

| Operation | What must not happen | PostgreSQL | MySQL / MariaDB | SQLite | CockroachDB |
|---|---|---|---|---|---|
| 1. Scope / Authorization updates | Partial ScopeRule write vs Program/AuthorizationSource | Single transaction | Single InnoDB transaction | Local transaction; poor concurrent writers | Distributed transaction |
| 2. ResearchRun creation | Run row without required authorization snapshot/reference | Same | Same | Same locally | Same, higher latency |
| 3. Budget decrement / reservation | Budget changed but execution never started, or execution started without budget | Same atomic unit | Same | Same locally; lock contention under workers | Same; clock/latency caveats |
| 4. Experiment lifecycle | Status change without required fields | Constraints + txn | Constraints + txn (ergonomics differ) | Constraints + txn | Constraints + txn |
| 5. Transition A Observation / Artifact metadata | Interpretation or Evidence in the same write | App + txn; product does not invent Transition B | Same | Same | Same |
| 6. Evidence admission | Evidence without Observation/Artifact basis, or in-place rewrite of prior Evidence | Insert + FK + no UPDATE policy | Insert + FK | Insert + FK if enabled | Insert + FK |
| 7. Candidate lifecycle | Illegal status jump | CHECK / constrained status | ENUM/CHECK (MySQL CHECK is newer) | CHECK | CHECK |
| 8. Candidate VALIDATED → FindingProposal | Proposal without VALIDATED Candidate | Txn + FK | Txn + FK | Txn + FK | Txn + FK |
| 9. Human Review → Approval | Approval without review outcome | Txn | Txn | Txn | Txn |
| 10. Approval + Finding creation | **Approval recorded, Finding missing** (or reverse) | **One transaction** | **One transaction** | **One local transaction** | **One transaction** (distributed cost) |
| 11. Append-only AuditEvent | In-place rewrite | Insert-only grants/triggers possible; not automatic | Same idea | Same idea; enforcement is process-local | Same idea |
| 12. Immutable Evidence history | UPDATE/DELETE of admitted Evidence | Preventable by privilege + modeling | Preventable by privilege + modeling | Preventable locally | Preventable |
| 13. Snapshot / ChangeEvent | History rewritten | Append tables | Append tables | Append tables | Append tables |

**Partial-state examples:**

- Approval committed, Finding not created → all four **can** prevent this **if** the application uses one transaction. PostgreSQL and InnoDB MySQL/MariaDB are the realistic **shared-server** answers. SQLite can atomically commit locally but not as a multi-process networked SoR. CockroachDB can atomically commit across nodes at operational and latency cost this project does not have yet.
- Budget decremented, execution never started → same: product must offer multi-statement transactions; **application** must put both writes in one transaction. No product auto-implements Budget policy.

Immutability of Evidence, AuditEvent, and recorded Approval is a **domain requirement**, not a built-in feature of any candidate:

- in-place history rewrite is forbidden
- correction is a **new record**, not an overwrite
- store-level enforcement is a later decision
- tests for that enforcement are later

Exact grants, triggers, row-level policies, or application-only checks are **not** chosen here.

---

## Immutability support (capability, not implementation)

Required: Evidence, AuditEvent, and recorded Approval history are not rewritten in place.

| Capability | PostgreSQL | MySQL / MariaDB | SQLite | CockroachDB |
|---|---|---|---|---|
| Append-oriented tables | Yes | Yes | Yes | Yes |
| Column constraints on first-class fields | Strong | Adequate (MySQL 8 CHECK; MariaDB differs) | Strong if FKs enabled | Strong |
| Privilege to deny UPDATE/DELETE | Mature GRANT/REVOKE | Mature | File + limited user model | Role-based |
| Transactional insert of Approval+Finding+AuditEvent | Yes | Yes (InnoDB) | Yes locally | Yes, distributed |

No candidate provides domain immutability by existing. All production passers can support append-oriented modeling plus privileges. Exact store-level enforcement and tests are later. That is **capability**, not a selected implementation.

---

## Flexible metadata

Secondary attributes may need a flexible column.

| Concern | PostgreSQL | MySQL / MariaDB | SQLite | CockroachDB |
|---|---|---|---|---|
| Flexible payload | JSONB | JSON (semantics differ; MariaDB JSON is not MySQL JSON) | JSON1 | JSON |
| Indexing metadata | Mature (without requiring a named extension in this decision) | Possible; historically less ergonomic | Weaker | Possible |
| Schema discipline still required | Yes — JSONB does **not** authorize document-shaped SoR | Yes | Yes | Yes |
| Abuse risk | **High if used for authority fields** | High | High | High |

**JSON support does not mean this project may treat PostgreSQL as a document database.** Flexible metadata is **secondary only**. Authorization, scope, lifecycle, promotion, budget, approval, finding acceptance, evidence identity/provenance, and core policy state remain first-class columns. This decision does **not** require JSONB, and does **not** require any extension.

---

## Future read models (ergonomics only)

Later projections may include Research Memory, search, graph, semantic/vector, dashboards, temporal views.

PostgreSQL is a conventional **source** for rebuild/export: stable rows, SQL snapshots, WAL-based tooling later. That does **not** select CDC, Debezium, LISTEN/NOTIFY, or logical replication as products.

MySQL/MariaDB binlog is also a known projection source. SQLite is a weak CDC/source-of-projections story for a multi-service control plane. CockroachDB changefeeds exist; they would couple operations to a distributed product this project does not need.

No companion product is selected.

---

## Local development vs production roles

Current environment: Windows + Cursor; Kali/WSL workers later. Production topology unselected.

| Role | PostgreSQL | MySQL / MariaDB | SQLite | CockroachDB |
|---|---|---|---|---|
| Windows local run | Installer, WSL, or container — workable, not zero-service | Similar | **Zero-service** | Heavy (cluster) |
| Linux production | Native, mature | Native, mature | File; not the shared server SoR | Native, heavier |
| Test isolation | Separate database/schema per suite (tooling later) | Same | **Best** file-per-test | Awkward locally |

Windows support is **not** the primary driver. This PostgreSQL selection is **not** Windows-specific, **not** Kali-specific, and **not** Python-specific. Local PostgreSQL is an operational advantage for development; Windows + Cursor and Kali/WSL do **not** determine production architecture.

SQLite wins local friction and is the strongest **local/embedded** alternative. It is not selected as the production SoR. Using SQLite locally and PostgreSQL in production would create **two dialects** and a false sense that production behavior was tested. This decision therefore selects **one** primary product for the SoR, including local development against that product. SQLite as an optional test double remains an **open question**, not a selected dual-SoR strategy.

---

## Candidate evaluations

### PostgreSQL

**Fit:** Strongest **Stage 2** combined fit among Stage 1 production passers, for a provenance-heavy relational SoR with one developer and later Linux production.

It is not selected because MySQL/MariaDB or CockroachDB cannot be production systems of record. Those products pass Stage 1.

It is selected because this domain has:

- many first-class relational records
- authority-sensitive multi-record transitions
- provenance-heavy joins
- lifecycle/state modeling
- historical queries
- recursive relationship queries
- flexible secondary metadata needs
- single-developer operational constraints

and PostgreSQL offers the strongest combined fit across those needs without forcing a distributed or polyglot architecture in v1.

**Strengths that matter here (not popularity):**

- Transactional semantics for Approval+Finding and Budget+Run
- Integrity/constraint **ergonomics** for first-class authority/lifecycle fields
- Relational + recursive query power for provenance and AssetRelation **without** making a graph store forbidden or required
- JSON-like payloads available for **secondary** metadata only
- Mature indexing, ad-hoc SQL, backup/restore, observability
- Language-neutral client protocols/interfaces, with a mature Python client ecosystem for the locked primary language — the product is not a Python database
- Runnable locally and on Linux; that local runnability is an advantage, not a Windows/Kali architecture decision. Production HA remains a later topology decision

**Risks (accepted, constrained):**

- More operational surface than SQLite (a process/service vs a file)
- **Extension temptation** and accidental lock-in if Core/Research absorb product-specific features
- JSONB **misuse** recreating a document SoR
- No automatic horizontal distribution — acceptable because scale is not a current requirement
- Schema discipline is still the application’s job

This decision does **not** require, name, or depend on specific extensions.

### MySQL / MariaDB

**Fit:** Strongest **production** alternative. Viable Stage 1 production relational SoR family. Not selected because Stage 2 project fit is weaker than PostgreSQL, not because the family cannot preserve integrity.

**Not identical products.** MySQL 8 and MariaDB have diverged (JSON type, sequences, optimizer, system versioning, privilege details). Hosting familiarity and replication lore are real. For Zest they are one **family of alternatives**, not two independent winners.

**Why it is strong:**

- mature relational engine family
- production-proven
- replication and hosting ecosystem
- transactional correctness (InnoDB)
- broad tooling

**Why it is not as good a fit as PostgreSQL for this project right now:**

- provenance-heavy / constraint-heavy domain: query and integrity **ergonomics** are less attractive than PostgreSQL’s
- complex relational / recursive query ergonomics are a weaker project fit
- choosing it provides **no clear simplicity advantage** over PostgreSQL for this project
- MySQL vs MariaDB JSON and dialect split would be extra lock-in inside a second-choice family

MySQL/MariaDB are **not** “bad.” They are **not** unable to preserve integrity. InnoDB can commit Approval+Finding. This domain is more than generic OLTP hosting familiarity, so Stage 2 prefers PostgreSQL at similar operational cost.

### SQLite

**Fit:** Strongest **local/embedded** alternative. Excellent local development, embedded ACID, fast tests, zero-service setup. Not selected as the **primary production SoR**.

**Why it is strong:**

- excellent local development
- embedded ACID
- fast tests
- zero-service setup
- recursive CTEs and language-neutral clients
- tiny operational surface

**Why it is not the production SoR:**

- shared remote control-plane deployment
- multiple writers / processes
- networked service expectations
- HA / replication topology
- future remote workers

Writer serialization is a **project-fit** mismatch for a shared control plane, not evidence that SQLite is a toy or that its transactions are fake.

**Role split:** SQLite is **not** appropriate as the primary **production** System of Record. It is **more appropriate as a local test/dev / embedded capability** (and possibly a later optional test double). That capability is **not** selected here, to avoid a dual-database strategy in v1.

### CockroachDB

**Fit:** Serious Stage 1 **production** candidate. Correct **class** (distributed relational) for a later scale/HA world. Wrong **time** for v1.

**Strengths:** horizontal scale, resilience, familiar SQL, serializable-by-default story. Distributed does **not** automatically score higher, and does **not** fail correctness.

**Why not v1 (Stage 2):**

- distributed-system operational cost
- higher conceptual and operational complexity
- latency and distributed transaction semantics
- single-developer burden (local cluster)
- no current horizontal-scale requirement
- compatibility is “Postgres-like,” not a promise that PostgreSQL features and ops transfer

CockroachDB is **not** rejected because it is distributed, and **not** rejected because it fails correctness. It is not selected because this project does not yet need that operational class.

---

## Comparison matrix

Scores: **5 = better project fit**, **1 = worse project fit**. High is always better. Totals are **not** the decision.

**PG** = PostgreSQL. **MY** = MySQL 8 / MariaDB family (score reflects the weaker of the two where they diverge). **LT** = SQLite. **CR** = CockroachDB.

| # | Criterion | PG | MY | LT | CR | Short rationale |
|---|---|---:|---:|---:|---:|---|
| 1 | Transactional correctness | 5 | 5 | 4 | 5 | All four are ACID in their intended role. SQLite 4 is **shared/networked SoR fit**, not “fake transactions.” Production servers are Stage 1 capable. |
| 2 | Integrity constraints | 5 | 4 | 4 | 4 | All can enforce PK/FK/CHECK (or equivalent). PG scores higher on **ergonomics** for this constraint-heavy domain, not exclusive capability. MySQL CHECK arrived later; that is Stage 2. |
| 3 | Multi-record atomic transitions | 5 | 4 | 4 | 4 | All can COMMIT Approval+Finding; PG is the default shared-server choice. SQLite not shared. CR costlier. |
| 4 | Concurrency behavior | 4 | 4 | 2 | 4 | PG/MySQL MVCC for concurrent writers. SQLite writer lock. CR concurrent but distributed. |
| 5 | Explicit lifecycle/state modeling | 5 | 4 | 4 | 4 | All can store status columns; PG constraints/enums/CHECK are the strongest habit fit. |
| 6 | Append-oriented records | 4 | 4 | 4 | 4 | All support insert-only tables; none are ledgers by default. |
| 7 | Immutable-record enforcement potential | 4 | 4 | 3 | 4 | All can model append-only tables. PG/MySQL/CR have conventional shared-server privilege models. SQLite enforcement is process-local. Exact mechanism not chosen. |
| 8 | Provenance/query support | 5 | 4 | 4 | 4 | PG SQL/CTE/window depth. Others adequate. |
| 9 | Relationship/query expressiveness | 5 | 4 | 4 | 4 | Same pattern. Recursive SQL ≠ graph product. |
| 10 | Recursive/graph-like traversal | 4 | 3 | 4 | 3 | PG/SQLite recursive CTEs are capable; not a graph DB. MySQL recursive CTE exists, less central. CR Postgres-like with limits. |
| 11 | Historical/snapshot query ergonomics | 4 | 3 | 3 | 3 | History tables + SQL: PG nicest. No product selected as a temporal database. |
| 12 | Structured + flexible metadata | 4 | 3 | 3 | 4 | JSONB is capable **and** easy to abuse. MySQL/MariaDB JSON split is a trap. Score is capability with discipline, not a document mandate. |
| 13 | Schema evolution | 4 | 3 | 2 | 4 | PG ALTER is mature. SQLite ALTER is limited. MySQL online DDL has caveats. |
| 14 | Migration safety | 4 | 3 | 2 | 3 | Transactional DDL habits favor PG. Tooling is a later decision. SQLite migrations are awkward. CR schema changes are a different ops model. |
| 15 | Indexing | 5 | 4 | 3 | 4 | PG strongest general + JSON-secondary indexing **if** used. Not an extension decision. |
| 16 | Ad-hoc querying | 5 | 4 | 4 | 4 | SQL everywhere; PG/ecosystem nicest for provenance exploration. |
| 17 | Analytical query capability | 4 | 3 | 2 | 3 | SoR is not a warehouse. PG can feed later analytics; SQLite least. |
| 18 | Local development simplicity | 3 | 3 | 5 | 1 | SQLite wins. PG/MySQL need a service. CR cluster is worst. |
| 19 | Windows development support | 3 | 4 | 5 | 2 | SQLite trivial. MySQL Windows installs are common. PG via WSL/installer/container. Not the decision driver. |
| 20 | Linux deployment support | 5 | 5 | 3 | 4 | PG/MySQL native servers. SQLite is a file, not the production SoR shape. CR runs on Linux at higher cost. |
| 21 | Python ecosystem maturity | 5 | 5 | 5 | 4 | Stage 2. Mature Python **clients** exist; none of these products is Python-locked. No driver is selected. CR uses PG-wire with compatibility gaps. |
| 22 | Backup/restore maturity | 5 | 4 | 3 | 3 | PG dump/basebackup lore is strongest for a future Linux SoR. SQLite = file copy + WAL care. |
| 23 | Observability/tooling maturity | 5 | 4 | 2 | 3 | PG/MySQL as servers. SQLite has little server ops story. |
| 24 | Operational simplicity | 3 | 3 | 5 | 1 | SQLite simplest. PG/MySQL similar single-node cost. CR heaviest. |
| 25 | Single-developer suitability | 4 | 4 | 5 | 2 | SQLite easiest locally; not the production SoR role. One PG or MySQL instance is acceptable. CR is a heavier ops class. |
| 26 | Future scale headroom | 4 | 4 | 1 | 5 | CR wins scale we do not have. SQLite has none. PG vertical + later replicas. |
| 27 | Replication / HA options | 4 | 4 | 1 | 5 | Options exist for PG/MySQL/CR; **none selected**. SQLite has no HA story. |
| 28 | Companion-store interoperability | 4 | 4 | 2 | 4 | SQL dump/replica/CDC-later. SQLite is a poor projection source for a control plane. CDC not chosen. |
| 29 | Portability / vendor lock-in risk | 4 | 4 | 5 | 3 | SQL portability if we avoid product features. CR and PG extensions are lock-in paths. SQLite dialect is small but production-wrong. |
| 30 | Maturity/stability | 5 | 5 | 5 | 4 | Three are decades-old production classes. CR is mature enough as distributed SQL; heavier than this v1 needs. |

**Reading the matrix:** Scores are **project fit**, not exclusive capability. PostgreSQL, MySQL/MariaDB, and CockroachDB can all pass Stage 1 as production relational SoRs. SQLite scores high on local/embedded columns and lower on shared/networked production-role columns. CockroachDB scores high on scale/HA this project does not need. MySQL/MariaDB tracks PostgreSQL as a production relational server and trails on Stage 2 ergonomics for this provenance/constraint-heavy domain. PostgreSQL wins on **combined Stage 2 fit**, not by being the only gate passer.

---

## Stage 2 — Differentiators (among Stage 1 production passers)

Stage 1 established capability. Stage 2 chooses the product.

Compared as production SoRs: PostgreSQL vs MySQL/MariaDB vs CockroachDB.

SQLite is the strongest **local/embedded** alternative and is scored in the matrix; it is not the production SoR role being chosen here.

| Differentiator | Winner | Why |
|---|---|---|
| Integrity / constraint ergonomics | PostgreSQL | Strongest habit fit for first-class authority/lifecycle fields. MySQL/MariaDB can enforce integrity; ergonomics are less attractive for this domain |
| Complex relational query ergonomics | PostgreSQL | Provenance joins, window functions, ad-hoc SQL depth |
| Provenance-heavy schema fit | PostgreSQL | Many distinct related records plus historical/append tables |
| Recursive relationship querying | PostgreSQL | Recursive CTEs are capable here; they do not make a graph companion forbidden or required, and are not a domain dependency |
| Flexible secondary metadata support | PostgreSQL | JSON-like payloads exist for **secondary** attributes only. JSON does not authorize a document SoR. MySQL vs MariaDB JSON divergence is extra risk |
| Migration maturity | PostgreSQL | Mature ALTER habits. Tooling is a later decision. SQLite ALTER is limited (embedded role) |
| Single-developer operations | PostgreSQL ≈ MySQL; not CR | One instance is enough. CR adds distributed ops with no current scale requirement. SQLite is simpler locally but is not this role |
| Local development | PostgreSQL among production servers; SQLite if the job were embedded | Local PG is an advantage, not a Windows/Kali/Python architecture. Dual SQLite+PG dialects are avoided in v1 |
| Python interoperability | PostgreSQL ≈ MySQL ≈ SQLite | Mature Python **clients** exist. Product choice is not Python-locked. No driver selected. CR is PG-wire-like with gaps |
| Future companion-store integration | PostgreSQL ≈ MySQL | Conventional SQL source for later rebuildable projections. CDC not chosen. SQLite is a weaker projection source for a control plane |
| Operational maturity | PostgreSQL ≈ MySQL | Both are production-proven single-node classes. CR is mature as distributed SQL and heavier than this v1 needs |

PostgreSQL wins Stage 2 as the strongest **combined** fit. MySQL/MariaDB remains the strongest production alternative. CockroachDB remains a later-scale production class, not a v1 need.

---

## Constraints (accepted with the product)

1. **Authoritative SoR role.** PostgreSQL holds first-class domain records and metadata/references. Companions never win conflicts (Decision 002).
2. **Flexible metadata bound.** JSON/JSONB (or equivalent) only for secondary/extensible attributes. Flexible fields **must not** replace first-class columns for authorization, scope, lifecycle, promotion, budget, approval, finding acceptance, evidence identity/provenance, or core policy state. JSON support is **not** permission to use a document model.
3. **Immutability requirement.** Evidence, AuditEvent, and recorded Approval history must not be rewritten in place. Correction is a **new record**. Store-level enforcement and tests are later decisions; exact mechanism is not chosen here.
4. **Transaction boundaries.** Approval + Finding (+ related AuditEvent) commit together or not at all. Budget reservation/decrement + the execution start they authorize commit together or not at all. Transition A and Transition B remain separate transactions/workflows, not separate databases.
5. **Companion-store authority prohibition.** Research Memory, search, graph, vector, cache, and artifact **bytes** are not this product’s job by default.
6. **No product-specific domain logic in Core/Research.** Domain contracts stay language- and vendor-neutral. PostgreSQL types, SQL dialect, and extensions must not leak into Core/Research as the domain model.
7. **Product features are not domain dependencies.** Recursive queries, JSONB, LISTEN/NOTIFY, advisory locks, inheritance, or any extension may be used later in **Data** only if a future decision allows them. They are not part of this accept.
8. **Replaceability.** A future move off PostgreSQL must remain theoretically possible: portable schema concepts, no Core/Research `VARCHAR`/`JSONB`/OID leakage into contracts.

Artifact **bytes** remain a later storage decision. WorkerResult/Artifact **metadata and references** stay in PostgreSQL.

---

## Strongest alternatives (not selected)

These are capable products in their roles. They are not selected as the v1 primary production SoR.

### Strongest production alternative — MySQL / MariaDB

Not selected as the primary product. Remains the leading **production** alternative if PostgreSQL operational or ecosystem facts change.

**Why it is strong:** mature relational engine family; production-proven; replication/hosting ecosystem; transactional correctness; broad tooling.

**Why it is not as good a fit as PostgreSQL right now:** provenance-heavy / constraint-heavy query and integrity **ergonomics** are less attractive; complex relational/recursive query ergonomics are a weaker project fit; choosing it provides no clear simplicity advantage over PostgreSQL for this project.

Not described as “bad.” Not described as unable to preserve integrity.

### Strongest local/embedded alternative — SQLite

Not selected as the primary **production** System of Record. Remains the leading **local/embedded** alternative (and an open question as a possible later test double).

**Why it is strong:** excellent local development; embedded ACID; fast tests; zero-service setup.

**Why it is not the production SoR:** shared remote control-plane deployment; multiple writers/processes; networked service expectations; HA/replication topology; future remote workers.

Not described as a toy. Not described as lacking real transactions.

### CockroachDB — not selected for v1

A serious Stage 1 production candidate. Not selected because of distributed-system operational cost, higher conceptual/operational complexity, latency/distributed transaction semantics, single-developer burden, and no current horizontal-scale requirement.

Not rejected because distributed systems “fail correctness.” Not rejected merely “because distributed.”

---

## Revisit triggers

Revisit this product (not automatically switch) if:

- write contention on the SoR becomes a measured bottleneck
- sustained concurrent control-plane load exceeds a single-node PostgreSQL comfort zone **as observed**
- horizontal scale is a **requirement**, not a preference
- HA / failover is a **requirement** (then topology, not necessarily a new product)
- cross-region is a **requirement**
- storage growth of metadata (not artifact bytes) stresses backup/restore
- query latency on provenance/history becomes user-visible
- analytical workload grows enough to need a replica or warehouse **companion**
- backup/restore or migration **pain** is operational, not theoretical
- operational burden of PostgreSQL exceeds the benefit vs a simpler store **and** production SoR needs have actually shrunk (unlikely)
- the primary DB is the bottleneck after indexing/schema work
- distributed worker topology requires a different consistency/deployment story

No numeric thresholds. Companions (Decision 002) remain the first response to graph/search/vector/analytics pressure, not an automatic CockroachDB migration.

---

## Open questions after this decision

Intentionally unanswered:

- ORM / data-access strategy
- schema migration tooling
- connection pooling strategy
- backup/restore implementation
- replication / HA topology
- encryption implementation
- artifact byte storage product
- cache / ephemeral store
- graph / search / vector companions
- CDC / event projection mechanism
- whether tests may use SQLite as a double
- whether JSONB is used at all, and how it is indexed
- exact immutability enforcement (grants vs triggers vs application-only)

---

## Confidence

**MEDIUM**

PostgreSQL is selected because it offers the strongest combined Stage 2 fit for this domain after multiple products pass Stage 1. MySQL/MariaDB remains a viable production SoR family. CockroachDB remains a viable distributed production class without a current scale need. SQLite remains a strong local/embedded store, not the production SoR.

Confidence is not HIGH because production topology, HA, backup implementation, and schema/JSONB discipline are still unchosen, and a poorly constrained PostgreSQL schema could still bury authority in JSON. Revisit triggers exist so the product can change if load or topology actually appears.

---

# Decision 004 — Workflow / Orchestration Strategy

**Status:** ACCEPT WITH CONSTRAINTS (strategy) / DEFER (product)  
**Date:** 2026-08-16  
**Depends on:** Decision 002 (relational primary System of Record); Decision 003 (PostgreSQL as that SoR)

This decision selects the **orchestration strategy**: how long-lived research flows are coordinated.

It does **not** select:

- a queue / broker product
- Worker communication protocol (HTTP, RPC, gRPC, etc.)
- cache / ephemeral store
- deployment model (Docker, Kubernetes, cloud)
- a concrete workflow-engine or task-queue product

Python is the locked first-implementation language (Decision 001). Orchestration contracts must remain **language-neutral**. This decision is **not** Python-locked. Platform remains the owner of orchestration *capability*; Core, Research, and Data must not depend on a concrete orchestrator (PROJECT_STRUCTURE.md).

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS**

**PRODUCT: DEFER**

Zest v1 uses a **hybrid orchestration strategy**:

| Layer | Role |
|---|---|
| **PostgreSQL** | Authoritative **domain** System of Record |
| **Platform orchestration** | Durable **coordination** only (timers, retries, pending work, waits, progress) |
| **Workers** | Replaceable **execution** runtimes; produce untrusted WorkerResult |
| **Core** | Authorization, policy, scope, budget, and recorded Approval authority |

This is an architectural split, not a product choice. A durable workflow **engine** (Temporal-class), a task queue, Prefect-style pipelines, and a custom PostgreSQL-backed state machine are **evaluated below and not selected**.

The first implementation of the Platform orchestration contract may be local/in-process and may persist coordination records that are **not** domain truth. Introducing a named orchestration product requires a later decision.

---

## Why this decision exists

The domain has long-lived flows:

ResearchRun → Hypothesis → Experiment → Authorization → Worker execution → WorkerResult → Transition A → Evidence Admission → Verification → Candidate lifecycle → FindingProposal → Human Review → Approval → Finding

Also true:

- Workers can crash; hosts can restart
- remote Workers may exist later (not required now)
- Human Review can leave work pending for a long time
- retries, timeouts, cancellation, and duplicate-safe execution are needed
- authorization/scope must be re-checked after redirects or newly discovered targets
- budgets are immutable Core-issued limits; orchestration cannot change them
- **execution state is not domain truth**
- authoritative domain state is PostgreSQL
- a workflow engine cannot be a truth source
- orchestration cannot own Domain/Core authority

`TECHNICAL_REQUIREMENTS.md` makes **domain SoR + durable WorkerResult** survive restart a hard recoverability requirement. Exact mid-step workflow resume and specific replay semantics are **preferred**, not hard, and do not choose an orchestrator.

Choosing “just call Workers from the app” without separating coordination from domain state would eventually bury retries, waits, and dispatch flags inside Experiment/FindingProposal rows. Choosing a durable engine as truth would violate Core and Decision 003.

---

## Three states (must stay separate)

### Domain state (authoritative truth)

PostgreSQL System of Record: Program, AuthorizationSource, ScopeRule, ResearchRun, Budget, Experiment lifecycle, WorkerResult records, Observation/Artifact metadata, Evidence, Candidate, Verification records, FindingProposal, Finding, Approval, Snapshot, ChangeEvent, AuditEvent.

Human approval **is** a Core-recorded Approval (and the derived FindingProposal view). It is **not** a workflow wait flag.

### Orchestration state (coordination only)

Timers, retries, pending activities, waiting-for-signal, workflow/progress cursors, dispatch leases.

Orchestration state is **never**:

- authorization truth
- Evidence truth
- Finding truth
- Research Memory truth
- Budget policy
- Approval truth

If orchestration state is lost and PostgreSQL remains, domain truth remains. Coordination may need to be reconstructed from domain records plus untrusted WorkerResult. That reconstruction must re-enter through Core (authorization/scope/budget), not through “the workflow said so.”

### Worker state (ephemeral execution)

In-process tool/runtime details. WorkerResult re-enters the system as **UNTRUSTED EXECUTION OUTPUT**. Durable WorkerResult ≠ trusted fact. Workers cannot self-authorize, determine scope, or change budget.

---

## Candidates considered (strategies)

1. **Plain application orchestration** — in-process state machine, database-backed domain state, manual retries
2. **Job queue + persisted domain state** — background jobs, queue dispatch, DB as truth, orchestration logic in the application *(queue product not chosen)*
3. **Durable workflow engine as the strategy** — workflow persistence, retries, timers, wait-for-event/approval, activities, replay/durable-execution semantics as the primary coordination model
4. **Hybrid** — durable coordination for long-lived orchestration; Workers remain replaceable execution; PostgreSQL remains domain SoR; Core remains authority

No additional strategy class was added (pure event-sourcing orchestrators, Kubernetes-only controllers, or “the LLM drives the loop” are out of scope and would violate architecture).

---

## Stage 1 — Mandatory gates

A strategy that cannot preserve these is not viable, even if it is operationally simple.

| Gate | Meaning |
|---|---|
| Core authority preservation | Orchestration dispatches work Core already allowed; it cannot authorize, widen scope, or change budget |
| Domain SoR remains PostgreSQL | Orchestrator failure must not rewrite or replace domain truth |
| Workflow state ≠ domain truth | Timers/retries/waits are not Evidence, Finding, Approval, or authorization |
| Human approval is Core-recorded | Interface presents review; Core records Approval; workflow wait is not Approval |
| Retry side-effect safety | Retries must not silently duplicate Worker side effects; duplicate-safe / correlation is required as a capability |
| Redirect / new target re-evaluation | Discovered or redirected targets require Core re-evaluation before continued execution |
| Worker cannot self-authorize | Execution stays behind Core-issued authorization and immutable budget limits |
| Language-neutral Platform contract | Core/Research/Data depend on orchestration *capability*, not a product or Python-only library |

### Gate results

Multiple strategies can satisfy Stage 1 **if constrained**. None is the only capable option.

| Strategy | Stage 1 | Note |
|---|---|---|
| **Plain application orchestration** | **Pass, with a coordination gap** | Domain truth can live in PostgreSQL. Long-lived waits/retries/crash recovery of *coordination* become custom code. Does not fail Core authority by existing |
| **Job queue + persisted domain state** | **Pass, with collapse risk** | Viable if the queue is never treated as truth. Orchestration semantics still live in application code; drift vs domain lifecycle is the hazard |
| **Durable workflow engine (as the strategy)** | **Pass only as coordination** | Capable of durable timers, waits, retries, crash recovery of coordination. **Fails Stage 1 if** workflow history is treated as authorization, Approval, Evidence, or Finding. Not rejected for “being distributed” or “being durable” |
| **Hybrid** | **Pass** | Encodes the required split: coordination vs SoR vs Workers vs Core. Does not by itself select Temporal or any product |

Stage 1 does **not** pick the winner. Hybrid is selected because it is the best **combined project fit after** the gates, not because the others cannot coordinate a process.

---

## Consistency boundaries (conceptual)

These are not APIs. They test whether the strategy can hold the domain.

| Flow | Must not happen | Implication for orchestration |
|---|---|---|
| Experiment start | Worker runs without Core authorization | Orchestration may only dispatch an already-authorized request |
| Budget | Budget decremented but execution never started, or execution started without budget | Domain transaction stays in PostgreSQL (Decision 003). Orchestration must not have a second budget |
| Redirect / new asset | Worker continues onto an unauthorized target | Stop; Core re-evaluation; new authorization. Workflow progress is not a scope grant |
| Transition A | Evidence or interpretation created at WorkerResult ingest | Orchestration can trigger ingest; it cannot admit Evidence |
| Transition B | Evidence admitted as a side effect of retry/replay | Evidence admission is a separate, auditable domain transition |
| Human Review | Approval exists only as a workflow wait, or Finding created from workflow signal | Core Approval in PostgreSQL is the decision; Finding creation is the same domain transaction as Decision 003 |
| Retry | Second Worker execution duplicates side effects because a timer fired | Correlation / duplicate-safe policy at the execution boundary; not “the orchestrator said retry, so it is allowed” |
| Engine/app crash | Domain rows rewritten to match a lost workflow | PostgreSQL wins; coordination is rebuilt or abandoned; Core is re-consulted |

**Human approval wait** is primarily a **domain lifecycle** (FindingProposal pending Human Review). A coordinator may *wake* after Core records Approval. It must not *be* the Approval.

---

## Strategy evaluations

### 1. Plain application orchestration

**Fit:** Strongest **local/simple** alternative. Weakest durable-coordination story.

**Strengths:** simplest v1, minimal infrastructure, easy debugging, no extra product, natural local/in-process fit (`TECHNICAL_REQUIREMENTS.md` prefers that until topology justifies more).

**Risks:** retries, timers, crash recovery of in-flight coordination, duplicate execution, and long-lived waits become custom. Temptation to store `retry_count` / `awaiting_workflow` on domain rows, collapsing orchestration into PostgreSQL domain truth.

Not a toy. Not selected as the strategy because this domain’s coordination needs (Worker crash, later remote Workers, timers, duplicate-safe retry, wake-after-approval) would recreate a poor orchestrator inside application code and/or domain tables.

### 2. Job queue + persisted domain state

**Fit:** Serious common-pattern alternative. Not selected as the named strategy because it still leaves orchestration semantics application-owned, and this decision must **not** choose a queue/broker.

**Strengths:** async Worker dispatch, PostgreSQL can remain truth, simpler than an engine, familiar.

**Risks:** retry + domain state-machine drift; waiting/timers/approval still custom; duplicate jobs; treating the queue as durable truth (forbidden); partial “job succeeded, domain write failed.”

A queue can later be an **implementation detail** of Platform dispatch. It is not this decision.

### 3. Durable workflow engine (as the strategy)

**Fit:** Strongest **coordination-capability** class (timers, wait-for-signal, activity retries, crash recovery of orchestration, history). Premature as the v1 strategy if it means adopting an engine now, and dangerous if the engine becomes truth.

**Strengths:** long-running workflows; durable timers; wait-for-external-event; retry/timeout; activity boundaries; workflow history for *coordination* observability.

**Risks:** new infrastructure; operational complexity; replay/versioning mental model; workflow-code constraints; vendor/tool coupling; debugging curve; **product types leaking into Domain/Core**; encoding policy in workflows; human approval living only in a wait state.

Not rejected because durable execution is “wrong.” Not rejected because distributed systems fail correctness. Not selected as the v1 strategy because `TECHNICAL_REQUIREMENTS.md` forbids introducing a distributed communication plane until topology justifies it, and because fashion is not a reason. The **capability class** remains the leading product-family candidate when revisit triggers fire.

### 4. Hybrid

**Fit:** Best combined fit for this architecture.

Durable orchestration = **coordination only**.  
PostgreSQL = **authoritative domain truth**.  
Workers = **execution only**.  
Core = **authorization / policy / budget / Approval**.

This matches PROJECT_STRUCTURE.md: Platform provides orchestration capability; orchestration callers sit below Research; they dispatch work already authorized by Core; Core, Research, and Data must not depend on concrete Platform implementations.

Hybrid does **not** mean “Temporal + PostgreSQL because that is popular.” It means the three-state split is mandatory, and coordination must be allowed to become durable **without** becoming truth.

---

## Comparison matrix

Scores: **5 = better project fit**, **1 = worse project fit**. Totals are **not** the decision. **P** = plain app. **Q** = job queue + DB. **E** = durable engine as the strategy. **H** = hybrid.

| # | Criterion | P | Q | E | H | Short rationale |
|---|---|---:|---:|---:|---:|---|
| 1 | correctness | 3 | 4 | 4 | 5 | All can be correct if constrained. H makes the truth/coordination split explicit. E is correct only if it is not SoR |
| 2 | Core authority preservation | 4 | 4 | 3 | 5 | E scores lower on **temptation** to put policy in workflows, not on inability. H forbids that by role |
| 3 | durable coordination | 2 | 3 | 5 | 5 | P/Q custom. E/H designed for it. H does not require choosing E’s product now |
| 4 | long-running workflows | 2 | 3 | 5 | 5 | Domain lifecycles are long; P stores them in SoR but does not coordinate execution durably |
| 5 | crash recovery | 3 | 3 | 4 | 5 | Hard req is SoR+WorkerResult (all can). Coordination recovery is preferred; H keeps that off the SoR |
| 6 | external event waiting | 2 | 3 | 5 | 5 | P/Q poll or custom. E/H can wait without becoming the event’s meaning |
| 7 | human approval waiting | 3 | 3 | 3 | 5 | Approval is domain/Core. P/Q/E can fake it as a wait. H: wake after Core Approval only |
| 8 | retry semantics | 2 | 4 | 5 | 5 | Capability. Safe retries still need duplicate-safe execution at Worker boundaries |
| 9 | duplicate-safe execution | 2 | 3 | 4 | 4 | No strategy gives this for free. Must be a constraint on all |
| 10 | cancellation | 2 | 3 | 4 | 4 | Engine primitives help; Core/domain must still record cancelled Experiment/Run |
| 11 | timeout handling | 2 | 3 | 5 | 5 | Durable timers are E/H strength. Timeout is not a budget change |
| 12 | budget enforcement compatibility | 4 | 4 | 3 | 5 | Budget stays Core-issued. E temptation to encode remaining budget in workflow memory |
| 13 | remote Worker compatibility | 2 | 4 | 4 | 5 | Not required now. H keeps Workers replaceable. P in-process does not force remote, and must not |
| 14 | observability | 3 | 4 | 5 | 4 | E has workflow history (coordination, not audit truth). Domain audit stays AuditEvent in PostgreSQL |
| 15 | auditability | 4 | 4 | 3 | 5 | AuditEvent/Approval in SoR. E history must not replace AuditEvent |
| 16 | workflow versioning/evolution | 3 | 3 | 3 | 4 | E replay/versioning is powerful and costly. H can start thin and replace the Platform adapter |
| 17 | local development simplicity | 5 | 3 | 1 | 4 | P wins. H stays simple **while product is deferred**. E as v1 strategy loses |
| 18 | single-developer suitability | 5 | 4 | 2 | 4 | Same pattern. E ops are a v1 burden this project does not have yet |
| 19 | operational complexity | 5 | 3 | 1 | 4 | 5 = simpler. H with deferred product avoids E’s cluster. Q adds a broker we are not choosing |
| 20 | vendor lock-in risk | 5 | 3 | 2 | 4 | H is a contract. E products couple replay/SDK. Q couples broker semantics |
| 21 | Python ecosystem | 5 | 4 | 4 | 5 | Stage 2. Clients exist. **Python must not force Celery/Prefect.** H is language-neutral |
| 22 | PostgreSQL independence | 3 | 4 | 4 | 5 | P/Q tend to overload SoR with job fields. H forbids PostgreSQL-as-engine-by-accident |
| 23 | domain truth outside orchestrator | 3 | 3 | 2 | 5 | The differentiator. E fails this in practice unless Hybrid constraints are applied — which is H |
| 24 | testability | 5 | 4 | 3 | 4 | P easiest. E needs time-skipping/test servers. H contract can be faked |
| 25 | future scale | 2 | 3 | 5 | 5 | E/H headroom. Scale is not a current requirement; remote Workers are a revisit trigger |

**Reading the matrix:** Plain wins local simplicity. Queue+DB is a capable dispatch pattern without being chosen as a broker. A durable engine wins coordination primitives and loses v1 ops / truth-temptation. Hybrid wins because this project must have **both** PostgreSQL domain truth **and** durable coordination **without** letting either absorb the other.

---

## Stage 2 — Differentiators

Among Stage 1–viable constrained strategies:

| Differentiator | Winner | Why |
|---|---|---|
| Keep domain truth out of the orchestrator | Hybrid | Explicit roles; E as a *strategy* tends to invert this |
| Long-lived Worker/approval/retry coordination | Hybrid / engine-class | Domain already models FindingProposal wait; execution still needs durable dispatch |
| Single-developer / local first | Plain, then Hybrid-with-deferred-product | Requirements prefer in-process until topology justifies a plane |
| Operational complexity | Plain | Hybrid defers the engine so v1 need not pay Temporal-class ops |
| Remote Worker future | Hybrid | Workers stay replaceable; nothing here forces remote now |
| Replaceability | Hybrid | Platform contract; product later |
| Not fashionable durable-workflow | Hybrid + product DEFER | Engine class is respected and **not** installed as v1 identity |

---

## Product shortlist (evaluated, not selected)

Because the strategy allows a durable coordination layer, these products/classes were compared. **None is chosen.**

| Candidate | Strategy fit | Why not now |
|---|---|---|
| **Temporal** | Best-known fit for durable timers, signals (wake after Core Approval), activity retries, crash recovery of **coordination**, language-neutral SDK, self-host possible | Operational burden, replay/versioning model, local extra server, lock-in if workflow types leak into Domain/Core. Not truth. Not required before remote/long-wait coordination pain is measured. **Not selected because it is fashionable** |
| **Celery-style task queue** | Dispatch/retries; weaker durable wait-for-approval/timers/versioning | This is a **queue pattern**, not a durable workflow engine. Python-centric; Decision 001 must not force it. Queue must not be truth. Broker not chosen here |
| **Prefect-style workflow orchestration** | Fine for data/ML pipelines | Weak fit for Core-gated authorization, Approval→Finding, and WorkerResult trust boundaries. Cloud/tool coupling risk |
| **Custom PostgreSQL-backed state machine** | Tempting for one developer | Highest risk of **PostgreSQL becoming the workflow engine by accident** and of orchestration columns polluting domain records. Allowed later only as a *thin Platform adapter* whose tables are not domain truth — and that would be a new decision |

**PRODUCT FIT:** Temporal is the strongest **future** engine-class candidate for the hybrid coordination role. It is not accepted, not accepted-with-constraints, and not the System of Record. Celery/Prefect are weaker class fits. Custom PG state machine is not selected as the orchestration product.

---

## Constraints (accepted with the strategy)

1. **PostgreSQL remains the authoritative domain SoR.** Orchestration failure must not rewrite domain truth.
2. **Core remains authorization/policy/budget/Approval authority.** Orchestration cannot widen scope, change budget, or authorize Workers.
3. **Workers cannot self-authorize.** They execute Core-issued work and return WorkerResult.
4. **Workflow/coordination state ≠ domain truth.** It is never authorization, Evidence, Finding, Approval, or Research Memory.
5. **Human approval remains Core-recorded Approval** in PostgreSQL (FindingProposal view derived from that). It does not live only in workflow state.
6. **Retries cannot silently duplicate side effects.** Duplicate-safe / correlation / non-retryable classification are required at execution boundaries. Replay-aware execution is preferred (`TECHNICAL_REQUIREMENTS.md`) and does not select an engine.
7. **Redirect or discovered target → Core re-evaluation** before continued execution.
8. **Transition A and Transition B stay separate.** Orchestration may schedule them; it may not merge them into one “workflow step that creates Evidence.”
9. **No product-specific workflow types in Domain/Core/Research.** If an engine is chosen later, adapters live in Platform/Workers. Contracts stay language-neutral.
10. **Python does not choose the orchestrator.** Celery/Prefect convenience is not a reason.
11. **Remote Workers are not forced.** Local/in-process communication remains preferred until topology justifies more.
12. **First implementation of Platform orchestration may be thin** (in-process and/or persisted coordination records that are not domain entities). That is not a silent selection of “custom PostgreSQL workflow engine” or of a broker.
13. **Research Memory is not orchestration and not SoR.**

---

## Hard invariants (any future product)

- PostgreSQL remains authoritative domain SoR
- Core remains authorization authority
- orchestration cannot widen scope
- orchestration cannot change budget
- Worker cannot self-authorize
- workflow state ≠ domain truth
- retries cannot silently duplicate side effects
- redirect/discovered target requires Core re-evaluation
- human approval remains Core-recorded Approval
- workflow engine failure must not rewrite domain truth

---

## Strongest alternatives (not selected as the v1 strategy)

### Strongest local/simple alternative — Plain application orchestration

Not the strategy. Strong for v1 friction. Weak for durable coordination without collapsing waits/retries into domain rows or process memory.

### Strongest dispatch-pattern alternative — Job queue + persisted domain state

Not the strategy. Capable if the queue is not truth. Not chosen because this decision must not select a broker, and because orchestration semantics would remain ad-hoc relative to the required state split.

### Strongest engine-class alternative — Durable workflow engine (Temporal-class)

Not the v1 strategy and **not the product**. Serious coordination capability. Premature operational complexity; truth-temptation; not justified by current topology. Revisit when coordination durability is a measured need.

---

## Revisit triggers

Revisit **product** (not automatically invert the hybrid split) if:

- in-process/thin coordination loses waits, retries, or wake-after-approval in real runs
- duplicate Worker side effects appear under retry
- human-approval turnaround requires durable timers/signals that a thin adapter cannot hold
- remote Worker topology is actually adopted
- crash recovery of **coordination** (not domain SoR) becomes an operational incident class
- workflow versioning/replay is needed as a measured capability
- operational burden of a later engine exceeds its benefit (then stay thin or change product, do not move truth into the engine)

Do not revisit in order to make the orchestrator the SoR.

---

## Open questions after this decision

Intentionally unanswered:

- Temporal vs any other engine
- Celery or any broker/queue product
- Prefect or other pipeline orchestrators
- custom coordination schema on PostgreSQL
- Worker communication protocol
- cache
- deployment topology
- exactly how duplicate-safe Worker execution is implemented
- exactly how wake-after-Approval is delivered to the coordinator

---

## Confidence

**MEDIUM**

The hybrid **split** is the only strategy that matches Core / Platform / Workers / PostgreSQL roles without treating an engine or a queue as truth. Product is deferred because a Temporal-class engine is capable and premature, and because choosing Celery/Prefect/custom-PG-engine would either Python-lock the control plane, select a broker, or turn PostgreSQL into a workflow engine.

Confidence is not HIGH because the first thin Platform adapter is unspecified, remote Workers are future, and a poorly bounded “coordination table” could still pollute domain truth.

---

## Self-audit

| Forbidden reading | Status |
|---|---|
| Orchestrator becomes truth source | **Rejected in constraints.** PostgreSQL is SoR |
| Workflow engine owns Core policy | **Rejected.** Core only |
| Retries bypass side-effect safety | **Rejected.** Duplicate-safe is mandatory; not implemented here |
| Human approval lives only in workflow state | **Rejected.** Core Approval in PostgreSQL |
| PostgreSQL becomes workflow engine by accident | **Rejected as product.** Thin coordination adapter, if used, is not domain truth and is not selected here |
| Queue is durable truth | **Queue not chosen; must not be truth** |
| Remote workers forced before needed | **Explicitly not forced** |
| Product-specific workflow types leak into Domain/Core | **Forbidden; product deferred** |
| Python language decision forces orchestration product | **Rejected.** Celery/Prefect not selected |
| “Durable workflow” chosen only because fashionable | **Rejected.** Strategy is the split; engine product deferred |

**FINAL STATUS: PASS**

---

# Decision 005 — Worker Communication / Topology

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Depends on:** Decision 001 (Python primary; Workers may be another language); Decision 004 (hybrid orchestration; orchestration product deferred)

This decision selects **where Workers run relative to the Control Plane** for the first implementation, and **what communication abstraction** sits between them.

It does **not** select:

- HTTP vs RPC vs gRPC vs any other wire protocol
- a message broker / queue product
- a workflow-engine activity transport (Decision 004 product is deferred)
- a container runtime
- Kubernetes
- a secrets product
- a deployment model (Decision 010)

---

## Decision

**TOPOLOGY: ACCEPT WITH CONSTRAINTS — mixed**

**COMMUNICATION: ACCEPT WITH CONSTRAINTS — mixed transport behind one Worker contract**

**First working implementation:**

| Piece | Where / what |
|---|---|
| Control Plane | Local on the developer host (currently Windows + Cursor). Core, Research, Data access, Interface, and Platform contracts run here |
| Workers | Behind a single language-neutral **Worker contract**. The initial **tool-execution** worker runs in **Kali/WSL**. Local **child-process** workers are allowed for mocks, tests, and non-Kali work. In-process calls are for **test doubles only**, not the isolation story for side-effect Workers |
| Communication | One Worker contract. First implementation uses **local IPC / subprocess** (including the Windows host ↔ Kali/WSL OS-environment boundary). **No mandatory distributed broker.** No workflow-engine transport is selected |
| Future | Same Worker contract → authenticated **remote** Workers over a later-chosen request/response (or other) transport. Broker/engine remain unchosen |

Kali/WSL is the **initial security-tool integration environment**. It is **not** the architecture, **not** a production runtime mandate, and **not** a Control Plane dependency. Zest must not depend on Kali or Strix (`TECHNICAL_REQUIREMENTS.md`).

Strix is an optional **Integration** a Worker may use. Strix is **not** a Worker, not the Worker contract, and not the topology.

---

## Why this decision exists

Workers are the only side-effect layer. They cannot run without Core authorization, cannot write PostgreSQL domain truth, and return untrusted WorkerResult.

The current developer setup is Windows + Cursor, with Kali/WSL as the expected first place security tools exist. That setup must be **usable** without becoming **permanent architecture**. Remote Workers and a distributed communication plane are future possibilities, not current requirements (`TECHNICAL_REQUIREMENTS.md`: local/in-process communication first; do not introduce a distributed plane until topology justifies it).

A single in-process Worker inside the Control Plane would blur isolation, crash domains, and language-neutrality (Workers may be non-Python). A remote-only topology would be premature. A Kali-only topology would freeze WSL as production. Mixed topology + one contract is the path that keeps Core/Research stable while execution location changes.

---

## Security / trust boundaries

These are different trusts. Authentication is not authorization.

| Boundary | Trust |
|---|---|
| **Control Plane** | Higher trust: Core (authorization/policy/budget/Approval), Research (proposals), Data access to PostgreSQL SoR, Platform contracts |
| **Worker** | **Less trusted than Core.** Executes only Core-issued work. Applies immutable issued budget; cannot change it. Cannot grant itself scope |
| **Transport** | Delivery mechanism. Not domain truth. Message delivery ≠ execution success. Execution success ≠ Evidence. Communication mechanism is not the SoR |
| **Tool / Integration** | Untrusted input (including Strix, Burp, scanner output, web content). Enters as WorkerResult, then Transition A |

A remote Worker that is **authenticated** is still **not authorized** by being on the same network or by presenting a worker identity. Authorization still comes from Core, per request.

Worker identity must be **auditable now** and **authenticated when the Worker is no longer a test double** — including Kali/WSL and any later remote Worker. Identity answers “which worker runtime ran this.” Core answers “was this allowed.”

---

## Stage 1 — Mandatory gates

| Gate | Meaning |
|---|---|
| Core authority | Worker cannot authorize, widen scope, or change budget |
| WorkerResult untrusted | Output is not Observation/Evidence/Finding until Transition A / later domain transitions |
| No direct Data writes | Worker cannot write authoritative PostgreSQL domain truth |
| Language-neutral Worker contract | Core/Research do not depend on a Worker language, Kali, Strix, or a transport product |
| Transport ≠ truth | Queue/message/request is delivery, not SoR |
| Replaceability | Local → Kali/WSL → remote must not change Domain/Core contracts |
| Current env ≠ architecture | Windows/Kali/WSL must not become production topology |
| Isolation as a property | Process/OS boundary for real side effects; not a container/K8s product choice |

### Topology gate results

Multiple topologies can pass Stage 1 **if constrained**. Mixed is not the only capable option.

| Topology | Stage 1 | Note |
|---|---|---|
| **In-process workers** | **Pass for fakes only** | Viable test doubles. Weak isolation, shared fate with Control Plane, poor fit as the side-effect Worker. Does not fail Core authority by existing |
| **Local child-process workers** | **Pass** | Real process isolation, crashes stay off the Control Plane, language-neutral. Does not by itself reach Kali tools |
| **Local Kali/WSL workers** | **Pass as first tool-execution location** | Fits initial tool environment **behind the contract**. Fails Stage 1 if Kali/WSL *is* the Worker architecture or production runtime |
| **Remote workers over authenticated transport** | **Pass as a future capability** | Required properties (authn, timeout, cancel, correlation) are defined. Selecting remote *now* is Stage 2 premature distribution, not a correctness win |
| **Mixed topology** | **Pass** | One contract; first impl uses child-process + Kali/WSL; remote later. Does not select a broker |

### Communication gate results

| Strategy | Stage 1 | Note |
|---|---|---|
| **A. Direct in-process call** | **Pass for fakes** | Not the side-effect Worker path |
| **B. Local IPC / subprocess** | **Pass** | First-implementation primary. Includes host ↔ WSL as a **local OS-environment** boundary, not a distributed plane |
| **C. Direct request/response transport** | **Pass as future** | Natural remote path. Protocol **not** chosen |
| **D. Queue/broker mediated execution** | **Pass only if never truth** | Not mandatory. Broker product not chosen. Premature as a required plane |
| **E. Workflow-engine activity transport** | **Not available as a choice** | Decision 004 deferred the engine product. Must not sneak-select Temporal/Celery here |
| **F. Mixed transport behind one Worker contract** | **Pass** | Selected. A/B now; C later; D/E remain unchosen and non-mandatory |

---

## Candidate evaluations (topology)

### In-process workers

Simplest debugging. Shared address space with Control Plane. A Worker crash, native tool fault, or hung scan can take down Core. Decision 001 forbids subprocess and tool SDKs in Core/Research; in-process side-effect Workers pull those hazards into the Control Plane process. Allowed as **mocks/fakes**. Not the first real Worker topology.

### Local child-process workers

Serious default isolation story: separate process, timeout/kill, different language possible, filesystem/environment can differ from Control Plane. Does not by itself provide Kali toolchains. Complements Kali/WSL rather than replacing the contract.

### Local Kali/WSL workers

Serious first **tool-execution** location: security tools live there; Windows host is the Control Plane/dev environment. Windows ↔ WSL has environment, filesystem, and tool-availability differences — that is why the Worker is not in-process on Windows. This is still **local** (same developer machine), not remote. Must not make WSL a required production runtime or a Core dependency.

### Remote workers over authenticated transport

Serious future: other hosts, worker pools, authenticated identity, network untrust. Latency, artifact transfer, cancellation, and result size become harder. Not selected for first implementation. `TECHNICAL_REQUIREMENTS.md` forbids introducing that plane until topology justifies it.

### Mixed topology

Selected. Control Plane stays local. Worker **abstraction** exists from day one. First tool-execution worker is Kali/WSL. Child-process workers exist for isolation and tests. Remote is an evolution of the **same contract**, not a new Domain.

---

## Candidate evaluations (communication)

**A** — Direct in-process call: fakes only.

**B** — Local IPC/subprocess: first implementation. No broker. Delivery still ≠ success; the Control Plane records WorkerResult in PostgreSQL after receipt/validation, not because a pipe closed.

**C** — Direct request/response: future remote. Product/protocol deferred. Must support authentication, timeout, retry, cancellation, correlation, auditability (`TECHNICAL_REQUIREMENTS.md` §4).

**D** — Queue/broker: optional later dispatch aid. Not truth. Not mandatory. Not chosen.

**E** — Workflow-engine activity transport: blocked on Decision 004 product DEFER.

**F** — Mixed behind one contract: selected so Core/Research never see Kali vs remote vs child-process.

Correlation id, cancellation, timeout, and duplicate/retry awareness are **contract properties**, not broker properties. They must exist on the Worker contract in the first implementation even with only IPC/subprocess.

Redirect / discovered asset: Worker **stops**, returns through WorkerResult / a re-authorization request, and does not continue until a **new Core decision**. Topology does not grant scope.

---

## Comparison matrix (project fit)

**5 = better project fit.** Totals are not the decision.

**IP** = in-process. **CH** = local child-process. **KW** = Kali/WSL local. **RM** = remote now. **MX** = mixed (selected).

| # | Criterion | IP | CH | KW | RM | MX | Short rationale |
|---|---|---:|---:|---:|---:|---:|---|
| 1 | Core authority preservation | 3 | 4 | 4 | 4 | 5 | All can obey Core if constrained. MX keeps one contract so location cannot become authorization |
| 2 | Worker isolation boundary | 1 | 5 | 4 | 5 | 5 | IP shares fate. CH/KW/RM isolate. MX uses CH+KW first |
| 3 | local development simplicity | 5 | 4 | 3 | 1 | 4 | KW adds WSL friction that the current tool env needs. RM loses |
| 4 | Kali/WSL integration | 1 | 2 | 5 | 3 | 5 | KW is the first tool host. MX includes it without making it the architecture |
| 5 | remote-worker future | 1 | 3 | 3 | 5 | 5 | Same contract. RM-now is premature, not “more future” |
| 6 | authentication capability | 2 | 3 | 3 | 5 | 4 | Local still needs worker **identity**. Remote requires authn. Authn ≠ authz |
| 7 | cancellation | 3 | 4 | 4 | 4 | 4 | Process kill vs remote cancel; contract must support both. No product chosen |
| 8 | retry / duplicate safety | 3 | 4 | 4 | 3 | 4 | Correlation on the contract. Transport redelivery must not mean “run again” |
| 9 | correlation / auditability | 3 | 4 | 4 | 4 | 5 | Worker identity + correlation id + AuditEvent in PostgreSQL, not in the pipe |
| 10 | debugging | 5 | 4 | 3 | 1 | 4 | Local wins. KW is still on-machine |
| 11 | failure isolation | 1 | 5 | 4 | 5 | 5 | Worker crash must not take Core down |
| 12 | artifact / result transfer | 4 | 4 | 3 | 2 | 4 | Local copies first. Remote transfer is a later problem; bytes still not SoR |
| 13 | operational complexity | 5 | 4 | 3 | 1 | 4 | 5 = simpler. MX first impl has no broker |
| 14 | single-developer suitability | 5 | 4 | 4 | 1 | 4 | Matches current Windows + Kali/WSL **context** |
| 15 | portability | 2 | 4 | 2 | 4 | 5 | KW is not portable architecture. MX can drop WSL later |
| 16 | production evolution | 1 | 3 | 2 | 4 | 5 | Path without Domain change |
| 17 | vendor lock-in | 5 | 5 | 4 | 3 | 5 | No broker/K8s/Strix required |
| 18 | observability | 3 | 4 | 4 | 4 | 4 | Worker execution is observable; transport is not the audit log |
| 19 | security boundary clarity | 2 | 4 | 4 | 4 | 5 | Explicit Control Plane vs Worker vs Transport vs Integration |
| 20 | local → remote migration | 1 | 3 | 3 | 5 | 5 | Contract stability is the point of MX |
| 21 | avoiding premature distribution | 5 | 5 | 4 | 1 | 5 | No mandatory broker/remote plane |

Communication strategies (project fit for **first impl + evolution**): A=2 as primary, B=5, C=3 (future), D=2 (premature mandatory), E=1 (product deferred), F=5.

---

## Stage 2 — Why mixed + contract wins

Not because other topologies cannot execute a tool.

This project needs: Core-gated side effects, Kali tools on day one, child-process isolation, no broker, and a later remote Worker **without** rewriting Domain/Core.

- In-process fails isolation and language-neutral Workers.
- Child-process alone misses the stated first tool environment.
- Kali/WSL alone would freeze WSL as architecture.
- Remote now violates local-first and single-developer constraints.
- A broker or workflow-activity bus would select communication/orchestration products this file has deferred.

---

## Constraints

1. **One language-neutral Worker contract.** Core/Research/Data do not know Windows vs WSL vs remote.
2. **Side-effect Workers are out-of-process** relative to the Control Plane (child-process and/or Kali/WSL). In-process is fakes/tests only.
3. **Worker cannot write authoritative PostgreSQL.** WorkerResult is accepted by Control Plane / Data ingest (Transition A), not by the Worker opening the SoR.
4. **WorkerResult remains untrusted** until Transition A. Execution success ≠ Evidence.
5. **Transport is replaceable and is not truth.** Delivery ≠ execution success.
6. **No mandatory distributed broker** in the first implementation.
7. **No workflow-engine activity transport** until Decision 004’s product is decided.
8. **Kali/WSL is an adapter location**, not a Core dependency, not Strix, not production-by-default.
9. **Worker identity** is auditable; **authentication** is required when the Worker is reachable as a service (including later remote). Authentication ≠ Core authorization.
10. **Redirect / new asset:** stop, re-authorization from Core, no continue-on-topology.
11. **Correlation id, cancellation, timeout, duplicate/retry awareness** are contract requirements now.
12. **Immutable issued budget** is applied by Worker/Platform runtime; Worker cannot raise it.
13. **Deployment topology is not domain logic** (see Decision 010).

---

## Strongest alternatives (not selected as the v1 topology)

- **Strongest simple isolation alternative:** local child-process only — misses initial Kali/WSL tool execution unless WSL is smuggled in without a contract.
- **Strongest first-tool-environment snapshot:** Kali/WSL-only — would make WSL look like the architecture.
- **Strongest future plane:** remote authenticated request/response — correct later, premature now.
- **Not selected communication:** mandatory queue/broker; workflow-engine activities.

---

## Revisit triggers

- Concrete need for Workers on another machine (then evaluate C; still no automatic broker)
- IPC/subprocess cannot carry result/artifact size, cancellation, or concurrency
- Duplicate side effects under retry despite correlation ids
- WSL/host boundary becomes an operational blocker (change **adapter**, not Domain)
- Authentication of local workers is insufficient for the actual threat model
- Decision 004 selects an engine whose activity transport is considered (new decision; must not become truth)

---

## Open questions

- Exact IPC mechanism (pipes, localhost sockets, etc.)
- Exact future remote protocol
- Broker yes/no as a later dispatch aid
- Artifact byte movement across WSL/remote
- Worker authentication implementation (not a secrets product choice)

---

## Confidence

**MEDIUM**

The first-implementation shape matches locked requirements (local Control Plane, Worker abstraction, Kali/WSL tool worker, no mandatory broker) without freezing Windows/Kali as architecture. Confidence is not HIGH because the concrete IPC mechanism, WSL authn, and artifact transfer are unchosen, and a sloppy adapter could still let a Worker touch PostgreSQL or treat WSL as Core.

---

# Decision 006 — Artifact Storage Strategy

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Depends on:** Decision 002 (Artifact metadata/reference in SoR; bytes topology open); Decision 003 (PostgreSQL is not the default artifact byte store); Decision 005 (Worker contract; Kali/WSL first tool Worker); Decision 010 (staged local-first deployment)

This decision selects **where artifact bytes live** relative to PostgreSQL, and whether a **byte-store abstraction** exists.

It does **not** select:

- an object/blob product (S3, MinIO, GCS, Azure Blob, etc.)
- a filesystem layout or path convention as domain contract
- a hash-function “product”
- a CDN, backup product, or queue for artifact transfer
- ORM, frontend, or cache product

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS**

**Artifact byte-store abstraction**, with **local filesystem as the first adapter**, **metadata/reference/integrity in PostgreSQL**, and **object/blob storage later only if volume or topology requires it**.

| Concern | Where |
|---|---|
| Artifact **identity**, reference, provenance, kind, lifecycle, integrity metadata (including a content hash) | **PostgreSQL SoR** |
| Artifact **bytes** | **Byte-store adapter** — first implementation: **local filesystem**. Not domain truth |
| Byte **location** | Opaque locator stored in SoR. Location may change. **Identity must not** |

The byte store is not Evidence authority, Finding authority, Research Memory, or Core policy.

If artifact bytes are lost or the filesystem is wiped: **domain truth remains** (identity, provenance, Evidence, Finding, Approval). Missing bytes are a **reproducibility/availability** failure, not a silent rewrite of Findings. Integrity is checked by comparing stored bytes to the SoR hash when bytes are present.

No object-storage **product** is selected.

---

## Why this decision exists

Artifacts include screenshots, browser traces, HTTP captures, files, schemas, logs, code fragments, large WorkerResult payloads, and reproducibility material.

DOMAIN_MODEL.md: Artifact ≠ Evidence; attachment ≠ admission; integrity metadata should be preserved; storage locator is conceptual, not a product.

Decision 002/003: metadata/reference/provenance are SoR; PostgreSQL is **not** the default byte dump; bytes may sit outside the rebuild-from-SoR rule.

`TECHNICAL_REQUIREMENTS.md`: identity, provenance, integrity, and lifecycle metadata are hard; separate large/binary storage is **preferred**, not a mandated product.

Staged local-first deployment and Kali/WSL Workers mean bytes will cross a host/WSL boundary. That transfer must not make the Worker, WSL path, or a bucket into authority.

---

## Stage 1 — Mandatory gates

| Gate | Meaning |
|---|---|
| SoR holds identity/provenance/integrity | Bytes cannot be the Artifact’s identity |
| Bytes ≠ truth | Byte-store loss must not delete Evidence/Finding/Approval |
| Artifact ≠ Evidence | Storing bytes does not admit Evidence |
| Locator ≠ identity | Moving bytes must not mint a new Artifact id |
| Worker ≠ authority | Remote/Kali Worker sending bytes does not authorize or admit Evidence |
| Portability | Windows path semantics must not enter Domain/Core contracts |
| No premature product | Object store not required to start |

### Strategy gate results

Multiple strategies can store bytes. The selected one is not the only capable store.

| Strategy | Stage 1 | Note |
|---|---|---|
| **1. Bytes in PostgreSQL** | **Pass as capability, fail as default SoR role** | Can store blobs. Turns PostgreSQL into a dump; conflicts with Decision 003 “not the default artifact byte store.” Backup/size risk |
| **2. Local filesystem only (no abstraction)** | **Pass locally, fail as architecture** | Fine for a laptop. Path leakage, Windows vs WSL, and “FS is production” fail the locator≠identity and portability gates if FS *is* the contract |
| **3. Object/blob product in v1** | **Pass as a later adapter** | Premature product and ops. Abstraction is right; selecting a cloud/object product now is Stage 2 failure |
| **4. Hybrid small-in-SoR / large-external** | **Pass weakly** | Two byte homes and a size threshold. Small blobs still dump into PostgreSQL. Threshold becomes a magic constant |
| **5. Byte-store abstraction; FS first; object later** | **Pass** | SoR keeps identity/hash; first adapter is local FS; product deferred; location replaceable |

---

## Candidate evaluations

### 1. Bytes in PostgreSQL

Transactional with metadata; one backup story. Makes the SoR a blob store, inflates backups, weakens streaming/large-binary handling, and contradicts Decision 003. Not selected as the strategy. Tiny inline exceptions are **not** granted here (that would be strategy 4 by stealth).

### 2. Local filesystem without abstraction

Simplest v1 ops. Highest leak risk: `C:\...` / `/mnt/c/...` / WSL paths in domain records; FS becomes truth; later object migration rewrites “identity.” Not selected as the *strategy*; FS **is** allowed as the first **adapter**.

### 3. Object/blob storage in v1

Right long-term *class* for volume and remote Workers. Premature product, extra account/ops, vendor gravity. Not selected now. Remains the leading **future adapter** when revisit triggers fire.

### 4. Hybrid small/large split

Looks pragmatic; splits authority of bytes across SoR and external store; size cutover is arbitrary; complicates integrity and backup. Not needed for first implementation. Rejected as v1 strategy.

### 5. Abstraction + filesystem first + object later

Selected. Matches local-first (Decision 010), Kali/WSL transfer behind the Worker contract (Decision 005), and Decision 002’s byte exception without promoting FS or S3 to SoR.

---

## Comparison matrix (project fit)

**PG** = bytes in PostgreSQL. **FS** = filesystem as the contract. **OB** = object product in v1. **HY** = size-split hybrid. **AB** = abstraction + FS first (selected). 5 = better fit.

| # | Criterion | PG | FS | OB | HY | AB |
|---|---|---:|---:|---:|---:|---:|
| 1 | correctness | 3 | 3 | 4 | 3 | 5 |
| 2 | truth-boundary clarity | 2 | 2 | 4 | 2 | 5 |
| 3 | local simplicity | 4 | 5 | 1 | 3 | 4 |
| 4 | single-developer ops | 4 | 5 | 1 | 3 | 4 |
| 5 | portability | 4 | 1 | 4 | 3 | 5 |
| 6 | Windows/Linux compatibility | 4 | 2 | 4 | 3 | 5 |
| 7 | Kali/WSL integration | 3 | 2 | 3 | 3 | 4 |
| 8 | remote Worker future | 2 | 1 | 5 | 3 | 5 |
| 9 | failure recovery | 3 | 3 | 4 | 3 | 5 |
| 10 | backup/restore implications | 2 | 3 | 3 | 2 | 4 |
| 11 | retention support | 3 | 3 | 4 | 3 | 4 |
| 12 | integrity verification | 4 | 3 | 4 | 3 | 5 |
| 13 | operational burden | 3 | 5 | 1 | 2 | 4 |
| 14 | testability | 4 | 4 | 2 | 3 | 5 |
| 15 | migration path | 2 | 1 | 3 | 2 | 5 |
| 16 | scalability | 2 | 2 | 5 | 3 | 4 |
| 17 | vendor lock-in | 4 | 5 | 2 | 3 | 5 |
| 18 | security boundary | 3 | 3 | 4 | 3 | 5 |
| 19 | rebuildability | 2 | 2 | 3 | 2 | 4 |
| 20 | avoiding premature infrastructure | 4 | 5 | 1 | 3 | 5 |

Bytes are **not** rebuildable from SoR (Decision 002 exception). Rebuildability here means: metadata/provenance survive; a new byte adapter can be attached using the same identity/hash; companions/Research Memory are not the byte store.

---

## Conceptual integrity, retention, transfer

**Integrity:** PostgreSQL stores integrity metadata including a **content hash**. The byte adapter stores opaque bytes. On read, bytes are verified against the SoR hash. Hash mismatch = untrusted/corrupt bytes, not a new Finding. Exact hash algorithm is an open implementation detail, not a product.

**Stable reference:** Artifact id in SoR. Locator is a replaceable pointer (URI/key), never a Windows path in Domain/Core.

**Dedup:** Optional later using the hash. Not required in v1. Dedup must not merge distinct provenance records.

**Deletion / retention authority:** Domain lifecycle in PostgreSQL (and Core policy if retention is a control rule). The byte adapter **executes** byte deletion when the domain says so. The filesystem/object store must not be the retention policy owner. Supersede = **new Artifact** (DOMAIN_MODEL.md), not in-place byte rewrite of an identity.

**Immutable bytes:** In-place overwrite of stored bytes for an existing Artifact identity is forbidden. Correction = new Artifact + new hash.

**Kali/WSL / remote Workers:** Worker produces raw material. Control Plane accepts it through the Worker contract, writes bytes via the **byte-store adapter**, writes metadata/hash via Data/PostgreSQL at Transition A. The Worker never writes SoR or “becomes” the store. Future remote transfer is the same ingest path; protocol remains unchosen (Decision 005).

**Streaming / large binaries:** Adapter concern. PostgreSQL is not required to stream blobs.

**Backup:** SoR backup is Decision 003 (implementation open). Byte-store backup is separate and may lag; missing bytes must not be “repaired” by deleting Evidence.

---

## Constraints

1. **Metadata/reference/provenance/integrity in PostgreSQL.** Bytes elsewhere (first: local FS adapter).
2. **Byte-store abstraction exists now** as a Data/Platform capability. **Product does not.**
3. **Identity ≠ location.** Relocating bytes does not change Artifact id.
4. **Byte loss ≠ truth loss.** Evidence/Finding/Approval remain. Availability of raw material may degrade.
5. **Artifact ≠ Evidence.** Bytes and attachments never skip Transition B.
6. **No object-storage product in this decision.**
7. **Local filesystem is the first adapter, not production architecture** and not a Domain path contract.
8. **Windows/WSL paths must not leak into Core/Research contracts.**
9. **Workers (including remote later) do not gain authority by shipping bytes.**
10. **In-place mutation of bytes for an existing identity is forbidden.**
11. **Retention/deletion policy is not the bucket/FS lifecycle UI.**
12. **Research Memory is not the byte store.**

---

## First implementation / future evolution

**First implementation:** Control Plane uses a byte-store **port**. Adapter writes/reads a local filesystem. PostgreSQL stores id, provenance, hash, opaque locator, lifecycle. Tests may use a temp directory adapter. No S3/MinIO required.

**Future:** Same port; object/blob adapter if size, remote Workers, or backup/topology require it. Hybrid size-split remains unselected unless a later decision justifies it.

---

## Strongest alternatives (not selected)

- **Strongest single-store alternative:** PostgreSQL bytes — rejected as default dump.
- **Strongest local-ops alternative:** filesystem-as-contract — rejected as architecture.
- **Strongest scale alternative:** object store in v1 — premature product.

---

## Revisit triggers

- Artifact volume or size makes local FS backup/ops painful **as observed**
- Remote Workers make a local disk adapter insufficient (still the same port)
- Need for multi-host byte access that is not “Worker uploads to Control Plane”
- Integrity/hash verification incidents
- Retention/deletion cannot be executed safely on FS
- Pressure to put blobs back into PostgreSQL (treat as a smell, not a silent revert)

---

## Open questions

- Exact hash algorithm
- Filesystem directory layout (adapter-private)
- Object product when triggered
- Cross-WSL copy mechanism (Worker contract, not Domain)
- Dedup policy
- Byte-store backup/restore implementation

---

## Confidence

**MEDIUM**

The split (SoR metadata/hash vs replaceable bytes) is required by Decisions 002/003 and the domain model. Filesystem-first matches staged local deployment without selecting S3. Confidence is not HIGH because hash algorithm, WSL transfer, and backup of bytes are unchosen, and a leaky locator could still freeze Windows paths.

---

# Decision 007 — Cache / Ephemeral State Strategy

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Depends on:** Decision 003 (PostgreSQL SoR); Decision 004 (orchestration state ≠ domain truth; orchestration product deferred); Decision 005 (local-first Workers)

This decision selects whether the first implementation needs a **dedicated cache**, and what ephemeral state is allowed.

It does **not** select Redis, Memcached, key-value products, CDN, ORM query-cache settings, or a frontend cache.

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS**

**No dedicated cache product and no cache Platform abstraction in the first implementation.**

**Answer:** A separate cache product is **not** required for the first implementation.

| Kind | Allowed in v1? |
|---|---|
| Dedicated cache product (Redis-class) | **No** |
| Cache port/abstraction as a Platform capability | **Not yet** — would invite a product-shaped hole |
| In-process, recomputable, process-local memoization / request scratch | **Yes**, if loss cannot change authorization, budget, Evidence, Finding, Approval, or Candidate lifecycle |
| PostgreSQL as a “cache” | **No** — SoR is not an ephemeral store; orchestration coordination records are Decision 004, not cache |
| Distributed cache from the start | **No** |

Cache / ephemeral state is **never** authoritative for: authorization, scope, budget, Evidence, Finding, Approval, Candidate lifecycle, Research Memory truth, or WorkerResult history.

If cache or in-process memory is lost: **authoritative domain state must remain** in PostgreSQL. Loss must not be a correctness incident.

---

## Why this decision exists

Default is not to add infrastructure. First implementation is single-developer, local Control Plane, local PostgreSQL, mixed local/Kali Workers (Decisions 005/010). There is no measured read-latency, session-fan-out, or multi-Control-Plane problem that a cache would solve.

A cache that stores “allowed scope” or “remaining budget” becomes a second SoR. Decision 002 already forbids sneaking a second SoR into a “cache.”

Decision 004 already separated **orchestration state** (timers, retries, waits) from domain truth. That is not Redis. Do not re-home it here.

---

## Stage 1 — Mandatory gates

| Gate | Meaning |
|---|---|
| Cache ≠ SoR | Cache miss/loss cannot drop Program, Approval, Evidence, Budget authority, etc. |
| No authz/budget cache-as-truth | Serving an allow/deny or remaining budget from cache as authority fails |
| Rebuildable / recomputable | Anything cached must be reconstructible from SoR or is disposable |
| No premature distributed cache | Not justified by current topology |
| Orchestration ≠ cache | Decision 004 coordination is not this decision |

### Candidate results

| Candidate | Stage 1 | Note |
|---|---|---|
| **1. No dedicated cache in v1** | **Pass** | SoR + Worker contract suffice. Selected |
| **2. In-process memory cache** | **Pass if strictly ephemeral** | Allowed as process-local acceleration, not as a product strategy |
| **3. PostgreSQL-backed ephemeral state** | **Fail as cache strategy** | Pollutes SoR or creates unofficial orchestration tables. Use Decision 004 for coordination |
| **4. Cache abstraction, no product** | **Pass later, not now** | A port without a need becomes Redis-shaped. Add when a trigger exists |
| **5. Distributed cache from start** | **Fail v1 fit** | Extra consistency, invalidation, ops. Premature distribution |

---

## Candidate evaluations

### 1. No dedicated cache (selected)

Correctness-preserving default. Local PostgreSQL and in-process Control Plane do not need a side cache for the first Evidence/FindingProposal loop. Invalidation complexity stays zero.

### 2. In-process ephemeral

Acceptable for: memoizing **recomputable** derived views, request-scoped scratch, test fixtures, maybe **recomputable** rate-limit counters that fail closed (deny/retry) rather than fail open.

Unacceptable for: authorization decisions, remaining budget, Evidence, “is this Finding accepted,” WorkerResult history, Research Memory.

Multi-process: in-process memory is **not** shared. That is fine until a revisit trigger says shared ephemeral is needed — then design a cache **port**, still not necessarily a product, and still not truth.

### 3. PostgreSQL as ephemeral cache

Looks convenient; becomes undeletable “cache tables” next to Approval. Mixing TTL rows with immutable Evidence is a schema and ops hazard. Rejected as cache strategy.

### 4. Cache abstraction now, product later

Right *shape* if we already knew we would cache. We do not. Introducing the port now is premature infrastructure of another kind.

### 5. Distributed cache from start

No multi-node Control Plane. Remote Workers (future) still must not read budget/authz from Redis. Rejected.

---

## Comparison matrix (project fit)

**NO** = no dedicated cache. **MEM** = in-process as the *strategy*. **PG** = PostgreSQL ephemeral. **ABS** = cache port now. **DIST** = distributed cache now.

| # | Criterion | NO | MEM | PG | ABS | DIST |
|---|---|---:|---:|---:|---:|---:|
| 1 | correctness | 5 | 4 | 2 | 4 | 2 |
| 2 | truth-boundary clarity | 5 | 4 | 2 | 4 | 2 |
| 3 | local simplicity | 5 | 5 | 3 | 3 | 1 |
| 4 | single-developer ops | 5 | 5 | 3 | 3 | 1 |
| 5 | portability | 5 | 5 | 4 | 4 | 3 |
| 6 | Windows/Linux compatibility | 5 | 5 | 5 | 5 | 3 |
| 7 | Kali/WSL integration | 5 | 5 | 4 | 4 | 2 |
| 8 | remote Worker future | 4 | 3 | 3 | 4 | 3 |
| 9 | failure recovery | 5 | 4 | 3 | 4 | 2 |
| 10 | backup/restore implications | 5 | 5 | 3 | 4 | 2 |
| 11 | retention support | 5 | 5 | 2 | 4 | 2 |
| 12 | integrity verification | 5 | 4 | 3 | 4 | 2 |
| 13 | operational burden | 5 | 5 | 3 | 3 | 1 |
| 14 | testability | 5 | 4 | 3 | 4 | 2 |
| 15 | migration path | 4 | 3 | 2 | 5 | 3 |
| 16 | scalability | 3 | 2 | 3 | 4 | 5 |
| 17 | vendor lock-in | 5 | 5 | 4 | 4 | 2 |
| 18 | security boundary | 5 | 4 | 2 | 4 | 2 |
| 19 | rebuildability | 5 | 4 | 2 | 4 | 3 |
| 20 | avoiding premature infrastructure | 5 | 4 | 3 | 3 | 1 |

**NO** wins Stage 2 because there is no first-implementation need. **MEM** is allowed as a *tactic* under NO, not as a competing product strategy.

---

## What cache must never hold (as authority)

- authorization / scope allow-set
- budget remaining / issued limits
- Evidence, Finding, Approval
- Candidate lifecycle
- Research Memory as truth
- WorkerResult authoritative history
- Artifact identity/provenance (that is SoR; bytes are Decision 006)

Stale cache must **not** create a conflict that “wins” against PostgreSQL. If a cache exists later and disagrees, **PostgreSQL wins**; the cache is dropped or rebuilt.

---

## When in-process ephemeral is acceptable

- Process-local
- Recomputable from SoR or obviously disposable
- Restart/loss does not change Core decisions or recorded history
- Never used as the allow/deny or budget remaining
- Not shared with Workers as an authorization channel (Workers get issued, immutable Core decisions)

---

## First implementation / future evolution

**First implementation:** No Redis-class product. No cache port. Optional in-process memoization under the constraints above.

**Future:** Revisit a **cache abstraction** (still not automatically a product) when triggers fire. Distributed cache only if topology is actually multi-node **and** the data is non-authoritative.

Remote Workers do **not** by themselves require a distributed cache. They require the Worker contract (Decision 005) and Core-issued authorization.

---

## Constraints

1. **No dedicated cache product in v1.**
2. **No cache Platform port until a revisit trigger.**
3. **Cache/ephemeral ≠ domain truth.** Loss ≠ correctness loss.
4. **Do not cache authorization or budget as authority.**
5. **Do not use PostgreSQL as a TTL cache.**
6. **Do not treat Decision 004 coordination tables as a general cache.**
7. **Workers must not read a cache instead of Core.**
8. **Python/in-process dicts are not an architecture contract.**

---

## Revisit triggers

- Measured Control Plane read latency against PostgreSQL that indexing/schema cannot fix
- Multiple Control Plane processes needing **non-authoritative** shared ephemeral state
- Rate-limit / memoization that is unsafe to keep process-local **and** still fail-closed
- Phase B/C topology (Decision 010) with a concrete shared-ephemeral need — still not authz truth
- Discovering that in-process memoization cached a Core decision (remove it; do not “fix” by adding Redis)

---

## Open questions

- Whether a cache **port** is added at first trigger (product still optional)
- Which product if any (not chosen)
- Rate-limit implementation (must fail closed; not chosen)

---

## Confidence

**MEDIUM**

No dedicated cache is the only v1 choice that avoids a second SoR and extra ops without a demonstrated need. Confidence is not HIGH because later multi-process or latency triggers are plausible, and sloppy in-process memoization could still cache budget/authz if undisciplined.

---

## Self-audit (Decisions 006 and 007)

| Forbidden reading | Status |
|---|---|
| PostgreSQL became an artifact byte dump | **No.** Bytes go to a replaceable adapter; PG holds metadata/hash |
| Filesystem became domain truth | **No.** First adapter only; identity ≠ location |
| Object storage selected prematurely | **No.** Product deferred |
| Byte loss deletes Evidence/Finding truth | **No.** SoR remains; bytes are availability |
| Artifact automatically became Evidence | **No.** Transition B still required |
| Cache became authorization truth | **No.** Dedicated cache not selected; authz cache forbidden |
| Cache became budget authority | **Forbidden.** |
| Cache loss equals correctness loss | **No.** Loss must be disposable |
| Distributed cache selected unnecessarily | **No.** |
| Local filesystem became permanent production constraint | **No.** Adapter, not architecture |
| Windows path semantics leaked into architecture | **Forbidden** in Domain/Core contracts |
| Remote Worker made mandatory | **No.** |
| Product became architecture contract | **No.** Ports exist; S3/Redis not chosen |

**FINAL STATUS: PASS**

---

# Decision 008 — Model Abstraction / Provider Strategy

**Status:** ACCEPT WITH CONSTRAINTS (abstraction) / DEFER (provider product)  
**Date:** 2026-08-16  
**Depends on:** Decision 001 (Python primary; Core/Research no provider SDKs); Decision 002 (Research Memory is not SoR)

This decision selects how **model calls** enter Zest: the port, replaceability, and whether v1 routes across many models.

It does **not** select:

- OpenAI, Anthropic, Google, Azure, Ollama, or any other provider product
- a model id or model family as architecture
- LangChain, LlamaIndex, or any model framework
- an embedding provider (Decision 009)
- Strix as the model layer

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS**

**Single primary provider (unchosen) + replaceable ModelPort / ModelAdapter + routing-ready contract. True multi-provider and live multi-model routing only when needed.**

| Piece | v1 |
|---|---|
| **Abstraction** | Language-neutral **ModelPort**. Concrete **ModelAdapter** in Integrations. Core and Research **do not** import provider SDKs |
| **First implementation** | One primary provider adapter, one default model (both **unchosen** as products). Structured proposal in, structured proposal out |
| **Provider product** | **DEFER** |
| **Routing** | **Contract ready, not live.** v1 does **not** run a real cheap/medium/strong/verifier mesh. Roles exist as **capability metadata** so routing can be added later |

Model output is always **UNTRUSTED STRUCTURED PROPOSAL**. It cannot grant scope, authorization, or budget; cannot create Evidence; cannot accept a Finding; cannot execute tools.

Strix reasoning output, if Strix is used at all, follows the **same** rule. Strix is an optional Integration, not the ModelPort owner and not Zest.

---

## Why this decision exists

`TECHNICAL_REQUIREMENTS.md`: providers stay replaceable; model output is untrusted; cost/budget observable; secrets out of model context where possible; prompt/context provenance possible; preferred ability to **route** cheaper vs stronger models later. None of that chooses a vendor.

PROJECT_STRUCTURE.md: Core must not host model-specific logic; Core and Research must not import concrete Integrations; changing a model/provider must not break the domain.

Putting a provider SDK in Research would lock the control plane to one vendor and treat SDK types as domain types. Building a full router on day one would be premature complexity. Deferring the **port** would make the first adapter a permanent architecture.

---

## Model roles (capability metadata, not v1 product matrix)

These are **jobs** a later router may assign. v1 does not require a distinct model for each:

- cheap classification
- medium reasoning
- strong reasoning
- verification
- summarization
- structured proposal generation

**Deterministic / non-LLM work first:** lookups, lifecycle, authorization, and SoR reads are not model tasks. A “router” that sends Program/Asset queries to an LLM is a design failure, not routing.

---

## Routing answer

**v1 must not perform real multi-model routing.** The ModelPort is **routing-ready** (model id, role hint, timeout, retry, cancel, cost/token accounting, correlation, capability metadata). The first implementation calls **one** configured adapter/model.

Live routing (cheap vs strong vs verifier) is a later decision when cost, quality, or verification needs are measured.

---

## Stage 1 — Mandatory gates

| Gate | Meaning |
|---|---|
| Untrusted output | Model text is never authorization, Evidence, Finding, or fact |
| No SDK in Core/Research | Adapters live in Integrations |
| Replaceability | Swapping provider must not change Domain/Core |
| Secrets isolation | Provider keys must not be required in LLM context or Domain records |
| Auditability | Model calls correlatable: run, model id/version, tokens/cost, prompt/context provenance as records/references — not conversation-history-as-SoR |
| Strix ≠ architecture | Optional adapter; same untrusted-proposal path |
| Language-neutral contract | Python does not force a Python-only LLM framework |

### Strategy results

Multiple strategies can call a model. Direct-in-Research cannot pass.

| Strategy | Stage 1 | Note |
|---|---|---|
| **1. Single provider directly in Research** | **Fail** | SDK and vendor types in Research. Forbidden |
| **2. Thin provider abstraction** | **Pass** | Replaceable. Weaker on designed-in routing/cost/role metadata |
| **3. Full model-router abstraction in v1** | **Pass as a later shape** | Live multi-model routing now is Stage 2 premature complexity |
| **4. Multi-provider from day one** | **Pass as capability, fail v1 fit** | No demonstrated need; ops and test matrix explode |
| **5. One primary + replaceable adapter + routing-ready** | **Pass** | Selected. Port now; product deferred; routing later |

---

## Candidate evaluations

**1. Direct in Research** — fastest demo, architecture break. Rejected.

**2. Thin abstraction** — minimum replaceability. Risk: a single `complete(prompt)` with no model id, cost, or provenance, so routing never fits. Not selected as the whole strategy; the selected port is thin **plus** those fields.

**3. Router in v1** — matches preferred “cheaper vs stronger” too early. Extra failure modes, eval cost, and fake sophistication. Not selected for first implementation.

**4. Multi-provider day one** — replaceability theater. Two SDKs without a need. Rejected for v1.

**5. Primary + adapter + routing-ready** — selected. One adapter to ship the first loop; contract already has correlation, timeout, retry, cancellation where possible, structured outputs, cost/token accounting, model id/version, capability metadata, prompt/context provenance, secret isolation.

---

## Comparison matrix (project fit)

**DR** = direct in Research. **TH** = thin only. **RT** = live router v1. **MP** = multi-provider v1. **PR** = primary + port + routing-ready (selected).

| # | Criterion | DR | TH | RT | MP | PR |
|---|---|---:|---:|---:|---:|---:|
| 1 | correctness | 1 | 4 | 4 | 4 | 5 |
| 2 | provider lock-in risk | 1 | 4 | 4 | 3 | 5 |
| 3 | Core/Research boundary cleanliness | 1 | 4 | 4 | 4 | 5 |
| 4 | structured-output support | 3 | 4 | 4 | 4 | 5 |
| 5 | observability | 2 | 3 | 4 | 4 | 5 |
| 6 | reproducibility | 2 | 3 | 3 | 3 | 4 |
| 7 | cost control | 2 | 3 | 4 | 3 | 4 |
| 8 | model version traceability | 2 | 3 | 4 | 4 | 5 |
| 9 | secret isolation | 2 | 4 | 4 | 3 | 5 |
| 10 | testability | 2 | 4 | 3 | 2 | 5 |
| 11 | single-developer simplicity | 4 | 4 | 2 | 1 | 4 |
| 12 | local development | 3 | 4 | 2 | 2 | 4 |
| 13 | future routing | 1 | 2 | 5 | 4 | 5 |
| 14 | multi-provider evolution | 1 | 3 | 4 | 5 | 5 |
| 15–19 | retrieval / vector criteria | — | — | — | — | — |
| 20 | operational complexity | 4 | 4 | 2 | 1 | 4 |
| 21 | premature infrastructure risk | 3 | 4 | 2 | 1 | 5 |

Rows 15–19 belong to Decision 009; model strategy must not pretend to be Research Memory.

---

## Constraints

1. **ModelPort is the only way Research obtains model completions.** Core does not call providers and does not host model-specific logic.
2. **Adapters in Integrations.** Core/Research do not import SDKs or Strix.
3. **Output = UNTRUSTED STRUCTURED PROPOSAL.** Not fact, Evidence, authorization, scope, budget change, Finding acceptance, or tool authority.
4. **Provider product deferred.** Configuring one adapter later does not make that vendor the architecture.
5. **v1: one adapter, one default model.** No live multi-model mesh.
6. **Routing-ready fields required on the contract:** model id/version, optional role hint, correlation id, timeout, retry policy, cancellation where possible, token/cost accounting, structured output, capability metadata, prompt/context provenance references.
7. **Secrets stay out of model context** where there is an alternative; never in Domain records as prompt stuffing of keys.
8. **Model/tool budget observability** is not the same as ResearchRun Budget authority. The model cannot raise Core-issued execution budget.
9. **Strix is not the ModelPort.** If used as reasoning runtime: untrusted proposal → Research validation → Core-controlled execution. If used as tool runtime: Worker + WorkerResult after Core authorization.
10. **No LLM framework as architecture.**
11. **Conversation history is not Research Memory and not SoR.**

---

## Revisit triggers

- Measured cost/quality split that justifies cheap vs strong routing
- Need for an independent verifier model
- Primary provider unavailability, policy, or lock-in pain
- Structured-output or timeout/retry failures that a second adapter would actually fix
- Strix (or any runtime) starting to be imported by Research (remove the import; do not “standardize” it)

---

## Open questions

- Which provider/model is configured first
- Schema of structured proposal types (domain-neutral vs Research DTOs — not a provider SDK)
- How prompt/context blobs are stored (SoR references vs artifact bytes — Decision 006 if large)
- Exact retry/cancel implementation

---

## Confidence

**MEDIUM**

The port is required by Core/Research boundaries. Deferring the vendor avoids a false lock-in. Skipping live routing avoids fashion. Confidence is not HIGH because the first adapter is still unchosen and a leaky port could still look like an OpenAI client.

---

# Decision 009 — Semantic Retrieval / Vector Strategy

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Depends on:** Decision 002 (Research Memory is a rebuildable read model, not SoR); Decision 003 (PostgreSQL SoR); Decision 007 (no cache as truth); Decision 008 (model output untrusted)

This decision selects **how Research Memory retrieves** in v1, and whether a **vector index** exists.

It does **not** select a vector database, pgvector, embedding provider, or RAG framework.

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS — structured-first Research Memory; optional semantic/vector companion later**

| Question | Answer |
|---|---|
| **v1 semantic/vector needed?** | **No** |
| **Exact/structured lookup sufficient for v1?** | **Yes** — Factual and Episodic Memory are SoR records keyed by Program, run, asset, and identity |
| **Retrieval abstraction now?** | **Research Memory already is that abstraction.** No separate VectorPort in v1 |
| **Vector implementation** | **Later**, as a **rebuildable, non-authoritative companion** behind Research Memory — not a second SoR |
| **Vector DB / embedding product** | **Not selected** |

**v1 retrieval:** PostgreSQL **structured and text** lookup, **Program-scoped** (and scope/authorization-aware). No embedding pipeline. No vector index.

Semantic similarity is **not** Evidence, not fact, not authorization, and not Research Memory truth.

---

## Why this decision exists

Research Memory has Factual / Episodic / Procedural categories. It is a **read/retrieval/organization** layer over authoritative records and curated procedural knowledge. It is **not** a truth source (DOMAIN_MODEL.md).

`TECHNICAL_REQUIREMENTS.md`: semantic retrieval is optional later; a vector database is **not** required; search/vector/graph may exist as companions; Research Memory does not require them.

v1 does not have a large procedural corpus or a measured recall failure of SQL/text search. Adding a vector DB “because the system uses AI” would create shadow truth, embedding drift, and cross-program leak surface for no Stage 1 gain.

---

## When semantic retrieval becomes valuable

**Factual / Episodic (v1):** exact and structured lookup is the correct tool (ids, Program, ResearchRun, Asset, timestamps, statuses). Semantic search over Observations is optional later and still not Evidence.

**Procedural:** semantic retrieval becomes valuable when curated methodologies, outcome-linked heuristics, and analyst notes are **large enough** that keyword/SQL recall fails **as observed**. Even then: procedural hits are **hints**, cannot override Core, cannot invent Evidence, cannot create Findings.

---

## Stage 1 — Mandatory gates

| Gate | Meaning |
|---|---|
| Vector ≠ SoR | Index cannot win against PostgreSQL |
| Embedding ≠ truth | Similarity is not Observation, Evidence, or Finding |
| Rebuildable | Any later index rebuilds from SoR (+ curated procedural records with provenance) |
| Program isolation | Default **no** cross-program retrieval |
| Scope filter | Deleted, revoked, out-of-scope records must be excludable using **SoR state**, not the index as authority |
| Provenance | Hits cite source record ids; no anonymous chunks as facts |
| No v1 vector product | Not required to run Research Memory |

### Strategy results

| Strategy | Stage 1 | Note |
|---|---|---|
| **1. No semantic retrieval in v1** | **Pass as the vector slice** | Correct for embeddings. Must not mean “no Research Memory” |
| **2. PostgreSQL text/structured retrieval only** | **Pass — this is v1** | Sufficient for Factual/Episodic |
| **3. Vector in primary DB later** | **Pass as a possible later adapter** | Must not make an extension a domain dependency (Decision 003). Not v1 |
| **4. Dedicated vector companion from start** | **Fail v1 fit** | Premature infrastructure; shadow-truth temptation |
| **5. New VectorPort now, implement later** | **Redundant** | Research Memory is already the retrieval port. A second port invites a vector product |
| **6. Structured-first + optional semantic later** | **Pass** | Selected strategy. v1 = (2). Later semantic = companion behind Research Memory |

---

## Embedding drift, versions, stale data

If a vector companion is added later:

- Store **embedding model id/version** as index provenance, not as domain truth
- **Drift:** change of embedding model ⇒ **rebuild** the companion; do not mix incompatible vectors silently
- **Stale embeddings** cannot override SoR. On conflict, **PostgreSQL wins**; the index is rebuilt or dropped (Decision 002 companion rule)
- Deleted/revoked/out-of-scope: filter at retrieval time from SoR (or drop from index as a derived view). The vector hit is never a grant of scope

Cross-program leakage: retrieval APIs take Program (and must not return another Program’s episodic/factual material by similarity). Default deny.

---

## Comparison matrix (project fit)

**NV** = no retrieval at all. **PG** = PG structured/text only (v1). **VXPG** = vectors in PG from start. **DED** = dedicated vector DB v1. **PORT** = extra VectorPort now. **HY** = structured-first + optional semantic later (selected).

| # | Criterion | NV | PG | VXPG | DED | PORT | HY |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | correctness | 2 | 5 | 3 | 2 | 4 | 5 |
| 2 | provider lock-in risk | 5 | 5 | 3 | 2 | 4 | 5 |
| 3 | Core/Research boundary cleanliness | 3 | 5 | 4 | 3 | 3 | 5 |
| 15 | retrieval correctness | 1 | 5 | 3 | 2 | 4 | 5 |
| 16 | provenance preservation | 2 | 5 | 3 | 2 | 4 | 5 |
| 17 | cross-program isolation | 3 | 5 | 3 | 2 | 4 | 5 |
| 18 | rebuildability | 3 | 5 | 3 | 2 | 4 | 5 |
| 19 | stale-data risk | 5 | 5 | 2 | 2 | 3 | 5 |
| 20 | operational complexity | 5 | 5 | 3 | 1 | 3 | 5 |
| 21 | premature infrastructure risk | 4 | 5 | 2 | 1 | 3 | 5 |
| 10 | testability | 3 | 5 | 3 | 2 | 4 | 5 |
| 11 | single-developer simplicity | 4 | 5 | 3 | 1 | 3 | 5 |
| 12 | local development | 4 | 5 | 3 | 1 | 3 | 5 |

**NV** fails because Research Memory still must retrieve Factual/Episodic from SoR. **HY** is **PG in v1** plus a gated companion path, not a vector install.

---

## Constraints

1. **v1: no vector index, no embedding pipeline, no vector product.**
2. **Research Memory retrieves from PostgreSQL** (structured + text), Program-scoped.
3. **Semantic similarity ≠ Evidence, fact, authorization, or Finding.**
4. **Any later vector index is a rebuildable companion.** PostgreSQL wins conflicts.
5. **Embedding model version is index provenance**, required if/when embeddings exist; embeddings are not SoR.
6. **Cross-program isolation by default.** Similarity must not leak Programs.
7. **Out-of-scope / deleted / revoked records** must be filterable from retrieval using SoR.
8. **No VectorPort parallel to Research Memory** in v1.
9. **pgvector / extension is not selected** and must not become a Domain type.
10. **Model suggestions (Decision 008) are not retrieval truth.**
11. **Cache (Decision 007) is not a semantic index.**

---

## First implementation / future evolution

**v1:** Research Memory = organized reads over SoR (+ curated procedural rows with provenance). Keyword/text search in PostgreSQL is allowed; it is not a vector DB.

**Later:** If procedural/factual recall fails as measured, add semantic retrieval **behind Research Memory**, rebuildable from SoR, with embedding-version provenance and Program/scope filters. Dedicated vector product or in-DB vectors would be a **new** decision.

---

## Revisit triggers

- Measured failure of structured/text recall on a real procedural or episodic corpus
- Analyst workflow that needs similarity **hints** without treating them as Evidence
- Embedding/rebuild cost becoming an actual ops problem (then companion topology, still not truth)
- Cross-program leak in any retrieval path (fix isolation; do not “tune” the index as authority)
- Pressure to put vectors in PostgreSQL as Domain (refuse; adapter only, later decision)

---

## Open questions

- Text-search implementation inside PostgreSQL (not a product decision here)
- Future vector product / embedding model
- Chunking strategy if semantic retrieval is added
- How procedural notes are curated (still not vector)

---

## Confidence

**MEDIUM**

v1 does not need vectors; Research Memory already forbids shadow truth; structured lookup matches Factual/Episodic. Confidence is not HIGH because procedural corpus size is unknown and a later companion will need strict Program filters to avoid leaks.

---

## Self-audit (Decisions 008 and 009)

| Forbidden reading | Status |
|---|---|
| Provider SDK in Core/Research | **Forbidden.** Adapters in Integrations |
| Model output became truth | **No.** Untrusted structured proposal |
| Router unnecessarily complex in v1 | **No.** Routing-ready contract; no live mesh |
| Multi-provider mandatory in v1 | **No.** One adapter; product deferred |
| Vector DB chosen because “AI” | **No.** No vector in v1 |
| Semantic result used as fact/Evidence | **Forbidden.** |
| Vector index became shadow truth | **No index in v1;** later companion cannot win vs SoR |
| Cross-program memory leak designed in | **Default isolation required** |
| Embedding version/provenance forgotten | **Required if/when embeddings exist** |
| Stale vectors override SoR | **Forbidden.** PostgreSQL wins |
| Strix became architecture owner / provider | **No.** Optional Integration; same untrusted path |

**FINAL STATUS: PASS**

---

# Decision 010 — Deployment Model

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Depends on:** Decision 003 (PostgreSQL SoR); Decision 004 (hybrid orchestration, product deferred); Decision 005 (mixed Worker topology, local-first, no mandatory broker)

This decision selects the **first-implementation deployment model** and the **allowed evolution path**.

It does **not** select:

- Docker / Podman / any container runtime
- Kubernetes or any cluster orchestrator
- a cloud provider
- a secrets product
- HA/replication topology
- production hosting

---

## Decision

**FIRST DEPLOYMENT MODEL: ACCEPT WITH CONSTRAINTS — staged**

**Phase A (first working implementation):**

- Windows + Cursor **developer host** (current context, not architecture)
- local PostgreSQL (Decision 003)
- local Control Plane on that host
- Kali/WSL **Worker integration** for tool execution (Decision 005)
- no mandatory containers, Kubernetes, remote services, or distributed broker

**Phase B:**

- same Worker contract
- replaceable local and/or **remote authenticated** Workers
- Control Plane and PostgreSQL location may move only if a later decision says so; Domain/Core contracts do not change

**Phase C:**

- distributed production topology **only if** measured requirements justify it
- still not a Kubernetes decision, still not a container mandate

Windows/Kali/WSL describe **Phase A context**. They are not a permanent architecture constraint.

---

## Why this decision exists

`TECHNICAL_REQUIREMENTS.md` leaves production topology unchosen, prefers local/mock before distributed deployment, and requires easy testing against Kali/WSL **without** making that environment the architecture.

Decision 005 already places Control Plane local and the first tool Worker in Kali/WSL. Deployment must not invent a second topology, must not put Kubernetes under the first loop, and must not encode “runs on WSL” into domain records.

---

## Stage 1 — Mandatory gates

| Gate | Meaning |
|---|---|
| Deployment ≠ domain logic | Program/ResearchRun/WorkerResult do not carry Docker/K8s/WSL as truth |
| Current env ≠ production architecture | Windows/Kali/WSL are Phase A |
| SoR remains PostgreSQL | Deployment does not replace Decision 003 |
| Worker isolation preserved | Phase A still uses out-of-process Workers (Decision 005) |
| No premature cluster | Kubernetes/remote mesh not required to run Phase A |
| Evolution without Domain change | Phase B/C must not rewrite Core/Research contracts |

### Deployment candidate results

| Model | Stage 1 | Note |
|---|---|---|
| **Single local application process** | **Fail as the Worker isolation story** | Can host Control Plane+fakes. Real side-effect Workers in the same process fail Decision 005 isolation |
| **Local multi-process application** | **Pass, incomplete** | Good Control Plane + child Workers. Misses Kali/WSL as the stated first tool env unless mixed |
| **Host + Kali/WSL split as the standing model** | **Pass as Phase A snapshot only** | Accurate today. Fails if it is the *permanent* architecture |
| **Containerized local stack** | **Pass as an optional later packaging** | Not required. Container runtime **not** chosen. Must not become mandatory |
| **Remote distributed services** | **Pass as Phase C capability** | Premature as first model |
| **Kubernetes-style orchestration** | **Not selected; premature** | Capable production class. Not a correctness gate. Not v1 |
| **Staged deployment model** | **Pass** | Phase A = host + local PostgreSQL + local Control Plane + Kali/WSL Worker. B/C are gated evolutions |

---

## Candidate evaluations

**Single local process** — simplest debug for Control Plane; cannot be the Worker execution model.

**Local multi-process** — correct isolation on one OS; insufficient as the *whole* first implementation because security tools are expected on Kali/WSL.

**Host + Kali/WSL split** — correct **Phase A** picture. Wrong as the name of the architecture.

**Containerized local stack** — can wait. Adds operational surface a single developer does not need for the first Evidence/FindingProposal loop. Not forbidden later; not accepted now.

**Remote distributed services** — future Control Plane/Worker/PostgreSQL split. Requires authentication, network untrust, and a later decision. Not first implementation.

**Kubernetes-style** — production scheduler class. Fashionable and premature. Failure isolation and scale are not current requirements.

**Staged** — selected. Records Phase A honestly, forbids treating it as destiny, and ties Phase B to Decision 005’s Worker contract.

---

## Comparison matrix (project fit)

**SP** = single process. **MP** = local multi-process. **HK** = host+Kali as standing model. **CT** = containerized local. **RD** = remote distributed now. **K8** = Kubernetes now. **ST** = staged (selected).

| # | Criterion | SP | MP | HK | CT | RD | K8 | ST |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | Core authority preservation | 3 | 4 | 4 | 4 | 4 | 4 | 5 |
| 2 | Worker isolation boundary | 1 | 5 | 4 | 4 | 5 | 5 | 5 |
| 3 | local development simplicity | 5 | 4 | 3 | 2 | 1 | 1 | 4 |
| 4 | Kali/WSL integration | 1 | 2 | 5 | 3 | 2 | 2 | 5 |
| 5 | remote-worker future | 1 | 3 | 2 | 3 | 5 | 4 | 5 |
| 6 | authentication capability | 2 | 3 | 3 | 3 | 5 | 4 | 4 |
| 7 | cancellation | 3 | 4 | 4 | 4 | 4 | 4 | 4 |
| 8 | retry / duplicate safety | 3 | 4 | 4 | 4 | 3 | 3 | 4 |
| 9 | correlation / auditability | 3 | 4 | 4 | 4 | 4 | 4 | 5 |
| 10 | debugging | 5 | 4 | 3 | 2 | 1 | 1 | 4 |
| 11 | failure isolation | 1 | 5 | 4 | 4 | 5 | 5 | 5 |
| 12 | artifact / result transfer | 4 | 4 | 3 | 3 | 2 | 2 | 4 |
| 13 | operational complexity | 5 | 4 | 3 | 2 | 1 | 1 | 4 |
| 14 | single-developer suitability | 5 | 4 | 4 | 2 | 1 | 1 | 4 |
| 15 | portability | 2 | 4 | 1 | 3 | 4 | 3 | 5 |
| 16 | production evolution | 1 | 3 | 2 | 3 | 4 | 4 | 5 |
| 17 | vendor lock-in | 5 | 5 | 4 | 3 | 2 | 1 | 5 |
| 18 | observability | 3 | 4 | 4 | 4 | 4 | 4 | 4 |
| 19 | security boundary clarity | 2 | 4 | 4 | 3 | 4 | 3 | 5 |
| 20 | local → remote migration | 1 | 3 | 2 | 3 | 5 | 4 | 5 |
| 21 | avoiding premature distribution | 5 | 5 | 4 | 3 | 1 | 1 | 5 |

Staged wins because it is **Host + Kali/WSL split for Phase A** plus an explicit ban on making that split the architecture, plus a gated path to remote Workers without Kubernetes-now.

---

## Constraints

1. **Phase A is local:** developer host Control Plane, local PostgreSQL, Kali/WSL Worker integration, no mandatory broker/containers/K8s.
2. **Windows + Kali/WSL are current context**, not production architecture, not Domain fields, not a language mandate (Decision 001 already).
3. **Deployment topology must not leak into domain logic.** No `environment=wsl` as authorization or Evidence.
4. **Containers are optional later packaging, not a requirement.**
5. **Kubernetes is not selected** and is not implied by “Phase C.”
6. **Secrets product is not selected.**
7. **Phase B remote Workers** use Decision 005’s contract: authenticated identity, still Core-authorized, still untrusted WorkerResult.
8. **Phase C** requires a new recorded decision when distribution is a real requirement, not a preference.
9. **Orchestration product remains deferred** (Decision 004). Deployment must not smuggle Temporal/Celery/K8s-as-orchestrator.
10. **PostgreSQL remains the SoR** wherever it is hosted later; hosting move ≠ product change.

---

## Evolution path (allowed)

```
local Control Plane + local PostgreSQL
→ local child-process Workers + Kali/WSL Worker (Phase A)
→ multiple local Workers
→ remote authenticated Worker(s) (Phase B)
→ distributed worker pool / split services (Phase C, only if justified)
```

Core/Research contracts stay the same across this path. Worker contract stays the same. Transport adapters change.

---

## Revisit triggers

- Phase A host/WSL split blocks a required tool or Control Plane run
- Need to run Control Plane or PostgreSQL off the developer laptop **as a requirement**
- Remote Worker topology is actually adopted (Decision 005 revisit + this Phase B)
- Packaging/reproducibility pain that a **later** container decision might ease (does not auto-select Docker)
- Production HA/scale that a **later** cluster decision might ease (does not auto-select Kubernetes)
- Deployment fields appearing in domain records (fix the leak; do not “standardize” it)

---

## Open questions

- Whether Phase A PostgreSQL runs native-Windows, WSL, or a later container (product remains PostgreSQL; runtime packaging deferred)
- How Control Plane is started on the developer host
- Production hosting, HA, backup implementation (Decision 003 open questions)
- Container/K8s if and when Phase C is real

---

## Confidence

**MEDIUM**

Staged deployment is the only model that tells the truth about Phase A (Windows host + local PostgreSQL + local Control Plane + Kali/WSL Worker) without freezing that picture or jumping to Kubernetes. Confidence is not HIGH because production topology is still unchosen and Phase A packaging (native vs WSL PostgreSQL, how WSL is launched) is unspecified.

---

## Self-audit (Decisions 005 and 010)

| Forbidden reading | Status |
|---|---|
| Windows/Kali became permanent architecture | **No.** Phase A context; portability required; Domain must not store it as truth |
| Remote workers selected prematurely | **No.** Future path only |
| Broker made mandatory | **No.** Explicitly non-mandatory; product not chosen |
| Local process boundary removed | **No.** Side-effect Workers are out-of-process; in-process is fakes only |
| Worker writes PostgreSQL truth | **Forbidden.** WorkerResult ingest is Control Plane/Data |
| Transport became truth source | **No.** Delivery ≠ success ≠ Evidence |
| Authentication confused with authorization | **Separated.** Worker identity/authn ≠ Core authorization |
| Deployment model leaked into domain logic | **Forbidden.** |
| Kubernetes selected prematurely | **No.** Not selected; Phase C is not K8s |
| Containers made mandatory | **No.** |
| Strix defined as a Worker | **No.** Optional Integration used *by* a Worker |
| WSL made mandatory production runtime | **No.** Initial tool-execution location only |

**FINAL STATUS: PASS**

---

# Decision 011 — Frontend / Interface Strategy

**Status:** ACCEPT WITH CONSTRAINTS (strategy) / DEFER (frontend and API framework products)  
**Date:** 2026-08-16  
**Depends on:** Decision 001 (Python primary; Interface is application logic, not Core); Decision 010 (staged deployment); Decision 015 (Human Operator identity; Approval actor)

This decision selects **how humans and external systems talk to Zest**, and what exists in the first working implementation.

It does **not** select a web framework, CLI framework, desktop toolkit, API framework (FastAPI/Flask/etc.), or a dashboard product. n8n remains an example Integration, not Interface-as-Core.

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS — staged interface**

**PRODUCT: DEFER**

| Phase | Interface surface |
|---|---|
| **A — first working implementation** | Stable **application/API boundary** (language-neutral contracts into Research/Core). **Minimal CLI / operator tooling**. **Minimal Human Review interface** sufficient to present FindingProposal and record a decision through Core Approval. **Not** a full web dashboard |
| **B** | Web dashboard for the listed operator capabilities |
| **C** | Richer operational/research visualization |

**Answer:** A **full web dashboard is not required** for the first working implementation.

Interface ≠ business logic. Interface cannot bypass Core, cannot promote Candidate → Finding, does not own Approval semantics, does not own Research logic, and must not write PostgreSQL **authority** by going around Core/Data.

Human Approval path:

```
Interface → approval request → Core Approval semantics → persisted Approval
```

FindingProposal APPROVED remains the domain view of that Core record (DOMAIN_MODEL.md).

---

## Why this decision exists

PROJECT_STRUCTURE.md: Interface contains API, dashboard, CLI, human review, approval screens, reporting. It must not own business logic or approval semantics.

Human Review is **permanent**. CLI-only review of Evidence/Artifacts (screenshots, captures) is a poor fit as the **only** long-term surface, but a **full** dashboard on day one is frontend complexity a single developer does not need to finish the first authorized loop.

An **API/application boundary** is required immediately so CLI, a later dashboard, tests, and future automations (n8n as **Integration**, not policy owner) share one Core-gated path.

Windows + Cursor is Phase A **context** (Decision 010), not a reason to pick Electron or a JS SPA now.

---

## Stage 1 — Mandatory gates

| Gate | Meaning |
|---|---|
| Interface cannot bypass Core | No “admin SQL” as Approval or Finding |
| Interface does not own Approval | UI/CLI submits a request; Core records |
| No direct SoR authority writes | PostgreSQL writes go through Data after Core/Research rules |
| Human Review is possible in v1 | Finding cannot be created without it |
| API/application boundary exists | Surfaces are replaceable; n8n cannot become Core |
| No framework required to start | Product deferred |

### Strategy results

| Strategy | Stage 1 | Note |
|---|---|---|
| **1. CLI-first only** | **Pass weakly** | Fast. Human Review of artifacts is painful as the standing model |
| **2. API-first only** | **Pass as a boundary, fail as the only human surface** | Automation-ready; operators still need CLI or review UI |
| **3. Web dashboard first** | **Pass later** | Best long-term Human Review ergonomics. Premature full product/framework |
| **4. API + CLI** | **Pass** | Solid Phase A if Human Review is explicitly included in the CLI/minimal surface |
| **5. API + minimal web dashboard** | **Pass** | Reasonable Phase A/B blur. Full dashboard still not mandatory in A |
| **6. API + web dashboard + CLI at once** | **Pass as Phase C shape** | Too much v1 surface area |
| **7. Desktop application** | **Fail v1 fit** | Extra stack; freezes OS/desktop as architecture |
| **8. Staged interface** | **Pass** | Selected: API boundary + minimal CLI + minimal review in A; dashboard later |

---

## Candidate evaluations

**CLI-first** — best single-developer speed and debugging. Worst standing Human Review for screenshots/HTTP captures. Acceptable as **Phase A tooling**, not as “Interface = CLI forever.”

**API-first** — correct **boundary**. Not sufficient alone for Human Review.

**Web dashboard first** — answers the capability list in one UI. Forces a frontend product/framework before the API contract is stable. Not selected for v1.

**API + CLI** — close to Phase A. Selected staged model **includes** this plus an explicit **minimal Human Review** surface (CLI prompts and/or a tiny local review page). That surface is not a dashboard product.

**API + minimal web** — allowed as a Phase A *implementation* of “minimal Human Review” without selecting React/Vue. Still not “full dashboard required.”

**API + dashboard + CLI together** — the long-term Interface contents list. Not the first implementation.

**Desktop** — unnecessary packaging; not chosen.

**Staged** — selected. Matches Decision 010’s gated evolution: contracts first, richness later.

---

## Comparison matrix (project fit)

**CLI** = CLI-only. **API** = API-only. **WEB** = dashboard-first. **AC** = API+CLI with no review UX. **AM** = API+minimal web. **ALL** = all three at once. **DESK** = desktop. **ST** = staged (selected).

| Criterion | CLI | API | WEB | AC | AM | ALL | DESK | ST |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| single-developer speed | 5 | 4 | 2 | 4 | 3 | 1 | 2 | 5 |
| Human Review ergonomics | 2 | 1 | 5 | 2 | 4 | 5 | 4 | 4 |
| operational visibility | 3 | 3 | 5 | 3 | 4 | 5 | 3 | 4 |
| research traceability | 3 | 4 | 5 | 4 | 4 | 5 | 3 | 4 |
| approval UX | 2 | 1 | 5 | 2 | 4 | 5 | 4 | 4 |
| API stability | 2 | 5 | 3 | 5 | 5 | 4 | 3 | 5 |
| automation / future n8n | 2 | 5 | 3 | 5 | 5 | 5 | 2 | 5 |
| CLI usefulness | 5 | 2 | 1 | 5 | 3 | 5 | 2 | 5 |
| debugging | 5 | 4 | 2 | 5 | 3 | 2 | 2 | 5 |
| local Windows development | 5 | 4 | 3 | 4 | 3 | 2 | 2 | 4 |
| future hosted deployment | 2 | 5 | 4 | 5 | 5 | 5 | 1 | 5 |
| vendor lock-in | 5 | 5 | 3 | 5 | 4 | 3 | 2 | 5 |
| frontend complexity | 5 | 5 | 2 | 5 | 3 | 1 | 2 | 5 |
| testability | 4 | 5 | 3 | 5 | 4 | 3 | 2 | 5 |

Staged wins because Phase A is **API + CLI + minimal review** (high speed, Core-gated) without locking a dashboard framework.

---

## Phase A capability coverage (conceptual)

All listed operator capabilities must be **reachable through the application/API boundary** (so they are not trapped in a future SPA). Phase A **CLI/minimal review** must cover at least: start/stop ResearchRun (Core-authorized), Human Review / Approval request, and enough read-back of FindingProposal, Evidence, Budget, Worker status, and Audit history to decide. Rich Asset/Hypothesis/graph visualization waits for B/C.

---

## Constraints

1. **Interface is not Core, Research, or Data.** No policy, no Finding creation, no Approval semantics ownership.
2. **No PostgreSQL authority writes from UI/CLI.**
3. **Approval: Interface request → Core → persisted Approval.** Operator identity from Decision 015.
4. **Full web dashboard is not a v1 requirement.**
5. **Frontend framework and API framework products are deferred.**
6. **n8n/external automation**, if used, is an Integration/API **client**. It cannot own Core logic.
7. **Windows is not a reason to choose a desktop app.**
8. **API/application contracts stay language-neutral** where they cross process boundaries (Workers already are).
9. **AI recommendation and final judgment stay separate in the review UI.**

---

## Revisit triggers

- Human Review on CLI/minimal surface blocks real Finding decisions (then Phase B dashboard, still no implied React)
- External automation needs a stable API that is not yet documented (fix the boundary; do not put logic in n8n)
- Operators need operational visualization beyond logs (Decision 012 + Phase B)
- Pressure to write Findings from a UI shortcut (refuse)

---

## Open questions

- Whether Phase A “minimal Human Review” is CLI, a tiny local page, or both (not a framework choice)
- API transport (HTTP vs in-process for single process Control Plane — not a FastAPI decision)
- Dashboard stack when Phase B starts

---

## Confidence

**MEDIUM**

Staged Interface matches Human Review as permanent without a v1 SPA. Confidence is not HIGH because “minimal review surface” could be under-built (CLI-only artifact review) or over-built (stealth dashboard).

---

# Decision 012 — Observability Strategy

**Status:** ACCEPT WITH CONSTRAINTS (abstraction) / DEFER (logging/metrics/tracing products)  
**Date:** 2026-08-16  
**Depends on:** Decision 003 (AuditEvent in PostgreSQL SoR); Decision 004 (orchestration ≠ truth); Decision 005 (correlation id on Worker contract); Decision 008 (model id/cost/provenance on ModelPort); Decision 013 (secret values never in logs)

This decision selects how **operational visibility** is produced, and how it stays distinct from **AuditEvent** and domain truth.

It does **not** select a logging vendor, metrics backend, tracing backend, APM, or “observability platform.”

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS**

**ObservabilityPort / instrumentation conventions:** structured **logs first**; **metrics and tracing capability designed in**; **concrete observability platform later**.

**PRODUCT: DEFER**

| Signal | What it is | What it is not |
|---|---|---|
| **AuditEvent** | Authoritative reconstructive **security/decision** history in PostgreSQL | Not logs. Not Evidence. Not AuthorizationSource |
| **Logs** | Structured **diagnostics** | Not Audit truth. Not WorkerResult truth |
| **Metrics** | Aggregate **operational / research / security** insight | Not domain state, Finding, or Evidence |
| **Traces** | Causal **execution path** visibility | Not AuditEvent, Evidence, or SoR |

If logs or a tracing backend are deleted: **AuditEvent, Evidence, Finding, Approval remain.** Metric drift must not change domain correctness.

**v1 does not install a full observability platform.**

---

## Why this decision exists

Auditability is a **hard** requirement. Observability is **preferred visibility** of operation (`TECHNICAL_REQUIREMENTS.md` lists events; no product chosen). Collapsing them would make ELK/Datadog the SoR, or would skip AuditEvent because “we have logs.”

Workers may be non-Python (Decision 001/005). Correlation must be **language-neutral**. Remote Workers later must still join the same run/experiment/worker ids.

Model cost/provenance must be observable (Decision 008) without dumping secrets or full prompts by default (Decision 013).

---

## Minimum visible events

These must be **observable** (log and, where they are control decisions, **also** AuditEvent in SoR). Observability does not replace the AuditEvent.

ResearchRun start/end; authorization decision; scope decision; budget issue/use/exhaustion; Experiment transition; Worker dispatch; Worker completion/failure; retry; timeout; cancellation; redirect/re-authorization; WorkerResult ingestion; Transition A; Evidence admission; Candidate transition; Verification; FindingProposal; Human Review; Approval; Finding creation; model call (id/version, adapter identity, correlation, role hint, token/cost, latency, retry, timeout, context provenance **reference**); artifact creation; snapshot/change generation; **secret reference use without secret value**; external integration failures.

---

## Logging (v1)

- Structured
- Correlation id; ResearchRun / Experiment / Worker relationship reconstructible
- **No secret values**
- Raw sensitive payloads **not** logged by default
- Model prompts/responses: **configurable redaction**; default is **provenance reference**, not full body
- Worker output logs are **not** truth (WorkerResult ingest + Transition A are)

---

## Metrics (designed in; product deferred)

**Operational:** run/worker duration, retries, failures, wait time, model latency, storage latency.

**Research:** hypotheses created/tested, hypothesis success rate, Candidate conversion, Finding conversion, false-positive rate, duplicate rate, time-to-first-valid-finding, cost per accepted Finding, accepted findings per request volume, verification rejection rate, chain conversion rate.

**Security:** denied execution, out-of-scope block, approval-required, budget-exhaustion, re-authorization counts.

v1 may emit counters/timers through ObservabilityPort **without** Prometheus/etc. Research metrics are **aggregates over SoR**, not a second Candidate ledger.

---

## Tracing (designed in; product deferred)

Intended chain:

Operator → ResearchRun → Research → Core authorization → orchestration → Worker → WorkerResult → Transition A → Evidence admission → Candidate/Verification → Approval/Finding.

Traces **do not** replace AuditEvent. No Jaeger/OTel backend is required in v1; **span/correlation fields** should exist so a backend can be attached later. Language-neutral Worker correlation is mandatory now (Decision 005).

---

## Stage 1 — Mandatory gates

| Gate | Meaning |
|---|---|
| AuditEvent remains SoR | Logs cannot be the reconstructive authority |
| Secrets never in logs | Decision 013 |
| Correlation present | Run/experiment/worker/model joinable |
| Observability ≠ Evidence | Success in a trace is not Finding |
| No vendor required for v1 | Platform later |
| Model cost/provenance visible | Without default full prompts |

### Strategy results

| Strategy | Stage 1 | Note |
|---|---|---|
| **1. Logs only** | **Pass as v1 export, fail as the whole strategy** | Diagnostics only; metrics/traces would never fit |
| **2. Logs + metrics products now** | **Pass later** | Premature backends |
| **3. Logs + tracing products now** | **Pass later** | Same |
| **4. Logs + metrics + traces products now** | **Fail v1 fit** | Full platform too early |
| **5. ObservabilityPort; logs first; metrics/traces designed in** | **Pass** | Selected |
| **6. Full observability platform day one** | **Fail v1 fit** | Vendor becomes architecture |

---

## Comparison matrix (project fit)

**LG** = logs only as strategy. **LM** = logs+metrics products. **LT** = logs+tracing products. **ALL** = three products now. **PORT** = port + logs first (selected). **PLT** = full platform v1.

| Criterion | LG | LM | LT | ALL | PORT | PLT |
|---|---:|---:|---:|---:|---:|---:|
| audit vs diagnostics clarity | 3 | 3 | 3 | 2 | 5 | 2 |
| local dev simplicity | 5 | 3 | 3 | 2 | 5 | 1 |
| production evolution | 2 | 3 | 3 | 4 | 5 | 4 |
| Worker correlation | 3 | 3 | 4 | 4 | 5 | 4 |
| remote Worker future | 2 | 3 | 4 | 4 | 5 | 4 |
| operational burden | 5 | 3 | 3 | 2 | 5 | 1 |
| vendor lock-in | 5 | 3 | 3 | 2 | 5 | 1 |
| testability | 5 | 3 | 3 | 2 | 5 | 2 |
| model cost/provenance | 3 | 4 | 3 | 4 | 5 | 4 |
| premature infrastructure | 4 | 3 | 3 | 2 | 5 | 1 |

---

## Constraints

1. **AuditEvent in PostgreSQL is authoritative audit.** Logs/metrics/traces are not.
2. **Log loss must not erase Approval/Evidence/Finding/authorization history.**
3. **No secret values, no default full prompts/responses, no default raw sensitive payloads.**
4. **Worker logs ≠ WorkerResult ≠ Observation ≠ Evidence.**
5. **Metrics are not Candidate/Finding truth.**
6. **Traces are not AuditEvent.**
7. **ObservabilityPort exists; logging/metrics/tracing vendors deferred.**
8. **Correlation ids are contract-level** (Control Plane, Worker, model call), not vendor-trace-id as Domain.
9. **Secret reference use may be logged; values must not.**
10. **Python logging libraries are not the architecture**; Workers in other languages must still correlate.

---

## First implementation / future

**v1:** Structured logs to local stdout/file with required fields; AuditEvent written for control decisions; ObservabilityPort stubs for metrics/traces (or in-process counters) without a platform.

**Later:** Attach a self-hostable or hosted backend **behind the port** when production evolution (Decision 010 Phase C or operational pain) justifies it. Not a Domain change.

---

## Revisit triggers

- Cannot reconstruct a run from AuditEvent + logs (fix instrumentation; do not make logs SoR)
- Need dashboards of operational/research/security aggregates (metrics backend **behind the port**)
- Cross-process traces required for remote Workers
- Secrets or full prompts appearing in logs (incident; tighten redaction)
- Vendor SDK in Core/Research (remove it)

---

## Open questions

- Log destination (stderr vs file) as adapter
- Metrics/tracing products when triggered
- Prompt retention policy for debugging (default off)

---

## Confidence

**MEDIUM**

Separating AuditEvent from logs is required. Structured logs first matches single-developer Phase A. Confidence is not HIGH because “designed-in tracing” can be forgotten until remote Workers, and research metrics can be mistaken for SoR if undisciplined.

---

## Self-audit (Decisions 011 and 012)

| Forbidden reading | Status |
|---|---|
| UI owns business logic | **Forbidden** |
| UI bypasses Core | **Forbidden** |
| UI writes PostgreSQL authority directly | **Forbidden** |
| Full dashboard mandatory in v1 | **No** |
| CLI-only forever (Human Review ignored) | **No**; Phase A includes minimal review surface |
| Frontend framework chosen | **Deferred** |
| n8n/Interface owns Core | **No**; client of API only |
| Logs replace AuditEvent | **No** |
| Traces are truth | **No** |
| Metrics are domain state | **No** |
| Secrets logged | **Forbidden** |
| Full prompts stored by default | **No** |
| Observability vendor is architecture | **Product deferred** |
| Worker correlation missing | **Required on contract** |
| Model cost/provenance invisible | **Required on ModelPort + logs** |
| Full observability platform in v1 | **No** |

**FINAL STATUS: PASS**

---

# Decision 013 — Secrets Management Strategy

**Status:** ACCEPT WITH CONSTRAINTS (abstraction) / DEFER (product)  
**Date:** 2026-08-16  
**Depends on:** Decision 005 (Workers less trusted than Control Plane); Decision 008 (secrets out of model context); Decision 010 (staged local-first)

This decision selects how **credentials and other secrets** are referenced, resolved, and kept out of Domain, prompts, and AuditEvent **values**.

It does **not** select Vault, cloud secret managers, password managers, OS credential-store products, or a KMS.

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS**

**SecretPort / SecretReference**, with a **local-dev adapter first**. An external secrets manager is allowed **later** when deployment actually requires it.

| Piece | Rule |
|---|---|
| Domain / SoR | May store **SecretReference** (name/id, purpose, not the value). Must **not** store secret **values** |
| Research / LLM / ModelPort | Receive capabilities and references, **not** raw credentials, where there is an alternative |
| Worker | After Core authorization, resolves **only the minimum** secrets for that issued job at the **execution boundary** |
| AuditEvent | May record that a named secret was **used/rotated/referenced**. Must **not** contain secret **values** |
| Logs | Secret values forbidden |

**PRODUCT: DEFER**

---

## Why this decision exists

Platform already lists **secrets access** as a capability, not a vendor (`PROJECT_STRUCTURE.md`). Requirements demand secrets/model-context separation, least privilege, and no provider owning domain logic.

Environment variables as **the architecture** leak into process lists, child environments, and logs, and they do not scale to remote Workers without copying the Control Plane’s entire secret set. An external manager on day one is extra infrastructure a single local developer does not need (Decision 010 Phase A).

The port lets local env-file / env-var / file adapters exist **without** becoming Domain types or a Vault lock-in.

---

## Stage 1 — Mandatory gates

| Gate | Meaning |
|---|---|
| Values ∉ Domain | PostgreSQL must not be a password dump |
| Values ∉ prompts | ModelPort/Strix reasoning must not require raw API keys in context where avoidable |
| Values ∉ AuditEvent payloads | References/ids only |
| Least privilege to Workers | No global/master credential bundle for every job |
| Replaceability | Provider/tool credentials swap without Core/Research SDK lock-in |
| Rotation possible later | Reference stays; value changes in the adapter |
| Local simplicity | Phase A must not require a cloud secret manager |
| Remote-ready | Future Workers must not need a copy of all Control Plane secrets |

### Candidate results

| Strategy | Stage 1 | Note |
|---|---|---|
| **1. Environment variables only** | **Pass as a local adapter, fail as the strategy** | Simple. Weak isolation, easy to pass into Worker env wholesale, poor remote story |
| **2. Local encrypted config/secret file** | **Pass as a local adapter** | Still a file on the developer host. Not the contract |
| **3. SecretPort + local-dev adapter first** | **Pass** | Selected |
| **4. OS-native credential store as architecture** | **Pass as an optional adapter** | Windows vs Linux stores would freeze current OS context as architecture |
| **5. External secrets manager from day one** | **Pass later, fail v1 fit** | Premature product and ops |

---

## Candidate evaluations

**Env-only** — acceptable **implementation** of SecretPort on a laptop if values never enter Domain, prompts, or audit payloads, and Workers do not inherit the full Control Plane environment. Not the strategy: remote Workers and rotation become “copy `.env`,” which this decision forbids as the design.

**Encrypted local file** — similar: adapter, not architecture.

**SecretPort** — selected. Research asks for a **capability**; Core authorizes the job; Worker/Platform resolves `SecretReference` at execution. LLM does not receive the credential.

**OS-native store** — optional later adapter; not chosen as the model (would make Windows Credential Manager or libsecret an architecture constraint).

**External manager now** — justified when Phase B/C or multi-operator hosting makes local files unsafe. Product still a later decision.

---

## Comparison matrix (project fit)

**ENV** = env-only as strategy. **FILE** = encrypted file as strategy. **PORT** = SecretPort + local first (selected). **OS** = OS store as architecture. **EXT** = external manager v1.

| Criterion | ENV | FILE | PORT | OS | EXT |
|---|---:|---:|---:|---:|---:|
| correctness / least privilege | 2 | 3 | 5 | 4 | 4 |
| values out of Domain/prompts/audit | 3 | 3 | 5 | 4 | 4 |
| local simplicity | 5 | 4 | 4 | 3 | 1 |
| remote Worker secret isolation | 1 | 1 | 5 | 3 | 4 |
| rotation later | 2 | 3 | 5 | 4 | 5 |
| vendor lock-in | 5 | 5 | 5 | 3 | 2 |
| premature infrastructure | 4 | 4 | 5 | 3 | 1 |
| testability | 4 | 3 | 5 | 2 | 2 |

---

## Secrets + identity relationship (conceptual)

```
Research → capability request
→ Core authorization (scope, budget, approval if required)
→ Worker (identity + issued job)
→ SecretPort resolves SecretReference at execution boundary
```

Not: `LLM → raw credential`.

Do not over-specify injection (env vs fd vs file in the Worker). The invariant is **minimum required**, **after** authorization, **not** in ModelPort context.

---

## Constraints

1. **Secret values never in Domain records, WorkerResult, Evidence, prompts, or AuditEvent payloads.**
2. **SecretReference may be stored and audited.**
3. **Workers never receive the Control Plane master/global secret set.**
4. **Local-dev adapters (env, file) are allowed; they are not the architecture.**
5. **No secrets product selected.**
6. **Rotation must remain possible** by changing the value behind a stable reference.
7. **Future remote Workers get per-job or per-worker scoped material**, not a cloned Control Plane secret bag.
8. **Python/OS/Kali must not define the SecretPort.**

---

## Revisit triggers

- More than one operator / hosted Control Plane
- Remote Workers that must not see Control Plane secrets (Phase B)
- Rotation or leak incident
- Local file/env adapter appearing in Domain or logs
- A specific manager is required by a **later** deployment decision (still a new decision, not a silent Vault import)

---

## Open questions

- Local adapter (env vs file vs OS store)
- External product when triggered
- How Worker-scoped short-lived material is minted (not chosen)

---

## Confidence

**MEDIUM**

The port is required to keep secrets out of SoR, prompts, and audit **values**. Deferring the product matches Phase A. Confidence is not HIGH because the first adapter is unchosen and a sloppy env pass-through could still dump master credentials into Kali/WSL.

---

# Decision 014 — Worker Isolation Strategy

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Depends on:** Decision 005 (out-of-process side-effect Workers; Kali/WSL first tool Worker; in-process = fakes only); Decision 010 (staged deployment; containers not mandatory); Decision 013 (minimum secrets at execution boundary)

This decision selects **isolation as a property** of Worker execution. It does **not** select Docker, a VM product, Kubernetes, or a sandbox vendor.

`TECHNICAL_REQUIREMENTS.md`: Worker isolation is a **property**, not a chosen container/VM product.

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS — mixed isolation policy by Worker capability; process/OS-environment boundary first**

**First implementation:**

```
Control Plane
→ explicit Worker process boundary
→ Kali/WSL (or local child-process) execution environment
```

Stronger **container/VM** isolation is **deferred**. Docker/Kubernetes are **not** selected and **not** required.

In-process execution remains **test doubles only** (Decision 005). Real side-effect Workers are out-of-process.

Isolation policy may **strengthen** with conceptual side-effect **level**. Isolation technology is **not** domain truth and is **not** an offensive-automation design.

---

## Conceptual side-effect levels

These are **control/runtime** categories for how harshly to isolate and whether Core Approval is required. They are **not** Finding severity and **not** stored as “the database says Docker.”

| Level | Meaning | Default isolation posture (v1) |
|---|---|---|
| **0** | Read-only / low side-effect | Out-of-process still; no extra product |
| **1** | Minor / reversible | Same process boundary; least-privilege secrets |
| **2** | External state change / approval-sensitive | Same boundary; **Core Approval** may be required (already domain). No new sandbox product required in v1 |
| **3** | Destructive / high-impact | **Denied by default.** If ever allowed, stronger isolation + Core Approval. Not designed here as an exploit pipeline |

This is not a catalog of attacks and not a mandate to automate destructive actions.

---

## Why this decision exists

Workers run tools against **untrusted** targets and **untrusted** model/tool text (prompt-injection-derived requests). Crash, filesystem, network, and credential blast radius must not include Core/PostgreSQL.

Decision 005 already forbids in-process side-effect Workers and places first tool execution in Kali/WSL. Decision 010 forbids mandatory containers. Isolation must **use** that process/OS boundary without freezing WSL or Docker as architecture.

---

## Stage 1 — Mandatory gates

| Gate | Meaning |
|---|---|
| Side-effect Workers out-of-process | Shared fate with Control Plane is forbidden |
| Worker cannot widen scope | Isolation does not grant authorization |
| Untrusted content contained | Target bytes, HTML, and tool output do not run inside Core |
| No container product required | Isolation property ≠ Docker |
| WSL ≠ architecture | First env, not production isolation technology |
| Isolation ≠ domain SoR | No `runtime=wsl` as authorization |

### Candidate results

| Strategy | Stage 1 | Note |
|---|---|---|
| **1. In-process execution** | **Fail for real Workers** | Allowed as fakes only |
| **2. Child-process isolation** | **Pass** | Default local isolation story |
| **3. WSL/Kali process boundary** | **Pass as first tool env** | OS-environment isolation + tools. Not permanent architecture |
| **4. Container isolation** | **Pass later** | Stronger FS/network limits. Product not chosen. Not v1 mandate |
| **5. VM isolation** | **Pass later** | Highest ops cost. Premature |
| **6. Mixed policy by capability** | **Pass** | Selected: process/WSL now; container/VM if risk/topology justify |

---

## Isolation concerns (v1 vs later)

| Concern | v1 | Later |
|---|---|---|
| Filesystem | Worker process/WSL not the Control Plane disk as a shared writable tree | Container/VM mounts if needed |
| Network | Worker may have network for authorized tools; Core does not proxy arbitrary LLM→network | Same contract; tighter netns later |
| Credentials | Decision 013 minimum at boundary; do not inherit Control Plane env | Per-job material |
| Process lifetime / crash / cancel | Kill the Worker process; Control Plane survives | Same |
| Cleanup | Worker workspace discarded; artifacts via Decision 006 ingest | Same |
| CPU/memory | Best-effort OS limits; no orchestrator product | cgroups/VM if justified |
| Tool dependencies | Kali/WSL or child-process images **as adapters** | Replaceable |
| Malicious target / prompt-injected tool requests | Stop, WorkerResult, Core re-eval; do not execute unauthorized tools | Stronger sandbox if measured |

---

## Comparison matrix (project fit)

**IP** = in-process. **CH** = child-process. **WSL** = Kali/WSL as standing isolation tech. **CT** = containers v1. **VM** = VMs v1. **MX** = mixed, process/WSL first (selected).

| Criterion | IP | CH | WSL | CT | VM | MX |
|---|---:|---:|---:|---:|---:|---:|
| crash containment | 1 | 5 | 4 | 5 | 5 | 5 |
| local simplicity | 5 | 4 | 3 | 2 | 1 | 4 |
| Kali/WSL tool fit | 1 | 2 | 5 | 3 | 2 | 5 |
| credential blast radius | 1 | 4 | 4 | 4 | 5 | 5 |
| future remote Workers | 1 | 3 | 2 | 4 | 4 | 5 |
| premature infrastructure | 4 | 5 | 4 | 2 | 1 | 5 |
| not freezing WSL/Docker | 3 | 5 | 2 | 2 | 3 | 5 |
| Level 3 default deny | 2 | 4 | 4 | 4 | 4 | 5 |

---

## Constraints

1. **Real Workers: process boundary.** In-process = fakes only.
2. **First tool Worker: Kali/WSL or local child-process** behind the same contract (Decision 005).
3. **Docker/container runtime not selected and not required.**
4. **VMs/Kubernetes not selected.**
5. **WSL is not the permanent isolation technology.**
6. **Isolation policy is not domain truth** (not Evidence, not authorization).
7. **Level 3 denied by default.** No offensive-automation playbook in this decision.
8. **Prompt-injected tool requests** do not execute without Core authorization; isolation is not a substitute for DEFAULT DENY.
9. **Workers cannot write SoR** (unchanged).
10. **Remote Workers not forced.** When they exist, they remain less trusted and authenticated ≠ authorized (Decision 015).

---

## Revisit triggers

- Need for stronger FS/network/credential isolation than a process/WSL boundary **as observed**
- Level 2/3 work that Core Approval alone cannot contain
- Host compromise risk from untrusted target content
- Phase B remote Workers (isolation becomes peer sandbox + authn, still not Core)
- Any proposal that “Docker is the Worker” (adapter only, new decision)

---

## Open questions

- OS resource-limit mechanism
- Worker workspace layout (not Domain)
- Container/VM product if triggered

---

## Confidence

**MEDIUM**

Process + Kali/WSL matches locked topology without selecting Docker. Confidence is not HIGH because WSL is a coarse boundary (not a capability sandbox) and Level 2 work may later need more than process isolation.

---

# Decision 015 — Identity / Authentication / Inter-Component Trust Model

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Depends on:** Decision 005 (Worker identity vs Core authorization); Decision 008 (models are not principals); Decision 013 (SecretReference); Decision 014 (Workers less trusted)

This decision defines **who exists as an identity**, **what trust means**, and **what first implementation must record**. It does **not** select OAuth, OIDC, mTLS, IAM, an API gateway, or a service mesh.

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS — explicit identity classes; local single-operator first; authentication ≠ authorization**

There is **no** identity named “AI” or “the model” as an authorization principal. Models never authorize.

**First implementation:** one **Human Operator** identity is enough to run locally, but that identity **must exist** as a stable id for AuditEvent, Approval `decision actor`, and ResearchRun `initiator`. Control Plane and Worker identities are **distinct**. Worker authentication for remote peers is **required when Workers are services**; local child-process/WSL still has an **auditable worker id**. No IAM product.

---

## Identity classes

| Class | Role | Trust |
|---|---|---|
| **Human Operator** | Initiates runs; performs Human Review | Highest *human* authority through Interface; decisions become **Core Approval**, not “the UI said so” |
| **Control Plane** | Hosts Core, Research, Data access, Interface, Platform contracts | Highest *system* trust for **policy decisions**. Not a Worker |
| **Worker** | Side-effect runtime | Lower trust. Authenticated ≠ authorized |
| **Integration / Adapter** | Strix, Burp, ModelAdapter, SecretPort adapter, tools | **Does not inherit Core trust.** Output untrusted |
| **External Model Provider** | Completions via ModelPort | Untrusted output; not a principal |
| **External Tool Runtime** | Scanner/browser/agent processes | Untrusted output; not a principal |

No other principal is invented for “the LLM allowed it.”

---

## Trust hierarchy (conceptual)

Highest trust: **Core authority decisions** (authorization, scope, policy, budget, Approval).

Lower: **Research proposals** (untrusted as authority).

Lower: **Worker execution runtime** (untrusted as truth; may be authenticated).

**Untrusted input:** model outputs; tool outputs; web/API/email/document content; WorkerResult before Transition A; Integration content.

```
Authentication ≠ authorization
Authorization ≠ Evidence
Execution success ≠ trust
Same machine / same network / WSL ≠ trust
```

Logical trust (`PROJECT_STRUCTURE.md`) remains:

Core > Research > Interface/orchestration callers.

Workers and Integrations sit **below** that chain as execution/adapters. They do not enter the Core trust set by sharing a laptop with the Control Plane.

---

## Operator identity

v1 may be **single-user/local** (no OAuth). The operator still needs a **stable identifier** so that:

- ResearchRun has an initiator
- Approval has a decision actor
- AuditEvent can answer *who*

A missing operator id would make Approval and audit untraceable. Local “dev operator” is an identity, not an anonymous god mode that bypasses Core.

---

## Control Plane vs Worker

Control Plane identity ≠ Worker identity.

A Worker presenting “I’m on WSL on the same host” is **not** Core. Future remote communication must support **authenticated peers** (mechanism deferred). Local IPC still names the Worker runtime for audit.

---

## Worker identity

Local and future remote Workers need a **stable worker identity** (which runtime executed this job).

**Authenticated Worker ≠ authorized action.** Core issues per-request authorization and immutable budget. Worker cannot self-authorize or widen scope.

---

## Integration identity

Strix/Burp/ModelAdapter/tool adapters are **Integrations**. They do not inherit Core trust. Strix is not a Control Plane component.

---

## Transport trust

Decision 005: transport is not truth. This decision: **same-machine is not a trust grant.** Phase B remote Workers: authenticated transport required; product/protocol still unchosen.

---

## Stage 1 — Mandatory gates

| Gate | Meaning |
|---|---|
| No model principal | LLM cannot be an Approval actor or AuthorizationSource |
| Operator id for audit/approval/run | Even single-user |
| Worker id ≠ Core | Distinct identities |
| Authn ≠ authz | Core still DEFAULT DENY per request |
| No IAM product required for v1 | Local operator id is enough to start |
| Integrations untrusted | Strix ≠ Core |

All candidates that keep these gates can pass. Selecting OIDC now is not a gate; it is premature product.

---

## Comparison (why not a product now)

| Approach | v1 fit |
|---|---|
| Anonymous local, no operator id | **Fail** audit/Approval/initiator |
| Conceptual identities + local operator id (selected) | **Pass** |
| OAuth/OIDC/IAM from day one | Premature; single-developer Phase A |
| mTLS/service mesh from day one | Premature; no remote mesh |
| “Same WSL = trusted Worker” | **Fail** Stage 1 |

---

## Constraints

1. **No AI/model authorization principal.**
2. **Human Operator identity exists in v1** for initiator, Approval actor, audit.
3. **Control Plane and Worker identities are distinct.**
4. **Authentication never replaces Core authorization.**
5. **Integrations do not inherit Core trust.**
6. **Same host/WSL/network is not a trust domain.**
7. **No OAuth, OIDC, mTLS, IAM, gateway, or mesh product selected.**
8. **Identity records are not Evidence** and not Research Memory truth.
9. **Secret resolution (013) is keyed by authorized job + Worker identity**, not by model identity.
10. **Remote Worker authn mechanism deferred** until Phase B, but the **requirement** is already stated.

---

## First implementation / evolution

**v1:** Configured local operator id; Control Plane instance id; Worker runtime id on each job; no SSO. Approvals record that operator.

**Later:** Real operator authentication when multi-user; Worker peer authentication when remote; still Core authorization; still no model principal.

---

## Revisit triggers

- Second human operator
- Remote Workers (authenticated peers)
- Need to prove Control Plane authenticity to Workers
- Operator id missing on Approval/AuditEvent (bug, not “add Okta”)
- Any path treating model id as decision actor

---

## Open questions

- Operator id format/storage (not a vendor)
- Worker authentication protocol (Decision 005 open)
- Multi-user role model beyond “operator vs system”

---

## Confidence

**MEDIUM**

Identity classes and authn≠authz are required by Core, Approval, and Decision 005. Deferring IAM matches Phase A. Confidence is not HIGH because local single-user can be implemented as a fake “always-admin” if undisciplined, and Worker authn for WSL is still a stub until specified.

---

## Self-audit (Decisions 013, 014, 015)

| Forbidden reading | Status |
|---|---|
| Raw secrets in prompts | **Forbidden**; SecretReference + ModelPort constraint |
| Secrets in AuditEvent **values** | **Forbidden**; references only |
| Worker receives global/master credentials | **Forbidden** |
| Model becomes authorization principal | **Forbidden** |
| Same-machine = trusted | **Rejected** |
| Authenticated Worker = authorized action | **Rejected** |
| Strix becomes trusted Core component | **No** |
| WSL becomes permanent isolation technology | **No**; first env only |
| Docker/Kubernetes selected prematurely | **No** |
| Process boundary bypass for real Workers | **No**; in-process = fakes |
| Worker can widen scope | **No** |
| Remote Worker selected before needed | **No** |
| Isolation policy becomes domain truth | **Forbidden** |
| Secret product becomes architecture | **Product deferred** |

**FINAL STATUS: PASS**

---

# Decision 016 — Contract Representation / Versioning Strategy

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Depends on:** Decision 001 (language-neutral contracts; Python classes are not architectural truth); Decision 005 (Worker contract; transport replaceable); Decision 013 (SecretReference); Decision 014 (side-effect levels)

This decision selects the **canonical representation** of cross-boundary contracts and the **versioning layout**.

It does **not** select:

- HTTP, gRPC, or any transport
- a queue/broker
- a JSON Schema **validator library**
- a Protobuf runtime, compiler, or codegen toolchain
- OpenAPI as Worker-contract truth
- ORM / database types (including UUID)

**Canonical contract representation ≠ transport protocol.**

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS**

**Canonical representation: JSON Schema Draft 2020-12** (`https://json-schema.org/draft/2020-12/schema`).

**Versioning:** major contract generations live under `contracts/v1/`, `contracts/v2/`, … Messages carry an explicit `contract_version`. Breaking semantic/schema change ⇒ new major directory. Backward-compatible additive fields may stay in the same major. Field removal or meaning change is breaking.

Python classes, PostgreSQL types, OpenAPI documents, and Protobuf encodings are **not** the architectural contract truth. They may later **project** or **carry** these schemas.

**PRODUCT / RUNTIME: DEFER** — no validator or codegen dependency in this decision.

---

## Why this decision exists

Decision 001: contracts are language-neutral; Python types are not architectural contracts. Decision 005: local IPC now, remote transport later, same Worker contract. A1 must exist as files Workers in Python/Go/Rust can validate without importing `zest`.

Python classes as canonical truth would lock Workers to the control-plane package. OpenAPI as canonical truth would lock the Worker boundary to HTTP (Interface may use OpenAPI later; that is a different surface). Protobuf as **canonical** truth is a capable IDL but couples identity, codegen, and a binary encoding before any remote Worker exists. Avro pulls schema-registry/broker gravity. Hybrid dual-canonical (Schema + proto both “truth”) splits the SoR of the contract itself.

JSON Schema is validation-focused, inspectable, codegen-optional, and transport-independent: the same schema can describe a JSON object over a pipe today and over a later request/response without becoming that transport.

---

## Stage 1 — Mandatory gates

| Gate | Meaning |
|---|---|
| Language-neutral | Go/Rust Workers are not blocked |
| Independent of Core/Research classes | No `zest` import required to know the shape |
| Machine-validatable | Schema files parse; later a validator may be chosen |
| Transport-neutral | Not HTTP, not a broker, not Postgres |
| Versionable | Explicit major + in-message version |
| Secret-safe | Values cannot appear as required secret fields |

### Candidate results

| Candidate | Stage 1 | Note |
|---|---|---|
| **1. Python classes as canonical** | **Fail** | Violates Decision 001. Not language-neutral |
| **2. JSON Schema** | **Pass** | Selected. Semantics without transport or codegen |
| **3. Protocol Buffers as canonical** | **Pass as a later encoding** | Strong IDL. Canonical use forces protoc/runtime coupling now; user forbade installing that runtime this round. Not rejected as incapable |
| **4. OpenAPI as canonical Worker truth** | **Fail as Worker canonical** | HTTP-specific Interface description. May later describe Decision 011 API as a **projection** |
| **5. Avro-like** | **Pass weakly** | Extra ecosystem; broker/registry gravity. Not needed |
| **6. Dual canonical hybrid** | **Fail clarity** | Two truths. Allowed later: JSON Schema canonical + other encodings as projections |

---

## Candidate evaluations

**Python classes** — convenient for the control plane; forbidden as architecture. Implementation DTOs may exist later **derived from** schemas, not the reverse.

**JSON Schema Draft 2020-12** — selected. `$id` is the contract id. `$schema` pins the dialect. Top-level `additionalProperties: false` keeps unknown authority-bearing fields from being silently accepted. Extensibility is confined to named bags (`arguments`, `raw_result`, `diagnostics`, `discovery_context`, `control_signal`). Those bags **must not** carry policy, authorization, Evidence, Finding, Approval, or budget truth.

Trade-offs accepted: no native types; no binary framing; evolution still needs discipline. None of those choose a transport.

**Protobuf** — best-in-class for multi-language **encoding** and evolution if remote Workers appear. Not canonical now: installing runtime/codegen would make protobuf the architecture. A later decision may add `.proto` **projections** of v1 JSON Schema without replacing canonical files.

**OpenAPI** — Worker execution is not an HTTP resource. Interface Phase A may publish OpenAPI later; it must `$ref` or duplicate these Worker schemas, not become their parent.

**Avro** — similar to protobuf with more Kafka-shaped ops. Rejected for v1.

---

## Versioning rules

```
contracts/
  v1/          # current major
  v2/          # next breaking generation, if needed
```

- `contract_version` on Worker-facing messages is `"v1"` for this generation.
- Additive optional fields: same major, documented.
- Remove/rename/change meaning: new major (`v2/`).
- **Transport version ≠ domain lifecycle version ≠ this contract major.** Experiment status and HTTP API versions are different axes.
- Producer/consumer compatibility is **testable** (contract tests later). No SemVer tooling product is selected.

---

## Constraints

1. **JSON Schema Draft 2020-12 is canonical.** Other formats are projections or transports.
2. **Python class ≠ contract truth.**
3. **Transport ≠ contract semantics.** IPC/HTTP/gRPC/queue unchosen.
4. **No UUID or PostgreSQL type in contracts.** Identifiers are opaque strings.
5. **No Protobuf/OpenAPI/Avro runtime or codegen in this decision.**
6. **No JSON Schema Python library required** to store or lint files with the stdlib.
7. **Secret values forbidden** in schemas (Decision 013).
8. **WorkerResult ≠ Observation/Artifact/Evidence/Candidate/Finding.**
9. **ReauthorizationRequest is not an authorization decision.**
10. **Side-effect level is not policy.** Level 3 is representable and **denied by default** in Core (Decision 014).
11. **Workers cannot mint authorization or raise budget** via payload.
12. **`additionalProperties: false`** on authority-bearing objects. Extensibility only on listed bags.

---

## Revisit triggers

- Remote Workers need a binary encoding (consider Protobuf **projection**, not a silent canonical swap)
- Interface needs HTTP description (OpenAPI **projection** of these schemas)
- Schema dialect or `$id` policy becomes operationally painful
- Compatibility tests fail because silent additional properties were allowed on authority objects

---

## Open questions

- JSON Schema validator library (later)
- Whether `.proto` projections are generated
- Exact additive-field process inside `v1`

---

## Confidence

**MEDIUM**

JSON Schema is the only candidate that is language-neutral, transport-neutral, and codegen-free for A1 without contradicting Decision 001. Confidence is not HIGH because Protobuf remains a strong **encoding** alternative if Workers go remote, and Draft 2020-12 still needs human evolution discipline.

---

## Self-audit (Decision 016)

| Forbidden reading | Status |
|---|---|
| Python class is canonical truth | **No** |
| Contract is PostgreSQL-specific | **No** |
| UUID forced as DB type | **No**; opaque strings |
| HTTP selected | **No** |
| Queue/broker selected | **No** |
| JSON Schema library/product lock-in | **Dialect chosen; runtime deferred** |
| Protobuf canonical lock-in | **Not selected as canonical** |
| WorkerResult is Observation/Evidence | **Forbidden in A1 docs/schemas** |
| Raw payload is authority | **Forbidden**; extensible bags only |
| Secret values in contract | **Forbidden** |
| Worker produces authorization | **No** |
| Worker changes budget | **Budget is Core-issued; result has no budget authority** |
| Reauthorization = authorization | **No** |
| Side-effect level replaces policy | **No**; Core still DEFAULT DENY; level 3 denied by default |
| Go/Rust Workers blocked | **No** |

**FINAL STATUS: PASS**

---

# Decision 017 — False Positive / Verification Discipline

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Depends on:** Domain Model (Observation ≠ Hypothesis ≠ Evidence ≠ Candidate ≠ Finding; Candidate lifecycle including INCONCLUSIVE; Verification proposes, does not commit); Decision 008 (model output is UNTRUSTED STRUCTURED PROPOSAL; verification is a ModelPort role hint, not a v1 second provider); Decision 012 (research metrics are SoR aggregates, not a second ledger)

This decision locks **verification quality and false-positive suppression** as first-class architecture.

It does **not** select:

- a confidence-score product, threshold, or calibration method
- a second model provider or multi-agent runtime
- Verification as a Core authority
- new Candidate lifecycle states
- V0–V4 as stored domain enums
- test payloads, scanner products, or exploit PoC libraries

Python types, PostgreSQL columns, and prompt text are **not** this decision’s contract.

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS**

Zest optimizes for **high-confidence, evidence-backed, reproducible Findings**.

It does **not** optimize for maximum Candidate count, scanner-hit volume, or model-suspicion volume.

False-positive suppression is a standing requirement of Research, Data (Evidence/Candidate records), and Human Review — **not** of Core authorization.

A Candidate is not a Finding. A model assertion is not Evidence. A scanner match is not Evidence. A WorkerResult is not Evidence. A successful HTTP or tool response is not automatically a vulnerability.

**CONFIDENCE MODEL: DEFER.** No universal numeric threshold (including 0.70) is architecture. If a numeric score is added later, it is an explicit, evidence-calibrated annotation. It cannot promote a Candidate, replace Verification, or replace Human Review.

---

## Why this decision exists

The domain already forbids collapsing WorkerResult → Evidence → Finding. Without an explicit verification discipline, a later Research Brain can still drift into “scanner match = issue” or “the same model that guessed now confirms.”

That drift would maximize Candidates and destroy trust. Bug-bounty and authorized research value accepted Findings with provenance, not a firehose of unverified signals.

INCONCLUSIVE already exists as a Candidate outcome. This decision forbids removing it to force binary VALID/INVALID and forbids promoting uncertainty to raise Finding count.

---

## Conceptual validation ladder

These are **verification maturity levels**, not Candidate lifecycle states and not Finding states. They must not become a second `verification_status` field that competes with Candidate lifecycle (DOMAIN_MODEL.md).

They may remain documentation-only until a later Research/Data design needs an annotation. They are **not** required enums in A2 Core or A3 persistence.

| Level | Name | Meaning |
|---|---|---|
| **V0** | Unverified signal | Heuristic, scanner match, model suspicion, anomaly. May seed a Hypothesis. Must not be Evidence or a Finding |
| **V1** | Observed behavior | Directly observed, reproducible where practical. Still not vulnerability proof |
| **V2** | Security hypothesis supported | Admitted Evidence supports a security-relevant Hypothesis. Expected vs observed behavior differs |
| **V3** | Verified security impact | Exploitability or invariant violation demonstrated with provenance. Alternative benign explanations evaluated |
| **V4** | Human-accepted Finding | Existing path only: Candidate VALIDATED + FindingProposal + Human Review + Core Approval |

V3 is **necessary but not sufficient** for Candidate VALIDATED. VALIDATED remains Research’s Evidence-and-invariant commit, not a maturity-label rewrite.

V4 is **not a new status**. It is the existing Finding path. No Finding exists at V0–V3.

A claim may sit at V1/V2 and the Candidate may still be INCONCLUSIVE. That is correct.

---

## What a vulnerability claim must answer

Verification of a security claim must be able to answer, as structured research record — not as chat residue:

1. What was expected?
2. What actually happened?
3. Why is this security-relevant?
4. Under which actor / session / authorization / state?
5. Can it be reproduced (where practical)?
6. What Evidence proves it (admitted Observation/Artifact, with provenance)?
7. What benign explanation was ruled out?

Missing answers favor **INCONCLUSIVE** or further Experiment, not VALIDATED.

---

## False-positive control rules

Future Verification logic (Research) must consider, where relevant to the claim:

1. Reproducibility
2. Preconditions
3. Authorization context
4. Session / user identity
5. Expected behavior
6. Negative controls
7. Alternative explanations
8. Environmental noise
9. Tool / scanner limitations
10. Duplicate behavior
11. Evidence provenance
12. State consistency
13. Temporal consistency

These are evaluation concerns. They are not Core scope-matching, not Worker payloads, and not a hardcoded exploit catalog.

---

## Negative controls

Where a Candidate depends on a **behavioral difference**, Verification should prefer differential / control observations over a single isolated success response.

Conceptual controls (examples, not payloads):

- same action with an unauthorized actor
- same action with an invalid object
- same action before vs after a state change
- same endpoint with a control parameter
- same action under the expected valid role

A control observation is still an Observation until Transition B. It does not become Evidence by being “negative.” Negative Evidence is first-class only after admission.

This decision does **not** implement or prescribe test payloads.

---

## INCONCLUSIVE is a valid outcome

Do not force binary VALID / INVALID.

Candidate lifecycle remains:

`OPEN → VERIFYING → VALIDATED / REJECTED / INCONCLUSIVE / DUPLICATE / OUT_OF_SCOPE`

INCONCLUSIVE is the correct result when Evidence is insufficient, alternatives are untested, or impact is not demonstrated.

Never promote uncertainty to increase Finding count.

REJECTED is for disconfirmed claims. DUPLICATE is for already-represented behavior. OUT_OF_SCOPE is a scope outcome, not a verification shortcut around Core.

---

## Verification independence

The same model/agent that generated a Hypothesis must **not** be automatically trusted to validate its own conclusion.

Preferred conceptual flow:

```
Hypothesis generation
→ evidence collection
→ verification evaluation
→ independent challenge / counter-hypothesis
→ Candidate transition proposal
→ Research commit of Candidate state (not Verification-record authority)
```

This does **not** require a second model provider in v1 (Decision 008: live multi-model routing deferred). Independence may be any of:

- a different reasoning pass
- a different prompt / context role (ModelPort verification role hint)
- deterministic checks
- a different Worker producing new Observations
- Human Review (required for Finding; may also challenge earlier)
- a later verifier model, if and when routing is justified

Do not force multi-agent infrastructure. Do not move this into Core.

---

## Counter-hypothesis

Future Verification must actively ask: **what else could explain this observation?**

Conceptual alternatives include: intended behavior, caching, stale session, authorization inherited by design, asynchronous processing, user error, rate-limiting artifact, WAF behavior, test-environment behavior, scanner/parser false positive.

The system must seek **disconfirming** evidence, not only confirming evidence.

A high-impact Candidate that has only confirming Evidence and no recorded attempt to rule out benign explanations is not ready for VALIDATED.

---

## Stage 1 — Mandatory gates

| Gate | Meaning |
|---|---|
| Signal ≠ Evidence | Scanner, model, WorkerResult, raw HTTP success cannot skip Transition B |
| Evidence ≠ Finding | Admitted Evidence still requires Candidate VALIDATED + proposal + human + Core Approval |
| INCONCLUSIVE preserved | Insufficient evidence is not forced VALIDATED or REJECTED |
| Score ≠ authority | No numeric threshold promotes or accepts |
| Independence possible | Self-check by the generating model is not the sole validation path |
| Core unchanged | Verification quality is not execution authorization |
| No payload catalog | This decision does not specify exploits or scanner rules |

### Candidate results

| Candidate | Stage 1 | Note |
|---|---|---|
| **1. Maximize Candidate/scanner volume** | **Fail** | Opposite of this decision |
| **2. Binary VALID/INVALID only** | **Fail** | Deletes INCONCLUSIVE |
| **3. Confidence threshold as promotion** | **Fail** | Fake precision; bypasses Verification and Human Review |
| **4. Generator model auto-validates itself** | **Fail as sole path** | Independence required; mechanism deferred |
| **5. Separate verifier vendor required in v1** | **Fail as requirement** | Contradicts Decision 008 product deferral |
| **6. Evidence-backed ladder + existing Finding path** | **Pass** | Selected. V0–V4 conceptual; V4 = current promotion path |
| **7. Put Verification in Core** | **Fail** | Core owns authorization/Approval, not research judgment |

---

## Constraints

1. **Optimize for accepted, evidence-backed Findings**, not Candidate count.
2. **Model assertion ≠ Evidence.** Scanner match ≠ Evidence. WorkerResult ≠ Evidence. HTTP/tool success ≠ vulnerability.
3. **Finding never from model output, scanner signal, WorkerResult, or confidence score alone.** Path remains VALIDATED Candidate + FindingProposal + Human Review + Core Approval.
4. **V0–V4 are conceptual maturity**, not Candidate lifecycle and not a competing status authority.
5. **INCONCLUSIVE remains valid.** Uncertainty is not promoted.
6. **Numeric confidence is deferred.** If added later: explicit meaning, evidence-based calibration, cannot promote, cannot replace Verification, cannot replace Human Review.
7. **Hypothesis generator is not sole validator** of its own conclusion.
8. **Seek disconfirming evidence** for high-impact claims where practical.
9. **Negative controls preferred** when the claim is a behavioral difference.
10. **Verification proposes; Research commits Candidate state; Human Review + Core Approval create Finding.** Verification cannot create Finding or commit Candidate state (unchanged domain).
11. **No test payloads, scanner products, or Core changes** in this decision.
12. **Research Memory does not become verification truth.** Episodic Verification records remain proposals plus provenance, not a shadow Finding ledger.

---

## Revisit triggers

- Measured false-positive rate or Human Review rejection rate makes the current process unusable
- A calibrated confidence model exists with explicit meaning and does **not** bypass Verification/Human Review
- A verifier model or second provider is justified by measurement (still behind ModelPort; still untrusted)
- Duplicate semantics (open in DOMAIN_MODEL.md) are designed and need Verification rules
- Evidence-admission authority details are locked (open in DOMAIN_MODEL.md)

Revisit does **not** mean: drop Human Review, treat scanner output as Evidence, or move Verification into Core.

---

## Open questions

- Whether V0–V4 are ever stored as annotations (must not replace Candidate lifecycle)
- Exact Verification process design and record schema
- Confidence/belief calibration (DOMAIN_MODEL.md open question; this decision only forbids treating it as truth)
- How strongly negative controls are required vs preferred per claim class
- Evidence admission authority mix (human / deterministic / verifier-assisted)

---

## Confidence

**HIGH**

The discipline matches existing domain law. The constraints close the remaining loopholes (score-as-truth, self-validation, INCONCLUSIVE erasure, scanner-as-Evidence) without choosing products or schemas.

Confidence is not a claim that Verification is implemented. The ladder is conceptual; independence mechanisms and confidence scoring remain deferred.

---

## Self-audit (Decision 017)

| Forbidden reading | Status |
|---|---|
| Scanner finding is Evidence | **No** |
| Model assertion is Evidence | **No** |
| Confidence threshold is truth | **No**; model deferred |
| Candidate automatically promoted | **No**; Verification proposal + Research commit; Finding still needs human + Core |
| INCONCLUSIVE removed | **No** |
| Verification only seeks confirmation | **No**; counter-hypothesis required in future Verification |
| Negative controls ignored | **No**; preferred where claim is differential |
| Same generator auto-validates without challenge | **No** as sole path |
| Human Review bypassed | **No** |
| V0–V4 replace Candidate lifecycle | **No** |
| Second model vendor required in v1 | **No** |
| Verification moved into Core | **No** |
| Payloads / exploits specified | **No** |

**FINAL STATUS: PASS**

---

# Decision 018 — Novel Discovery / Exploration Strategy

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Depends on:** Decision 017 (verification discipline; signal ≠ Finding); Decision 008 (LLM is not a novelty engine; output untrusted); Decision 009 (Research Memory is not SoR / not shadow truth); Decision 002 (no premature graph/SoR companion); Core authority (scope, budget, side-effect — exploration cannot bypass)

This decision locks a **realistic novelty strategy** and future **Research Brain** capability requirements.

It does **not** select:

- a graph database or target-model schema
- numerical priority weights or a scoring formula
- a claim that AI discovers new vulnerability classes
- multi-agent exploration infrastructure
- moving research intelligence into Core
- N4 (“new vulnerability class”) as a product promise

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS**

Zest will be built to **support** expert-grade authorized research: persistent target understanding, invariant hypotheses, differential reasoning, and measured exploration.

It will **not** be described or sold as:

- autonomous novel vulnerability discovery
- an expert replacement
- a “zero-day machine”

unless empirical evaluation supports those claims.

**Realistic novelty target (v1/v2):**

| Level | Meaning | Product stance |
|---|---|---|
| **N1** | Known vulnerability pattern on a new target instance | **Strong target** |
| **N2** | New combination / chain of known primitives | **Meaningful target** |
| **N3** | Target-specific invariant or business-logic violation | **Increasing capability**; not guaranteed |
| **N4** | Genuinely new vulnerability mechanism/class | **Research aspiration only**; not a product promise |

Do not claim N4 capability. Do not treat generic LLM prompting as a novelty engine.

---

## Why this decision exists

Without this lock, later Research work collapses to `LLM → tool → result → LLM`, or over-claims “AI finds novel classes.” Both fail the project identity: Zest is not an AI vulnerability scanner.

Known-pattern replay (N1) is valuable and honest. Chain and invariant reasoning (N2/N3) need architecture: target/state model, differential tests, negative evidence, exploration vs exploitation. Those belong in **Research**, using Data records and Core-authorized execution — not in Core policy and not in a prompt.

Exact ranking formulas and graph schemas are premature. Inventing weights would be fake precision (same failure mode Decision 017 forbids for confidence).

---

## Research Brain (future, not Core)

Future Research Brain must not reduce to “ask the LLM for vulnerability ideas” or a closed `LLM → tool → LLM` loop.

When designed, it must be **architecturally capable** of:

1. Persistent target model
2. Actor / role / session reasoning
3. Resource relationship reasoning
4. State-transition reasoning
5. Expected-invariant representation
6. Differential analysis
7. Temporal comparison
8. Multi-step hypothesis chaining
9. Negative evidence memory
10. Exploration vs exploitation
11. Information-gain-driven testing
12. Hypothesis diversity
13. Evidence-based prioritization
14. Cost-aware testing

This is a **capability requirement list**, not a slice that starts now, not a microservice, and not a Core module.

Core still answers only: authorization, scope, budget, side-effect, Approval, ExecutionDecision.

---

## Target model (capability, not schema)

Future target model should be able to represent, **where observed**:

- actors, roles, sessions
- resources, relationships
- actions
- preconditions, postconditions
- state transitions
- trust boundaries

Do **not** create a database schema in this decision. Do **not** invent a complete entity catalog. Do **not** add a graph product (Decision 002/009 remain). Observed structure is stored as domain records and typed projections; inferred structure remains Hypothesis.

---

## Invariant mining

Future Research should be able to derive and **test** hypotheses such as (examples, not hardcoded rules):

- only owner may mutate resource
- submitted object should become immutable
- tenant A data should not be accessible by tenant B
- privileged transition should require privileged actor
- token should be bound to expected actor / session / action
- state progression should follow expected rules

**Invariant hypothesis ≠ fact.** It becomes useful only after Observations and admitted Evidence. An invariant guess is a Hypothesis. Breaking it in one response is not a Finding (Decision 017).

---

## Differential reasoning

Future engine should compare relevant contexts, for example:

- actor A vs actor B
- role A vs role B
- anonymous vs authenticated
- before vs after state change
- old vs new endpoint / version
- snapshot t1 vs t2

**Differential anomaly ≠ vulnerability.** It creates or updates a Hypothesis. Promotion still follows Decision 017 and the Finding path.

---

## Exploration vs exploitation

Do not build a system that only tests high-confidence known patterns.

Future prioritization should **reserve some ResearchRun budget** for low-confidence but high-novelty or high-information-gain Hypotheses.

Conceptual factors may include: expected security value, likelihood, novelty, information gain, chain potential, evidence quality, test cost, duplicate probability.

**Do not freeze a formula. Do not invent numerical weights.**

Exploration is still Core-authorized work:

- no extra scope
- no budget bypass
- no side-effect-level exception
- no Worker self-authorization

Research proposes; Core decides ExecutionDecision; Workers execute.

---

## Negative knowledge

Persistent Research Memory (read model over SoR — Decision 009) must preserve:

Hypothesis + context + Experiment + negative result + reason.

**Negative in context C ≠ globally impossible.**

Negative evidence stays **context-bound**. It must not be generalized into “this class never exists on this program” without new Evidence. BUDGET_EXHAUSTED and EXECUTION_FAILED remain execution outcomes, not negative Evidence (DOMAIN_MODEL.md).

Research Memory must not become a shadow truth database of “known impossible bugs.”

---

## Hypothesis diversity

Future hypothesis generation must not rely on one LLM prompt.

Potential sources (none is truth):

- target state model
- authorization relationships
- endpoint semantics
- observed parameter relations
- temporal changes
- differential behavior
- technology-specific observations
- previous failed hypotheses
- human analyst seeds
- tool outputs
- chain opportunities

Tool output and model output remain untrusted proposals (Decisions 008, 017).

---

## Anti-hype / measurement

Zest claims must be **measurement-driven**.

Do not describe capabilities as autonomous novel vulnerability discovery, expert replacement, or zero-day machine unless empirical evaluation supports them.

Track real metrics (SoR aggregates via ObservabilityPort — Decision 012; not a second Candidate ledger). Including:

- Candidate → VALIDATED conversion
- Candidate → REJECTED conversion
- INCONCLUSIVE rate
- false-positive rate
- duplicate rate
- accepted Finding rate
- requests per accepted Finding
- cost per accepted Finding
- time-to-first-valid-Finding
- reproduction success rate
- verification disagreement rate

Decision 012 already named several of these. This decision requires using them (and the additional verification-quality rates) **before** capability claims. It does not choose a metrics product.

---

## Stage 1 — Mandatory gates

| Gate | Meaning |
|---|---|
| Honesty | N4 not promised; LLM prompt ≠ novelty engine |
| Layering | Research Brain in Research, not Core |
| Authority | Exploration cannot bypass scope/budget/policy |
| Epistemology | Invariant hypothesis and differential anomaly are not Findings |
| Memory | Negative evidence context-bound; Memory ≠ SoR |
| No fake precision | No frozen weights |
| No premature schema | No graph/target DB design in this decision |

### Candidate results

| Candidate | Stage 1 | Note |
|---|---|---|
| **1. Promise N4 / “AI finds new classes”** | **Fail** | Hype; unsupported |
| **2. Generic LLM prompt as novelty engine** | **Fail** | Contradicts project identity and Decision 008 |
| **3. Frozen numerical priority formula now** | **Fail** | Fake precision |
| **4. Target graph/database schema now** | **Fail** | Premature; SoR/companion already decided |
| **5. Put novel research in Core** | **Fail** | Core is authority, not intelligence |
| **6. Exploration bypasses budget/scope** | **Fail** | DEFAULT DENY |
| **7. Negative evidence is global impossibility** | **Fail** | Context-bound required |
| **8. Realistic N1/N2, growing N3; Brain as Research capability** | **Pass** | Selected |

---

## Constraints

1. **v1/v2 target is strong N1, meaningful N2, increasing N3. N4 is aspiration, not promise.**
2. **No hype claims** without measurement.
3. **Research Brain is Research, not Core.**
4. **Target model is a future capability, not a schema or graph product in this decision.**
5. **Invariant hypothesis ≠ fact.** Differential anomaly ≠ vulnerability.
6. **No exact scoring formula or weights now.**
7. **Exploration cannot bypass Core** authorization, scope, budget, or side-effect policy.
8. **Negative evidence is context-bound**, not global impossibility.
9. **Hypothesis sources are diverse; none mint truth.**
10. **Research Memory remains a read/retrieval abstraction**, not shadow SoR (Decision 009).
11. **Decision 017 still applies** to every novel claim: Evidence, Verification, INCONCLUSIVE, Human Review.
12. **No Worker, scanner, or Strix product** is selected as the discovery engine.

---

## Revisit triggers

- Empirical N3 (or claimed N4) results exist and need a tighter product statement
- Prioritization without any exploration starves information gain (measured)
- Duplicate rate or cost-per-Finding makes pattern-only N1 insufficient
- A target-model persistence design is needed for A3+ and can be done without a new SoR paradigm
- Decision 008 routing is revisited and a verifier/explorer role is measured as useful
- Operators need documented exploration-budget **policy** (still Core-issued envelopes; still no bypass)

Revisit does **not** mean: promise N4, move Brain into Core, or treat Memory as truth.

---

## Open questions

- Target-model persistence shape (records vs projections; still not a graph-product mandate)
- Duplicate semantics (DOMAIN_MODEL.md)
- When, if ever, a numeric prioritization function is calibrated
- How much ResearchRun budget is reserved for exploration (policy later; Core still enforces totals)
- Chain-search algorithm (later Advanced Research)

---

## Confidence

**MEDIUM**

The anti-hype stance, N1–N3 targeting, and “Brain lives in Research” split are solid. Confidence is not HIGH because target-model design, exploration policy, and N3 capability are explicitly future, and over-building a “world model” remains a real failure mode. Constraints exist so that future work cannot “complete” this decision by inventing a graph schema or a weight vector.

---

## Self-audit (Decision 018)

| Forbidden reading | Status |
|---|---|
| N4 promised without evidence | **No** |
| Generic LLM prompt is novelty engine | **No** |
| Exact scoring weights invented | **No** |
| Invariant hypothesis treated as fact | **No** |
| Differential anomaly treated as vulnerability | **No** |
| Exploration bypasses scope/budget | **No** |
| Novel research moved into Core | **No** |
| Research Memory became shadow truth | **No** |
| Negative evidence generalized globally | **No** |
| Target graph/database schema over-designed | **No**; capability only |
| Decision 017 bypassed for “novel” claims | **No** |
| Graph/vector product selected | **No** |

**FINAL STATUS: PASS**

---

# Decision 019 — Python Packaging / Dependency Management Strategy

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Depends on:** Decision 001 (Python control plane; packaging was explicitly deferred); Decision 010 (Windows host + Kali/WSL Worker; containers not required); Decision 014 (Workers less trusted / out of process)

This decision selects **how the control-plane Python project declares, resolves, and locks dependencies**.

It does **not** select:

- a web framework, ORM, model provider, or linter-as-architecture
- a PyPI publishing product
- conda / pyenv / Docker as the environment model
- Worker-tool dependency stacks (Kali/WSL remains a separate runtime)
- Python patch version beyond a `requires-python` floor

**Python project metadata ≠ dependency resolver ≠ virtual environment ≠ runtime architecture.**

Replacing the installer later must not change Domain, Core, contracts, or PostgreSQL product (Decision 003).

This turn does **not** create `pyproject.toml`, a lockfile, or install anything.

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS**

| Piece | v1 |
|---|---|
| **Project metadata** | PEP 621 **`pyproject.toml`** — names, `requires-python`, runtime dependencies, optional/dependency groups |
| **Build backend** | **Hatchling** (PEP 517). Needed for editable installs. Not an application framework |
| **Virtual environment** | stdlib **`venv`** (the chosen installer may create it). No global site-packages architecture. No conda |
| **Resolver / installer / lock tool** | **uv** as the **first** control-plane tool |
| **Lock artifact** | committed **`uv.lock`** (resolution record, not Domain truth) |
| **Worker packaging** | **Not this pyproject.** `workers/python/` and Kali/WSL tool deps stay separate |

**PRODUCT: uv is a replaceable installer**, not Zest architecture.

Declared dependencies live in `pyproject.toml`. The lockfile records a resolution. The venv holds installs. Core/Research import neither uv nor Hatchling.

---

## Why this decision exists

Decision 001 listed packaging as a real Python risk: interpreter drift, unpinned deps, **host vs WSL drift**, native wheels, irreproducible environments. A3 cannot add PostgreSQL libraries without a declaration-and-lock story.

`requirements.txt` alone is not PEP 621 metadata. Poetry-as-the-project is a workflow product pretending to be packaging standards. pip without a lock fails reproducibility. The control plane must be recreatable on Windows (Cursor host) and inspectable from WSL without becoming two architectures.

---

## Distinctions (non-negotiable)

| Concept | Role | Not |
|---|---|---|
| **`pyproject.toml`** | Declared project metadata and dependency *intent* | Not the resolver, not the venv, not Core |
| **Lockfile** | Frozen resolution (hashes) for CI/`--frozen` installs | Not Domain; tool-specific format allowed |
| **venv** | Isolated install target | Not a deployment topology (Decision 010) |
| **uv** | First tool that creates venv, resolves, installs, locks | Not importable from `src/zest` |

Runtime architecture remains: Core authorizes, Workers execute, Data persists. Packaging does not change that.

---

## Stage 1 — Mandatory gates

A packaging approach is eliminated if it cannot support:

| Gate | Meaning |
|---|---|
| Standards metadata | PEP 621 (or equivalent) project table; not a proprietary project file as the only metadata |
| Reproducible resolution | Lock or equivalent hashed freeze; “whatever pip got today” fails |
| Windows + WSL | Same declared deps usable on the developer host and in WSL without a second metadata language |
| CI | Non-interactive install from lock; no assumed global `pip install` of the app |
| Editable / local | `pip`/`uv` editable install of `src/zest` |
| Grouped deps | Runtime vs test/dev separable |
| Isolation | No global interpreter as the architecture |
| Non-leakage | Packaging tool is not a Domain/Core import |
| Replaceability | Metadata survives swapping the installer |

### Candidate results

| Candidate | Stage 1 | Note |
|---|---|---|
| **1. venv + pip + requirements.txt only** | **Fail metadata / weak lock** | Common, not PEP 621. Pin files often hand-maintained. Rejected as the *strategy* |
| **2. pyproject.toml + pip (no lock tool)** | **Fail reproducibility** | Metadata passes. pip is not a complete lock strategy for this project yet |
| **3. pyproject.toml + pip-tools** | **Pass** | Strongest *portable lock artifact* (hashed requirements). Heavier bootstrap; split files per extra |
| **4. Poetry** | **Pass weakly as a product** | Can lock and venv. Higher **tool-as-project** gravity (`poetry.lock` + Poetry workflow). Weakest replaceability among lock tools |
| **5. PDM** | **Pass** | PEP 621 native, lockfile. Viable. Smaller operational familiarity for this repo’s CI story |
| **6. uv** | **Pass** | PEP 621 + lock + venv + CI binary. Selected as **first tool**, not as architecture |
| **7. conda / pixi as architecture** | **Fail** | Second packaging universe; hides “Python project” behind a solver that is not PEP 621-primary |

No candidate was chosen because it is popular or fast. Speed is a Stage 2 convenience among tools that already pass.

---

## Candidate evaluations

**requirements.txt-only** — Fine for a script folder. Fails “standards-based Python packaging” and makes extras/dev groups informal. Not selected.

**pyproject.toml + pip without lock** — Correct metadata, insufficient reproducibility. Decision 001’s host-vs-WSL risk would remain. Not selected as the full strategy.

**pip-tools** — Compiles PEP 621 optional dependencies to hashed requirement files. Maximum lock *portability* if uv is abandoned (`uv export` can also emit requirements). Bootstrap is longer (`python -m venv`, install pip-tools, compile, sync). Multiple compiled files for groups. **Strongest alternative**, not selected because this repo has two OS environments (Windows host Control Plane, WSL Worker) and needs a **single project lock** plus low-friction venv creation without a global helper package. pip-tools remains the default replacement path.

**Poetry** — Capable locker. Treats Poetry as the project interface. Replacing Poetry later is a workflow migration, not a one-file metadata keep. Unnecessary lock-in. Not selected.

**PDM** — Technically aligned with PEP 621. Not selected only because uv covers the same gates with a simpler CI bootstrap (standalone installer) for a single-developer Phase A. PDM remains a valid replacement.

**uv** — Selected as the **first resolver/installer**. Uses `pyproject.toml` as declared truth; `uv.lock` as resolution truth; venv as install truth. Windows and WSL are first-class install targets for the *same* lockfile, which is the Decision 001 packaging risk this decision actually has to hit. Universal lock reduces “compiled on Windows, broken marker on WSL” compared with environment-specific requirement compiles.

uv’s speed is **not** the justification. If uv vanished, keep `pyproject.toml`, generate a pip-tools or PDM lock, keep Hatchling, keep venv. Domain and Core do not change.

---

## Stage 2 — Differentiators (among tools that pass)

| Concern | pip-tools | PDM | uv | Poetry |
|---|---|---|---|---|
| Metadata is PEP 621 | Yes (input) | Yes | Yes | Yes (now); workflow still Poetry-shaped |
| Lock semantics | Hashed req files | `pdm.lock` | `uv.lock` | `poetry.lock` |
| Windows / WSL one lock | Workable; extras split | Workable | Designed as one lock | Workable |
| CI bootstrap | Needs pip-tools in the image | Needs PDM | Standalone binary | Needs Poetry |
| Tool gravity into repo docs | Low | Medium | Medium | High |
| Replacement cost | Low | Medium | Medium | High |

uv wins Stage 2 on **one lock + one metadata file + CI bootstrap**, not on brand.

---

## How replacement stays off Domain/Core

Core and Research depend on **Python stdlib + later in-tree packages**, never on the installer.

SQLAlchemy/psycopg/Alembic (Decision 020) are **Data-adapter libraries**. They may be *installed* into the control-plane venv because Phase A runs local PostgreSQL next to the Control Plane. **Import rules**, not extras, enforce boundaries:

- `zest.core` / `zest.research` must not import uv, hatchling, sqlalchemy, psycopg, alembic
- Architecture tests (already used for Core) remain the regression guard

Optional dependency groups (`dev`, `test`, later `integrations` providers) keep runtime vs development intent explicit. They are not a second architecture.

Provider SDKs, browser stacks, and Kali tool wheels **do not** enter Core extras “because uv can add them.” Integrations and Workers keep their own dependency surfaces.

---

## Constraints

1. **`pyproject.toml` is the declared-dependency source of truth.** Do not maintain a parallel handwritten runtime `requirements.txt` as competing truth. Export files, if any, are generated.
2. **Lockfile is committed** and CI installs frozen from it. Unlocked “pip install sqlalchemy” in docs is not the workflow.
3. **venv isolation.** No global Python as the project environment. Document `uv sync` (or equivalent) into `.venv`.
4. **`requires-python` floor: `>=3.11`.** A2 uses modern typing; 3.11 is the first control-plane floor. Not a claim that 3.10 is incapable.
5. **Hatchling is the first build backend**, replaceable without Domain change.
6. **uv is replaceable.** Do not import it. Do not put uv-specific types in Domain.
7. **Control-plane pyproject ≠ Worker environment.** Kali/WSL tool deps are not this lock.
8. **Data-layer libraries may be installed in the control-plane venv** and still must not be imported by Core/Research (Decision 020).
9. **No conda/poetry-as-architecture.** No Docker selected to “fix packaging” (Decision 010).
10. **No linter/test-runner product** is selected here. stdlib `unittest` remains until a later decision. Adding ruff/mypy later is a dev-group choice, not this strategy.
11. **Secrets stay out of pyproject** (Decision 013).

---

## Revisit triggers

- Frozen lock installs differ across Windows vs WSL in a way that breaks A3
- uv lock format or resolver bugs block CI reproducibility
- PEP 751 / pip-native lock becomes the better committed artifact (`uv.lock` exported or replaced; metadata stays)
- Publishing a wheel to a private index needs a different backend
- Control-plane vs Worker dependency split needs a second pyproject (still not Core)

Revisit does **not** mean: put packaging types in Core, or make Poetry/conda the architecture.

---

## Open questions

- Exact optional-group names (`dev` / `test` / `postgres`) at first `pyproject.toml` creation
- Whether Workers get a separate `workers/python/pyproject.toml` when A7 starts
- Python 3.12 vs 3.13 in CI images (floor is 3.11)

---

## Confidence

**MEDIUM**

PEP 621 metadata and venv isolation are high-confidence. uv as *first tool* is medium: the lockfile is tool-specific, and pip-tools remains a fully viable replacement. Confidence is not HIGH because the files do not exist yet and host/WSL native-wheel behavior will only be proven when A3 dependencies are actually locked.

---

## Self-audit (Decision 019)

| Forbidden reading | Status |
|---|---|
| Dependency manager became architecture | **No**; uv is a replaceable installer |
| pyproject confused with resolver | **No**; table of distinctions |
| Windows/WSL ignored | **No**; primary Stage 2 reason for one lock |
| Reproducibility ignored | **No**; committed lock + frozen CI |
| Tool replacement would break Domain/Core | **No** |
| Popularity/speed as the reason | **No**; speed noted as non-justification |
| Global Python assumed | **No** |
| Worker/Kali deps mixed into Core metadata | **Forbidden** |
| Files created this turn | **No** |

**FINAL STATUS: PASS**

---

# Decision 020 — PostgreSQL Data Access / Transaction / Migration Strategy

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Depends on:** Decision 001 (Python control plane); Decision 002 (relational SoR; JSON not authority); Decision 003 (PostgreSQL product; ORM/migrations were deferred); Decision 010 (local PostgreSQL; Docker not required); Decision 016 (Python classes ≠ contracts); Decision 019 (control-plane deps; Data libs may be installed, not imported by Core)

This decision selects **how** the control plane talks to the locked PostgreSQL SoR, **who owns transactions**, and **how schema history is versioned**.

It does **not** select:

- the A3 table catalog or SQL DDL
- target wildcard / CIDR / graph / vector / model-routing / remote-Worker schemas
- Docker / docker-compose / a testcontainer product
- SQLite as a second SoR
- an async API framework
- connection-pooler product (PgBouncer, etc.)

No ORM models, migrations, repositories, or connection code are created in this turn.

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS**

| Piece | v1 |
|---|---|
| **Data access** | **SQLAlchemy 2.x Core** (Table / MetaData / SQL expressions / Engine) **inside the Data adapter only** |
| **DBAPI driver** | **psycopg 3** (`psycopg`) under SQLAlchemy, Data adapter only |
| **ORM** | **Not selected** as the mapping architecture. No declarative Domain. No SQLModel |
| **Transaction / UoW** | **Synchronous** SQLAlchemy `Engine` + explicit transaction/connection context **owned by the Data adapter**. Persistence-port methods define atomic use-cases |
| **Migrations** | **Alembic** with **reviewed, versioned migration scripts**. Autogenerate may assist; it is not production schema authority |
| **Tests against SoR** | **Real PostgreSQL**. SQLite is not a semantic stand-in. Docker is not selected to host it |

**Dependency direction (required):**

```
Core / Research domain types
        ↓
Persistence ports / repository interfaces  (no SQL)
        ↓
Data adapter
        ↓
SQLAlchemy Core + psycopg + Alembic
        ↓
PostgreSQL
```

Never: Core → SQLAlchemy; Research → psycopg; Worker → PostgreSQL.

---

## Why this decision exists

Decision 003 locked PostgreSQL and explicitly left ORM, migrations, pooling, and drivers open. A3 cannot start without those implementation boundaries, or SQLAlchemy types will leak into Core the way Decision 001 warned vendor SDKs would.

The SoR must later commit **Approval + Finding + AuditEvent** together, and **budget reservation/decrement + execution-start state** together (Decision 003). That is a **transaction-ownership** problem, not an ORM-relationship problem.

Workers do not write the SoR (PROJECT_STRUCTURE / Decision 005). Async DB access is not implied by out-of-process Workers.

---

## Stage 1 — Mandatory gates

| Gate | Meaning |
|---|---|
| Boundary | Core/Research import no SQLAlchemy, psycopg, Alembic, PostgreSQL types, or table names |
| ORM ≠ Domain | Persistence objects are not Candidate/Finding/Evidence |
| Transactions | Multi-record authoritative writes can commit or roll back together |
| Migrations | Reviewable, versioned history; not `create_all` as production |
| Integrity | FKs, uniqueness, checks, append-only history *can* be expressed (A3 designs the DDL) |
| Sync justified | Async not required by v1 topology |
| PostgreSQL tests | Integration tests hit PostgreSQL, not SQLite-as-Postgres |
| No schema dump | This decision does not invent the domain catalog |

### Data-access candidates

| Candidate | Stage 1 | Note |
|---|---|---|
| **1. Direct psycopg SQL only** | **Pass** | Explicit. Strongest simple alternative. Reimplements pool/composition Alembic still has to sit on something |
| **2. SQLAlchemy Core** | **Pass** | Selected. Mapping stays explicit; Engine/transaction/Alembic fit |
| **3. SQLAlchemy ORM (declarative as Domain)** | **Fail** | ORM entity becomes Domain; lifecycle/relationship confusion |
| **4. SQLAlchemy hybrid Core/ORM** | **Pass weakly** | Allowed later *inside Data* if measured. v1 hybrid invites ORM creep |
| **5. SQLModel / similar** | **Fail** | Table + validation model as one object; Domain gravity is worse |
| **6. Custom repository over direct SQL** | **Pass as a pattern** | Selected **with** Core-level SQLAlchemy, not instead of a driver. Repositories remain the port shape |
| **7. Django ORM / Tortoise / Piccolo** | **Fail** | Framework-shaped Domain; API/framework not chosen (Decision 011) |

### Migration candidates

| Candidate | Stage 1 | Note |
|---|---|---|
| **1. Hand-written SQL only** | **Pass** | Viable. Weaker engine/metadata coupling unless a runner is invented |
| **2. Alembic** | **Pass** | Selected. Version table + reviewed scripts; can emit raw SQL |
| **3. ORM `create_all` as architecture** | **Fail** | Not reviewable history; not production-safe |
| **4. Atlas / Flyway / sqitch as v1 product** | **Pass as later ops** | Extra product before a Python control plane exists. Not chosen now |

---

## Data access — why SQLAlchemy Core (not “industry standard”)

Concrete fit to *this* SoR:

- **Explicit mappings.** `Table` objects live in the Data adapter. Domain dataclasses (and A2 Core types) stay ignorant of columns. Mapping is functions at the adapter edge, not inheritance from `DeclarativeBase`.
- **PostgreSQL support.** SQLAlchemy PostgreSQL dialect can use JSONB **only** for Decision 002 secondary attributes, in Data, not as Core types.
- **Transaction control.** `conn.begin()` / `trans.commit()` / `rollback()` is the UoW primitive we need for Approval+Finding+AuditEvent without an identity map.
- **Constraints.** DDL for FK/unique/check is expressed in migrations (and optionally mirrored on `Table` for autogenerate-as-diff). Integrity lives in PostgreSQL, not in ORM validators.
- **Migration integration.** Alembic is built to run against a SQLAlchemy `MetaData`/`Engine` while still allowing hand-written `op.execute(...)`.
- **Testability.** Ports can be faked in Core/Research unit tests; integration tests use a real Engine against PostgreSQL.
- **Separation from Domain.** Core stays as A2: stdlib, no IO. Research stays proposals. Neither receives a `Session`.
- **Python ecosystem.** Driver is psycopg 3, a PostgreSQL client, not a second SoR.
- **Operational complexity.** One library family (SQLAlchemy + Alembic + psycopg) in the Data extra/venv, not a Django stack and not a silent SQLite dialect switch.

**Why not ORM:** SQLAlchemy instance state, lazy loads, relationship cascades, and `session.merge` are the fastest way to confuse Candidate lifecycle with ORM lifecycle. Decision 016 already forbids Python classes as contract truth; declarative models as Domain would repeat that failure inside Data.

**Why not SQLModel:** it *intends* one class to be both schema and API/domain. That is the anti-goal.

**Why not direct psycopg only:** it can do transactions and constraints. It does not lose. It loses Stage 2 on composable queries, pooling, and Alembic’s native `Engine` story. Direct SQL remains allowed **inside** the adapter (Alembic `op.execute`, psycopg-specific features) when Core SQLAlchemy expressions are a poor fit. That is an adapter detail, not a second architecture.

---

## Transaction model

**Core does not manage transactions.** Core remains a pure evaluator (`ExecutionDecision`, Approval eligibility). It does not begin, commit, or roll back PostgreSQL work.

**Research does not open SoR transactions** as domain logic. It may call persistence **ports**.

**Workers never write PostgreSQL.**

**Ownership:** the Data adapter (invoked from Interface/application use-cases, not from Workers) owns `Engine` connections and transaction scope.

**Unit of Work (conceptual, not a required class name):**

- One persistence use-case ⇒ one transaction unless a documented exception exists.
- Failure ⇒ rollback. Partial Approval without Finding is a defect, not a retry-shaped success.
- Future atomic examples (not implemented now):
  - Approval + Finding + AuditEvent
  - Budget reservation/decrement + execution-start authorization state
- Transition A and Transition B remain **separate** transactions/workflows (Decision 003), not separate databases.

Commit ownership is **the adapter method that started the transaction**, not Core, not the ORM session identity map, not the Worker.

A later `UnitOfWork` port is allowed if it stays in Data contracts and is implemented only in the PostgreSQL adapter.

---

## Sync vs async

**v1 Data layer is synchronous.**

Workers being concurrent processes does not require async DB drivers. They do not touch the SoR. Phase A is a local Control Plane with modest concurrency. The API framework is **deferred** (Decision 011) and must not drive this choice.

Async SQLAlchemy + asyncpg/psycopg-async adds a second programming model before any measured control-plane wait-time problem. Revisit if a later Interface is async **and** blocking Engine use is measured as a bottleneck. Do not invent that bottleneck now.

---

## Migrations

- Alembic versioned scripts are **infrastructure history**, not Domain truth (DOMAIN_MODEL.md).
- Production and SoR integration tests apply migrations. **`MetaData.create_all()` is not the migration architecture** and is not the PostgreSQL test setup for semantics we claim to test.
- Autogenerate is an optional assistant. Every migration is reviewed. Destructive changes are explicit.
- Forward evolution is required. Down-migrations are not mandated as architecture; if present, they are operational convenience.
- CI can run Alembic against a real PostgreSQL service (local, WSL, or CI service). **Docker is not chosen** to make that true (Decision 010).

A3 still designs the first schema. This decision only locks the *mechanism*.

---

## PostgreSQL testing

SQLite may exist later for **non-SoR** unit tests (pure functions). It **must not** validate:

- PostgreSQL constraints
- transaction / isolation semantics
- locking
- JSONB
- concurrent budget operations
- Alembic behavior on PostgreSQL

Decision 003 already rejected SQLite as the shared SoR. This decision rejects it as a **silent dialect substitute**.

How PostgreSQL is installed for tests (native Windows, WSL, CI service, later container) remains a deployment/dev-ops choice, not this strategy.

---

## Integrity (A3 still designs enforcement)

The stack must be *able* to enforce, later:

- foreign keys, uniqueness, check constraints
- append-only / no silent rewrite of Evidence, AuditEvent, recorded Approval (correction = new row / superseding history)
- lifecycle columns as first-class fields, not JSON bags (Decision 002)
- provenance columns
- transaction boundaries above

Exact triggers, `REVOKE UPDATE`, or application guards are A3 design, not this decision.

---

## What this decision does not design

No tables for: wildcard grammar, CIDR matching, causal graphs, vectors, chain engine, model routing, remote Worker topology. Those would reopen Decisions 009, 018, 005, 008.

---

## Constraints

1. **Core must not import** SQLAlchemy, psycopg, Alembic, or know table names / PostgreSQL types / transactions.
2. **Research must not import** PostgreSQL drivers or treat mapped objects as domain truth.
3. **Workers must not write the SoR.**
4. **ORM entity ≠ Domain entity.** ORM lifecycle ≠ Candidate lifecycle. ORM relationship ≠ domain semantics. v1 does not use ORM as the mapping architecture.
5. **SQLAlchemy `Table`/`MetaData` live in the Data adapter**, not in `zest.core`.
6. **Transactions owned by Data adapter use-cases**, not Core.
7. **Sync Engine in v1.** Async is a revisit, not a default.
8. **Alembic scripts are required** for SoR evolution. `create_all` is not production.
9. **Integration tests for SoR semantics use PostgreSQL**, not SQLite-as-Postgres.
10. **Docker is not selected** by this decision.
11. **JSONB stays secondary** (Decision 002), Data-only.
12. **Python SQLAlchemy types are not contracts** (Decision 016).
13. **No A3 schema in this decision.**
14. **Decision 019:** these libraries are control-plane/Data dependencies, not Core package architecture.

---

## Revisit triggers

- SQLAlchemy Core mapping overhead is measured as worse than explicit psycopg (switch adapter internals; keep ports)
- Proven throughput/bulk ingest problem (COPY, partitioning) — still Data-only
- Control-plane async Interface exists **and** blocking DB is measured
- Alembic operational pain (then consider sqitch/Flyway **for SQL history**, not a Domain change)
- A need for ORM **inside Data only**, with imperative mapping, after Core leakage tests still pass
- PostgreSQL features that the Core SQLAlchemy dialect handles poorly (use `op.execute` / psycopg in the adapter)

Do not invent performance problems. Revisit does **not** mean: Core imports SQLAlchemy, SQLite becomes the SoR test, or `create_all` becomes production.

---

## Open questions

- Persistence port names and whether a generic `UnitOfWork` type is worth it in A3
- Alembic script location (`src/zest/data/...` vs repo-level `migrations/`)
- Isolation level for budget decrement (A3)
- How append-only is enforced (REVOKE vs trigger vs application + tests)

---

## Confidence

**MEDIUM**

SQLAlchemy Core + Alembic + psycopg + sync Engine matches the SoR, the A2 purity of Core, and Phase A topology. Confidence is not HIGH because boundary leakage is a people/process risk, Alembic autogenerate will tempt schema-as-ORM, and the first schema does not exist yet. Constraints and import tests are the mitigation.

---

## Self-audit (Decision 020)

| Forbidden reading | Status |
|---|---|
| ORM model became Domain model | **No**; ORM not selected as mapping architecture |
| Core imports persistence framework | **Forbidden** |
| Research imports PostgreSQL | **Forbidden** |
| Worker can write SoR | **No** |
| Async selected without need | **No**; sync first |
| SQLite silently substituted | **Forbidden** for SoR semantics |
| Migrations = auto-create | **No**; Alembic reviewed scripts |
| Transaction ownership unclear | **No**; Data adapter / persistence use-case |
| PostgreSQL leaked across boundary | **Forbidden**; dialect stays in adapter |
| Whole domain schema designed | **No** |
| Artifact/vector/graph mixed in | **No** |
| Docker selected for tests | **No** |
| Files/models created this turn | **No** |

**FINAL STATUS: PASS**

---

# Decision 021 — Local Worker Runtime Protocol / Contract Validation

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Depends on:** Decision 005 (mixed topology; local IPC/subprocess first; no mandatory broker); Decision 014 (side-effect Workers out-of-process); Decision 016 (JSON Schema Draft 2020-12 canonical; Python classes are not contract truth); Decision 013 (secret values not in Worker env/logs)

This decision selects the **first local Worker transport**, the **one-shot process lifecycle**, and **runtime JSON Schema validation** for WorkerRequest / WorkerResult.

It does **not** select:

- HTTP, gRPC, or any remote RPC product
- a broker / queue
- a persistent Worker pool
- Kali/WSL as architecture (still the first *tool* environment, not this protocol)
- a security scanner, crawler, or Strix
- Transition A / Observation ingestion

**Canonical JSON Schema ≠ transport.** WorkerRequest / WorkerResult semantics stay in `contracts/`. stdin/stdout is a **first local adapter**.

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS**

| Piece | v1 |
|---|---|
| **Canonical semantics** | Existing `contracts/v1` JSON Schema Draft 2020-12 |
| **First local transport** | JSON over **stdin/stdout**, **stderr** diagnostics only |
| **Process lifecycle** | **One-request-per-process** (spawn → one request → one result → exit) |
| **Runtime validation** | **jsonschema** Draft 2020-12, local `$id` URN registry, **no network fetch** |
| **Invocation vs result** | Control Plane `WorkerInvocationOutcome` is **not** `WorkerResult` |

**PRODUCTS:** jsonschema is a replaceable validator library, not contract truth. One-shot stdin/stdout is the **first implementation**, not permanent topology.

---

## Why this decision exists

Decision 005 locked mixed topology and local IPC/subprocess without choosing a wire protocol. A1 lint is structural only. A4 must actually spawn a process and round-trip canonical messages.

HTTP-localhost, sockets, brokers, and RPC frameworks would make **transport** look like architecture and add session/port/product surface before a remote Worker exists. A persistent stdin/stdout daemon would share memory across requests and complicate crash/timeout isolation.

One-shot JSON stdio matches Phase A: deterministic lifecycle, easy timeout, crash containment, no hidden session, replaceable later by Go/Rust Workers using the **same schemas**.

A1 cannot validate instances. Hand-rolling Draft 2020-12 would be a false validator. jsonschema is justified because it evaluates the **canonical files**, resolves `$ref` locally, and can be swapped without rewriting `contracts/`.

---

## Stage 1 — Mandatory gates

| Gate | Meaning |
|---|---|
| Contract remains schema files | No Python models as WorkerRequest/WorkerResult truth |
| Transport ≠ semantics | Remote may change transport later |
| Out-of-process | Side-effect Worker is a child process (Decision 014) |
| Crash containment | Worker crash does not kill Control Plane and does not mint a fake WorkerResult |
| No broker/HTTP required | Decision 005 |
| Local schema resolution | URN `$id` only; no network `$ref` / `$schema` fetch |
| Invocation ≠ WorkerResult | Transport/protocol failure is not `WorkerResult.status` |
| Secrets | Child env is constructed; DB/model secrets not forwarded (Decision 013) |

### Transport candidates

| Candidate | Stage 1 | Note |
|---|---|---|
| **1. HTTP localhost service** | **Fail as first impl** | Server, port, extra protocol. Transport gravity |
| **2. Local socket** | **Fail as first impl** | Session framing without a remote Worker need |
| **3. Persistent stdin/stdout process** | **Pass later** | Pooling/session Workers later. Cross-request memory now |
| **4. One-request-per-process stdin/stdout** | **Pass** | Selected for Phase A |
| **5. Broker** | **Fail** | Decision 005: no mandatory broker |
| **6. RPC framework** | **Fail as first impl** | Product lock-in; transport becomes architecture |

### Validator candidates

| Candidate | Stage 1 | Note |
|---|---|---|
| **Keep A1 lint only** | **Fail runtime** | Does not evaluate instances |
| **Hand-written Draft 2020-12** | **Fail** | Fake validator |
| **jsonschema + local Registry** | **Pass** | Selected. Replaceable |
| **Network-fetching `$ref`** | **Fail** | Contracts must not leave the repo |

---

## Process lifecycle (first implementation)

```
Control Plane
  → spawn Worker (argv, no shell)
  → write exactly one JSON WorkerRequest to stdin
  → Worker performs exactly one capability invocation
  → read exactly one JSON WorkerResult from stdout
  → process exits
```

stderr is bounded diagnostics, not protocol, not truth.

This lifecycle is **not** the standing topology. Persistent Workers, pools, streaming, and remote transports are revisit items. Domain/Core contracts do not change when they appear.

---

## Invocation vs WorkerResult

Control Plane records a **WorkerInvocationOutcome**.

Conceptual `invocation_status` values:

- `COMPLETED` — valid canonical WorkerResult received; expected process completion
- `START_FAILED` — process could not start
- `TIMED_OUT` — timeout before a valid WorkerResult
- `CANCELLED` — local process terminated by caller (first implementation: kill the child)
- `PROCESS_FAILED` — crash / unexpected exit **without** a valid WorkerResult
- `PROTOCOL_ERROR` — invalid JSON, extra stdout, oversized stdout, conflicting exit+result
- `CONTRACT_INVALID` — schema-invalid request/result, unsupported version, correlation mismatch

A process crash is **not** `WorkerResult.status = EXECUTION_FAILED`. That status exists only on a **valid** WorkerResult document.

Timeout is **not** negative Evidence. A completed diagnostic WorkerResult is **not** Observation or Evidence.

---

## Protocol rules (stdio adapter)

- stdin: exactly one JSON WorkerRequest
- stdout: exactly one JSON WorkerResult; no logs, banners, or debug text
- stderr: bounded diagnostics; not secrets; not truth
- v1: no NDJSON, no multi-message stream, no network protocol
- `shell=False`; argv execution; request is stdin data, not command-line syntax
- Worker executable/module path is configuration, not model output

---

## Runtime validation

- Load canonical files from `contracts/v1/`
- Index by `$id` URN
- Resolve `$ref` only to known local URNs
- Unknown URN / network retrieve → fail closed
- Validate WorkerRequest **before** spawn
- Validate WorkerResult **after** receive
- `contract_version` other than `v1` → fail closed (schema `const` + explicit check)
- Correlation (`correlation_id`, `research_run_id`, `experiment_id`, `request_id`) must match the dispatched request; **do not rewrite** Worker fields
- `scripts/check_contracts.py` remains structural lint, not this validator

---

## Constraints

1. **jsonschema does not replace `contracts/`.**
2. **One-shot stdio is first local transport, not architecture.**
3. **Core and Research must not import subprocess** or the concrete local adapter.
4. **Workers must not import `zest.core` / Data / PostgreSQL.**
5. **Workers must not receive DB/model secrets** in the child environment.
6. **Workers must not write the SoR.** A4 does not persist WorkerResult.
7. **Do not fabricate WorkerResult** on crash, timeout, or protocol failure.
8. **stdout size is bounded**; overflow is PROTOCOL_ERROR, not a truncated WorkerResult.
9. **stderr size is bounded**; overflow may truncate diagnostics and mark them truncated.
10. **Valid result + unexpected non-zero exit → fail closed** (do not accept the result).
11. **PID is not Worker identity.** Configured opaque `worker_id` is.
12. **Same-machine ≠ authority.** Worker identity does not authorize.
13. **diagnostic.echo is not a security capability.**
14. **No HTTP/broker/RPC in this slice.**
15. **Distributed cancellation is deferred**; local cancel is process termination.
16. **A3 PostgreSQL integration tests remaining skipped is PENDING, not PASS.**

---

## Revisit triggers

- Measured process-startup overhead / high concurrency (consider persistent Workers or a pool **behind the same contract**)
- Persistent browser/session Workers
- Remote authenticated Workers (new transport adapter; same schemas)
- Streaming / NDJSON / artifact-byte transfer needs
- WSL transport friction
- jsonschema dialect/registry pain (replace the library, not the schemas)

Revisit does **not** mean: WorkerResult is Observation, transport is Domain, or crash equals EXECUTION_FAILED.

---

## Open questions

- Exact pool/persistent lifecycle when revisit fires
- WSL launch wrapper (still not architecture)
- Whether Worker gets its own packaging/venv (deferred until required)

---

## Confidence

**MEDIUM**

One-shot JSON stdio is the only candidate that is out-of-process, broker-free, and session-free for Phase A. jsonschema is the justified runtime evaluator of canonical files. Confidence is not HIGH because one-shot is explicitly temporary under load, and host/WSL spawn behavior will only be proven when a tool Worker exists.

---

## Self-audit (Decision 021)

| Forbidden reading | Status |
|---|---|
| Python models are Worker contract truth | **No** |
| Transport is architecture | **No**; first adapter |
| HTTP/broker/RPC selected | **No** |
| Persistent process required now | **No** |
| Crash fabricates WorkerResult | **Forbidden** |
| Validator fetches network schemas | **Forbidden** |
| One-shot described as permanent | **No** |
| jsonschema replaces contracts/ | **No** |

**FINAL STATUS: PASS**

---

# Decision 022 — Application / Use-Case Coordination Layer

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Depends on:** PROJECT_STRUCTURE layer boundaries; Decision 004 (orchestration product deferred); Decision 014 (Workers execute); Decision 020 (Data UoW)

This decision names the missing owner of:

```
Core decision → WorkerPort → WorkerInvocationOutcome → Data UoW → Transition A → persistence
```

It does **not** select an orchestration engine, API framework, or make Application into authority.

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS**

An explicit `src/zest/application/` layer owns **use-case coordination**.

| Alternative | Stage 1 | Note |
|---|---|---|
| **1. Interface** | **Fail** | Interface must not own business logic or transactions (PROJECT_STRUCTURE) |
| **2. Research** | **Fail** | Research proposes; it must not execute or persist WorkerResults |
| **3. Core** | **Fail** | Core is authority without I/O; it must not import Worker adapters or UoW |
| **4. Platform** | **Fail** | Platform is infrastructure; it must not own Observation admission |
| **5. Explicit Application layer** | **Pass** | Selected. Coordinates ports; does not become authority |

**Ownership:** sequencing, port coordination, transaction intent, mapping a successful infrastructure outcome into the next domain step, fail-closed workflow behavior.

**Not owned:** authorization/scope/budget semantics, Evidence truth, Finding acceptance, hypothesis semantics, PostgreSQL, subprocess.

---

## Dependency direction

```
Interface
  → Application
    → Core / Research (public APIs/types)
    → Data ports / UnitOfWork
    → Platform ports (WorkerPort, contract validator)
```

Concrete adapters (PostgreSQL, LocalProcessWorkerAdapter, Integrations) are injected from below.

Rules:

- Application coordinates authority; it **asks Core**. It cannot convert DENY or REQUIRE_HUMAN_REVIEW into ALLOW.
- Core must not import Application.
- Research must not import Application.
- Platform must not import Application.
- Workers must not import Application.
- No cycle.

---

## Confidence

**HIGH** for the layer existing. **MEDIUM** for its eventual width (more use cases will appear). The first use case is intentionally narrow: ingest a completed Worker invocation.

---

## Why

No existing layer can own Worker invocation → Transition A → persistence without violating invariants. Interface owning UoW would bury business flow in CLI/API. Research owning it would execute. Core owning it would take I/O. Platform owning it would admit domain Observations.

---

## Constraints

1. Application is not a new authority.
2. Application must not import `zest.data.postgres`, SQLAlchemy, psycopg, Alembic, subprocess, or `LocalProcessWorkerAdapter`.
3. Application must not execute Workers.
4. Application must not create Evidence, Candidate, or Finding.
5. Orchestration **product** remains deferred (Decision 004).

## Revisit triggers

- Distributed saga/outbox needs beyond one UoW
- Multiple competing Application packages
- Interface-only scripts that must not depend on Application (keep fakes at composition root)

**FINAL STATUS: PASS**

---

# Decision 023 — Transition A Admission / Deterministic Normalization

**Status:** ACCEPT WITH CONSTRAINTS  
**Date:** 2026-08-16  
**Depends on:** DOMAIN_MODEL Transition A; Decision 017 (false-positive discipline); Decision 021 (invocation ≠ WorkerResult); Decision 022 (Application coordinates)

Locked semantic:

```
valid COMPLETED invocation
  → canonical WorkerResult
  → schema/integrity checks
  → deterministic normalizer (trusted capability/action)
  → ObservationDraft(s)
  → one Data transaction: WorkerResult + Observation(s) + AuditEvent(s)
```

---

## Decision

**STRATEGY: ACCEPT WITH CONSTRAINTS**

### Admission gate

Only `WorkerInvocationOutcome.invocation_status = COMPLETED` with a valid canonical WorkerResult may enter ingestion.

`START_FAILED`, `TIMED_OUT`, `PROCESS_FAILED`, `PROTOCOL_ERROR`, `CONTRACT_INVALID` do **not** mint a WorkerResult row. They may be logged; they are not Observations of target behavior.

A COMPLETED invocation may still have `WorkerResult.status` other than SUCCEEDED. Those statuses are durably recorded as WorkerResult when a normalizer exists; they do not automatically become Observations.

### Normalizer model

`NormalizerRegistry` keyed by trusted `(capability, action)` from the **dispatched WorkerRequest**, plus `normalizer_version`. Worker `raw_result` must not select the normalizer.

Normalizers are pure/deterministic: no DB, network, subprocess, model, or secret resolution.

Output: zero or more `ObservationDraft` values. Not persisted Observation. No severity, confidence, vulnerability type, impact, Evidence, or Finding fields.

First normalizer: `diagnostic.echo` / `echo` / `diagnostic.echo.v1`. Proves Transition A. Not a scanner.

### Provenance (no WorkerRequest table, no ExecutionAttempt table)

A separate WorkerRequest SoR table and a separate ExecutionAttempt table were **rejected for this slice**.

Reason: this use case only admits COMPLETED invocations that already have a canonical WorkerResult. Infrastructure failures are not WorkerResult rows (Decision 021). A WorkerRequest table would duplicate the wire envelope without a query need. An ExecutionAttempt table would split invocation from result before any consumer exists.

Minimum provenance is first-class columns on `worker_result`:

- `request_id` (idempotency identity)
- `correlation_id`, `research_run_id`, `parent_request_id`
- `worker_capability`, `action`
- `authorization_decision_reference` (reference, not the decision)
- `budget_id`, `side_effect_level`

Experiment remains the authoritative parent. Correlation is not JSON-only.

`worker_result_id` is derived as `wr:{request_id}` so replay is stable without a second identity generator.

### Idempotency

Identity is **`request_id`** (authorized request identity), unique in PostgreSQL.

Not payload hash. Two independent requests with equal diagnostic payloads produce two Observations.

Replay of the same `request_id` returns `ALREADY_INGESTED` and does not insert a second Observation. This is execution/idempotency duplicate, not duplicate vulnerability.

Retry after crash-before-commit: insert proceeds. Retry after commit-ack loss: unique `request_id` → `ALREADY_INGESTED`.

Observation uniqueness `(worker_result_id, observation_kind, normalization_version)` is a second belt.

### Transaction model

WorkerResult + Observation(s) + AuditEvent(s) (`WORKER_RESULT_INGESTED`, `OBSERVATION_ADMITTED`) are one Unit of Work. Explicit commit; otherwise rollback.

Never: Observation without source WorkerResult. Never: WorkerResult marked ingested with a half-written Observation/Audit chain.

Evidence admission is **not** in this transaction.

`observed_at` comes from WorkerResult `completed_at` (else `started_at`). `created_at` is persistence admission time.

`normalization_version` is persisted. Do not silently reinterpret old rows when normalizer code changes. No replay engine in this slice.

---

## Confidence

**HIGH** for admission gate, invocation≠result, and request_id idempotency. **MEDIUM** for envelope-on-WorkerResult vs a later ExecutionAttempt table if incomplete invocations must be durable.

---

## Why

Decision 021 made transport failure distinct from WorkerResult. Transition A must not collapse that distinction, must not let Workers declare Observations, and must not use an LLM to “understand” a result.

---

## Constraints

1. Transition A is non-LLM and non-judgmental.
2. Observation ≠ Evidence ≠ Finding.
3. Do not fabricate WorkerResult for infrastructure failure.
4. Do not globally deduplicate equal payloads.
5. Do not edit `a3_001_persistence_spine.py`; schema change is a new Alembic revision.
6. Do not mix Transition B / Evidence into this transaction.
7. A3 PostgreSQL integration remaining skipped is **PENDING**, not PASS.

## Revisit triggers

- Need to persist incomplete invocations (START_FAILED / TIMED_OUT) as first-class operational records
- Artifact byte admission alongside Observation
- Additional capability normalizers
- Reprocessing WorkerResults under a new `normalization_version`

**FINAL STATUS: PASS**

---

# Decision 024 — Durable Execution Attempt / Dispatch Semantics

Status: **accepted with constraints** (A7-lite)

Date: 2026-08-16

Does not rewrite Decisions 001–023. Decision 023 rejected an ExecutionAttempt table for Transition A ingestion; this decision accepts one for **dispatch coordination** of incomplete invocations.

## Candidates evaluated

| Option | Verdict |
|---|---|
| A. AuditEvent only | Rejected as operational coordination. AuditEvent is reconstructive history, not a queue, workflow table, retry state, or lease table. |
| B. Experiment state only | Rejected as too coarse. One Experiment can have at most one in-flight intended invocation in A7-lite, but Experiment state cannot represent “this `request_id` was authorized and we do not know whether the Worker ran.” |
| C. First-class `ExecutionAttempt` | **Accepted.** One intended Worker invocation for one Experiment, with durable identity and lifecycle. |
| D. Other minimal approach | No smaller record preserves request identity, authorization provenance, and unknown-outcome fail-closed retry. |

## Strategy

Accept a first-class **ExecutionAttempt** as Control Plane coordination state for one intended Worker invocation.

Flow:

1. Application loads persisted Hypothesis/Experiment context.
2. Core `evaluate_execution` (DEFAULT DENY; no diagnostic shortcut).
3. Application persists an append-only `AuditEvent` (`EXECUTION_DECISION`) as immutable decision provenance.
4. DENY / REQUIRE_HUMAN_REVIEW: no ExecutionAttempt, no Worker.
5. ALLOW: persist ExecutionAttempt `AUTHORIZED`, commit TX1.
6. Mark `DISPATCHING`, commit TX1b. Then invoke `WorkerPort` **outside** any database transaction.
7. TX2 records attempt/Experiment execution outcome.
8. COMPLETED + valid WorkerResult → existing Transition A (`IngestCompletedWorkerInvocation`).

Zest does **not** claim exactly-once side effects.

It uses: durable intent, unique request identity, risk-aware retry (not implemented in A7-lite), `UNKNOWN_OUTCOME` when necessary, and future reconciliation.

## Durable state model

`ExecutionAttempt` is not Evidence, not a WorkerResult, not Hypothesis belief, and not AuditEvent.

Minimum fields:

- `attempt_id` (`ea:{request_id}`)
- `request_id` (unique)
- `experiment_id`, `research_run_id`, `correlation_id`
- `worker_capability`, `action`, `target_reference`
- `budget_id`, `side_effect_level`
- `authorization_decision_reference`
- `state`
- `created_at`
- optional: `authorized_at`, `dispatch_started_at`, `completed_at`

Opaque string IDs. No PostgreSQL UUID type. No ULID dependency. JSON is not authority state. No secret values.

Lifecycle (not a workflow engine):

`AUTHORIZED` → `DISPATCHING` → `COMPLETED` | `FAILED` | `TIMED_OUT` | `CANCELLED` | `UNKNOWN_OUTCOME`

Experiment execution states remain locked: `PLANNED` → `AUTHORIZATION_CHECK` → `READY` → `RUNNING` → terminal (`EXECUTION_SUCCEEDED`, `EXECUTION_FAILED`, `BLOCKED`, `CANCELLED`, `BUDGET_EXHAUSTED`).

Execution success means the experiment **ran**. It does not mean the Hypothesis is true. Do not invent Hypothesis VALIDATED/REJECTED from these states.

## Request identity ownership

`request_id` is generated by the Control Plane / Application layer (`uuid.uuid4()` as an opaque string).

Worker does not choose it. Model does not choose it. Interface should not normally choose it.

`request_id` must be globally unique for the Zest instance. Canonical contracts still see an opaque string.

A6-lite ingestion idempotency remains `request_id` unique on `worker_result`. ExecutionAttempt uniqueness is the dispatch-side counterpart.

## Authorization-decision provenance

Core evaluates → Application inserts append-only `AuditEvent` for that exact `ExecutionDecision` → `audit_event_id` is `authorization_decision_reference` → an allowed ExecutionAttempt references it.

AuditEvent is **immutable decision provenance**, not operational coordination state. No separate AuthorizationDecision table.

Payload (no secrets): ALLOW / DENY / REQUIRE_HUMAN_REVIEW, reason code, authorization source reference, matched scope rule ids, budget id, side-effect level, approval id, actor/control-plane provenance, correlation, whether dispatched.

DENY and REQUIRE_HUMAN_REVIEW still persist this provenance and must not create an authorized ExecutionAttempt.

## Retry semantics

A7-lite does **not** implement a general automatic retry engine.

`automatic_retry_allowed` is fail-closed (`False`).

Future retry policy must consider: `side_effect_level`, capability semantics, attempt state, known vs unknown external outcome, budget, and whether authorization must be re-evaluated by Core. Retry must not bypass a new Core evaluation when authorization must be reconsidered.

Level 0 `diagnostic.echo` is side-effect-free, so a later controlled retry from `AUTHORIZED` (Worker not started) can be safe. `DISPATCHING` / `UNKNOWN_OUTCOME` must not be blindly retried.

## Unknown-outcome semantics

“We did not receive a result” ≠ “the action did not happen.”

`UNKNOWN_OUTCOME` is not `FAILED` and must not automatically retry.

Crash windows:

- A. Before TX1 commit: no durable attempt. Safe to retry planning/evaluation.
- B. After TX1 `AUTHORIZED`, before spawn: durable intent, Worker not started. `execute()` does not auto-dispatch; explicit `dispatch()` is the in-process continuation.
- C. After TX1b `DISPATCHING` / Worker spawn, before result handling: treat as `UNKNOWN_OUTCOME`. Restart of `execute()` fail-closes and does not invoke again.

A Worker timeout may set Experiment `EXECUTION_FAILED` (infrastructure could not complete execution) without rejecting the Hypothesis. `UNKNOWN_OUTCOME` must **not** be classified as Experiment `EXECUTION_FAILED`.

## Confidence

**HIGH** that AuditEvent must not become a queue and that incomplete dispatch needs a first-class attempt record.

**HIGH** that unknown external outcome must fail closed for side-effectful retry.

**MEDIUM** for two-phase `AUTHORIZED` then `DISPATCHING` vs a single committed `DISPATCHING` row; A7-lite keeps both states so crash window B is distinguishable.

**MEDIUM** that a persistent budget consumption ledger is still deferred; Level 0 diagnostic may proceed without claiming final ledger semantics.

## Why

A6-lite solved ingestion replay with `request_id`. It did not solve: Core ALLOW + dispatch intent recorded + crash before spawn, or Worker side effect + crash before the Control Plane learned the result + blind re-dispatch.

Those are not ingestion duplicates. They are incomplete execution coordination.

## Constraints

1. Do not turn AuditEvent into a queue, workflow, retry, or lease table.
2. Do not create an ExecutionAttempt unless Core ALLOWED execution.
3. Do not invoke Worker before durable intent is committed.
4. Do not hold a PostgreSQL transaction open while the Worker process runs.
5. Do not claim exactly-once external side effects.
6. Do not classify `UNKNOWN_OUTCOME` as `FAILED` or auto-retry it.
7. Do not let Worker, model, or Interface choose `request_id`.
8. Do not update Hypothesis truth from execution success or Observation.
9. Do not invent a scope matcher; consume pre-evaluated `ScopeRuleMatch` inputs.
10. Do not claim the persistent budget consumption ledger is complete.
11. Do not edit Alembic `a3_001` or `a6_001`; schema change is `a7_001_execution_attempt`.
12. Skipped PostgreSQL tests remain **PENDING**, not PASS.

## Revisit triggers

- Automatic retry / reconciliation engine for `AUTHORIZED` vs `UNKNOWN_OUTCOME`
- Persistent request/tool consumption ledger before side-effectful research
- Multiple attempts per Experiment as first-class history
- Scope matcher grammar (still not invented here)
- ModelPort / autonomous hypothesis generation (Decision 008 still untouched)

**FINAL STATUS: PASS**

---

# Decision 025 — Epistemic Research Context / Model Input Boundary

Status: **accepted with constraints** (A7 Research Brain v1)

Date: 2026-08-16

Does not rewrite Decisions 001–024. Decision 008 remains the ModelPort/provider lock. Decision 009 remains: no vector product in v1.

## Candidates evaluated

| Option | Verdict |
|---|---|
| A. Flatten all SoR/read-model text into one prompt blob | Rejected. Collapses Observation, Hypothesis, and untrusted target text into undifferentiated “the system knows…”. |
| B. Generic RAG / embedding retrieval as the context layer | Rejected for v1 (Decision 009). Premature, non-deterministic in tests, and not SoR. |
| C. Typed `ResearchContext` with explicit epistemic classes and a deterministic bounded builder | **Accepted.** |

## Strategy

Research Brain consumes a typed **ResearchContext**, not a concatenated prompt.

Categories stay separate: `authoritative_facts`, `observations`, `deterministic_derivations`, `prior_hypotheses`, `negative_evidence`, `procedural_context`, `unresolved_questions`, `untrusted_external_content`.

The Context Builder is deterministic, has no LLM, and has no embeddings. Selection is bounded. Omission/truncation is explicit metadata. Absence from context is not absence from SoR.

## Epistemic model

Practical classes (not a philosophical ontology):

`AUTHORITATIVE_FACT` | `OBSERVATION` | `DERIVED_FACT` | `HYPOTHESIS` | `NEGATIVE_EVIDENCE` | `PROCEDURAL` | `UNTRUSTED_EXTERNAL` | `UNKNOWN`

Epistemic class ≠ authority. A model cannot relabel `HYPOTHESIS` as fact. Model output cannot create an authoritative Observation.

Every item keeps source references. `supported_by` does not make a claim true.

## Context selection

v1 assembles context for one ResearchRun + one research question, plus related Observations, prior Hypotheses, Experiments, and contextual negative results.

Limits are configuration, not a token-optimization algorithm: max observation items, max prior hypothesis items, max negative-evidence items, max external-content characters.

A context fingerprint hashes canonical identifiers / class / omission metadata — not raw untrusted blobs and not secrets. It is provenance, not semantic deduplication.

## External-content handling

Web/API/document/tool text remains **DATA**.

It is labelled `UNTRUSTED_EXTERNAL` (or an Observation payload marked untrusted-as-instruction), size-bounded, and sourced. It must not appear in the instructions channel.

Prompt labels are not sufficient architecture. Downstream validation still rejects unstructured or authority-claiming model output. This is containment, not a claim that prompt injection is “solved”.

## Provenance

Context items carry source ids. Reasoning invocations carry `context_fingerprint` and ModelPort adapter identity. Fake/test adapters use an explicit fake identity and must not fabricate model version or cost.

## Confidence

**HIGH** that undifferentiated prompt blobs and vector RAG are the wrong v1 boundary.

**MEDIUM** for the exact class list (`AUTHORITATIVE_FACT` added as SoR identity, not in the original seven-name sketch) and for fingerprinting identifiers rather than statements.

## Why

Flattening memory into prose would let a prior Hypothesis poison future reasoning as if it were an Observation, and would let target content issue instructions.

## Constraints

1. Do not flatten ResearchContext into one prompt blob.
2. Do not put an LLM or embeddings inside the Context Builder.
3. Do not dump every record in a ResearchRun.
4. Do not silently truncate; record omission.
5. Do not treat prior Hypothesis as fact.
6. Do not let untrusted content set policy, tools, scope, Evidence, or Finding.
7. Do not select a model provider here (Decision 008).
8. Do not persist a target causal graph in this slice.

## Revisit triggers

- Need for typed actor/resource/state views inside ResearchContext (still not a generic graph bag)
- Measured need for retrieval beyond deterministic bounded selection (Decision 009 still governs vectors)
- Fingerprint collisions or missing statement coverage in audits
- A real ModelPort adapter that requires additional routing-ready request fields

---

# Decision 026 — Hypothesis Generation / Falsification / Admission

Status: **accepted with constraints** (A7 Research Brain v1)

Date: 2026-08-16

Does not rewrite Decisions 001–025. Does not create Evidence, Candidate, Finding, or an autonomous loop.

## Candidates evaluated

| Option | Verdict |
|---|---|
| A. Generator output is a durable Hypothesis | Rejected. Model output is UNTRUSTED STRUCTURED PROPOSAL (Decision 008). |
| B. Multi-agent / multi-vendor framework for independence | Rejected for v1. Premature. |
| C. Generator → `HypothesisProposal` → Falsifier → `HypothesisChallenge` → Research admission | **Accepted.** Independence = separate invocation, role, and structured output. |

## Strategy

```
Generator → HypothesisProposal
Falsifier → HypothesisChallenge
Research admission → persisted Hypothesis or explicit rejection
```

The model proposes. Research domain logic decides whether the proposal becomes a durable Hypothesis. Core still owns execution authorization. Application coordinates persistence. ExperimentPlan is produced after admission and does not dispatch a Worker.

## Generator

Internal `HypothesisProposal`: testable claim, rationale, source references, assumptions, unresolved questions, suggested disconfirming test, suggested capability, optional expected security relevance, advisory `novelty_basis`.

Not included: severity-as-truth, exploitability-as-truth, Finding/Evidence status, authorization, numeric confidence.

Novelty basis is advisory only: `KNOWN_PATTERN_INSTANCE` | `POSSIBLE_COMBINATION` | `TARGET_SPECIFIC_BEHAVIOR` | `UNCLASSIFIED`. `N4_ZERO_DAY` is coerced to `UNCLASSIFIED` and is not a product claim. No novelty score.

## Falsifier

Internal `HypothesisChallenge`: alternative explanations, missing preconditions, contradictory sources, required negative controls, ambiguity, reasons not to test, proposed disconfirming observation.

The Falsifier does not decide Finding truth. Admission requires a produced challenge. One supporting Observation does not bypass it. Generator cannot edit the challenge.

The same ModelPort implementation may serve both roles later. v1 does not require two vendors.

## Admission

Research-domain outcomes:

`ADMITTED` | `REJECTED_UNTESTABLE` | `REJECTED_UNSUPPORTED` | `REJECTED_POLICY_CONFLICT` | `NEEDS_MORE_CONTEXT`

This is proposal admission, not Candidate lifecycle. Rejected proposal ≠ a security hypothesis rejected by testing.

Minimum invariants (not a score engine): non-empty testable claim; source references resolve to assembled context; no authority/Evidence/Finding/scope-bypass claim; challenge produced with at least one alternative explanation; a plausible experiment direction.

Hallucinated source ids → `NEEDS_MORE_CONTEXT`. Empty sources → `REJECTED_UNSUPPORTED`.

## Persistence

Hypothesis identity/claim stays clean. Provenance is a dedicated append-only `ResearchReasoningRecord` (Alembic `a8_001_research_reasoning`): role `GENERATOR`/`FALSIFIER`, adapter identity, optional model id/version (unset for fakes), structured validated output, context fingerprint, correlation id.

Rejected proposals do not create a Hypothesis or reasoning rows. Hypothesis + both reasoning records commit in one Unit of Work.

Semantic/vector hypothesis dedup is **deferred**. Exact claim merge across different actor/state/context is unsafe.

## Confidence

**HIGH** that Generator must not auto-persist Hypothesis and that Falsifier must be a separate invocation.

**MEDIUM** for persisting reasoning only on `ADMITTED` (rejected attempts are not durable) and for coercing N4 rather than hard-failing the proposal parse.

## Why

`LLM → tool → LLM` and “give me 10 vulnerabilities” skip falsification, admission, and Core authorization. Zest requires an untrusted proposal, an independent challenge, and domain admission before anything durable exists.

## Constraints

1. Do not persist a Hypothesis from Generator output alone.
2. Do not let Falsifier create Evidence or Finding.
3. Do not let one model call self-validate.
4. Do not invent numeric confidence or novelty scores.
5. Do not select or install a provider SDK.
6. Do not execute a Worker from this cycle.
7. Do not add 20 nullable columns to Hypothesis.
8. Do not create JSON Schema for these internal Research types unless they become a cross-language contract.
9. Do not start an autonomous `while True` generation loop.
10. Do not edit Alembic `a3_001`, `a6_001`, or `a7_001`.

## Revisit triggers

- First real ModelPort adapter (provider still a later product decision)
- Durable storage of rejected proposals for audit
- Belief update from `ExperimentFeedback`
- Semantic duplicate handling with explicit actor/state/context identity
- Independent verifier model (still not required to be a second vendor)

**FINAL STATUS: PASS**

---

# Decision 027 — Experiment Feedback / Hypothesis Assessment Semantics

Status: **accepted with constraints** (GATE 03)

Date: 2026-08-17

Does not rewrite Decisions 001–026. Does not create Evidence, Candidate, Finding, or an autonomous loop.

## Candidates evaluated

| Option | Verdict |
|---|---|
| A. Map experiment outcome onto Hypothesis `status = true/false` | Rejected. One experiment is not global Hypothesis truth. |
| B. `SUPPORTED` / `REJECTED` as final assessment labels | Rejected. Those words collapse prediction-fit into Hypothesis/security truth. |
| C. Explicit `ExperimentFeedback` + context-bound `HypothesisAssessment` with append-only history | **Accepted.** |

## Strategy

After execution, Application reconstructs `ExperimentFeedback` from SoR (Hypothesis, Experiment, durable ExperimentPlan, ExecutionAttempt/WorkerResult, Observations) and asks a trusted Research evaluator what the experiment taught **under this context**.

```
Hypothesis H
+ Experiment E
+ durable ExperimentPlan
+ Observation set
→ HypothesisAssessment A
```

Never: “failed once” → globally rejected Hypothesis.

## Assessment semantics

| Outcome | Meaning | Not meaning |
|---|---|---|
| `CONSISTENT_WITH_PREDICTION` | Observed facts are compatible with this experiment’s prediction | Hypothesis proven; vulnerability proven |
| `CONTRADICTS_PREDICTION` | Observed facts contradict this experiment’s prediction under this context | Hypothesis globally false; false positive |
| `INCONCLUSIVE` | Valid data, but not enough to distinguish explanations | Negative evidence |
| `EXECUTION_UNUSABLE` | Runtime/infrastructure outcome prevents research interpretation (timeout, process failure, unknown, invalid result) | Negative evidence against the Hypothesis |
| `NEEDS_MORE_CONTEXT` | Observations cannot be safely interpreted without additional state/context | Authorization or Finding decision |

## Context binding

Assessment is bound to Hypothesis + Experiment + plan + observations (+ later actor/session/state). Negative/contradictory knowledge remains negative **under context C**. This is the foundation for future actor/session/state/temporal differentials. It is a read projection over SoR, not a shadow memory database.

## Evaluator model

`ExperimentEvaluatorRegistry` is keyed by the plan’s trusted `evaluation_strategy`, not by WorkerResult.

GATE 03 implements only `diagnostic.echo.v1`:

- matching echo Observation → `CONSISTENT_WITH_PREDICTION`
- mismatched valid echo Observation → `CONTRADICTS_PREDICTION`
- unusable runtime → `EXECUTION_UNUSABLE`
- valid execution, no relevant Observation → `INCONCLUSIVE`

Deterministic evaluators must not use network, models, randomness, or current external state. Same plan + feedback + evaluator version → equivalent assessment. Later reasoning-assisted evaluators are allowed as a registry entry; they are not required and must not be faked as security reasoning.

## Persistence

Append-only `HypothesisAssessmentRecord`. Hypothesis claim/lifecycle is not overwritten with truth. Rationale is a bounded structured mapping. Forbidden inside assessment: Evidence, Candidate, severity, Finding, numeric confidence.

Dedicated append-only `experiment_plan` stores the executed specification (capability, action, target, arguments without secrets, expected/disconfirming observations, evaluation strategy, side-effect level, budget reference). Experiment lifecycle stays on `experiment`. Once dispatched/persisted, the plan must not silently mutate.

## Confidence

No numeric belief, Bayesian weight, or support score. Decision 017 remains. Collect empirical assessment history first.

## Why

“What happened?” is Observation/execution state. “What does this teach us?” is assessment. “Is this a verified security issue?” is later Verification/Candidate/Human Review. Collapsing those three is how scanners mint false positives.

## Constraints

1. Do not treat experiment result as Hypothesis truth.
2. Do not use `SUPPORTED`/`REJECTED` as global truth labels.
3. Do not let one contradiction delete or globally reject a Hypothesis.
4. Do not create Evidence from assessment.
5. Do not let WorkerResult choose the evaluator.
6. Do not require an LLM for diagnostic.echo assessment.
7. Do not invent numeric confidence.
8. Do not auto-plan the next experiment or start an autonomous loop.
9. Do not edit Alembic `a3_001`, `a6_001`, `a7_001`, or `a8_001`.
10. Research assessment must not import PostgreSQL adapters, Worker adapters, subprocess, provider SDKs, or Strix.

## Revisit triggers

- Real diagnostic data showing the five outcomes are insufficient
- Actor/session/state differential requiring explicit context identity fields
- A second trusted evaluation strategy beyond `diagnostic.echo.v1`
- Calibrated belief model **after** empirical assessment history exists
- Transition B / Verification consuming assessments (still not Evidence)

---

# Decision 028 — Research Reasoning / Admission Provenance Ledger

Status: **accepted with constraints** (GATE 03)

Date: 2026-08-17

Does not rewrite Decisions 001–027. Revisits Decision 026’s “persist reasoning only when ADMITTED” choice. Does not create Evidence, Candidate, Finding, or an autonomous loop.

## Candidates evaluated

| Option | Verdict |
|---|---|
| A. Keep GATE 02: persist reasoning only on `ADMITTED` | Rejected for learning. False-positive reduction needs rejected-proposal patterns. |
| B. Persist rejected proposals as Hypothesis rows | Rejected. Reasoning record ≠ Hypothesis. |
| C. Persist reasoning for all completed bounded cycles; separate append-only `ResearchAdmissionRecord`; rejected proposals never become Hypothesis | **Accepted.** |

## Strategy

Every completed Generator → Falsifier → admission cycle writes process history:

- structured Generator output (when the model call completed)
- structured Falsifier output (when that call completed)
- `ResearchAdmissionRecord` (always)

Rejected proposal **must not** become a Hypothesis. `research_reasoning.hypothesis_id` is nullable.

## Reasoning ledger

`ResearchReasoningRecord` remains untrusted provenance, not Observation, Evidence, or Hypothesis truth. Persist adapter/model identity when present, context fingerprint, source references inside structured output, and role. Do **not** store full prompts by default. Do not dump secrets. Raw external content stays referenced through source ids.

## Admission provenance

Append-only `ResearchAdmissionRecord`: admission id, research run, generator/falsifier reasoning ids (nullable), outcome, nullable `admitted_hypothesis_id`, reason codes, context fingerprint, created_at.

This is research-process history, not authoritative target truth.

If Generator or Falsifier **invocation** fails (`ModelPortError`): no Hypothesis, no fabricated structured proposal, admission outcome `MODEL_INVOCATION_FAILED`, no reasoning row unless a completed model result exists. Invocation failure is not research-negative evidence. No provider retry engine.

## Rejected reasoning

Persisting rejected reasoning is allowed because reasoning ≠ Hypothesis ≠ fact. Future measurements: generator rejection rate, hallucinated-source rate, untestable proposal rate, falsifier intervention rate, policy-conflict rate, `NEEDS_MORE_CONTEXT` rate. Do not treat `CONTRADICTS_PREDICTION` as a false positive; that metric needs Verification/Candidate/human outcomes later.

## N4 claim handling

**Option B.** Unsupported novelty tokens `N4_ZERO_DAY` / `ZERO_DAY` / `N4` normalize system `novelty_basis` to `UNCLASSIFIED` and preserve `model_claimed_novelty` as the raw model token.

- N4 never becomes product truth
- the model’s claim is not silently erased
- novelty claim cannot promote a Hypothesis

Other unknown novelty tokens still fail proposal parse (`ResearchInputError`). Authority keys such as `n4` / `zero_day` still fail as `ProposalAuthorityError`.

## Confidence

**HIGH** that rejected proposals must not become Hypotheses and that N4 cannot be product novelty.

**HIGH** that completed-cycle provenance should include rejections.

**MEDIUM** for omitting reasoning rows on raw `ModelPortError` (admission-only operational provenance) versus inventing an adapter identity.

## Why

GATE 02’s admitted-only ledger hid the failures the system must learn from. Learning requires seeing what Generator proposed, what Falsifier challenged, and why admission refused—without laundering that into Hypothesis or Finding truth.

## Constraints

1. Do not persist a Hypothesis from a rejected proposal.
2. Do not store raw prompts by default.
3. Do not silently erase model-claimed N4; do not promote it.
4. Do not treat model invocation failure as Hypothesis-negative evidence.
5. Do not build a provider retry system.
6. Do not add Evidence/Candidate/Finding tables.
7. Do not edit Alembic `a3_001`, `a6_001`, `a7_001`, or `a8_001`.
8. Core remains unaware of assessment/admission semantics. Workers remain unaware of Hypothesis.

## Revisit triggers

- First real ModelPort adapter (provider still deferred)
- Need to persist bounded operational failure envelopes with adapter identity
- Prompt/redaction policy if a later audit requires selected prompt excerpts
- Semantic hypothesis dedup with explicit actor/state/context identity
- Dashboard metrics over this SoR (not in this slice)

**FINAL STATUS: PASS**

---

# Decision 029 — Research Benchmark Scenario / Ground-Truth Separation

Status: **accepted with constraints** (GATE 04A)

Date: 2026-08-17

Does not rewrite Decisions 001–028. Does not select a model provider. Does not add a PostgreSQL benchmark schema.

## Candidates evaluated

| Option | Verdict |
|---|---|
| A. Grade model output subjectively after the fact | Rejected. Not reproducible. |
| B. Put expected answers in the prompt / ResearchContext | Rejected. The model repeats the answer. Leakage. |
| C. Versioned scenario with a hard visible/hidden split; leakage is a test failure | **Accepted.** |

## Strategy

A benchmark scenario is engineering/evaluation data under `benchmarks/research/`. It is not Domain SoR, Research Memory, production configuration, or a Worker contract.

JSON fixtures are enough. YAML is not introduced.

Each scenario has explicit `scenario_id`, `version`, `category`, `split`, `visible_input`, and `hidden_evaluation`.

## Visible vs hidden material

**Model-visible** (`visible_input` → `ResearchContext` only):

- ResearchContext-compatible facts
- Observations
- prior Hypotheses
- negative evidence / experiment execution state
- procedural context / research question
- untrusted external content

**Hidden evaluation** (evaluator only):

- known source ids
- forbidden fabricated source ids
- expected epistemic distinctions
- known benign explanations
- required negative-control concepts
- known policy traps / injection needles
- scenario-specific invariants
- evaluation tags
- leakage canary
- expected admission outcomes
- benchmark-only ground truth

Hidden evaluation MUST NEVER enter:

- Generator prompt
- Falsifier prompt
- ResearchContext
- ModelCallRequest
- model-visible metadata

## Versioning

`scenario_id` + `version` is the identity. A material change to hidden expected semantics increments `version`. Do not silently rewrite benchmark history after results exist.

## Leakage prevention

Hidden key names, leakage canaries, and forbidden fabricated ids must not serialize into `ResearchContext` or the model-visible portions of `ModelCallRequest`. An accidental merge is a test failure, not a footnote. The harness records Generator/Falsifier requests and scans them.

## Development / holdout policy

| Split | Rule |
|---|---|
| development | May be inspected while building the harness |
| holdout | Must not be used to tune prompts or admission logic by hand |

GATE 04A ships development scenarios only. The holdout rule is in force for any future holdout files. Otherwise Research Brain is optimized to the benchmark.

## Confidence

**HIGH** that hidden evaluator data must not enter the model channel.

**HIGH** that scenarios must be versioned.

**MEDIUM** for the exact initial category list; more synthetic categories can be added without changing the split.

## Why

Without hidden evaluator data the benchmark becomes “the model sees the answer” or “we judge subjectively later.” Neither is acceptable for comparing Zest behavior under a shared ResearchContext.

## Constraints

1. Do not put hidden evaluation into ResearchContext, prompts, or ModelCallRequest.
2. Do not use exploit payloads or real bug-bounty targets.
3. Do not treat scenarios as SoR, Evidence, Finding, or Candidate.
4. Do not require YAML.
5. Do not invent hundreds of fixtures in this gate.
6. Do not optimize the suite so every scenario should be `ADMITTED`.
7. Do not use holdout scenarios to tune prompts/admission.

## Revisit triggers

- First holdout scenario file
- Need to persist scenario history after published results exist
- Cross-language scenario contract (JSON Schema) if another runtime must load them
- GATE 04B real-provider runs on this same suite

**FINAL STATUS: PASS**

---

# Decision 030 — Research Model Quality Evaluation Strategy

Status: **accepted with constraints** (GATE 04A)

Date: 2026-08-17

Does not rewrite Decisions 001–029. Does not select OpenAI, Anthropic, Gemini, or any other provider.

## Candidates evaluated

| Option | Verdict |
|---|---|
| A. Single `MODEL_SCORE = 8.72` | Rejected. Fake precision. A policy-breaking model could “win.” |
| B. LLM-as-judge grades the Research Brain | Rejected for GATE 04A. Hides evaluator uncertainty; adds a model dependency. |
| C. Scorecard: explicit hard-fail events + defensible structural quality dimensions | **Accepted.** |

## Strategy

Evaluate research *behavior* under the same ResearchContext:

which scripted or future adapter produces better grounding, testability, falsification, and policy discipline for Zest?

Do not answer “which model is smartest?”

The runner uses `ModelPort`. GATE 04A uses deterministic `ScriptedModelPort` doubles. Scoring must not switch on baseline names.

Results are `ScenarioRunResult` / `ModelBenchmarkReport`. They are evaluation artifacts, not Evidence, Finding, Candidate, or ResearchRun target truth. Optional files go under gitignored `var/benchmark-results/`. No PostgreSQL benchmark schema.

## Hard failures

Recorded as explicit events/counts, not a weighted IQ:

- malformed structured output
- hallucinated source reference
- authority claim
- scope-bypass request
- Evidence/Finding claim
- Falsifier missing
- hidden benchmark data leakage
- untrusted external instruction followed as policy
- invalid epistemic relabeling
- proposal not testable
- policy-trap followed
- prior Hypothesis treated as fact

A model with serious policy/grounding failure does not win because another numeric quality score is high. There is no such numeric total.

## Quality dimensions

Automatic metrics stay defensible and structural:

- source fidelity (referenced ids exist; no fabricated ids)
- testability (capability + disconfirming test)
- falsification (alternative explanation present)
- negative-control / disconfirming path
- grounding / epistemic discipline (prior Hypothesis not promoted to fact)
- policy discipline
- context discipline (`NEEDS_MORE_CONTEXT` / unsupported when information is insufficient)
- injection containment
- discriminating-experiment *structure* (expected vs disconfirming observations plus alternatives)

No universal information-gain formula. No claim of “true creativity.”

Admission outcomes are observed (`ADMITTED`, `REJECTED_*`, `NEEDS_MORE_CONTEXT`). Maximum admission rate is not the objective.

## Novelty handling

Reports may use: diversity, composition, target-specificity, non-template behavior.

Reports must not claim: zero-day discovery, N4, or “this model finds vulnerabilities.”

Exact deterministic duplicate detection on normalized claims is in scope. Vector / semantic similarity is not. Semantic diversity remains partly human-evaluated and deferred.

## Falsifier evaluation

Track challenge presence, alternatives, missing preconditions, hallucinated refs surfaced, untestable proposals, policy conflicts, and admission changes after challenge.

Do **not** equate “Falsifier rejected many things” with “good Falsifier.” Over-rejection is also bad.

## Scoring philosophy

Hard-fail counts and per-dimension pass/total. No magic aggregate. Human/pairwise or LLM-assisted judging may be evaluated later. A documented ordinal rubric (POOR / ACCEPTABLE / STRONG) exists for future blind human review. No dashboard in GATE 04A.

Model-call counts (Generator / Falsifier) are recorded now. Latency, tokens, and provider cost are not fabricated; they wait for a real adapter.

## Confidence

**HIGH** that a single scalar score would be misleading.

**HIGH** that hidden leakage, hallucinated sources, and policy-following must be hard fails.

**MEDIUM** for the exact quality dimension set; dimensions can be added without introducing an aggregate score.

## Why

Provider choice must be empirical against Zest behavior, not marketing or a hidden judge model. GATE 04A builds the measuring instrument. GATE 04B attaches real adapters to it.

## Constraints

1. Do not emit `MODEL_SCORE` or equivalent.
2. Do not use LLM-as-judge in GATE 04A.
3. Do not add vector similarity.
4. Do not infer N4 / zero-day capability.
5. Do not persist benchmark reports into authoritative Research SoR.
6. Do not install a provider SDK.
7. Do not treat ScriptedModelPort as a real model.
8. Do not claim prompt injection is “solved”; measure containment.
9. Do not score Falsifier quality by rejection count alone.
10. Do not optimize the suite for always-`ADMITTED`.

## Revisit triggers

- GATE 04B real provider adapters
- Human-review UI for the ordinal rubric
- Optional LLM-as-judge experiment with explicit uncertainty
- Semantic diversity once actor/state/context identity exists
- Provider-reported latency/token/cost fields when a real adapter exists

**FINAL STATUS: PASS**

---

# Decision 031 — Real-Model Experimental Evaluation Protocol

Status: **accepted with constraints** (GATE 04B-PREP)

Date: 2026-08-17

Does not rewrite Decisions 001–030. Does not install a provider SDK or select a vendor.

## Candidates evaluated

| Option | Verdict |
|---|---|
| A. One run per scenario, then pick a winner | Rejected. Stochastic models and hidden hard fails. |
| B. Average quality into one score / automatic WINNER | Rejected. Fake precision; Decision 030 already forbids this. |
| C. Controlled experiment: shared config identity, repeated runs, paired scenarios, explicit failure classes | **Accepted.** |

## Strategy

Real-model comparison is a controlled experiment. Compared configurations must share:

- benchmark suite fingerprint / scenario versions
- visible ResearchContext construction
- Generator and Falsifier instruction versions/fingerprints
- structured-output specification fingerprint
- admission logic version
- context-builder version
- evaluator/harness version

If those differ, results are **incomparable**.

`gpt` / `claude` / `gemini` aliases are not experiment identity. `ModelConfigurationIdentity` records adapter identity and only those provider fields the adapter actually supplies. Unset fields stay unset; they are not fabricated.

## Repeated runs

`runs_per_scenario` is configurable. The development default is 3. That number is a conservative engineering default, not a scientific law.

Reports expose attempted, completed, provider/runtime failures, research-quality failures, hard-fail occurrence fractions, admission distributions, exact-duplicate rate, and per-scenario variance. A 1/5 hallucination is reported as `1/5`, not “80% success” and not PASS.

Single-run output is allowed for harness debugging. It is **not** an authoritative real-model comparison.

## Paired comparison

Model A and Model B are compared on the same `scenario_id@version`. Humans inspect per-scenario observations. No p-values. No automatic winner line.

## Stability

Instability is tracked as separate observations: hard-fail occurrence, admission-outcome mix, source-reference set diversity, exact-repeat rate, experiment-direction diversity. There is no single stability score. A model that is occasionally ungrounded is operationally worse than a consistently grounded one.

## Provider vs research failures

Keep separate:

- provider/API unavailable, timeout, malformed provider envelope (`PROVIDER_RUNTIME`)
- structured-output parse failure
- Generator research-quality failure
- Falsifier research-quality failure
- harness invariant (hidden leakage)

A provider outage is not a bad security hypothesis. A hallucinated source is not infrastructure noise.

## Cost / latency philosophy

Reports have fields for latency, tokens, provider-reported or deterministically calculated cost, retries, timeout, and a pricing reference. Values are recorded only when a real adapter provides them. Pricing tables are not baked into Research domain logic. Cost is operational metadata, not target truth. Quality priority remains: hard safety/grounding, usefulness, falsification, stability, then cost. No weighted formula yet.

## Metamorphic testing

Variants keep hidden semantics and change surface form (wording, opaque ids, order, irrelevant benign observation, paraphrased untrusted instruction). Exact natural-language equality is not required. Epistemic class, policy discipline, testability, and admission family must remain compatible.

## Scenario specificity

Structural proxies only: relevant sources used, required source groups combined, irrelevant stuffing avoided, scenario-specific tokens or ids in the proposal/challenge. This is **context utilization**, not creativity measurement.

## Reproducibility

Reports record suite fingerprint, scenario identities, Zest git commit when the script can obtain it (otherwise `unknown`), instruction fingerprints, model configuration identity, runs_per_scenario, timestamp, harness/evaluator versions. Git is engineering metadata in the benchmark script, not a Domain dependency. Reports are immutable files; overwrite is refused. Not PostgreSQL SoR.

## Confidence

**HIGH** that one run is insufficient for real-model claims.

**HIGH** that provider outage must not be scored as research failure.

**MEDIUM** for the default `runs_per_scenario=3`.

## Constraints

1. Do not print `WINNER` or a magic aggregate score.
2. Do not hide a hard fail behind an average.
3. Do not fabricate provider telemetry.
4. Do not install provider SDKs in this gate.
5. Do not demand exact prose equality on metamorphic variants.
6. Do not treat development fixtures as unseen holdout.

## Revisit triggers

- First live provider adapter (GATE 04B)
- Need for statistical tests with justified sample sizes
- Pricing reference format once a real adapter exists
- Human pairwise review UI

**FINAL STATUS: PASS**

---

# Decision 032 — Holdout Integrity / Benchmark Contamination Strategy

Status: **accepted with constraints** (GATE 04B-PREP)

Date: 2026-08-17

Does not rewrite Decisions 001–031. Does not require a cloud holdout service.

## Candidates evaluated

| Option | Verdict |
|---|---|
| A. Call the repo `development` fixtures a holdout | Rejected. Cursor and developers read them. Contaminated. |
| B. Encrypt holdout in-repo and give the agent the key | Rejected. Not statistically clean. |
| C. DEVELOPMENT / CALIBRATION / SEALED_HOLDOUT with sealed files outside the development workspace | **Accepted.** |

## Strategy

Three dataset classes:

| Class | Who may see it | Use |
|---|---|---|
| DEVELOPMENT | Developers and coding assistants | Harness debug, metric creation, deterministic bug fixes. Current fixtures belong here. |
| CALIBRATION | Inspected at defined milestones | Compare instruction changes; inspect failures. After tuning, it is not holdout. |
| SEALED_HOLDOUT | Not in the normal Cursor/repo context | Evaluation that must not have driven prompt/admission tuning. |

## Sealed holdout

Preferred: external directory at runtime via `ZEST_BENCHMARK_HOLDOUT_PATH` or `--sealed-holdout-path`.

The repository contains schema/format docs, loader, and integrity rules. It does **not** need the sealed scenario files.

Sealed files must not live under `benchmarks/research/scenarios/`. A missing path is reported **unavailable**, not PASS.

## Fingerprinting

A suite manifest records suite id, version, scenario count, identities, and a fingerprint over visible+hidden integrity hashes. Reports include the fingerprint and omit hidden evaluator contents.

Changing hidden semantics changes the fingerprint. Unchanged files keep the same fingerprint.

## Model leakage vs development contamination

| Kind | Meaning | Owner |
|---|---|---|
| Model leakage | Hidden evaluation entered a model-visible request | GATE 04A tests |
| Development contamination | Developers/agents saw sealed semantics while tuning | Decision 032 |

Both must be tracked. Solving one does not solve the other.

## Report policy

Do not log hidden answers. Do not store sealed scenario bodies inside benchmark reports. Do not rewrite previous report files.

## Confidence

**HIGH** that in-repo fixtures cannot be a strong unseen holdout.

**HIGH** that missing holdout must not be reported as PASS.

**MEDIUM** for operational discipline (humans must actually keep sealed files out of Cursor).

## Constraints

1. Do not describe development fixtures as holdout.
2. Do not put sealed scenarios in the default repo tree.
3. Do not print hidden evaluation into reports.
4. Do not require a cloud service.
5. Do not treat encryption-in-repo as cleanliness.

## Revisit triggers

- First sealed suite actually used for a live model comparison
- Need for signed manifests
- Multi-operator holdout custody

**FINAL STATUS: PASS**

---

# Decision 033 — Live Model Adapter / Empirical Comparison

Status: **accepted with constraints** (GATE 04B)

Date: 2026-08-17

Does not rewrite Decisions 001–032. Does not select a production default provider.

## Strategy

Provider choice is an empirical GATE 04B result, not hype. The GATE 04A/04B-PREP harness is reused. Live adapters implement `ModelPort` below Research.

Comparative PASS requires at least two real model configurations actually executed on the same comparable suite (suite fingerprint, scenario versions, instruction fingerprints, evaluator versions, Zest commit). One live configuration, or only scripted baselines, is **PENDING**, not PASS.

Missing SDK, credential, or explicit model id is **UNAVAILABLE**. UNAVAILABLE is not a benchmark failure and not a research-quality failure.

## Adapter boundary

Research, Core, Application, and `zest.benchmark` do not import provider SDKs. Concrete adapters live in `integrations/models/`. The benchmark script is the composition root: it may resolve a live adapter from an environment secret reference and inject a `ModelPort`.

Provider-specific authentication, request translation, structured-output transport, telemetry normalization, and error mapping are adapter responsibilities.

## Identity

Reports persist adapter id, requested model id, provider-returned model/version when the provider actually returns it, Generator/Falsifier instruction fingerprints, structured-output spec version, and relevant generation settings that were set. Unset version/snapshot fields stay unset. Alias-only identity is not reproducible.

## Secrets

API keys do not enter ResearchContext, ModelCallRequest content, SoR, logs, or benchmark reports. The composition root may hold a `SecretReference` (environment name). Missing key = UNAVAILABLE. Full secret-management product is out of scope.

## Failure taxonomy

Keep distinct: `PROVIDER_RUNTIME`, `PROVIDER_AUTH`, `PROVIDER_RATE_LIMIT`, `PROVIDER_TIMEOUT`, `STRUCTURED_OUTPUT_FAILURE`, `GENERATOR_RESEARCH_QUALITY`, `FALSIFIER_RESEARCH_QUALITY`, `HARNESS_INVARIANT`.

Provider failure is not research-quality failure. Malformed JSON is not silently repaired into a valid proposal.

## Telemetry

Record latency, input tokens, output tokens, retries, and provider cost only when the adapter actually received them, with provenance if cost is present. Do not fabricate. Pricing tables do not live in Research.

## Comparison policy

Same suite fingerprint, prompt fingerprints, evaluator versions, and Zest commit are required for direct comparison. Output is dimensional. There is no `WINNER = X`. Repeated runs follow Decision 031. Hard failures stay fractions (`1/3`), not averaged PASS.

Sealed holdout: run only if an external path exists; otherwise `SEALED HOLDOUT = UNAVAILABLE`. Development fixtures are not unseen.

## Confidence

**HIGH** that Research must stay provider-neutral.

**HIGH** that missing credentials must not be labeled PASS.

**MEDIUM** for current SDK request shapes; adapters map current OpenAI Responses, Anthropic `output_config.format`, and google-genai `response_json_schema` APIs and must be revisited if those SDKs change.

## Constraints

1. Do not import provider SDKs into Research.
2. Do not put API keys in ResearchContext, ModelCallRequest, SoR, logs, or reports.
3. Do not count scripted baselines as live providers.
4. Do not print `WINNER`.
5. Do not call the development suite a sealed holdout.
6. Do not silently repair invalid structured output.

## Revisit triggers

- SDK/API wire-format change
- Need for a fourth provider
- First sealed holdout used in a live comparison
- Provider-supplied cost provenance format

**FINAL STATUS: PASS** (implementation). Live comparative execution is **PENDING** until ≥2 real configurations run.

---

# Decision 034 — Evidence Admission / Transition B Authority

Status: **accepted with constraints** (Transition B)

Date: 2026-08-17

Does not rewrite Decisions 001–033. Does not implement Verification, Candidate, or Finding.

## Strategy

Locked chain:

Observation / Artifact → Research evaluation → EvidenceProposal → explicit auditable Evidence admission → Evidence.

Evidence ≠ Candidate ≠ Finding. Evidence admission ≠ Verification.

The first implemented path is deterministic diagnostic.echo plumbing: a diagnostic hypothesis, durable ExperimentPlan, successful Observation, and `CONSISTENT_WITH_PREDICTION` (or a narrow `CONTRADICTS_PREDICTION` mismatch) may yield an EvidenceProposal for the claim that the diagnostic echo matched or did not match the executed plan. That is not vulnerability Evidence.

## Admission authority

Candidates evaluated:

| Option | Verdict |
|---|---|
| Core admits Evidence | Rejected. Core is authorization authority, not Evidence truth. |
| Data admits Evidence | Rejected. Persistence is not judgment. |
| Application admits Evidence | Rejected. Application coordinates; it must not become Evidence authority. |
| Model admits Evidence | Rejected. Model output is an untrusted structured proposal. |
| Human admits every diagnostic plumbing record | Rejected for this gate. Human Review remains later for Finding. |
| Research owns Evidence admission semantics; Application coordinates persistence; Data persists | **Accepted.** |

The model may never admit Evidence. Workers cannot create Evidence. Generator cannot skip to Evidence.

## EvidenceProposal

Internal Research type. Conceptual fields: proposal id, research run, hypothesis/experiment references, Observation/assessment references, polarity (`SUPPORTING` / `CONTRADICTING` / `NEUTRAL`), bounded claim scope, rationale, provenance.

Must not contain severity, Finding, Candidate, exploitability verdict, authorization, or numeric confidence as authority.

## Admission rules

Outcomes: `ADMITTED`, `REJECTED_INSUFFICIENT_SUPPORT`, `REJECTED_BROKEN_PROVENANCE`, `REJECTED_EXECUTION_UNUSABLE`, `REJECTED_POLICY_CONFLICT`, `NEEDS_VERIFICATION`.

No score threshold. Admission checks at least: sources exist; sources belong to the correct run/context; Observation provenance is intact; unusable execution is not Evidence; model assertion alone is insufficient; assessment without Observation is insufficient; source references cannot be fabricated; Evidence cannot claim more than sources support.

Timeout / `UNKNOWN_OUTCOME` / `EXECUTION_UNUSABLE` → no admitted Evidence.

Rejected proposals create admission history and no Evidence row.

## Evidence semantics

Append-only Evidence records store identity, research run, related hypothesis/experiment, admitted source references, polarity, admission record reference, and created_at. They reference underlying immutable provenance. They do not copy WorkerResult bodies. Artifact attachment is not automatic Evidence.

`NEEDS_VERIFICATION` is reserved and is not a Verification Engine.

## Persistence

New Alembic revision `a10_001_evidence_admission` only. a3/a6/a7/a8/a9 are not edited. Real PostgreSQL is required.

## Confidence

**HIGH** that Research must own Evidence admission semantics.

**HIGH** that Observation/assessment auto-promotion would be a false-positive path.

**MEDIUM** for later security-specific Evidence classes; not opened here.

## Constraints

1. Do not create Evidence from model assertion alone.
2. Do not auto-promote Observation or HypothesisAssessment.
3. Do not treat CONSISTENT_WITH_PREDICTION as vulnerability proof.
4. Do not implement Verification, Candidate, or Finding here.
5. Do not edit old Alembic revisions.
6. Do not put numeric confidence on Evidence.

## Revisit triggers

- First security-relevant Evidence class
- Verification Engine
- Human review of Evidence (if ever required beyond Finding)
- Multi-observation composition rules

**FINAL STATUS: PASS**

---

# Decision 035 — Verification Engine

Status: **accepted with constraints** (GATE 05)

Date: 2026-08-17

Does not rewrite Decisions 001–034. Does not implement Finding, FindingProposal, Human Review, severity, or live-model verification.

## Strategy

Evidence admission ≠ Verification. Evidence ≠ verified vulnerability.

Verification asks whether collected Evidence survives deliberate reproduction and falsification of a Candidate claim. It consumes typed references (Candidate, Hypothesis, Evidence, Observation, Experiment/ExperimentPlan, assessment history, execution provenance). It does not consume arbitrary prose and does not treat WorkerResult as trusted truth.

The first implemented path is deterministic diagnostic.echo plumbing: reproduce with a new Experiment / request_id (original `alpha`, reproduction `beta`). That proves Verification machinery. It is not a security vulnerability.

## Verification subject

Candidate is created OPEN from an Evidence-backed diagnostic claim. Verification operates on Candidate.

Verifier → `VerificationResult` → Research transition rules → Candidate state update.

A model/verifier must not mutate Candidate directly. Verification records are append-only proposals, not Candidate lifecycle authority.

## Independence

Decision 017 remains: the generating source is not sufficient to validate itself. A second provider is not required. This slice uses a deterministic verifier plus a new Worker experiment. Original Evidence cannot be the sole proof.

## Reproduction

Reproduction Evidence must be distinguishable from original Evidence: different evidence id, experiment id, request_id, and observation ids. Missing or non-independent reproduction yields INCONCLUSIVE, not VALIDATED.

All verification observations travel Worker → Transition A → Observation → Transition B → Evidence. There is no Verification bypass that manufactures Evidence.

## Negative controls

`VerificationPlan` requires a negative-control intent. For diagnostic.echo, the fail token `__diagnostic_control_fail__` must not be the observed echo. An optional mismatch-fixture Evidence path may also be supplied. A control that does not hold yields INCONCLUSIVE, not VALIDATED.

## Result semantics

Outcomes: VALIDATED, REJECTED, INCONCLUSIVE, DUPLICATE, OUT_OF_SCOPE.

Infrastructure failure (timeout, UNKNOWN_OUTCOME, process failure) → INCONCLUSIVE. Failure to verify is not proof the claim is false.

REJECTED requires an affirmative contradiction (reproduction consistently contradicts the claim). Tool failure is not REJECTED.

OUT_OF_SCOPE only from authoritative existing Core/Program scope input, not a new URL/CIDR matcher.

DUPLICATE only from an explicit known Candidate reference. No semantic/vector guessing.

## Persistence

Append-only `verification` records: identity, candidate, strategy, outcome, original/reproduction/control evidence refs, alternative-explanation checks, verifier kind/identity, created_at. No payload copies, severity, or Finding.

New Alembic revision `a11_001_candidate_verification` only. a3/a6/a7/a8/a9/a10 are not edited.

## Confidence

**HIGH** that Verification must not commit Candidate state.

**HIGH** that infrastructure failure must not be REJECTED.

**MEDIUM** for later security-class verification plans; not opened here.

## Constraints

1. Do not validate from original Evidence alone.
2. Do not treat timeout/process failure as REJECTED.
3. Do not create Finding or severity.
4. Do not invent scope or guess duplicates.
5. Do not import provider SDKs, PostgreSQL, subprocess, or Strix into Research.

## Revisit triggers

- First security-relevant VerificationPlan
- Human-in-the-loop verification steps
- Multi-evidence composition beyond diagnostic.echo
- Need for a second reasoning role as verifier

**FINAL STATUS: PASS**

---

# Decision 036 — Candidate Lifecycle

Status: **accepted with constraints** (GATE 05)

Date: 2026-08-17

Does not rewrite Decisions 001–035. Does not implement FindingProposal, Human Review, Finding, or CVSS.

## Strategy

Locked lifecycle:

OPEN → VERIFYING → VALIDATED | REJECTED | INCONCLUSIVE | DUPLICATE | OUT_OF_SCOPE

Candidate means a security-testable (here: diagnostic-testable) claim requiring verification. It does not mean a verified issue. VALIDATED does not mean Finding.

This slice’s only creation path is diagnostic.echo Evidence → explicit `CandidateProposal` → OPEN. Observation, model prose, HypothesisAssessment, and Evidence auto-promotion are forbidden.

## Creation gate

Research owns Candidate admission semantics. Application coordinates persistence. Data persists. Core remains authorization authority, not vulnerability truth. Worker cannot create or transition Candidate.

Minimum checks: Evidence exists; provenance resolves; claim is the diagnostic testable claim and does not exceed Evidence scope; ResearchRun consistent; no authoritative out-of-scope flag. No score threshold. No giant rule engine.

Rejected proposals create admission history and no Candidate row.

## State machine

Legal transitions are enforced centrally in Research (`transition_candidate`). OPEN → VALIDATED is illegal. REJECTED → VALIDATED is illegal. Terminal states are not silently rewritten. Future reopen is a later decision.

## Transition authority

Verifier output is a proposal. Research applies it. Application cannot invent VALIDATED. Model cannot write Candidate state.

## VALIDATED semantics

VALIDATED means the Candidate passed the configured VerificationPlan. Locked formula remains:

VALIDATED Candidate + FindingProposal + Human Review + Core Approval = Finding

That formula is **not** implemented in this slice.

## INCONCLUSIVE semantics

INCONCLUSIVE is a first-class correct outcome. It is not auto-retried and not auto-demoted to REJECTED. Later Research may propose another Experiment; there is no autonomous loop here.

## Persistence

`candidate` (lifecycle state mutable; originating evidence refs immutable), `candidate_evidence`, append-only `candidate_admission`, append-only `verification`. New Alembic `a11_001` only.

## Confidence

**HIGH** that Candidate must not skip Verification to VALIDATED.

**HIGH** that VALIDATED must not be treated as Finding.

**MEDIUM** for later security Candidate classifications; this slice allows `DIAGNOSTIC_PLUMBING` only.

## Constraints

1. Do not create Candidate from Observation, model, or assessment.
2. Do not auto-create Candidate from Evidence.
3. Do not allow OPEN → VALIDATED.
4. Do not add severity, CVSS, exploitability, or numeric confidence.
5. Do not implement FindingProposal / Finding.
6. Do not edit old Alembic revisions.

## Revisit triggers

- First non-diagnostic Candidate class
- FindingProposal
- Explicit reopen-from-terminal decision
- Human mapping of duplicates

**FINAL STATUS: PASS**

---

# Decision 037 — FindingProposal Boundary

Status: **accepted with constraints** (GATE 06)

Date: 2026-08-17

Does not rewrite Decisions 001–036. Does not implement security-specific Finding classes, CVSS, CVE, bounty, or a dashboard.

## Strategy

VALIDATED Candidate ≠ Finding. There is no shortcut.

Minimum chain:

VALIDATED Candidate → explicit FindingProposal → Human Review → Core Approval → Finding

Research owns FindingProposal admission and lifecycle semantics. Application coordinates persistence and review use cases. Data persists. Core does not decide vulnerability truth. A model or Worker cannot create an authoritative FindingProposal.

The first implemented path is diagnostic.echo plumbing. The proposal title is `Diagnostic echo verification proposal`. It is not a vulnerability.

## Creation gate

A FindingProposal may be created only from a VALIDATED Candidate.

Rejected source states: OPEN, VERIFYING, REJECTED, INCONCLUSIVE, DUPLICATE, OUT_OF_SCOPE.

Forbidden direct paths: Evidence → FindingProposal, Observation → FindingProposal, model → FindingProposal.

Admission checks ResearchRun consistency, Evidence/Verification provenance, diagnostic title/claim, and `DIAGNOSTIC_PLUMBING` classification. No score threshold. No numeric confidence.

## Lifecycle

PROPOSED → HUMAN_REVIEW → APPROVED | REJECTED

APPROVED is the domain view of the same human/Core Approval event. It is not a second independent approval authority. Illegal shortcuts such as PROPOSED → APPROVED are rejected.

## Immutability / version semantics

Reviewed content is frozen after insert. Only lifecycle `state` may change.

`content_fingerprint` is SHA-256 of canonical JSON over candidate_id, title, claim, evidence_ids, and verification_ids.

Approval subject is `finding-proposal:{proposal_id}:{content_fingerprint}`.

Material content change requires a new proposal/version. An Approval for proposal A or an old fingerprint cannot authorize different content.

## Provenance

FindingProposal preserves exact Evidence and Verification references used at creation. It does not copy WorkerResult bodies.

## Confidence

**HIGH** that only a VALIDATED Candidate may create a FindingProposal.

**HIGH** that FindingProposal is not a Finding.

**MEDIUM** for later security-class proposals; not opened here.

## Constraints

1. Do not create FindingProposal from OPEN/INCONCLUSIVE/other non-VALIDATED Candidate states.
2. Do not auto-create Finding from FindingProposal.
3. Do not add CVSS, severity, CVE, bounty, exploitability, or numeric confidence.
4. Do not let Core, Application, model, or Worker own FindingProposal truth.
5. Do not edit Alembic a3–a11.

## Revisit triggers

- First non-diagnostic FindingProposal class
- Explicit proposal versioning beyond fingerprint + new proposal_id
- Human mapping of duplicate proposals

**FINAL STATUS: PASS**

---

# Decision 038 — Human Review / Core Approval / Finding

Status: **accepted with constraints** (GATE 06)

Date: 2026-08-17

Does not rewrite Decisions 001–037. Does not implement security Finding classes, CVSS, CVE, bounty, or a dashboard.

## Strategy

Locked formula:

VALIDATED Candidate + FindingProposal + Human Review + Core Approval = Finding

Human review is permanent architecture. No AUTO_APPROVE. No automatic Finding path.

This gate’s only fixture is DIAGNOSTIC_PLUMBING. A Finding here is workflow plumbing proof, not a security vulnerability.

## Human identity

Approval is tied to `ActorType.HUMAN_OPERATOR`. Tests use explicit identity `operator-test-1`.

Model cannot be the approval principal. Worker cannot approve. Integration cannot impersonate a human.

## Review semantics

HumanReviewDecision is APPROVE or REJECT. Review references the exact FindingProposal content fingerprint. It may include a bounded note, reason codes, reviewer identity, and timestamp. No secrets.

Human Review ≠ Core Approval. Recording a review does not create Approval or Finding.

## Core Approval

Core already owns Approval semantics. Smallest compatible extension: `evaluate_recorded_approval` validates a durable human decision for an explicit subject. `authorizes` is true only for APPROVE. A valid REJECT is a recorded decision and does not authorize.

Flow: Human submits review → Application requests Core evaluation of a constructed recorded Approval view → Core validates actor, subject, decision, and recorded provenance → Approval is persisted in the finalize transaction → Application asks Research transition/creation rules.

Application cannot fabricate Approval. Core does not interpret Candidate, Evidence, or vulnerability truth.

Subject/version matching fails closed. An Approval for proposal A cannot approve proposal B.

## Finding creation gate

Finding is created only when:

1. Candidate == VALIDATED
2. FindingProposal is in HUMAN_REVIEW and Research admits APPROVED
3. valid HumanReview exists
4. matching Core Approval exists and authorizes
5. all references belong to the same ResearchRun/context

Finding provenance: Finding → FindingProposal → Candidate → Verification → Evidence.

Finding is append-only. Unique identity is one Finding per `finding_proposal_id`. Duplicate finalize returns the same Finding.

## Rejection semantics

Human REJECT → FindingProposal REJECTED → no Finding.

Candidate may remain VALIDATED. Human rejection of a FindingProposal does not mutate Candidate to REJECTED. These are different lifecycle layers.

## Persistence

New Alembic `a12_001_finding_acceptance` only. Tables: `finding_proposal` (state mutable), append-only `human_review`, `approval`, `finding`.

Finalize is one transaction for Approval + proposal state + optional Finding + audit. Review interaction is a prior short transaction. Failure must not leave an APPROVED proposal with a fabricated Finding, or a Finding without Approval provenance.

Audit events: FINDING_PROPOSAL_CREATED, HUMAN_REVIEW_RECORDED, CORE_APPROVAL_RECORDED, FINDING_CREATED. AuditEvent ≠ Approval ≠ Finding.

## Confidence

**HIGH** that Finding requires Human Review and matching Core Approval.

**HIGH** that Human REJECT must not demote a VALIDATED Candidate.

**MEDIUM** for later security Finding classes; not opened here.

## Constraints

1. Do not auto-approve.
2. Do not let Application, model, or Worker approve.
3. Do not reuse Approval across proposals or fingerprints.
4. Do not treat Core as a vulnerability judge.
5. Do not call diagnostic plumbing a vulnerability.
6. Do not add CVSS/CVE/bounty/severity yet.
7. Do not edit Alembic a3–a11.

## Revisit triggers

- First security-relevant Finding class
- Explicit FindingProposal version table
- Dashboard / Interface review UX
- Reopen of a REJECTED proposal

**FINAL STATUS: PASS**

---

# Decision 039 — Target / Causal Model

Status: **accepted with constraints** (GATE 07)

Date: 2026-08-17

Does not rewrite Decisions 001–038. Does not implement invariant mining, chain engine, Temporal Intelligence, Strix, or a graph/vector database.

## Strategy

The Target Model is a Research projection/read model over the System of Record, plus explicit inferred records where persistence is required.

It is not a second source of truth. Authoritative facts remain Observation, Experiment, WorkerResult, and other existing SoR records. Inference never becomes OBSERVED.

The model is black-box capable. Source/AST/call-graph enrichment is later and optional.

The first builder is deterministic diagnostic.echo plumbing: Actor handle executes diagnostic Action and produces Observation. It is not a security authorization graph.

## Entity model

Minimal typed kinds: Actor, Role, Session, Resource, Action, State, Relationship, StateTransition.

Only what Research reasoning needs. Opaque handles only. Session/token secret material is forbidden. Session, if present later, uses references/handles; secrets stay behind SecretPort/runtime.

No universal ontology. No “Actor owns Resource” fact unless directly/deterministically established.

## Epistemic status

Every element carries one of: OBSERVED, DERIVED, INFERRED, HYPOTHESIZED.

- OBSERVED: reconstructed from SoR Observation provenance.
- DERIVED: deterministic projection (for example echoed value, unknown-precondition transition).
- INFERRED / HYPOTHESIZED: explicit admitted records only.

A model proposal may enter only as INFERRED or HYPOTHESIZED, with resolving source references. Hallucinated sources are rejected. Inference cannot silently upgrade to OBSERVED or DERIVED.

ResearchContext gains `INFERRED` as a distinct class so inferences cannot be relabelled Observation.

## Causal / state transitions

Represent, where known: State S + Actor A + Action X → observed/derived State S2.

Permit uncertainty: unknown precondition, derived postcondition, hypothesized relation. Do not invent certainty.

## Persistence / projection

PostgreSQL remains SoR. No Neo4j. No vector DB. No giant graph JSON blob as authority.

OBSERVED/DERIVED elements are rebuilt from existing records. Append-only `target_inference` stores only INFERRED/HYPOTHESIZED elements with source refs, epistemic status, strategy/version, and created_at.

New Alembic `a13_001_target_differential` only. a3–a12 are not edited.

## Model-generated inference

ModelPort may later propose relationships. They remain untrusted structured proposals until `admit_target_inference`. They cannot modify authorization or scope.

## Confidence

**HIGH** that Target Model must not become a second truth store.

**HIGH** that inference must not become OBSERVED.

**MEDIUM** for later security-class relations and source-code enrichment.

## Constraints

1. Do not persist inference as Observation.
2. Do not persist session secrets.
3. Do not introduce a graph or vector product.
4. Do not let Target Model change Core authorization/scope.
5. Do not treat diagnostic actor/resource handles as ownership or authorization.
6. Do not edit Alembic a3–a12.

## Revisit triggers

- First security-relevant relationship class
- Source/AST enrichment
- Explicit Asset SoR records beyond diagnostic handles
- Temporal change feed

**FINAL STATUS: PASS**

---

# Decision 040 — Differential Reasoning Engine

Status: **accepted with constraints** (GATE 07)

Date: 2026-08-17

Does not rewrite Decisions 001–039. Does not implement Temporal Intelligence, invariant mining, or autonomous exploration.

## Strategy

Difference ≠ vulnerability.

Differential reasoning detects meaningful, controlled differences and produces HypothesisProposal inputs. It does not create Evidence, Candidate, or Finding.

Do not compare two arbitrary response blobs and ask an LLM what differs. The comparison must know which dimensions changed.

## Comparison dimensions

Explicit dimensions: ACTOR, ROLE, SESSION, RESOURCE, STATE, ACTION, INPUT, TIME.

TIME is reserved/deferred. Using TIME as a changed dimension is rejected in this slice.

## Controlled comparison

`DifferentialCase` names baseline/variant observation refs, changed dimensions, and common dimensions.

Prefer one/few changed variables. The diagnostic fixture is: same capability/action, different input.

Undeclared extra changes are INCOMPARABLE / rejected, not auto-interpreted.

## Result semantics

`DifferentialObservation` records changed/common dimensions, observed differences/similarities, source refs, strategy/version, and interpretation:

CONTROLLED_DIFFERENCE | EQUIVALENT | INCOMPARABLE

No severity, Evidence, Candidate, Finding, or vulnerability verdict. Same response is not authorization proof. 200 vs 403 is not implemented and would not be IDOR proof.

## Alternative explanations

Results carry explanation slots (intended input difference, runtime/protocol difference, asynchronous processing). These are not universal hardcoded verdicts. Falsifier still owns adversarial challenge.

## Hypothesis integration

Flow: Observations → Target Model → DifferentialCase → DifferentialObservation → ResearchContext → Generator → Falsifier → Admission → Hypothesis.

DifferentialObservation enters context as DERIVED_FACT with explicit not-Evidence / not-vulnerability flags. It is not Hypothesis truth.

## Persistence

Append-only `differential_observation` with baseline/variant refs, dimensions, strategy/version. No global payload-hash deduplication. ResearchRun isolation is required; cross-run sources fail closed.

## Confidence

**HIGH** that a difference must not auto-promote to Evidence/Candidate/Finding.

**HIGH** that comparisons must declare changed dimensions.

**MEDIUM** for later security actor/role/session cases.

## Constraints

1. Do not call a difference a vulnerability.
2. Do not auto-create Evidence or Candidate from a differential result.
3. Do not delegate arbitrary blob diff to an LLM.
4. Do not implement Temporal Intelligence.
5. Do not allow cross-run comparison by default.
6. Do not edit Alembic a3–a12.

## Revisit triggers

- First security actor/role/session differential
- Explicit cross-run comparison design
- TIME/Temporal Intelligence
- Invariant mining consuming differentials

**FINAL STATUS: PASS**

---

# Decision 041 — Invariant Mining

Status: **accepted with constraints** (GATE 08)

Date: 2026-08-17

Does not rewrite Decisions 001–040. Does not implement Exploration Policy, Temporal Intelligence, Strix, a generic exploit engine, or a universal vulnerability taxonomy.

## Strategy

Invariant Hypothesis ≠ Fact ≠ Authorization rule ≠ Evidence ≠ Vulnerability.

Invariant mining asks what behavior appears expected to remain true across relevant actor/resource/session/state contexts, then produces testable expected-behavior hypotheses.

The first implemented path is deterministic diagnostic.echo plumbing:

`for diagnostic.echo, output should correspond to the submitted input.`

That is an `INPUT_OUTPUT_RELATION` expectation class, not an authorization bug and not a vulnerability.

An admitted invariant may later produce a HypothesisProposal. It does not create a direct execution path.

Flow:

Invariant → HypothesisProposal → Falsifier → Admission → Hypothesis → ExperimentPlan → Core

Existing safety chain remains. Core scope is unchanged.

## Invariant semantics

Expectation classes, not vulnerability classes:

ACCESS_RELATION, STATE_TRANSITION, OWNERSHIP_RELATION, ROLE_BOUNDARY, SESSION_BINDING, RESOURCE_ISOLATION, IMMUTABILITY_AFTER_STATE, SEQUENCE_PRECONDITION, INPUT_OUTPUT_RELATION, OTHER

GATE 08 admits only `INPUT_OUTPUT_RELATION`. Other kinds are rejected as untestable in this slice.

Epistemic lifecycle is separate from Candidate lifecycle:

PROPOSED (in-memory only) → persisted TESTABLE | CHALLENGED | RETIRED

There is no `CONFIRMED_TRUE`. Repeated supporting observations do not turn an invariant into an authoritative application rule. An admitted record cannot remain PROPOSED and cannot become OBSERVED.

## Inputs

Potential inputs: Target Model, OBSERVED/DERIVED facts, DifferentialObservations, ExperimentFeedback, HypothesisAssessment history, admitted Evidence where relevant, explicit human research seed, procedural knowledge.

INFERRED/HYPOTHESIZED Target Model items may inform proposal generation but must preserve epistemic status. Do not flatten all inputs into facts.

GATE 08 uses deterministic diagnostic views plus an optional DifferentialObservation source ref. No live model is required. If a ModelPort is used in tests, ScriptedModelPort only.

## Proposal / admission

`InvariantProposal` is an untrusted structured proposal. It carries proposal id, ResearchRun, kind, subject refs, expected behavior, source refs, applicability context, assumptions, known counterexamples, falsification direction, proposer provenance, and version.

It does not carry vulnerability verdict, severity, confidence score, Evidence status, Finding status, or Core authorization semantics.

Model, human, or heuristic may propose. Research admits. No numeric threshold.

Admission outcomes: ADMITTED, REJECTED_UNTESTABLE, REJECTED_BROKEN_PROVENANCE, REJECTED_CONTRADICTED, REJECTED_CROSS_RUN, REJECTED_POLICY_CONFLICT, NEEDS_MORE_CONTEXT.

Strong requirements: source refs resolve in the same ResearchRun; expected behavior is testable; applicability context is explicit; unacknowledged contradiction of known source facts is rejected; authorization/scope claims are not treated as truth; at least one falsification direction exists.

Strategy version: `invariant.diagnostic.echo.v1`. Proposer provenance: `deterministic.diagnostic.echo.v1`.

## Counterexamples / context

Counterexample discipline is mandatory. A contradiction under context C is not automatically a global disproof.

Acknowledged mismatches admit as CHALLENGED. Unacknowledged contradicting observations reject as REJECTED_CONTRADICTED.

Recording a later counterexample moves TESTABLE|CHALLENGED → CHALLENGED, preserves applicability context, and does not auto-create Evidence, Candidate, or Finding.

## Persistence

PostgreSQL remains SoR. No universal policy table. No graph DB.

New Alembic `a14_001_invariant_chain` only. a3–a13 are not edited.

Tables:

- `invariant_hypothesis` — status mutable; TESTABLE|CHALLENGED|RETIRED
- append-only `invariant_source_ref`
- append-only `invariant_counterexample_ref`

Persist only what reconstructive research history needs.

## Model role

Architecture supports deterministic proposal now and future ModelPort-assisted proposal.

Model output remains an untrusted structured proposal until Research admission. GATE 08 does not require a live model.

## Confidence

**HIGH** that an invariant must not become application truth, Core scope, or a vulnerability verdict.

**HIGH** that counterexamples must stay context-bound.

**MEDIUM** for later security-class invariants such as ownership or role boundary.

## Constraints

1. Do not treat an invariant as OBSERVED or CONFIRMED_TRUE.
2. Do not feed an invariant as a ScopeRule.
3. Do not call an invariant violation a vulnerability.
4. Do not generalize a context-bound counterexample globally.
5. Do not create a direct execution path from invariant to Worker.
6. Do not edit Alembic a3–a13.
7. Do not introduce a graph or vector product.

## Revisit triggers

- First non-diagnostic invariant kind
- ModelPort-assisted invariant proposal
- Explicit RETIRED workflow beyond counterexample challenge
- Exploration Policy consuming invariant features

**FINAL STATUS: PASS**

---

# Decision 042 — Chain Engine

Status: **accepted with constraints** (GATE 08)

Date: 2026-08-17

Does not rewrite Decisions 001–041. Does not implement Exploration Policy, Temporal Intelligence, Strix, a generic exploit engine, or an arbitrary attack graph.

## Strategy

Chain ≠ LLM story.

A chain is an explicit sequence of research capabilities / state transitions supported by provenance:

Observation → capability/state consequence → next precondition satisfied → next action/experiment → new Observation → …

Purpose: support N2 primarily (novel combinations of known primitives) and prepare for N3 where target-specific state/invariant violations compose. Do not claim N4 discovery.

GATE 08 proves diagnostic provenance/state composition only. The fixture is not an exploit chain.

Chain Engine does not execute tools. It outputs chain research plans/hypotheses. Each executable step still goes Research → Application → Core → Worker. Level 2 still requires Core Approval. Level 3 still DENY.

## Chain representation

Minimal research nodes: OBSERVATION, CAPABILITY, STATE, STATE_TRANSITION, INVARIANT, EXPERIMENT, HYPOTHESIS.

`ChainHypothesis` is a Hypothesis structure. Not Evidence. Not Candidate. Not Finding. Not an exploit.

Fields: chain id, ResearchRun, ordered steps, source refs, preconditions, expected resulting capability/state, unresolved assumptions, falsification points, strategy/version, exact structural identity.

Every node/reference preserves source identity. Sequence is not causality. Do not interpret “A happened before B” as “A caused B”.

Strategy version: `chain.diagnostic.echo.v1`.

## Capability semantics

Capability means a research-relevant consequence established under context. It is not a global permission.

Diagnostic fixture uses only `CAN_OBSERVE_ECHO`. Do not implement a real security capability taxonomy yet.

Do not globally state `Actor A CAN_MUTATE` without resource/state/context. Diagnostic attributes mark `not_global_permission`.

## Edge semantics

Defensible relations: PRODUCES, ENABLES, REQUIRES, TRANSITIONS_TO, CONTRADICTS, SATISFIES_PRECONDITION.

There is no CAUSES edge. An LLM must not invent arbitrary causal edges as fact. Model-proposed edges remain HYPOTHESIZED/INFERRED until tested/admitted.

`PRODUCES` requires the same `experiment_id`. `ENABLES` requires a previous CAPABILITY, OBSERVATION, or STATE node. Missing edges and unsupported causal leaps are rejected.

An invariant violation may be one chain step, not automatically the final finding.

## Bounded search

v1 uses bounded deterministic search. No autonomous graph explosion.

`ChainSearchLimits`: max_depth, max_branching, max_generated_chains.

Zero means no allowance. Negative is invalid. Conservative test defaults: depth 4, branching 1, generated chains 2.

Cycle identity is `(node_kind, source_ref, state_signature)` including input/actor/resource/action. Revisiting the same resource under a different state/context is not the same visit.

Dedup is exact structural identity (SHA-256) per ResearchRun. No vector similarity. Same actions with different actor/session/state context must not collapse.

## State / context handling

Chain search may use Target Model state transitions. If an intermediate state is only inferred, the chain preserves INFERRED/HYPOTHESIZED. No hidden certainty promotion.

Descriptive features exist for a later Exploration Policy: depth, unresolved assumptions, supported steps, inferred steps, side-effect requirement, evidence coverage, novelty/composition marker. No weighted priority score.

## Model role

ModelPort may later propose a missing edge, suggest composition, or explain a potential chain. Those proposals remain untrusted.

GATE 08 uses a deterministic diagnostic fixture only. No live model is required.

## Persistence

PostgreSQL remains SoR. No Neo4j. No vector DB.

Append-only `chain_hypothesis` with unique `(research_run_id, structural_identity)`.

New Alembic `a14_001_invariant_chain` only. a3–a13 are not edited.

## Confidence

**HIGH** that a chain must not execute a Worker, bypass Core, or auto-promote to Evidence/Candidate/Finding.

**HIGH** that sequence must not be treated as causality and inferred state must not become observed.

**MEDIUM** for later security capability taxonomies and N3 composition.

## Constraints

1. Do not let a chain dispatch a Worker.
2. Do not bypass Core authorization, including Level 2/3.
3. Do not treat temporal order as causality.
4. Do not promote inferred state to observed.
5. Do not collapse different actor/session/state context into one chain identity.
6. Do not introduce unbounded graph search, Neo4j, or vectors.
7. Do not edit Alembic a3–a13.

## Revisit triggers

- First non-diagnostic capability class
- Exploration Policy consuming descriptive features
- ModelPort-assisted edge proposal
- Explicit N3 target-state composition beyond diagnostic plumbing

**FINAL STATUS: PASS**

---

# Decision 043 — Exploration / Exploitation Policy

Status: **accepted with constraints** (GATE 09)

Date: 2026-08-17

Does not rewrite Decisions 001–042. Does not implement an autonomous infinite loop, Strix, final Model Runtime architecture, Codex CLI, subscription OAuth, a production scheduler, or distributed orchestration.

## Strategy

Research priority ≠ truth ≠ authorization ≠ Evidence.

Exploration Policy decides what Research should investigate next. It never decides what is a vulnerability.

Without an explicit policy the system tends to repeat known patterns, over-test high-confidence hypotheses, ignore weak but high-information observations, converge on local maxima, and paraphrase existing hypotheses.

GATE 09 implements one bounded selection cycle:

Research state → Opportunity set → Selection policy → selected `ResearchOpportunity`(s)

Execution remains separate. Core still authorizes every Experiment. There is no `while budget: select; execute; repeat`.

Strategy version: `exploration.diagnostic.echo.v1`.

## Opportunity model

`ResearchOpportunity` is a research workflow category, not a vulnerability class.

Conceptual fields: opportunity id, ResearchRun, kind, source references, proposed direction, unresolved question, expected information-value description, known assumptions, estimated execution cost class, required side-effect level, novelty/composition marker, prior-attempt references, exact structural identity.

It does not carry a vulnerability verdict, severity, Finding, confidence-as-authority, or automatic authorization.

Kinds: `HYPOTHESIS_FOLLOWUP`, `DIFFERENTIAL_FOLLOWUP`, `INVARIANT_CHALLENGE`, `CHAIN_EXTENSION`, `NEGATIVE_KNOWLEDGE_REVISIT`, `UNRESOLVED_TARGET_RELATION`, `CONTROL_EXPERIMENT`, `OTHER`.

Modes: `EXPLOITATION` (continue promising existing directions) and `EXPLORATION` (bounded spend on uncertain but informative directions). Neither implies a vulnerability.

GATE 09 generates opportunities deterministically from structured Research state: Hypotheses, Assessments, Target Model, DifferentialObservations, Invariants, ChainHypotheses, negative/counterexample history, previous attempts, budget state, duplicate history, and ResearchRun context. Finding count is not next-action authority. The input is not flattened into giant prose.

## Selection dimensions

Do not invent one magic weighted score. Do not implement `priority = 0.4 novelty + 0.3 confidence`.

Independent ordinal dimensions (`LOW` / `MEDIUM` / `HIGH` where applicable):

- expected information value
- security relevance potential
- novelty/composition
- unresolved uncertainty
- chain potential
- evidence coverage
- execution cost
- side-effect requirement
- duplicate/repetition risk
- previous failed attempts

`ResearchSelectionDecision` outcomes: `SELECT`, `DEFER`, `SKIP_DUPLICATE`, `SKIP_LOW_INFORMATION`, `BLOCKED_BUDGET`, `BLOCKED_POLICY`, `NEEDS_MORE_CONTEXT`.

`SELECT` means the direction is worth planning. It is not Core execution ALLOW.

## Exploration budgeting

`ResearchPolicyBudget` is a selection allowance, not Core `IssuedBudget`.

- `max_selected`
- `max_exploratory` (exploration slots, not a percentage)
- `max_chain_extensions`
- `max_estimated_cost_rank` (`0..3`)

`0` means no allowance. Negative is invalid.

The policy may reserve bounded exploration capacity so the system does not choose only exploitation forever. There is no hardcoded universal “20% must always be exploration.” A persistent budget ledger remains deferred.

## Diversity

Exact structural identity (SHA-256) and provenance/source/context keys suppress equivalent selections. No vectors.

When otherwise comparable, selection favors different source combinations, actor/state contexts, invariant classes, chain branches, and research questions. Semantic diversity is not claimed solved.

## Negative knowledge handling

Negative history suppresses waste; it is not a permanent blacklist.

A failed or contradicted test under context C using strategy/version S must not globally eliminate the opportunity. A revisit may be legitimate if context changed, a new Observation exists, a new actor/state exists, a different strategy exists, or a later temporal change occurred.

Historical assessments are not rewritten when a revisit is selected.

## Model role

ModelPort may later propose opportunities. The model cannot select itself for execution. Research policy decides from a structured proposal.

GATE 09 uses deterministic opportunity generation/selection only. No live model is required. Tests may use ScriptedModelPort only.

## Confidence

**HIGH** that selection must not authorize, dispatch, or auto-promote to Evidence/Candidate/Finding.

**HIGH** that a weighted priority formula would become fake truth.

**MEDIUM** for later model-proposed opportunities and a persistent exploration ledger.

## Constraints

1. Do not treat priority as confidence or truth.
2. Do not bypass Core with a selected opportunity.
3. Do not introduce an autonomous infinite loop.
4. Do not permanently blacklist negative knowledge.
5. Do not auto-create a Hypothesis from an opportunity.
6. Do not use Finding count as next-action authority.
7. Do not edit Alembic a3–a14.

## Revisit triggers

- ModelPort-proposed opportunities
- Persistent exploration budget ledger
- Semantic diversity beyond exact structural identity
- Non-diagnostic opportunity kinds beyond diagnostic.echo plumbing

**FINAL STATUS: PASS**

---

# Decision 044 — Temporal Intelligence

Status: **accepted with constraints** (GATE 09)

Date: 2026-08-17

Does not rewrite Decisions 001–043. Decision 040 reserved TIME; this decision implements TIME only with compatible snapshot/change provenance. Does not implement Strix, Model Runtime, Codex CLI, OAuth, a production scheduler, or autonomous orchestration.

## Strategy

Target state at t1 ≠ target state at t2. Change ≠ vulnerability.

Temporal Intelligence asks what materially changed over time. It does not ask whether a vulnerability was introduced.

Keep separate:

- **Stateful research:** changes caused within one research interaction/session/workflow.
- **Temporal Intelligence:** comparison of target observations/snapshots across time.

Preferred flow:

Snapshot t1 → Snapshot t2 → deterministic ChangeEvent → ResearchOpportunity → Generator/Falsifier if needed → Hypothesis → Experiment

Never ChangeEvent → Finding. Never ChangeEvent → Evidence/Candidate.

Strategy version: `temporal.diagnostic.echo.v1`.

## Snapshot semantics

A Snapshot is a bounded point-in-time view by reference. It is not a second SoR and not a full database copy.

GATE 09 snapshots reference ResearchRun / program, selected observation ids, target identity, `captured_at`, and strategy/version. No secrets.

A Snapshot is immutable once created. Later pictures are new Snapshots. Earlier Snapshots remain historical.

## ChangeEvent semantics

A ChangeEvent is a deterministic delta where possible. Categories: `ADDED`, `REMOVED`, `MODIFIED`, `RELATION_CHANGED`, `STATE_CHANGED`, `BEHAVIOR_CHANGED`, `UNKNOWN_CHANGE`.

`VULNERABILITY_INTRODUCED` is not a ChangeEvent category. That would be Research interpretation.

A ChangeEvent may later seed a ResearchOpportunity, DifferentialCase, or HypothesisProposal. It never becomes Evidence, Candidate, or Finding.

## Temporal differential

Compare compatible snapshots only:

- same relevant target identity
- same ResearchRun
- compatible strategy/schema version
- explicit t1/t2 with variant after baseline
- source provenance

Cross-program comparisons are denied by default.

TIME is a legitimate `DifferentialDimension` only when backed by baseline/variant snapshot ids. Timestamp-only difference is rejected. TIME without snapshot provenance is `REJECTED_MISSING_TEMPORAL_PROVENANCE`.

A temporal DifferentialCase must identify the baseline snapshot, variant snapshot, controlled target identity, and known changes.

## Negative-knowledge interaction

Something previously tested unsuccessfully may become interesting again after a relevant ChangeEvent.

Example: Hypothesis H contradicted under snapshot t1. At t2 a related diagnostic behavior changed. Policy may allow revisiting H under the new temporal context.

Do not automatically revive every rejected direction. Do not rewrite the historical assessment.

## Persistence

PostgreSQL remains SoR. Append-only `snapshot`, `snapshot_member`, and `change_event`. Snapshot members are observation references, not payload copies.

New Alembic `a15_001_exploration_temporal` only. a3–a14 are not edited.

Retention/compaction is deferred. Never silently delete Evidence, Verification, or Finding provenance because temporal snapshot cleanup occurs.

## Confidence

**HIGH** that change must not be called a vulnerability and must not auto-promote to Evidence/Candidate/Finding.

**HIGH** that TIME comparison without snapshot provenance is not Temporal Intelligence.

**MEDIUM** for later security actor/role/session temporal cases and retention policy.

## Constraints

1. Do not call a change a vulnerability.
2. Do not auto-create Evidence or Candidate from a ChangeEvent.
3. Do not allow cross-program temporal comparison by default.
4. Do not mutate a Snapshot after creation.
5. Do not rewrite historical assessments because a later change occurred.
6. Do not compare timestamps without snapshot provenance.
7. Do not snapshot the entire database blindly.
8. Do not edit Alembic a3–a14.

## Revisit triggers

- Snapshot retention/compaction policy
- Cross-program temporal comparison design
- Non-diagnostic temporal fixtures
- Persistent exploration ledger consuming ChangeEvents

**FINAL STATUS: PASS**


# Decision 045 — Model Runtime Architecture

Status: **accepted with constraints** (GATE 10)

Date: 2026-08-17

Does not rewrite Decisions 001–044. Does not select a model winner, implement full routing policy, scrape undocumented credentials, or bypass provider/runtime safety refusals. GATE 04B remains PENDING until >=2 real comparable runtime configurations execute.

## Strategy

Keep Research `ModelPort` provider-neutral. Research classifies runtime identity and operational outcomes. It does not know provider SDKs, CLI processes, OAuth/session implementation, or local model transport.

Lower runtime abstraction:

```
ModelPort
    ↓
ModelRuntimeAdapter
```

Concrete adapters live in Integrations. Argv execution lives in Platform (`argv_process`) as first transport, not architecture. Application and Research do not import argv or Integrations.

API key is one auth mode, not the primary architecture.

## Runtime kinds

- `API`
- `SUBSCRIPTION_OAUTH`
- `CLI_SESSION`
- `LOCAL_MODEL`
- `EXTERNAL_AGENT`

Same underlying model through API vs CLI is two different runtime configurations.

`LOCAL_MODEL` is a runtime kind, not an Ollama/LM Studio product commitment.

`SUBSCRIPTION_OAUTH` is reserved. No OAuth client is implemented in this gate.

## Inference vs agent runtime

`INFERENCE_RUNTIME` is completion/structured-output only.

`AGENT_RUNTIME` may have tools or side effects. An authenticated CLI is not automatically an ordinary inference-only ModelPort.

Codex CLI is `CLI_SESSION` + `AUTHENTICATED_CLI_SESSION` + `AGENT_RUNTIME`. Unrestricted tool capability (`*`, `all`, `unrestricted`, `shell`, `yolo`, `danger-full-access`) is rejected. Explicit capability allowlist is required. `--yolo` / `--full-auto` / `danger-full-access` are forbidden.

An agent runtime cannot gain Core authority because it is authenticated. Side-effecting execution still requires Core ALLOW and a capability-controlled boundary.

## Identity

`ModelRuntimeIdentity` records:

- runtime_kind
- runtime_class
- adapter_id
- runtime_id
- auth_mode
- configuration_fingerprint
- runtime_version when actually available
- session_reference where safe (label only)

No secrets. Session references that look like `sk-`, `token=`, or `bearer ` are rejected. Benchmark `ModelConfigurationIdentity` records the same runtime fields so GATE 04B can compare API vs CLI vs local without treating them as one identity.

## Authentication/session model

Supported auth modes:

- `API_KEY`
- `SUBSCRIPTION_OAUTH`
- `AUTHENTICATED_CLI_SESSION`
- `LOCAL_NO_REMOTE_AUTH`
- `EXTERNAL_RUNTIME_AUTH`

Credentials and session material are not ResearchContext, SoR, Evidence, logs, or benchmark reports. Adapters hold composition-root references or rely on an already-authenticated local CLI session via a constructed child environment (`HOME`/`USERPROFILE` passthrough). Database URLs and provider API keys are stripped from child env. Zest does not scrape undocumented credentials from another application.

## Outcome taxonomy

Operational runtime outcomes, not research conclusions:

- `COMPLETED`
- `UNAVAILABLE`
- `AUTH_FAILED`
- `RATE_LIMITED`
- `TIMED_OUT`
- `PROCESS_FAILED`
- `PROTOCOL_ERROR`
- `STRUCTURED_OUTPUT_INVALID`
- `CONTENT_POLICY_BLOCKED`
- `CANCELLED`

`CONTENT_POLICY_BLOCKED` ≠ Hypothesis rejection ≠ Evidence ≠ research conclusion. Application maps it to `AdmissionOutcome.MODEL_INVOCATION_FAILED` with reason_code `CONTENT_POLICY_BLOCKED`. There is no guardrail-bypass behavior. Research may later choose another explicitly configured runtime through normal policy/routing; that is not a hidden bypass.

Process failure is not a research result. Unavailable CLI is `UNAVAILABLE`, not a fake PASS.

## CLI/session semantics

Authenticated CLI runtime (Codex CLI as first adapter):

- argv execution, `shell=False`
- bounded stdout/stderr
- timeout and kill
- cancellation via timeout/kill in this gate
- explicit working directory
- constructed environment
- documented flags only (`codex --version`, `codex exec --sandbox read-only`)
- structured-output validation
- version/capability probe before use

If CLI is unavailable: `UNAVAILABLE`. If installed, a controlled diagnostic ModelPort test may run. No security-testing capability in this gate. Codex `--json` event streams are not treated as ModelPort structured objects by default.

## External-agent semantics

Boundary prepared for Codex-style host agents, Claude-style host agents, and MCP-connected external agents.

External-agent output remains UNTRUSTED. It cannot alter scope, authorize execution, admit Evidence, validate Candidate, or approve Finding. Empty or unrestricted capability sets are rejected. Live host-agent product is deferred; GATE 10 exposes the contract only.

## Confidence

**HIGH** that API must not be the only runtime type and that CLI/session is first-class.

**HIGH** that inference and agent runtimes must stay distinct, and that content-policy blocks are operational outcomes.

**MEDIUM** for later OAuth subscription adapters, local-model product choice, and full runtime routing policy.

## Constraints

1. Research must not import provider SDKs, subprocess, or Integrations.
2. Do not treat an unrestricted agent CLI as ordinary inference.
3. Do not persist API keys or CLI session tokens.
4. Do not scrape undocumented credentials.
5. Do not implement provider safety bypass.
6. Do not mark GATE 04B PASS without >=2 real comparable runtime configurations.
7. Do not hardcode undocumented CLI flags.

## Revisit triggers

- Full runtime routing policy
- Live >=2-runtime GATE 04B comparison
- OAuth/subscription adapter product
- Local-model product selection
- Persistent secret manager (Decision 013 product still deferred)


# Decision 046 — Strix Integration

Status: **accepted with constraints** (GATE 10)

Date: 2026-08-17

Does not rewrite Decisions 001–045. Does not make Strix the Research Brain, implement security scanning workflows, expose unlimited MCP tools, or create Findings from Strix output.

## Strategy

Strix is a replaceable Integration / security execution runtime.

Locked:

- Strix ≠ Research Brain
- Strix ≠ Core
- Strix ≠ Research Memory
- Strix ≠ Evidence authority
- Strix ≠ Finding authority

Zest ModelRuntime remains independently usable. No circular dependency with Strix model internals.

## Architectural placement

```
Zest
  → Application/Core authorization
  → controlled execution boundary
  → StrixIntegration (Platform port)
  → Strix runtime/tools (Integrations adapter)
```

Research and Application must not import `integrations.strix`. Core is unaware of Strix internals. The adapter must not import Data or write the SoR.

Future topology allowed:

```
Research Brain → ModelRuntimePort → Codex CLI
authorized Experiment → StrixIntegration → Strix tools
```

Those paths do not share authority.

## Execution boundary

Research must not send arbitrary free-form shell authority to Strix.

Controlled envelope:

- ResearchRun id
- Experiment id
- correlation/request id
- capability
- authorized target/scope reference
- execution budget id
- side-effect level
- authorization-decision reference
- allowed capabilities
- artifact/result constraints

GATE 10 allowlist: `strix.diagnostic.ping` only. Unrestricted markers are rejected.

## Authorization/scope handling

Core `evaluate_execution` ALLOW is required before the adapter is invoked. Denied requests never reach Strix.

Redirect, newly discovered asset, or scope expansion: stop and request Core re-evaluation (`SCOPE_RECHECK_REQUIRED`). Assumptions such as “same domain family” are not authorization.

## Result semantics

Normalize runtime outcome separately from semantic research result.

Distinguish:

- runtime failure
- tool failure
- policy/content block
- budget exhaustion
- successful execution

from a security research conclusion.

Strix result ≠ Observation automatically ≠ Evidence ≠ Candidate ≠ Finding.

Runtime failure fabricates no Observation. Diagnostic success still remains untrusted boundary data and follows controlled normalization. It does not write SoR.

## External-agent/MCP handling

An authenticated external coding agent may later consume Strix/security capabilities. Zest remains authority.

External agent/MCP cannot bypass:

- scope
- Core authorization
- side-effect policy
- budgets
- Evidence admission
- Verification
- Human Finding approval

Unlimited MCP tool exposure is rejected. Capability allowlist is required. Production Strix security capability set is deferred.

## Runtime availability

Reported separately from architecture PASS:

- API runtime: AVAILABLE / UNAVAILABLE
- CLI/session runtime: AVAILABLE / UNAVAILABLE
- local runtime: AVAILABLE / UNAVAILABLE
- Strix runtime: AVAILABLE / UNAVAILABLE

If Strix is not installed: UNAVAILABLE / PENDING. Architecture tests may still PASS. Availability must not be fabricated.

## Confidence

**HIGH** that Strix must remain Integration and cannot bypass Core.

**HIGH** that Strix outputs remain untrusted and cannot become Evidence/Finding by arrival.

**MEDIUM** for later production capability sets, sandbox topology, and agent-native MCP mode.

## Constraints

1. Do not let Research import the concrete Strix adapter.
2. Do not execute Strix without an authorization-decision reference and Core ALLOW.
3. Do not implement security-specific scanning workflows in this gate.
4. Do not implement provider/runtime safeguard bypass via Strix.
5. Do not let Strix write the SoR.
6. Do not treat Strix model configuration as Zest ModelRuntime.

## Revisit triggers

- Production Strix security capability set
- Agent-native MCP mode with an explicit allowlist
- WorkerResult/normalization path for non-diagnostic Strix artifacts
- Persistent budget consumption ledger for Strix execution


# Decision 047 — Runtime Routing / Model Selection Policy

Status: **accepted with constraints** (GATE 11)

Date: 2026-08-17

Does not rewrite Decisions 001–046. Does not declare a universal model winner, introduce a weighted magic score, let a model choose itself, or bypass provider/runtime safety refusals.

## Strategy

Routing asks which **configured** runtime should handle this reasoning role. It does not ask which runtime is always best. It does not decide vulnerability truth, scope, authorization, Evidence, Candidate, or Finding.

Policy version: `runtime.routing.v1`.

Research owns the deterministic policy (`select_runtime` / `reconsider_runtime`). Application coordinates and audits. Integrations remain concrete adapters. Core authorization is unchanged. The model is not a routing principal.

## Hard filters

Mandatory constraints are applied before any quality preference. A cheaper or faster runtime cannot bypass them.

Reject when:

- unavailable
- missing authentication
- wrong runtime class
- agent tools not permitted for an inference-only role
- unrestricted capability exposure
- side-effect capabilities on the reasoning path
- no structured-output compatibility when required
- explicit operator prohibition
- privacy/locality mismatch (`LOCAL_REQUIRED` vs remote)
- Strix presented as a ModelRuntime
- already attempted in this routing episode

## Role-specific routing

Generator and Falsifier are separate `RoutingRequest`s. Architecture permits Runtime A for Generator and Runtime B for Falsifier, or the same runtime for both, based on measured/operator configuration. This is routing-ready architecture, not automatic multi-model hype.

## Preference semantics

After hard filters, compare surviving candidates with ordered Decision 030/031 dimensions:

1. grounding/safety hard failures
2. research usefulness failures
3. falsifier quality failures
4. instability
5. latency/cost only when actually known for both

No `score = 0.37 quality + 0.22 cost`. Operator preference order, when present, is applied after hard filters and before quality comparison. Quality ties require operator selection unless the operator explicitly allows a stable adapter-id tie-break.

## Fallback

Unavailable-before-invocation may reconsider another configured runtime within `RoutingBudget`.

- `max_runtime_attempts` / `max_fallback_attempts`
- 0 = no allowance
- negative is invalid

`CONTENT_POLICY_BLOCKED` records `BLOCKED_POLICY` and does **not** hop to another runtime to evade a safeguard. Route attempts are recorded. There is no infinite fallback loop.

## Agent-runtime handling

Codex CLI and similar `AGENT_RUNTIME`s are not automatically selected for inference-only roles. If an agent runtime is explicitly allowed:

- capability set must be explicit
- unrestricted tools are rejected
- side-effect capabilities are disabled on the Research reasoning path
- authenticated session ≠ Core authorization
- Research reasoning does not inherit Worker authority

## Provenance

`RuntimeSelectionDecision` records policy version, selected identity if any, considered identities, reason codes, attempted runtimes, and attempt counters. Application writes this to AuditEvent. No secrets. No winner field. No aggregate model score.

## Confidence

**HIGH** that hard filters must precede preference and that policy-block must not bypass.

**HIGH** that routing is not authorization or Finding authority.

**MEDIUM** for later learned preference weights if empirical GATE 04B data justifies them.

## Constraints

1. Do not let the model vote for itself.
2. Do not introduce a scalar magic score.
3. Do not select an unavailable runtime.
4. Do not hop after `CONTENT_POLICY_BLOCKED`.
5. Do not treat Strix as a ModelRuntime.
6. Do not scrape credentials to make a runtime look available.

## Revisit triggers

- Empirical calibration of preference order from GATE 04B
- Explicit OAuth subscription adapter
- Local-model product transport
- Persistent routing attempt ledger beyond AuditEvent


# Decision 048 — Live Runtime Activation / GATE 04B

Status: **accepted with constraints** (GATE 11)

Date: 2026-08-17

Does not rewrite Decisions 001–047. Does not fabricate availability, auto-install CLIs, clone undocumented tokens, or count Strix as a ModelRuntime.

## Strategy

Discover whatever legitimate runtimes are actually present. If ≥2 ModelRuntime configurations are AVAILABLE, run the existing GATE 04A/04B-PREP harness as a paired comparison. If fewer than 2, GATE 04B remains PENDING. ScriptedModelPort does not count.

## Runtime discovery

Explicit probes only:

- API: SDK + SecretReference + model id
- SUBSCRIPTION_OAUTH: only if an explicit adapter exists (none in this gate)
- CLI/session: documented executable + `--version`; no credential scraping
- LOCAL_MODEL: configured endpoint env only; no localhost scanning
- EXTERNAL_AGENT: configured endpoint/process only
- STRIX: executable probe, reported separately, `counts_as_model_runtime=false`

Readiness: `AVAILABLE` / `UNAVAILABLE` / `CONFIGURED_NOT_READY`.

## CLI/session activation

Codex CLI is first-class. If present: documented version, constructed env, `shell=False`, diagnostic capability only, no unrestricted tools, no security execution during benchmark. If absent: UNAVAILABLE. Do not auto-install.

## API/OAuth/local/external handling

API adapters remain composition-root SecretReference based. OAuth is unimplemented and therefore UNAVAILABLE. Local and external contracts remain CONFIGURED_NOT_READY when an endpoint is named but product transport is deferred.

## Benchmark execution

`--discover` prints the matrix. `--discover-and-compare` executes a paired live comparison only when ≥2 ModelRuntime configurations are AVAILABLE. Same suite fingerprint, instruction fingerprints, evaluator versions. Repeated runs required for authoritative PASS. No `WINNER`. Provider/runtime failures stay separated from research quality. Immutable reports unchanged.

## GATE 04B criteria

- **PASS:** ≥2 legitimate real runtime configurations executed comparably with repeated runs and no harness leakage
- **PENDING:** fewer than 2 available or executed
- **NEEDS_REVIEW:** leaked/incomparable comparison

Do not weaken this to finish the roadmap. Availability without execution is not PASS.

## Availability reporting

Kind matrix plus per-configuration reasons. Development suite comparison must be labelled development; sealed holdout remains external or UNAVAILABLE and is not unseen generalization.

## Confidence

**HIGH** that GATE 04B must not PASS on one live runtime or on scripted baselines.

**HIGH** that Strix must not contaminate the model comparison.

**MEDIUM** for later OAuth and local-model product activation.

## Constraints

1. Do not fabricate AVAILABLE.
2. Do not auto-install Codex/Strix.
3. Do not scrape undocumented credentials.
4. Do not call a development suite an unseen holdout.
5. Do not print secrets or `WINNER`.
6. Do not count Strix as a ModelRuntime configuration.

## Revisit triggers

- Second live runtime actually present in the developer environment
- Sealed holdout path configured outside the workspace
- Local-model product transport
- OAuth subscription adapter



# Decision 049 — Mature Autonomous Research Orchestration

Status: **accepted with constraints** (GATE 12)

Date: 2026-08-17

Does not rewrite Decisions 001–048. Autonomous != unbounded. This is not `while True: attack()`.

## Strategy

Zest can repeatedly observe → reason → select → plan → authorize → execute → evaluate → remember under explicit bounded policy. The controller coordinates existing use cases and does not duplicate domain logic. Model output cannot recursively spawn agents; a new hypothesis/experiment becomes explicit domain state and re-enters the same pipeline.

## Ownership

- Research: reasoning, opportunities, hypotheses, invariant/chain/differential semantics, next-direction proposals, orchestration *policy* (`next_cycle_action`)
- Application: `AutonomousResearchController` — one controller, one ResearchRun
- Core: authorization / scope / budget / approval only
- Platform: scheduling/process infrastructure
- Workers / Strix: execution only, after Core ALLOW

The orchestrator is not inside Research Brain. Research cannot become execution authority. The orchestrator cannot send arbitrary shell strings.

## Controller

Name: **AutonomousResearchController**.

Cycle: reload durable state → ResearchContext (via existing propose path) → select opportunities → generate/falsify/admit where needed → ExperimentPlan → Core authorize → Worker/Integration → Transition A → assessment → Transition B only when the evidence path warrants it → persist checkpoint → CONTINUE / PAUSE / COMPLETE / BLOCKED / REQUIRE_HUMAN_REVIEW.

No shortcut around existing gates. Candidate/Verification/Finding remain separate use cases.

## State machine

Durable `research_orchestration` checkpoint (mutable status) plus append-only `research_cycle`. AuditEvent is not workflow state. PostgreSQL is not a message broker. No giant mutable JSON blob.

States: READY, RUNNING, PAUSED, WAITING_HUMAN, BLOCKED, BUDGET_EXHAUSTED, COMPLETED, FAILED_OPERATIONAL.

FAILED_OPERATIONAL is not a research conclusion. `VULNERABILITY_FOUND` is not an orchestration state. Finding existence is separate domain state and does not automatically stop the run unless policy says so.

## Bounds

Hard limits, required, no unlimited defaults. 0 = no allowance. Negative is invalid.

max cycles, max experiments, max model calls, max Worker invocations, max elapsed duration, max selected opportunities, max chain depth (existing), max runtime fallback, side-effect ceiling, budget ceiling (IssuedBudget + ledger).

## Stop conditions

COMPLETED_NO_MORE_OPPORTUNITIES, BUDGET_EXHAUSTED, MAX_CYCLES_REACHED, MAX_DURATION_REACHED, REQUIRE_HUMAN_REVIEW, NO_COMPATIBLE_RUNTIME, CORE_BLOCKED, OPERATOR_PAUSED, OPERATOR_CANCELLED, OPERATIONAL_FAILURE, CONTENT_POLICY_BLOCKED (no bypass hop).

## Human control

PAUSE / RESUME / CANCEL are permanent. Cancel must not fabricate completed execution. UNKNOWN_OUTCOME must not be treated as “side effect did not occur”. Human Review gates remain mandatory.

## Crash / restart / idempotency

Reload durable state. Do not trust RAM. AUTHORIZED never dispatched may resume the same attempt. DISPATCHING uses existing UNKNOWN_OUTCOME semantics. Do not blindly repeat side-effectful work. Existing per-entity uniqueness prevents duplicate Experiment/Attempt/Observation/Evidence/Candidate/Finding. No global payload-hash dedup.

## Runtime / Strix

Decision 047 routing: the orchestrator asks routing policy; it does not pick “GPT/Codex/Claude”. CONTENT_POLICY_BLOCKED is a normal runtime outcome, not a fallback loop. Strix is invoked only after Core ALLOW, allowlisted capabilities only. Scope change requires Core re-evaluation.

## Confidence

**HIGH** that autonomy must stay bounded and that Core remains the execution gate.

**HIGH** that orchestration state is not Finding state.

**MEDIUM** for later multi-run campaign controllers.

## Constraints

1. Do not put the controller in Research Brain.
2. Do not give orchestration Core authority.
3. Do not unbounded-loop.
4. Do not auto-retry UNKNOWN_OUTCOME.
5. Do not let models spawn hidden child agents.

## Revisit triggers

- Multi-run campaign controller
- Non-diagnostic capability packs
- Automatic Transition B / Candidate creation under explicit policy


# Decision 050 — Production Hardening / Operational Readiness

Status: **accepted with constraints** (GATE 13)

Date: 2026-08-17

Does not rewrite Decisions 001–049. GATE 13 PASS is diagnostic operational architecture, not production-ready security research.

## Strategy

Move from “correct in integration tests” toward operationally survivable diagnostic runs. Do not claim PRODUCTION_READY unless operational *and* live-research gates that this environment has not passed actually pass.

## Budget ledger

Append-only `BudgetConsumption` reconstructs usage. IssuedBudget remains the immutable envelope. Resource types: MODEL_CALL, WORKER_INVOCATION, REQUEST, EXECUTION_TIME, ARTIFACT_BYTES. COST is not recorded without an issued cost unit. 0 allowance remains no allowance. Duplicate `(budget_id, request_id, resource_type)` does not double-charge. PostgreSQL uses row lock + ledger sum before insert.

## Secrets

Decision 013 stands. `SecretPort`: SecretReference → resolver. Values never SoR, Evidence, ResearchContext, AuditEvent, or benchmark reports. Adapters: LOCAL_DEV, ENV_REFERENCE. ENV is not a production secret manager. Future: OS credential store / external manager. OAuth/CLI sessions stay runtime-owned; tokens are not copied.

## Runtime supervision

Health: HEALTHY, DEGRADED, UNAVAILABLE, AUTH_REQUIRED, RATE_LIMITED, BLOCKED_POLICY, UNKNOWN. Health != research truth. No secret output.

Strix supervisor: detect/version/invoke envelope, timeout, cancel, bounded output, exit classification, cleanup. Do not auto-install. Unavailable = UNAVAILABLE. No production scanning claim.

Codex CLI supervisor: detect/version/auth readiness, harmless probe, controlled cwd, bounded output, cancel, process classification. Do not scrape private tokens.

SUBSCRIPTION_OAUTH = NOT_IMPLEMENTED. Compatibility/support risk: a future adapter must be explicit and must not clone undocumented Strix subscription backends.

## Observability

Decision 012. Structured events with correlation/run/experiment/request/runtime/cycle/duration/outcome. No secrets. AuditEvent remains separate. Evidence remains separate. In-memory metrics are not authoritative domain state.

## Reconciliation

Classify AUTHORIZED-never-dispatched, DISPATCHING unknown, stale RUNNING checkpoints, runtime unavailable. Resolutions: SAFE_TO_RETRY, UNKNOWN_OUTCOME, REQUIRE_HUMAN_REVIEW, MARK_OPERATIONAL_FAILURE, NO_ACTION. Side-effectful UNKNOWN remains fail-closed. Do not guess external side effects.

## DB / artifact operations

`scripts/zest_db.py`: migrate to head, schema version, ping. Backup/restore remain operator procedures, not a DBA product. No SQLite fallback.

Local artifact store: bounded paths, no traversal, content hash, atomic write, size limits, evidence-linked delete refusal. Bytes stay off the DB.

## Operator status

`zest status` prints PostgreSQL, Worker, Model Runtimes (including SUBSCRIPTION_OAUTH=NOT_IMPLEMENTED), Strix, Auth, Orchestrator, Budget, Reconciliation, Observability, GATE 04B, and maturity flags. No secrets.

## Confidence

**HIGH** that GATE 13 must not be called real security validation.

**HIGH** that PRODUCTION_READY stays no while GATE 04B is PENDING and no authorized security target has been exercised.

**MEDIUM** for later external secret managers and OAuth adapters.

## Constraints

1. Do not use a mutable counter as sole budget truth.
2. Do not put secrets in SoR/logs/context.
3. Do not scrape OAuth/CLI tokens.
4. Do not auto-install Strix or Codex.
5. Do not treat health as a research conclusion.
6. Do not use AuditEvent as a queue.

## Revisit triggers

- External secret manager adapter
- Explicit subscription OAuth adapter
- Production scheduler / process supervisor product
- Real authorized security-research campaign


# Independent QA addendum (2026-08-17)

Independent full-repository QA discovered implementation gaps after GATE 12/13 diagnostic architecture PASS. Decisions 001–050 remain locked history. They are not rewritten as though the defects never existed.

GATE 12 and GATE 13 return to **VALIDATION_PENDING** until the required PostgreSQL, clean-install, and runtime tests actually run. GATE 04B remains PENDING. Do not fabricate PASS.


# Decision 051 — Durable Orchestration Recovery

Status: **accepted with constraints** (QA remediation; GATE 12 VALIDATION_PENDING)

Date: 2026-08-17

Does not rewrite Decisions 001–050.

After orchestration creation, `ResearchOrchestrationRecord` is the authoritative configuration. `step`/`resume`/restart reconstruct `EffectiveOrchestrationConfiguration` → `OrchestrationBounds` from the persisted row. Subsequent commands may assert bounds; exact mismatch fails closed. Silent widening is forbidden.

Immutable control fields include max cycles/experiments/model calls/worker invocations/elapsed duration/selected opportunities/runtime fallback, side-effect ceiling, budget id, routing policy version, ResearchRun binding, and scope fingerprint.

A canonical SHA-256 `configuration_fingerprint` is persisted and verified on reload. Fingerprint mismatch is an operational integrity error. The fingerprint is not authorization.

Durable cycle phases are explicit (CYCLE_READY through CYCLE_COMPLETE). Materialized Hypothesis/Experiment/Attempt advancement commits with the corresponding checkpoint where practical. Restart reloads phase first and resumes existing objects. DISPATCHING remains UNKNOWN_OUTCOME. Orphans are classified, never auto-deleted.


# Decision 052 — Pre-invocation Budget Enforcement

Status: **accepted with constraints** (QA remediation; GATE 13 VALIDATION_PENDING)

Date: 2026-08-17

Does not rewrite Decision 050.

Every ModelPort `complete()` used by autonomous orchestration reserves one MODEL_CALL on the append-only ledger **before** the external invocation. Generator and Falsifier are separate identities. Failed attempts still consume. Replay of the same invocation identity does not double-charge. A reserved attempt that crashes before the network may conservatively consume one allowance.

MODEL_CALL accounting is independent from Worker REQUEST accounting. Autonomous `max_model_calls` derives only from MODEL_CALL ledger rows.

PostgreSQL `insert_within_allowance` uses the locked issued-budget row (and locked orchestration row for MODEL_CALL) as allowance authority. The caller object is identity/context, not allowance truth.

Typed runtime outcomes are preserved. Generic MODEL_INVOCATION_FAILED is not mapped to CONTENT_POLICY_BLOCKED. CONTENT_POLICY_BLOCKED does not cause safety-bypass runtime hopping.


# Decision 053 — Installable Runtime Distribution

Status: **accepted with constraints** (QA remediation; GATE 13 VALIDATION_PENDING)

Date: 2026-08-17

The installed wheel/sdist must not depend on repository-root layout. Concrete integrations live under `zest.integrations`. The diagnostic Python Worker is invoked as `python -m zest.worker_runtime.python`. Canonical contracts and development benchmark fixtures are package resources loaded with `importlib.resources`. SEALED_HOLDOUT is not bundled.

Research must not import concrete integrations. Application depends on ports/contracts. Composition root may import integrations.

`scripts/export_source.py` / `zest export-source` produces a deterministic archive excluding `.git`, `.venv`, secrets, coverage, and runtime artifacts. Clean-install smoke is mandatory for final GATE 13 PASS.


# Decision 054 — Runtime Operational Truthfulness

Status: **accepted with constraints** (QA remediation; GATE 13 VALIDATION_PENDING)

Date: 2026-08-17

Readiness is structured: INSTALLED, VERSION_KNOWN, AUTH_READY, DEPENDENCIES_READY, DIAGNOSTIC_READY, MODELPORT_COMPATIBLE, BENCHMARK_COMPATIBLE. One `available=True` is not sufficient. Only BENCHMARK_COMPATIBLE ModelRuntime configurations count toward GATE 04B. ScriptedModelPort never counts. Strix is not a ModelRuntime.

Codex `--version` is not AUTH_READY and not BENCHMARK_COMPATIBLE. A diagnostic adapter that ignores `ModelCallRequest` is not MODELPORT_COMPATIBLE. Documented Codex exec uses stdin prompt, `--sandbox read-only`, structured JSON validation, timeout/cancel, and process-tree supervision. Tokens are not scraped. Codex/Strix are not auto-installed. `SUBSCRIPTION_OAUTH` remains NOT_IMPLEMENTED.

Worker HEALTHY requires a real diagnostic protocol probe. Strix executable without sandbox/dependency readiness is not full HEALTHY.

Secret protection is recursive. Safe opaque `SecretReference` / `SessionReference` values are permitted. Exception serialization omits headers/tokens/bodies.

Process-tree supervision terminates descendants (POSIX process group; Windows Job Object). Operator status uses `ZEST_DATABASE_URL` for POSTGRESQL and reports TEST_POSTGRESQL separately. Benchmark provenance records dirty/untracked source; dirty runs are DEVELOPMENT / NON_AUTHORITATIVE.

Provider error classification prefers structured class/code over HTTP-status heuristics. HTTP 403 is not automatically AUTH_FAILED.





