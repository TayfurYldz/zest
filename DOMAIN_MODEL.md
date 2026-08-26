# Zest — Domain Model

This document defines the conceptual domain of Zest.

It is not a database schema, ORM map, API design, or technology choice.

It defines which ideas are separate domain concepts, where their boundaries are, and how they live and change.

Core loop:

- AI reasons.
- System decides.
- Tools execute.
- Evidence proves.
- Human validates.

---

## 1. Domain Model Principles

- The domain model is technology-independent.
- Domain concepts do not have to map one-to-one to database tables.
- Domain concepts are independent of infrastructure implementations.
- Observation, Hypothesis, Experiment, Evidence, Candidate, FindingProposal, and Finding remain separate.
- AI output is not a fact and is not Evidence.
- Worker output is not Observation, Evidence, FindingProposal, or Finding.
- Truth and provenance must be preserved.
- Domain state is not held in LLM conversation history. LLM conversation history is not durable system memory or authoritative persistent state.
- Authorization and scope are not outside the domain. They are domain concepts related to the Core control model and must remain traceable on research activity.

Persistent domain state is held only in explicit data models.

Program != AuthorizationSource.
AuthorizationSource != ScopeRule.
FindingProposal != Finding.
Candidate VALIDATED != Finding.
Research Memory is not a truth source.

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

## 2. Core Domain Concepts

Ownership below is conceptual:

- **Core** owns control, authorization, policy, budget, approval, and promotion-contract semantics.
- **Research** owns reasoning and proposals.
- **Data** stores truth records and does not decide promotion.
- **Workers** produce `WorkerResult` values only. They do not declare truth.
- **Interface** presents human review and approval. It does not own approval semantics.

### Program

**Purpose:** Identify an engagement and hold program-level context.

**Meaning:** Engagement identity, engagement lifecycle, program metadata, and program-level context. A Program is not an Asset, not a Finding, and not an authorization instrument.

Program does not carry written scope content or authorization truth.

**Key attributes (conceptual):**

- identity
- engagement lifecycle status
- program metadata
- program-level context
- related AuthorizationSources

**Ownership:** Core owns Program control meaning. Data stores the Program record.

**Lifecycle:** created → active → paused / closed. A closed Program cannot start new active execution.

**Invariants:**

- Program != AuthorizationSource.
- Program does not contain written scope or authorization truth.
- A Program may relate to one or more AuthorizationSources.
- “Same company” or “same domain family” does not create a Program.
- LLM output cannot create or widen a Program.

**Relationships:** may have one or more AuthorizationSources; has ResearchRuns; relates to Assets only through authorized ResearchRuns.

---

### AuthorizationSource

**Purpose:** Represent the explicit written authority instrument behind allowed work.

**Meaning:** The explicit, written authority instrument a Program and its ResearchRuns rely on. It is required system input. It is not a ScopeRule and not a Program.

**Key attributes (conceptual):**

- identity
- linked Program
- reference to the written material
- immutable provenance
- effective period
- authority state: active / revoked / expired

**Ownership:** Core.

**Lifecycle:** recorded → active → revoked / expired / superseded. Supersession is a new record, not a silent rewrite.

**Invariants:**

- AuthorizationSource != ScopeRule.
- Cannot be produced or assumed by an LLM.
- Assumptions such as “probably in-scope” are not an AuthorizationSource.
- A Program may have more than one AuthorizationSource.
- No active ResearchRun without a valid AuthorizationSource.
- Every ResearchRun carries a traceable reference to the AuthorizationSource it relies on.

**Relationships:** belongs to a Program; may yield or carry one or more ScopeRules; referenced by ResearchRuns, AuditEvents, and execution authorization decisions.

---

### ScopeRule

**Purpose:** Express allow, deny, and out-of-scope constraints derived from authority.

**Meaning:** A Core-enforced rule of kind allow, deny, or out-of-scope. It is derived from an AuthorizationSource or explicitly bound to one. Ambiguous scope is not an implicit allow.

**Key attributes (conceptual):**

- identity
- rule kind: allow / deny / out-of-scope
- target pattern or asset selector
- bound AuthorizationSource
- precedence / explicitness

**Ownership:** Core policy/scope layer.

**Lifecycle:** recorded → effective → superseded. Not silently rewritten.

**Invariants:**

- AuthorizationSource != ScopeRule.
- One AuthorizationSource may produce or carry many ScopeRules.
- Enforced outside the model.
- Derived Assets are never automatically authorized.
- Missing valid AuthorizationSource or resolvable effective scope blocks active execution.

**Effective scope** is evaluated by Core from:

```
Program + active AuthorizationSource + ScopeRules
```

Ambiguous effective scope resolves to DENY or REQUIRE_HUMAN_REVIEW.

**Relationships:** bound to an AuthorizationSource; constrains ResearchRuns, Experiments, and execution authorization. Does not live as authorization truth on Asset.

---

### ResearchRun

**Purpose:** Represent one authorized research execution context.

