# Zest — Technical Requirements

This document defines the technical needs that later technology choices must satisfy.

It is not a stack decision. It does not choose a language, database paradigm, database product, orchestrator, broker, HTTP/RPC, graph store, vector store, Docker, Kubernetes, model provider, framework, or deployment model.

Strix is not a mandatory dependency.

It must be read with:

- `.cursor/rules/zest.mdc`
- `PROJECT_STRUCTURE.md`
- `DOMAIN_MODEL.md`

Authority chain:

- AI/Research proposes
- Core authorizes/controls
- Workers execute
- Evidence supports
- Research validates Candidate state
- Human Review decides
- Core records Approval
- Finding is created only after approval

---

## Requirement Classification

Requirements fall into two categories.

### Hard Architectural Requirements

Invariants required by the domain, security, and authority model.

If the system does not provide these, the architecture is wrong.

### Preferred First-Implementation Properties

Strong preferences for the first local implementation. They are not domain invariants.

They must not lock a technology choice without a justified trade-off.

A preferred property may change when a concrete trade-off is stated.

This document does not force:

- a relational database
- Temporal or any workflow engine
- a queue/broker
- HTTP or RPC
- a graph database
- a vector database
- Docker or Kubernetes
- a specific programming language
- Strix as a required integration

---

## 1. Current Development Constraints

These describe the current developer setup. They are not production architecture requirements, not a programming-language mandate, not a deployment-topology mandate, and not a communication-technology mandate.

- The developer workstation currently uses Windows + Cursor.
- Zest source currently lives in the main desktop project.
- Kali Linux / WSL is the expected initial security-tool integration environment.
- Strix and other security/recon tools may initially run there.

The main Zest architecture must not depend on Kali or Strix.

The worker execution environment must remain replaceable.

A production deployment model has not been chosen.

---

## 2. Hard Architectural Requirements

The system is wrong if it cannot provide the following.

### Authority and security

- explicit authorization
- DEFAULT DENY
- Core authority boundaries
- Worker cannot self-authorize
- Worker cannot determine scope
- Worker cannot raise, change, or bypass budget
- Worker cannot write truth directly
- no concrete provider or security tool owns domain logic
- secrets/model-context separation
- no direct LLM → shell/network
- prompt-injection isolation
- redirect/discovered-asset re-authorization
- least privilege
- Worker isolation as a property, not as a chosen container/VM product

Authorization concepts remain:

- Program = engagement context
- AuthorizationSource = explicit/written authority instrument
- ScopeRule = allow / deny / out-of-scope rules

Active research requires a valid AuthorizationSource and resolvable effective ScopeRules. Derived Assets are not automatically authorized. Asset carries no authorization state.

### Standing security / domain constraint

Finding acceptance always requires Human Review and Core-recorded Approval.

```
Candidate VALIDATED
+ FindingProposal
+ Human Review
+ Core Approval APPROVE
= Finding
```

This does not become an autonomous acceptance system if scale grows, unless the domain model is explicitly changed.

Research cannot create a Finding. Candidate VALIDATED != Finding. FindingProposal != Finding.

A Finding must never be created solely from:

- model output
- scanner signal
- WorkerResult
- a confidence score

INCONCLUSIVE is a valid Candidate outcome. Insufficient evidence must not be promoted to increase Finding count (Decision 017).

### Truth and execution boundaries

- Transition A / Transition B separation
- Worker returns results only through the WorkerResult boundary
- Worker cannot produce Evidence, FindingProposal, or Finding
- provenance preservation
- execution correlation and auditability
- replaceable integrations
- Core and Research stay off concrete infrastructure
- modular boundaries among Core, Research, Data, Tools, Workers, Integrations, Platform, and Interface

**Transition A — Deterministic Ingestion**

```
Worker
→ WorkerResult
→ schema/integrity validation
→ deterministic normalization
→ Observation and/or Artifact
```

No semantic interpretation, hypothesis generation, evidence promotion, or vulnerability inference in this transition.

**Transition B — Evidence Admission**

```
Observation and/or Artifact
→ Research evaluation
→ Evidence proposal
→ explicit auditable evidence-admission transition
→ Evidence
```

