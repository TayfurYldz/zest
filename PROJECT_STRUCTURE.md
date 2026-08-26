# Zest — Project Structure

This document defines the high-level architectural boundaries of Zest.

It is a design-phase artifact. It does not choose a technology stack, create folders, or prescribe implementations.

Zest is not an AI vulnerability scanner. The LLM is a reasoning component, not the system.

Core loop:

- AI reasons.
- System decides.
- Tools execute.
- Evidence proves.
- Human validates.

In this architecture, "Tools execute" means execution proceeds through the Tools contract, Worker runtime, and Integration adapter path. Workers are the only layer that may perform side effects.

```
Zest
├── Core
├── Research
├── Application
├── Data
├── Tools
├── Workers
├── Integrations
├── Platform
└── Interface
```

Trust hierarchy and dependency direction are separate concepts. They are defined after the layer boundaries.

---

## Core

**Purpose:** Own the system's invariant control logic. Core is the highest-trust layer and the final authority for control decisions.

**Responsibility:** Decide whether a requested operation is allowed. Core evaluates `request → policy → scope → budget → execution`. Default posture is DEFAULT DENY. Core authorizes execution; it does not perform the side effect itself.

Core is the final authority for:

- authorization
- scope
- policy
- budget ownership
- approval semantics
- execution authorization

**Contains:**

- authorization boundary evaluation
- AuthorizationSource and Program as required system inputs
- ScopeRules
- policy and permission contracts
- budget policy ownership: max requests, max runtime, max tool calls, max concurrency, and related limits
- approval requirement, approval state, and approval decision semantics
- FindingProposal → Human Review → Core Approval → Finding promotion contract
- execution control contracts
- audit semantics for control decisions

AuthorizationSource, ScopeRules, out-of-scope assets, and Program context are enforced in Core's policy/scope layer.

Ambiguous scope must resolve to **DENY** or **REQUIRE_HUMAN_REVIEW**.

If a valid AuthorizationSource or resolvable effective ScopeRules are missing, active execution does not start.

**Must not:**

- host security tool implementations
- host model-specific logic
- host vendor-specific integrations
- depend on Research
- import concrete Integrations or concrete Platform implementations
- treat LLM output as authorization, scope, policy, or budget
- declare a target authorized from model reasoning
- accept "probably in-scope", "same company", or "same domain family" as authorization
- start active testing when AuthorizationSource or effective ScopeRules are missing
- treat derived targets (redirects, discovered assets, new subdomains) as automatically authorized
- perform side effects
- let budget limits be raised, changed, or bypassed after they are issued

---

## Research

**Purpose:** Own research intelligence and reasoning-domain logic.

**Responsibility:** Plan, hypothesize, prioritize, and propose controlled work. Produce untrusted structured proposals. Research may depend on Core contracts. Research cannot change Core decisions.

Research may produce a Candidate, a Verification result, and a FindingProposal. Research must not create a Finding.

Promotion flow:

```
Research
→ Candidate
→ Verification
→ Candidate VALIDATED
→ FindingProposal
→ Human Review
→ Core Approval
→ Finding
```

**Contains:**

- planning
- context building
- hypothesis generation
- experiment planning
- verification logic
- Candidate proposals
- verification-result proposals
- FindingProposal creation from VALIDATED Candidates
- Evidence proposals
- adaptive reasoning
- chain reasoning
- prioritization

**Must not:**

- execute
- perform network, browser, shell, or other side effects
- change scope, policy, budget, or its own authority
- obtain direct shell, network, or browser authority
- treat its own claim as evidence or its own hypothesis as fact
- accept a Candidate or Finding on its own
- create a Finding directly
- bypass Core
- import concrete Integrations or concrete Platform implementations
- use LLM conversation history as durable system memory or authoritative persistent state

Research produces proposals only. Agent decision and actual tool execution remain separate layers.

---

## Application

**Purpose:** Own use-case coordination. Application sequences authorized work; it is not authority (Decision 022).

**Responsibility:** Coordinate Core public APIs, Research public APIs, Data ports/Unit of Work, and Platform ports. Map a successful infrastructure outcome into the next domain step. Fail closed.