**Meaning:** A bounded run of research activity under a Program, AuthorizationSource, evaluated effective scope, ResearchRun Budget, and initiator.

**Key attributes (conceptual):**

- identity
- Program
- AuthorizationSource
- initiator
- status
- time bounds
- ResearchRun Budget
- audit linkage

**Ownership:** Core owns run authorization. Data stores the run record. Research may propose work inside a run. Workers never create a run’s authority.

**Lifecycle:** proposed → authorization-checked → active → paused / completed / cancelled / blocked. Missing valid AuthorizationSource or resolvable effective scope prevents an active run.

**Invariants:**

- No active ResearchRun without AuthorizationSource.
- No execution inside a run without Core authorization.
- A run cannot widen its own scope or Budget.
- Derived targets discovered during a run are not automatically authorized.
- Effective scope is Core-evaluated, not stored as Asset truth.

**Relationships:** belongs to Program; references AuthorizationSource; carries ResearchRun Budget; contains Experiments, WorkerResults, Observations, Candidates, FindingProposals, Findings, and AuditEvents.

---

### Budget

**Purpose:** Bound execution with Core-issued limits.

**Meaning:** Immutable execution limits issued by Core. Runtime may enforce them. Runtime may not own them.

There are two conceptual scopes. ResearchRun Budget is always the superior authority.

#### ResearchRun Budget

The authoritative execution envelope for the run.

Conceptual limits:

- total requests
- total tool calls
- total runtime
- max concurrency

#### Experiment Budget

A sub-allocation issued from the parent ResearchRun Budget.

**Key attributes (conceptual):**

- scope: ResearchRun or Experiment
- parent ResearchRun Budget, when Experiment-scoped
- request / tool-call / runtime / concurrency limits
- issued-by / issued-at

**Ownership:** Core owns budget policy. Worker/Platform runtime applies issued limits as immutable constraints.

**Lifecycle:** issued → in-force → exhausted / superseded by a new Core-issued Budget. Exhaustion is an execution outcome, not an authorization failure and not a research conclusion.

**Invariants:**

- Experiment Budget cannot exceed ResearchRun Budget.
- Experiment Budget cannot widen parent limits.
- Experiment Budget cannot act independently of remaining parent budget.
- Workers cannot raise, change, or bypass Budget.
- Platform may provide enforcement primitives but is not the budget-policy owner.
- A new Budget requires a new Core decision.

**Relationships:** ResearchRun Budget attaches to ResearchRun; Experiment Budget attaches to Experiment and remains inside the parent envelope.

---

### Asset

**Purpose:** Name a researched resource identity/context.

**Meaning:** A resource identity/context record. An Asset does not say that it is authorized.

Authorization is a Core decision from AuthorizationSource + ScopeRule + current context.

Asset does not carry authorization state.

**Key attributes (conceptual):**

- identity
- kind (not a closed enum yet)
- locator / descriptor
- discovery method
- first-seen / last-seen
- provenance

**Ownership:** Data stores Assets. Research may propose Asset records after ingestion. Core decides whether an Asset may be acted on.

**Lifecycle:** discovered/proposed → recorded → updated by later Observations/Snapshots. Discovery does not imply authorization.

**Invariants:**

- Asset carries no authorization state.
- Asset ≠ AuthorizationSource.
- A discovered or derived Asset is not automatically authorized.
- Acting on an Asset requires a current Core authorization decision against effective scope.

**Relationships:** may have observed or derived AssetRelations; appears in Observations, Snapshots, Experiments, Candidates, and Findings; constrained by Core-evaluated ScopeRules, not by a status field on the Asset.

---

### AssetRelation

**Purpose:** Record an authoritative relationship between Assets.

**Meaning:** An authoritative relationship record. Only two bases are allowed. Inference is not an AssetRelation.

Allowed bases:

**Observed**

- supported by direct Observation
- Observation provenance is required

**Derived**

- produced by a reproducible deterministic transformation
- must be able to carry source inputs, transformation identity/version, and reproducible output

Inferred relationship must be represented as a Hypothesis, not written to the truth layer as AssetRelation.

There is no third basis named “validated relationship.”

**Key attributes (conceptual):**

- identity
- from-asset / to-asset
- relation kind
- basis: observed / derived
- supporting Observation, or source inputs + transformation identity/version
- provenance

**Ownership:** Data stores the relation. Research may propose a Hypothesis for an inferred relationship. Core/Data must not persist inference as AssetRelation.

**Lifecycle:** proposed → recorded → superseded / withdrawn. Withdrawal does not rewrite history.

**Invariants:**

- Inferred relation != AssetRelation fact.
- Observed AssetRelation requires Observation provenance.
- Derived AssetRelation requires deterministic, reproducible transformation provenance.
- A relation does not authorize either Asset.

**Relationships:** connects Assets; observed relations are supported by Observations; derived relations are supported by known inputs and transformation identity; inferred ideas become Hypotheses.

---

### Observation

**Purpose:** Record a directly observed fact.