Evidence is not created during WorkerResult ingestion.

On redirect, new hostname, discovered Asset, or new subdomain, execution stops and waits for Core re-evaluation.

### Contracts

Cross-boundary contracts must be:

- explicit
- versionable
- machine-validatable
- testable
- documented for backward/forward compatibility where needed

Static typing may be a preferred implementation property. It does not mandate a programming language or type system.

### Durable authoritative state

Authoritative durable domain state and accepted durable WorkerResult records must remain reachable after:

- process restart
- worker crash
- normal host restart

as long as the durable storage itself remains reachable.

The following state must not be lost while durable storage remains available:

- Program
- AuthorizationSource
- ScopeRule
- ResearchRun
- Budget state
- Asset
- Observation
- Hypothesis
- Experiment
- WorkerResult
- Artifact identity/reference and artifact metadata
- Evidence
- Candidate
- Verification records
- FindingProposal
- Finding
- Approval
- Snapshot
- ChangeEvent
- AuditEvent

LLM conversation history is not durable state and is not authoritative persistent state.

WorkerResult may be durable. Durable != trusted. Durable WorkerResult remains untrusted execution output.

These are **not** hard requirements:

- exact mid-step workflow resume
- transparent continuation from an arbitrary instruction
- specific workflow replay semantics

Those belong under preferred first-implementation properties and do not choose an orchestrator.

### Data

The storage strategy must preserve:

- distinct domain records
- lifecycle state
- provenance links
- relationships
- audit history
- integrity constraints
- indexing/queryability
- transactional consistency where one authoritative decision updates multiple related records

Domain concepts do not have to map one-to-one onto any storage shape.

The database paradigm is unresolved: relational, document, graph, or mixed/polyglot.

Domain relationships may be graph-shaped. That does not require a graph database.

Search, vector, and graph systems may later exist as companion capabilities. Research Memory does not require any of them.

### Artifacts

The domain layer must be able to preserve, for an Artifact:

- stable identity/reference
- provenance
- integrity metadata
- lifecycle metadata

Artifact attachment is not Evidence admission. A Finding's supporting-Evidence requirement is not satisfied by attaching an Artifact.

It is not a hard requirement that artifact bytes live in a separate product or store.

Primary domain store and artifact byte store may be the same or different. That topology is unresolved.

### Research Memory

Research Memory must:

- operate over authoritative domain records
- not become a shadow truth database
- support Factual / Episodic / Procedural retrieval
- support provenance-aware retrieval
- preserve target/program boundaries

Semantic retrieval is an optional later capability. A vector database is not required.

### Model / tools

The model layer must:

- keep providers replaceable
- treat model output as an untrusted structured proposal
- keep secrets out of model context where possible
- make model budget/cost observable
- make prompt/context provenance possible

Model output is not fact, Evidence, authorization, or Finding acceptance.

Tools must sit behind capability contracts.

Strix is an optional, replaceable integration. It is not a mandatory dependency. It may be used as a reasoning runtime or a tool runtime. It is not Core policy, a Research Memory truth layer, Evidence authority, a direct system-authority owner, or Zest itself.

If used as a reasoning runtime, Strix output re-enters as an untrusted structured proposal, then Research validation, then a Core-controlled execution path.

If used as a tool runtime, output returns through the WorkerResult boundary after Core authorization.

### Observability

The system must later be able to observe at least:

- ResearchRun
- Experiment
- Worker execution
- tool calls
- authorization decisions
- policy decisions
- budget usage
- retries
- failures
- model calls
- artifact creation
- Evidence admission
- Candidate transitions
- Approval
- Finding creation

No logging, metrics, or tracing product is chosen here.

Audit history should be immutable/append-oriented where practical. AuditEvent is not Evidence, Finding, or AuthorizationSource.

---

## 3. Preferred First-Implementation Properties

These are preferred for the first local implementation. They may change with a stated trade-off. They do not choose a product.