The first use cases are Transition A ingestion of a completed Worker invocation (Decision 023), `ExecutePlannedExperiment` (Decision 024 / A7-lite), `ProposeResearchHypothesis` (Decisions 025–026 / A7 Research Brain v1), `PreparePlannedExperiment`, and `EvaluateExperimentFeedback` (Decisions 027–028 / GATE 03). Application invokes Worker only through an injected `WorkerPort`, after durable ExecutionAttempt intent is committed. Application invokes models only through an injected `ModelPort`. It does not dispatch a Worker from hypothesis proposal. Assessment does not create Evidence.

**Contains:**

- use-case sequencing
- port coordination
- transaction intent (explicit commit / rollback)
- Transition A normalizer registry and ObservationDraft production
- mapping WorkerInvocationOutcome into persistence
- durable ExecutionAttempt dispatch coordination
- WorkerRequest construction after Core ALLOW

**Must not:**

- own authorization, scope, budget, or Approval semantics
- convert DENY or REQUIRE_HUMAN_REVIEW into ALLOW
- import PostgreSQL adapters, SQLAlchemy, psycopg, subprocess, or LocalProcessWorkerAdapter
- create Evidence, Candidate, or Finding
- become a second Core

Core and Research must not import Application.

---

## Data

**Purpose:** Act as the system's truth and persistence layer.

**Responsibility:** Store durable research state in explicit data models, preserve provenance, and keep Observation, Hypothesis, Experiment, Evidence, Candidate, FindingProposal, and Finding strictly separate.

LLM conversation history is not durable system memory or authoritative persistent state.

Persistent state is held only in explicit data models.

Data persists accepted domain records. Data does not decide promotion.

**Contains:**

- assets
- observations
- artifacts
- hypotheses
- experiments
- worker results (untrusted durable output)
- evidence
- candidates
- finding proposals
- findings
- snapshots
- research memory (factual, episodic, procedural)
- provenance
- relationships
- AuthorizationSource references for research runs

**Must not:**

- use LLM conversation history as durable system memory or authoritative persistent state
- collapse Observation into Hypothesis, Hypothesis into Evidence, or Evidence into Finding
- mutate evidence in place when immutability is possible
- drop provenance for important records (source, timestamp, target, discovery method, related run, artifact reference)
- encode policy or execution authority
- decide Candidate → FindingProposal → Finding promotion
- leak persistence-implementation details into domain logic

Data stores Candidate, FindingProposal, and Finding records. Data does not decide promotion.

---

## Tools

**Purpose:** Define capability contracts / abstractions.

**Responsibility:** Describe what kinds of actions the system can request, without binding business logic to a specific tool and without performing the action.

Tools, Workers, and Integrations are distinct:

- **Tools:** capability contract / abstraction
- **Workers:** controlled execution runtime
- **Integrations:** concrete adapter / connector

**Contains:**

- HTTP capability contract
- browser capability contract
- shell capability contract
- file capability contract
- recon capability contract
- traffic capability contract
- typed capability contracts

**Must not:**

- perform side effects
- own or set policy, scope, budget, or authorization
- bind business logic to a specific tool product
- treat model output as a direct execution command
- host concrete vendor implementations

---

## Workers

**Purpose:** Perform controlled execution in isolated work units.

**Responsibility:** Run authorized execution requests after Core has allowed them. Workers are the only execution layer that may perform side effects.

Workers execute and produce WorkerResult. They do not declare truth records.

**Contains:**

- recon worker
- browser worker
- traffic worker
- code-analysis worker
- artifact-processing worker

**Must not:**

- determine, evaluate, or produce authorization
- determine scope
- bypass policy
- raise, change, or bypass budget limits
- write directly to the persistent Data truth layer
- declare Observation, Evidence, FindingProposal, or Finding records as truth
- treat derived or discovered targets as automatically authorized
- continue after a redirect, new hostname, discovered asset, new subdomain, or any other scope-changing event without a new Core decision

WorkerResult re-enters the system through two transitions:

**Transition A — Deterministic Ingestion**