**Meaning:** The deterministic representation of a directly observable signal created only by Transition A (WorkerResult ingestion). An Observation is not a claim, vulnerability, Hypothesis, Evidence, Candidate, or Finding.

There is no unnamed alternate Observation path. A later manual or imported Observation path would be a separate domain decision and is not defined here.

**Key attributes (conceptual):**

- identity
- target Asset or locator
- observed content / statement of fact
- source
- timestamp
- discovery method
- related ResearchRun
- related WorkerResult and/or Artifact references
- provenance

**Ownership:** Data. Created only by Transition A (WorkerResult ingestion). Workers cannot declare an Observation.

**Lifecycle:** ingested → recorded → immutable as a historical fact. Later contradiction is a new Observation or a deterministic ChangeEvent, not a silent edit.

**Invariants:**

- Observation ≠ Hypothesis.
- Observation ≠ Evidence.
- WorkerResult ≠ Observation.
- Model output ≠ Observation.
- Source and provenance are required.
- Semantic interpretation, vulnerability inference, hypothesis generation, and evidence promotion are not part of Observation creation.

**Relationships:** created only from WorkerResult via Transition A; may accompany Artifacts; may later be admitted as Evidence; may inform Hypotheses without becoming them.

---

### Hypothesis

**Purpose:** State a research claim that must be tested.

**Meaning:** A prediction or research assertion, not a fact and not a Finding. Inferred relationships are Hypotheses, not AssetRelations.

**Key attributes (conceptual):**

- subject
- claim
- rationale
- confidence / belief
- priority
- status
- related Assets
- related ResearchRun

**Ownership:** Research proposes and updates belief from Evidence. Data stores the Hypothesis. Core does not treat it as fact.

**Lifecycle:**

`PROPOSED → PRIORITIZED → TESTING → SUPPORTED / WEAKENED / REJECTED / INCONCLUSIVE`

Belief may be updated by positive or negative Evidence. Negative evidence is first-class.

Hypothesis outcome is independent of Experiment execution outcome.

**Invariants:**

- Hypothesis ≠ Evidence.
- Hypothesis ≠ Finding.
- A Hypothesis cannot authorize execution by itself.
- Model Suggestion may create a Hypothesis proposal; it does not make the claim true.
- Non-deterministic interpretation is Inference or Hypothesis, not Derived Fact.
- Experiment EXECUTION_FAILED != Hypothesis REJECTED.

**Relationships:** tested by Experiments; supported or weakened by Evidence; may generate Candidates; belongs to a ResearchRun.

---

### Experiment

**Purpose:** Evaluate a Hypothesis with a controlled test plan.

**Meaning:** The planned, constrained research action used to test a Hypothesis. The plan is not the Worker execution. Experiment execution outcome is not Hypothesis outcome.

**Key attributes (conceptual):**

- goal
- hypothesis link
- preconditions
- expected signal
- execution constraints
- approval requirements
- Experiment Budget
- execution lifecycle status

**Ownership:** Research owns the plan. Core owns authorization of execution. Workers execute only after Core authorization. Data stores the Experiment record.

**Execution lifecycle:**

```
PLANNED
→ AUTHORIZATION_CHECK
→ READY
→ RUNNING
→ EXECUTION_SUCCEEDED
  / EXECUTION_FAILED
  / BLOCKED
  / CANCELLED
  / BUDGET_EXHAUSTED
```

These states are execution results only.

Hypothesis outcome remains:

`SUPPORTED / WEAKENED / REJECTED / INCONCLUSIVE`

Do not collapse:

- **EXECUTION_FAILED** != Hypothesis REJECTED
- **BUDGET_EXHAUSTED** != negative evidence
- **BLOCKED** != research conclusion
- **Authorization failure / BLOCKED:** Core denied or required review; execution must not start or continue

**Invariants:**

- Experiment plan ≠ Worker execution.
- Experiment execution failure != Hypothesis rejection.
- No running Experiment without Core authorization.
- Experiment Budget cannot exceed ResearchRun Budget.
- An Experiment cannot widen ScopeRules or Budget.
- Redirect or derived-asset discovery requires Core re-evaluation before continuation.

**Relationships:** tests a Hypothesis; authorized under a ResearchRun; consumes Experiment Budget from the parent ResearchRun Budget; may produce WorkerResults; may yield Artifacts and later Observations.

---

### WorkerResult

**Purpose:** Capture the raw result of authorized Worker execution before any truth admission.

**Meaning:** Untrusted execution output returned through the controlled result boundary.

WorkerResult may be stored durably. Durable != trusted.

Durable storage may exist only for reproducibility, debugging, provenance, and audit.

**Key attributes (conceptual):**

- identity
- ResearchRun / Experiment link
- worker / capability reference
- target at execution time
- raw payload reference
- execution timestamps
- authorization/run-context reference
- Budget consumption report
- stop reason (execution succeeded, execution failed, blocked, redirect, derived asset, budget exhausted, cancelled)