- simple local development
- minimal distributed complexity until a concrete execution-topology requirement justifies more
- local/in-process communication before a distributed communication plane
- local/mock implementations before distributed deployment
- integrations replaceable with mocks/fakes
- testable component boundaries
- a local test environment manageable by a single developer
- resume from the last durable checkpoint where practical
- durable workflow progress where valuable
- explicit retry semantics
- replay-aware execution
- duplicate-safe processing
- idempotent handlers where practical
- retry-capable orchestration where valuable
- timeout, cancellation, and concurrency limits at execution boundaries
- static typing
- structured model output
- ability to route models and to separate cheaper models from stronger reasoning models
- separate large/binary artifact byte storage
- optional later semantic retrieval
- easy testing against the initial Kali/WSL worker environment without making that environment the architecture

### Research quality (preferred)

Where a Candidate depends on a behavioral difference, Verification should prefer differential / control observations over a single isolated success response.

The system should actively seek **disconfirming** evidence for high-impact Candidate claims where practical (Decision 017).

Hypothesis generation and verification independence may use a different reasoning pass, deterministic checks, a different Worker, Human Review, or a later verifier model. A second model provider is not required in v1 (Decision 008).

Numeric confidence, if added later, must not replace Verification or Human Review. No universal threshold is specified here.

These preferences do not move research intelligence into Core (Decision 018).

Execution boundaries must be replay-aware.

Where an operation may be retried or redelivered, the system should support:

- correlation identity
- duplicate detection
- a safe retry policy
- explicit non-retryable classification
- side-effect awareness

Idempotency is preferred where practical. External operations are not assumed to be naturally idempotent.

This does not force a specific orchestrator.

Do not introduce a distributed communication plane until a concrete execution-topology requirement justifies it.

Local/in-process communication is preferred for the first implementation.

If a remote-worker need appears, broker, RPC, HTTP, workflow transport, and event transport may be re-evaluated. None is chosen here.

---

## 4. Cross-Environment Communication

The main Zest process and a worker environment may later be physically or logically separate. That topology is unresolved.

Any later communication model must be able to support:

- authenticated communication
- explicit contracts
- timeout
- retry
- cancellation
- result correlation
- run correlation
- auditability

This document does not choose HTTP, RPC, queue, event bus, or workflow engine.

---

## 5. Open Capacity Questions

These are not requirements. No numbers are invented here.

- expected number of Programs
- assets per Program
- active concurrent ResearchRuns
- concurrent Workers
- WorkerResults per day
- artifact volume
- retention period
- execution/control-plane message volume
- tool/result communication volume
- model calls per day
- acceptable recovery time
- acceptable latency
- expected single-user vs multi-user usage

---

## 6. Non-Goals For First Implementation

Do not treat these as required for a first implementation:

- Kubernetes
- multi-region
- massive horizontal scale
- dozens of microservices
- distributed database
- complex service mesh
- proprietary cloud dependency
- all security tools at once

They may be evaluated later if a measured need appears.

Fully autonomous Finding acceptance is not a deferred first-implementation item. It is a standing domain constraint: Finding acceptance always requires Human Review and Core-recorded Approval unless the domain model is changed.

---

## 7. Decision Drivers

When choosing technology later, evaluate in this initial order:

1. correctness
2. authorization/security boundaries
3. data durability/integrity
4. auditability/provenance
5. recoverability
6. implementation simplicity
7. testability
8. execution-environment interoperability, including replaceable local/remote execution environments and the initial Kali/WSL environment
9. replaceability
10. observability
11. performance
12. cost

This is an initial prioritization for the first architecture phase. It may change later with measurements.

Driver 8 does not make the current laptop or Kali/WSL topology a production requirement.

---

## 8. Open Technical Questions

These remain unresolved. No answer is chosen here.

- programming language
- primary database
- primary data paradigm: relational, document, graph, mixed/polyglot
- workflow/orchestration technology
- worker communication model
- worker topology: in-process, local-process, remote, mixed
- message/event transport
- artifact storage
- artifact byte-storage topology
- cache/ephemeral state
- model abstraction/provider strategy
- semantic retrieval/vector strategy
- frontend technology
- deployment model
- observability stack
- secrets management
- worker isolation technology
- operator identity/authentication model
- control-plane identity model
- worker identity/authentication model
- inter-component trust model
- contract serialization/validation strategy