```
Worker
→ WorkerResult
→ schema/integrity validation
→ deterministic normalization
→ Observation and/or Artifact
```

Rules:

- no semantic interpretation
- no hypothesis generation
- no evidence promotion
- no vulnerability inference

**Transition B — Evidence Admission**

```
Observation and/or Artifact
→ Research evaluation
→ Evidence proposal
→ explicit auditable evidence-admission transition
→ Evidence
```

Evidence is not created during WorkerResult ingestion.

On redirect, new hostname, discovered asset, new subdomain, or any event that requires a scope change:

1. Worker execution stops.
2. The Worker requests Core re-evaluation.
3. The Worker does not continue until a new authorization/policy decision arrives.

Worker/Platform runtime applies immutable budget limits issued by Core. It cannot raise, change, or bypass those limits.

---

## Integrations

**Purpose:** Isolate external systems and vendor/tool connections so they remain replaceable.

**Responsibility:** Provide concrete adapters used by Workers. Integrations are adapters, not the system, and cannot make authorization decisions.

Names such as Strix, Burp, n8n, and model providers are **examples of possible integrations**. They are not a v1 commitment and are not a decided dependency.

**Contains examples of possible integrations:**

- Strix, as a replaceable agent/tool runtime adapter
- Burp
- n8n
- external model providers
- notification systems
- bug bounty platform connectors

**Must not:**

- own Core or Research business logic
- become the memory, policy, authorization, evidence, or judgment truth layer
- make authorization, scope, policy, or budget decisions
- create vendor lock-in
- be imported by Core or Research as concrete implementations
- be treated as a committed product choice

---

## Platform

**Purpose:** Provide infrastructural capabilities the system needs to run, without becoming the research or control domain.

Platform has two logical roles:

**Platform contracts/capabilities:**

- configuration
- secrets access
- orchestration
- scheduling
- runtime isolation
- storage capability
- observability

**Concrete platform implementations:**

- the actual infrastructure technologies that will be chosen later

Core, Data, and Research must not depend on concrete Platform implementations. They may depend only on Platform contracts/capabilities.

Platform may provide enforcement primitives for issued budget limits. Platform is not the budget-policy owner.

Orchestration callers sit below Research in the trust hierarchy. They dispatch work already authorized by Core; they do not create authorization.

**Must not:**

- select or assume a specific product, framework, or vendor in this document
- own domain or business logic
- own authorization, research reasoning, evidence semantics, or budget policy
- put secrets into LLM context unless there is no alternative
- bypass Core security boundaries
- leak persistence-implementation details into domain logic

---

## Interface

**Purpose:** Let humans and external systems interact with Zest.

**Responsibility:** Expose requests, review, approval screens, and reporting. Human review is a permanent part of the system. AI recommendation and final judgment stay separate.

Interface presents approval screens/flows. Core owns approval requirement, approval state, and approval decision semantics. Interface cannot define approval logic on its own.

Human Review gives final acceptance/judgment for FindingProposal. Finding is created only after that decision is recorded as Core Approval.

**Contains:**

- API
- dashboard
- CLI
- human review
- approval screens/flows
- reporting interfaces

**Must not:**

- own business logic
- own approval requirement, approval state, or approval decision semantics
- bypass Core
- treat an operator shortcut as a policy exception unless Core records an explicit approval
- collapse AI recommendation into final judgment

---

## Execution Ownership

One rule, applied across layers:

- **Application** coordinates use cases. It asks Core. It does not execute. It does not become authority.
- **Research** produces proposals. It does not execute. It runs Candidate/Verification/FindingProposal/Evidence-proposal domain logic. It does not create Findings.
- **Core** decides authorization, policy, scope, and budget, and owns approval semantics. It does not perform the side effect.
- **Tools** define capability contracts. They do not perform side effects. They do not set policy.
- **Workers** perform authorized execution and produce WorkerResult. They are the only execution layer that may perform side effects. They do not declare truth records.
- **Integrations** are concrete adapters used by Workers, for example a Strix, Burp, or provider adapter. They cannot make authorization decisions on their own.
- **Data** persists accepted domain records after Transition A/B. It does not decide promotion.
- **Platform** provides contracts and later concrete infrastructure. It is not an execution-authorization layer.
- **Interface** presents interaction and approval UI. It is not an execution layer.

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