**Ownership:** Produced by Workers. Held as untrusted output. Not owned as Data truth.

**Lifecycle:** returned → held at controlled result boundary → Transition A (ingestion) and/or retained as untrusted durable output → discarded/rejected with audit when invalid.

**Invariants:**

- WorkerResult ≠ Observation.
- WorkerResult ≠ Evidence.
- WorkerResult ≠ Finding.
- Durable WorkerResult != trusted fact.
- WorkerResult is not Factual Memory.
- Workers cannot declare truth records from a WorkerResult.
- WorkerResult can only become Observation/Artifact through deterministic ingestion.
- WorkerResult cannot directly become Evidence.

**Relationships:** produced under an Experiment/ResearchRun; may be ingested into Observation and/or Artifact; never admitted directly as Evidence.

---

### Artifact

**Purpose:** Hold raw supporting material.

**Meaning:** Raw supporting material such as response material, screenshot, captured traffic, file, browser trace, code fragment, schema, or log excerpt.

Artifact != Evidence.

**Key attributes (conceptual):**

- identity
- kind
- integrity information
- source
- timestamp
- related ResearchRun / WorkerResult
- target reference
- storage locator (conceptual, not a product)

**Ownership:** Data after Transition A. Workers may produce raw material; they do not promote it to Evidence.

An Artifact may be attached to a Candidate or Finding as contextual/supporting material.

Artifact attachment != Evidence admission.

Attaching an Artifact does not satisfy a Finding's supporting-Evidence requirement.

**Lifecycle:** captured → ingested by Transition A → retained / superseded as a new Artifact. Integrity metadata should be preserved.

**Invariants:**

- Artifact != Evidence.
- Observation != Evidence.
- Creating Evidence requires Transition B.
- Artifact attachment != Evidence admission.
- Attaching an Artifact to a Candidate or Finding does not satisfy the supporting-Evidence requirement.
- External content inside an Artifact remains untrusted input until admitted as Evidence, and remains untrusted as prompt/policy input even then.

**Relationships:** may originate from WorkerResult via deterministic ingestion; may later be admitted as Evidence; may attach to Observations, Candidates, FindingProposals, and Findings.

---

### Evidence

**Purpose:** Represent admitted, verifiable support for evaluating a Hypothesis or Candidate.

**Meaning:** A specific Observation and/or Artifact after it has been admitted, through an auditable transition, as verifiable support for a Hypothesis or Candidate evaluation.

Evidence is not created by WorkerResult ingestion.

Evidence may be positive or negative.

**Key attributes (conceptual):**

- identity
- polarity: positive / negative
- related Hypothesis / Candidate
- related Observation and/or Artifact
- related Experiment
- provenance
- timestamp
- target
- discovery method
- related ResearchRun
- admission record reference

**Ownership:** Data stores Evidence after Transition B. Research proposes Evidence admission. The admission transition is an explicit recorded domain transition. Core does not treat model assertions as Evidence. Human Review may rely on Evidence but does not rewrite it.

**Lifecycle:** Observation/Artifact exist → Research evaluation → Evidence proposal → auditable evidence-admission transition → recorded Evidence. Contradiction is new Evidence, not an in-place mutation.

**Invariants:**

- Observation != Evidence.
- Artifact != Evidence.
- Hypothesis != Evidence.
- Model output != Evidence.
- AI-generated assertion cannot be Evidence.
- WorkerResult cannot directly become Evidence.
- Observation/Artifact require a separate Evidence admission transition.
- Provenance is required.
- Evidence is immutable whenever possible.

**Relationships:** admitted from Observation and/or Artifact; supports or weakens Hypothesis and Candidate; consumed by Verification; required by FindingProposal and Finding.

---

### Candidate

**Purpose:** Represent a potential security issue that has not become a Finding.

**Meaning:** A possible issue under investigation. Candidate ≠ Finding. Candidate VALIDATED ≠ Finding.

**Key attributes (conceptual):**

- identity
- subject Assets
- supporting Hypotheses
- evidence set
- unresolved questions
- lifecycle state
- ResearchRun / Program
- provenance

Candidate does not carry a duplicate `verification_status` field. Candidate lifecycle state is the only Candidate status authority.

Verification only produces a Candidate transition proposal. Verification is not Candidate lifecycle authority.

**Ownership:** Research may propose a Candidate. Data stores it. Data does not promote it.

Research domain logic evaluates the Verification outcome proposal, supporting Evidence, and Candidate transition invariants, then commits the Candidate state transition.

That commit is:

- not an LLM-only decision
- not a Core authorization decision
- not authority of the Verification record itself

Core applies promotion-contract semantics later for FindingProposal review, not for Candidate lifecycle.

**Lifecycle:**

```
OPEN
→ VERIFYING
→ VALIDATED / REJECTED / INCONCLUSIVE / DUPLICATE / OUT_OF_SCOPE
```

**VALIDATED** means:

- sufficiently supported by Evidence
- has gone through Verification
- eligible to be proposed as a Finding

VALIDATED != Finding.

Only a VALIDATED Candidate may produce a FindingProposal.

**Invariants:**

- Candidate ≠ Finding.
- Candidate VALIDATED is required before FindingProposal.
- A Candidate may exist with incomplete Evidence; a FindingProposal and Finding may not.
- Out-of-scope is a Candidate outcome, not silent continuation.
- Verification cannot create Finding.
- Verification cannot commit Candidate state.
- Candidate lifecycle is the only Candidate status authority.

**Relationships:** supported by Hypotheses and Evidence; evaluated by Verification process records; may produce a FindingProposal only when VALIDATED.

---

### Verification

**Purpose:** Evaluate a Candidate and propose a Candidate state transition.

**Meaning:** A domain process. Verification assesses a Candidate, consumes Evidence, and evaluates reproducibility, validity, and impact. It proposes a Candidate state transition. It does not create a Finding. It does not commit Candidate state.

Verification’s own record status is not the authoritative Candidate status.

If a Verification record is stored, it is an episodic process record carrying:

- inputs
- evidence used
- outcome proposal
- provenance

Research domain logic evaluates that proposal against supporting Evidence and Candidate transition invariants, then commits the Candidate state transition.

The only authority for Candidate lifecycle is the Candidate’s own lifecycle state.

**Key attributes (conceptual, when recorded):**

- identity
- Candidate link
- consumed Evidence
- reproducibility / validity / impact assessment
- proposed Candidate transition
- unresolved questions
- ResearchRun link
- process provenance

**Ownership:** Research owns verification reasoning and the outcome proposal. Core may require Approval before high-side-effect verification steps. Data may store Verification as an episodic process record. Human Review remains required for Finding creation.

**Lifecycle:** started → in-progress → outcome proposal recorded. The proposal may support moving Candidate to VALIDATED / REJECTED / INCONCLUSIVE / DUPLICATE / OUT_OF_SCOPE. That proposal is not Finding creation.

**Invariants:**

- Verification cannot create Finding.
- Verification cannot self-accept a Finding.
- Verification cannot commit Candidate state.
- Verification status is not Candidate status.
- Verification consumes Evidence; it does not invent Evidence from model text.

**Relationships:** process link to Candidate (`Candidate ↔ Verification`); consumes Evidence; may propose Candidate transitions; never commits Candidate state; never creates Finding or FindingProposal by itself. FindingProposal is created by Research from a VALIDATED Candidate.

---

### FindingProposal

**Purpose:** Ask Human Review to accept a VALIDATED Candidate as an internal Finding.

**Meaning:** A proposal only. It is not an accepted security issue and not a Finding.

A FindingProposal can be created only from a VALIDATED Candidate.

**Key attributes (conceptual):**

- identity
- supporting Candidate
- supporting Evidence
- proposed impact
- proposed root cause
- unresolved caveats
- ResearchRun / Program / AuthorizationSource
- lifecycle state

Exact impact and root-cause representations remain open questions; the proposal may carry them as unresolved structured fields.

**Ownership:** Research creates the proposal. Data stores it. Data does not approve it. Core owns the promotion/approval contract. Interface presents Human Review. Human Review decision is recorded through Core Approval semantics.

FindingProposal cannot approve itself.

FindingProposal APPROVED is not an independent authority. It is the domain view of the same decision event as the Core Approval record.

**Lifecycle:**

```
PROPOSED
→ HUMAN_REVIEW
→ Core Approval decision
```

Legal path:

```
FindingProposal
→ HUMAN_REVIEW
→ Core Approval decision
```

If Core Approval = APPROVE:

- FindingProposal state = APPROVED
- a Finding may be created

If Core Approval = REJECT:

- FindingProposal state = REJECTED

FindingProposal is not a Finding in any of these states.

**Invariants:**

- FindingProposal != Finding.
- Candidate VALIDATED is required before FindingProposal.
- Research cannot create a Finding directly.
- FindingProposal cannot set itself APPROVED.
- There are not two independent approval authorities.
- Finding requires FindingProposal whose APPROVED state is derived from a Core Approval APPROVE decision.
- Supporting Evidence is required. Artifact attachment is not enough.

**Relationships:** derived from a VALIDATED Candidate; supported by Evidence; linked as `FindingProposal ↔ Human Review ↔ Approval`; if Core Approval is APPROVE, FindingProposal becomes APPROVED and a Finding may be created.

---

### Finding

**Purpose:** Represent a human-approved internal accepted security issue.

**Meaning:** An accepted internal security finding created only after Human Review on a FindingProposal and a Core Approval APPROVE decision. FindingProposal APPROVED is that same decision, not a second authority.

Finding is never an unaccepted proposal.

External bug-bounty platform acceptance is a separate future concept, not this Finding.

**Key attributes (conceptual):**

- identity
- originating Candidate
- originating FindingProposal
- supporting Evidence
- recorded Approval
- provenance chain
- acceptance actor and time
- ResearchRun / Program / AuthorizationSource