## Trust Hierarchy

Trust hierarchy is not the same as dependency direction.

Core is the highest-trust layer.

Logical trust order:

```
Core
↑
Research
↑
Interface / orchestration callers
```

Core is the final authority for authorization, scope, policy, budget ownership, approval semantics, and execution authorization.

Research cannot change Core decisions.

No layer may bypass Core security boundaries.

---

## Dependency Direction

Concrete implementation dependencies must be built through abstractions/contracts.

Logical dependency direction:

```
Interface
→ Application
→ Research
→ Core
→ Tool / Data / Platform contracts
```

Concrete implementations:

- Workers
- Integrations
- Platform adapters

These implementations implement the contracts defined above them.

Rules:

- Core must not depend on Research or Application.
- Research may depend on Core contracts and must not depend on Application.
- Application may depend on Core, Research, Data ports, and Platform ports.
- Application must not depend on concrete Integrations or concrete Platform/Data adapters.
- Core and Research must not depend on concrete Integrations.
- Core, Research, and Data must not depend on concrete Platform implementations.
- Workers and Integrations implement contracts defined above them.
- Data persistence implementation must not leak into domain logic.
- No circular dependency.

---

## Cross-Layer Rules

1. Core is the highest-trust layer.
2. Research proposes; it does not execute and cannot change Core decisions.
3. Tools provide capability contracts; they do not set policy and do not perform side effects.
4. Workers execute authorized work; they do not determine authorization, scope, or budget policy.
5. Integrations are replaceable adapters, not committed vendors.
6. Data behaves as the truth layer and does not decide promotion.
7. Interface does not own business logic or approval semantics. Application coordinates use cases without becoming authority.
8. No layer may bypass Core security boundaries.
9. Changing a model, provider, or tool must not break the domain architecture.
10. Core must not depend on Research. No circular dependency.

Every active operation still follows `request → policy → scope → budget → execution`.

External content (web, API responses, email, documentation, and other outside sources) is untrusted input. Prompt injection must never change system policy.

Every research run must carry a traceable reference to the AuthorizationSource it relies on.

Important decisions and tool executions must remain auditable: workflow, agent/model, tool, target, policy decision, budget, evidence, and result.

Finding promotion:

```
Research
→ Candidate
→ Verification
→ Candidate VALIDATED
→ FindingProposal
→ Human Review
→ Core Approval
→ Finding
```

- Research may propose a Candidate, a Verification result, and a FindingProposal.
- Research must not create a Finding.
- Data stores the records and does not promote them.
- Core applies approval semantics and records the Approval decision.
- Human Review decides; Core records that decision.
- Candidate VALIDATED != Finding.
- FindingProposal != Finding.

This keeps the authority chain: AI/Research proposes. Core authorizes/controls. Workers execute. Evidence supports. Research validates Candidate state. Human Review decides. Core records Approval. Finding is created only after approval.

---

## Strix Placement

Strix is not Zest.

Strix is an example of a possible integration. It is not a v1 commitment and is not a decided dependency.

```
Zest
↓
controlled execution boundary
↓
tool contract / worker runtime
↓
Strix integration adapter
```

If Strix is used as a reasoning runtime, its output re-enters as:

```
Strix output
→ UNTRUSTED STRUCTURED PROPOSAL
→ Research validation
→ Core-controlled execution path
```

If Strix is used as a tool runtime, it is reached only as a Worker-used Integration adapter after Core has authorized the operation.

Strix is replaceable.

Strix is not:

- policy
- an authorization source
- a memory truth layer
- an evidence truth layer
- final judgment

Core and Research must not depend on Strix and must not import it as a concrete implementation.

---

## Unresolved Architecture Decisions

The following are not decided. No solution is chosen here.

- programming language
- primary database
- workflow/orchestration technology
- message broker
- artifact storage
- model providers
- frontend framework
- deployment model