**Ownership:** Data stores the Finding after creation. Core owns the promotion contract and Approval record. Human Review is the decision actor through Interface. Research owns only the FindingProposal.

**Lifecycle:** created as accepted when the creation invariant is satisfied. Later correction is a new record or explicit supersession, not a rewrite of acceptance history. Finding has no PROPOSED or HUMAN_REVIEW states.

**Creation invariant:**

```
Candidate VALIDATED
+ FindingProposal
+ Human Review
+ Core Approval APPROVE
= Finding
```

FindingProposal APPROVED is the domain view of that Core Approval decision.

**Invariants:**

- Finding requires FindingProposal APPROVED derived from Core Approval APPROVE.
- Finding requires supporting Evidence.
- Artifact attachment does not satisfy the Evidence requirement.
- Human Review decision must be recorded through Core Approval semantics.
- Research cannot create Finding.
- Verification cannot create Finding.
- Model output cannot accept a Finding.

**Relationships:** created from approved FindingProposal; supported by Evidence; linked to Candidate, Approval, AuditEvent, Program, and ResearchRun.

---

### Approval

**Purpose:** Represent Core-owned approval semantics.

**Meaning:** The control record for whether a requested action is allowed by the required authority. Interface only presents review/approval interaction.

The only legal path for Finding acceptance:

```
FindingProposal
→ HUMAN_REVIEW
→ Core Approval decision
```

If Core Approval = APPROVE, FindingProposal state becomes APPROVED and a Finding may be created.

If Core Approval = REJECT, FindingProposal state becomes REJECTED.

FindingProposal APPROVED is the domain view of the Core Approval record. There are not two independent approval authorities.

**Key attributes (conceptual):**

- requested action
- required authority
- state
- decision
- decision actor
- decision time
- related ResearchRun / Experiment / FindingProposal

**Ownership:** Core owns approval requirement, decision semantics, and decision-record contract. Interface presents the screen/flow. Humans act through Interface; the decision becomes a Core Approval record.

**Lifecycle:** required → requested → pending → granted / denied / expired. History is not silently rewritten.

**Invariants:**

- Interface cannot define approval logic.
- Operator shortcut is not approval unless Core records it.
- Finding can be created only after Core Approval APPROVE on a FindingProposal.
- FindingProposal cannot approve itself.
- Human Review decision must be recorded through Core Approval semantics.

**Relationships:** control/process link `FindingProposal ↔ Human Review ↔ Approval`; may also gate Experiment execution or high-side-effect Verification steps; recorded as AuditEvent.

---

### Snapshot

**Purpose:** Capture an observed representation of Assets or attack surface at a point in time.

**Meaning:** A time-bounded observed picture. A Snapshot is not a vulnerability and not a Finding.

**Key attributes (conceptual):**

- identity
- time
- included Assets / observed surface
- related ResearchRun
- provenance
- comparison identity for later ChangeEvents

**Ownership:** Data. Produced from ingested Observations/Artifacts, not declared by Workers as truth.

**Lifecycle:** captured → recorded → superseded by a later Snapshot. Earlier Snapshots remain historical.

**Invariants:**

- Snapshot ≠ Finding.
- Snapshot ≠ Hypothesis.
- A Snapshot does not authorize Assets it contains.
- Snapshot comparison produces ChangeEvent / Derived Fact only when the comparator is deterministic.

**Relationships:** composed from Observations and Assets; deterministic comparison yields ChangeEvents.

---

### ChangeEvent

**Purpose:** Record a deterministic difference derived from Snapshot or Observation comparison.

**Meaning:** An added, removed, or changed delta produced only by a deterministic comparator. A ChangeEvent may trigger a new Hypothesis. It is not itself a Hypothesis.

**Key attributes (conceptual):**

- identity
- kind: added / removed / changed
- before / after references
- related Assets
- source inputs
- transformation identity/version
- timestamp
- ResearchRun

**Ownership:** Data stores ChangeEvents. Research may react by proposing Hypotheses.

**Lifecycle:** derived → recorded → optionally consumed by Hypothesis generation.

**Invariants:**

- ChangeEvent ≠ Hypothesis.
- ChangeEvent is a Derived Fact only when produced by a deterministic, reproducible, provenance-preserving comparator with known inputs and known transformation semantics.
- Non-deterministic interpretation of a delta is Inference or Hypothesis.
- A ChangeEvent does not authorize newly appeared Assets.

**Relationships:** derived from Snapshots/Observations; may lead to Hypothesis proposals; must not be written as inferred AssetRelation.

---

### AuditEvent

**Purpose:** Make important decisions and executions reconstructable.

**Meaning:** A traceable record of a significant control decision or execution. Audit history must not be silently rewritten.

**Key attributes (conceptual):**

- workflow
- agent/model
- tool
- target
- policy decision
- budget
- evidence references
- result
- actor
- timestamp
- ResearchRun / AuthorizationSource

**Ownership:** Core owns audit semantics. Data stores AuditEvents as historical records.

**Lifecycle:** appended → retained. Corrections are new AuditEvents, not edits in place.

**Invariants:**

- AuditEvent history is not silently rewritten.
- Important Core decisions and Worker executions must be auditable.
- AuditEvent is not Evidence, Finding, or AuthorizationSource.

**Relationships:** references Program, AuthorizationSource, ResearchRun, Budget, Experiment, WorkerResult, Evidence, Candidate, FindingProposal, Finding, Verification, and Approval as applicable.

---

## 3. Research Memory Model

Research Memory is a read/retrieval/organization abstraction over authoritative domain records and curated procedural knowledge.

Research Memory is **not** a truth source.

Research Memory must not become a shadow database or shadow truth layer.

LLM conversation history is not Research Memory.

### Factual Memory

Read only from authoritative domain records:

- Assets
- Observations
- observed or deterministic derived AssetRelations
- accepted Findings
- deterministic ChangeEvents

Technology/behavior concepts are not first-class entities. If present in memory, they are typed Observation or read-model projections, not separate truth records.

### Episodic Memory

- Hypotheses
- Experiments
- WorkerResults
- Verification records
- Evidence
- rejected/inconclusive attempts

WorkerResult may be durable.

Durable != Trusted.

WorkerResult remains **UNTRUSTED EXECUTION OUTPUT**.

### Procedural Memory

- methodologies
- heuristics
- analyst notes
- curated research patterns
- historical outcome-linked heuristics
- links to accepted/rejected Candidate/Finding outcomes

A pattern should reference an authoritative Candidate, Finding, or Verification outcome where possible.

Procedural knowledge is not authoritative target truth. It cannot override Core authorization, invent Evidence, admit Evidence, or create Findings.

---

## 4. State Lifecycles

These lifecycles are technology-independent.

### Hypothesis

```
PROPOSED
→ PRIORITIZED
→ TESTING
→ SUPPORTED / WEAKENED / REJECTED / INCONCLUSIVE
```

SUPPORTED and WEAKENED are belief updates from Evidence, including negative evidence. They are not Findings.

Hypothesis outcome is independent of Experiment execution outcome.

### Experiment

```
PLANNED
→ AUTHORIZATION_CHECK
→ READY
→ RUNNING
→ EXECUTION_SUCCEEDED
  / EXECUTION_FAILED
  / BLOCKED
  / CANCELLED
  / BUDGET_EXHAUSTED
```

These states are execution results.

- EXECUTION_FAILED != Hypothesis REJECTED
- BUDGET_EXHAUSTED != negative evidence
- BLOCKED != research conclusion

### Candidate

```
OPEN
→ VERIFYING
→ VALIDATED / REJECTED / INCONCLUSIVE / DUPLICATE / OUT_OF_SCOPE
```

VALIDATED means Evidence-supported, Verification-processed, and eligible for FindingProposal. VALIDATED != Finding.

### FindingProposal

```
PROPOSED
→ HUMAN_REVIEW
→ Core Approval decision
```

If Core Approval = APPROVE, FindingProposal state = APPROVED and a Finding may be created.

If Core Approval = REJECT, FindingProposal state = REJECTED.

FindingProposal is never a Finding.

FindingProposal APPROVED is the domain view of a Core Approval APPROVE decision, not a second authority.

### Finding

Finding has no proposal states.

Finding is created only when:

```
Candidate VALIDATED
+ FindingProposal
+ Human Review
+ Core Approval APPROVE
= Finding
```

External bug-bounty platform submission, triage, or bounty outcome is a separate future concept and is not this lifecycle.

---

## 5. Provenance Model

This is a traceability model. It is not a database foreign-key requirement, not a linear timeline, and not a schema.

Logical graph:

```
Program
→ AuthorizationSource
→ ResearchRun

ResearchRun
→ Hypothesis
→ Experiment
→ WorkerResult
→ Observation / Artifact
→ Evidence
→ Candidate
→ FindingProposal
→ Finding
```

Process/control links:

```
Candidate
↔ Verification

FindingProposal
↔ Human Review
↔ Approval
```

Rules:

- This is not a linear timeline.
- A Candidate may exist with partial Evidence.
- A record does not have to carry every node.
- The purpose is backward traceability.

Where possible, important records should preserve: source, timestamp, target, discovery method, related run, and artifact reference.

Evidence provenance is required. Finding provenance chain should be preserved. AuditEvents should make the chain reconstructable.

---

## 6. Fact vs Inference

Keep these distinct:

| Concept | Meaning | Trust |
| --- | --- | --- |
| Observed Fact | Directly observed; stored as Observation | Highest factual trust among research records, still not a Finding |
| Derived Fact | Produced only by a deterministic, reproducible transformation with known inputs, known transformation semantics, and provenance | Lower than Observed Fact; never stored as Observation; never stored as inference |
| Inference | Non-deterministic interpretation | Not a fact; must not be stored as fact; inferred relationships are Hypotheses |
| Hypothesis | Testable research claim | Not a fact |
| Model Suggestion | Untrusted structured proposal from an LLM or reasoning runtime | Lowest; never auto-promoted |
| Human Judgment | Final acceptance/rejection recorded through Core Approval / Human Review | Required for Finding creation |

Rules:

- A Model Suggestion never automatically rises to a higher trust level.
- Derived Fact may be produced only from a deterministic, reproducible, provenance-preserving transformation with known inputs and known transformation semantics.
- Non-deterministic interpretation is Inference or Hypothesis.
- Inference must not be stored as fact.
- Snapshot comparison may produce Derived Fact / ChangeEvent only if the comparator is deterministic.
- Observed Fact ≠ Hypothesis ≠ Evidence ≠ FindingProposal ≠ Finding.

---

## 7. Result Ingestion Boundary

This is the conceptual form of the controlled result boundary in `PROJECT_STRUCTURE.md`.

No service, queue, module, or technology is chosen here.

WorkerResult becomes truth only through two separate transitions.

### Transition A — Ingestion

```
Worker
→ WorkerResult
→ schema/integrity validation
→ deterministic normalization
→ Observation and/or Artifact
```

Rules:

- no semantic interpretation
- no vulnerability inference
- no hypothesis generation
- no evidence promotion

Observation may only be the deterministic representation of a directly observable signal in the WorkerResult.

Core enforces that authorization/run context, provenance validity, and allowed execution context are valid for this transition.

The Worker cannot declare this transition as truth.

### Transition B — Evidence Admission

```
Observation and/or Artifact
→ Research evaluation
→ Evidence proposal
→ auditable evidence-admission transition
→ Evidence
```

Evidence is never created automatically during WorkerResult ingestion.

Evidence admission must be:

- explicit
- auditable
- provenance-preserving
- testable

Research produces the proposal. Truth promotion is recorded as an open domain transition.

Who may admit Evidence (human-only, deterministic policy, verifier-assisted, or mixed) is an open question and is not decided here.

Whichever option is chosen later, these invariants do not change:

- Evidence admission is explicit
- Evidence admission is auditable
- Evidence admission is provenance-preserving
- Workers cannot promote WorkerResult directly to Evidence

Redirect, new hostname, discovered Asset, or new subdomain stops execution and requests Core re-evaluation before any further action on that target.

---

## 8. Domain Invariants

- Program != AuthorizationSource
- AuthorizationSource != ScopeRule
- Asset carries no authorization state
- Experiment Budget cannot exceed ResearchRun Budget
- Inferred relation != AssetRelation fact
- WorkerResult can only become Observation/Artifact through deterministic ingestion
- WorkerResult cannot directly become Evidence
- Observation/Artifact require a separate Evidence admission transition
- Artifact attachment != Evidence admission
- Verification cannot create Finding
- Verification cannot commit Candidate state
- Candidate VALIDATED is required before FindingProposal
- FindingProposal != Finding
- FindingProposal cannot approve itself
- FindingProposal APPROVED is the domain view of Core Approval APPROVE
- Finding requires FindingProposal APPROVED from Core Approval APPROVE
- Human Review decision must be recorded through Core Approval semantics
- Experiment execution failure != Hypothesis rejection
- Durable WorkerResult != trusted fact
- Research Memory is not a truth source
- No active ResearchRun without AuthorizationSource
- No execution without Core authorization
- WorkerResult ≠ Observation
- Observation ≠ Hypothesis
- Hypothesis ≠ Evidence
- Evidence ≠ Finding
- Candidate ≠ Finding
- Model output ≠ Evidence
- Artifact != Evidence
- Observation != Evidence
- Finding requires supporting Evidence
- Workers cannot self-authorize
- Workers cannot mutate budget policy
- Derived assets are not automatically authorized
- Persistent domain state does not live in LLM conversation history
- Ambiguous scope is DENY or REQUIRE_HUMAN_REVIEW
- Missing valid AuthorizationSource or resolvable effective scope blocks active execution
- AuthorizationSource cannot be created by an LLM
- Evidence is immutable whenever possible
- AuditEvent history is not silently rewritten
- Data does not decide Candidate → Finding promotion
- Research cannot change Core decisions
- Research cannot create Finding directly

---

## 9. Non-Domain Concepts

The following are not domain entities. They may implement or support the domain later. They are not concepts in this model:

- database tables
- ORM models
- message queues
- workflow/orchestration products
- Strix internals
- Burp internals
- model provider SDKs
- HTTP client libraries
- container/runtime technology

Strix, Burp, n8n, and model providers remain examples of possible integrations, not domain objects and not committed dependencies.

---

## 10. Open Domain Questions

These questions are intentionally unanswered.

- Asset granularity
- identity/session representation
- external platform submission/triage result representation
- confidence/belief model
- duplicate semantics
- root-cause representation
- impact representation
- cross-program knowledge sharing boundaries
- Evidence admission authority details (human-only, deterministic policy, verifier-assisted, or mixed)
