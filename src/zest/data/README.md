# Data

Data owns the **authoritative persistence spine** for the first future closed research loop.

Spine:

```
Program
→ AuthorizationSource
→ ResearchRun
→ IssuedBudget
Hypothesis / Experiment
→ ExecutionAttempt (durable intended Worker invocation)
→ WorkerResult
→ Observation
+ AuditEvent
+ ResearchReasoningRecord (append-only Generator/Falsifier provenance; not Hypothesis truth)
+ ResearchAdmissionRecord (append-only admission process history; not target truth)
+ ExperimentPlanRecord (immutable executed-plan specification; not Experiment lifecycle)
+ HypothesisAssessmentRecord (append-only context-bound assessment; not Evidence)
+ EvidenceRecord (append-only admitted Evidence; not Candidate or Finding)
+ EvidenceAdmissionRecord (append-only Evidence admission history; rejected proposals create no Evidence)
+ CandidateRecord (lifecycle state is mutable; VALIDATED is not a Finding)
+ CandidateAdmissionRecord (append-only Candidate admission history; rejected proposals create no Candidate)
+ VerificationRecord (append-only process record; does not commit Candidate state by itself)
+ FindingProposalRecord (lifecycle state is mutable; content is immutable; APPROVED is not a Finding)
+ HumanReviewRecord (append-only human decision; not Core Approval and not a Finding)
+ ApprovalRecord (append-only Core Approval record; not AuditEvent and not Finding truth)
+ FindingRecord (append-only accepted research result; diagnostic plumbing is not a vulnerability)
+ TargetInferenceRecord (append-only INFERRED/HYPOTHESIZED projection record; not Observation)
+ DifferentialObservationRecord (append-only comparison; not Evidence, Candidate, or Finding)
+ InvariantHypothesisRecord (status mutable; expected-behavior hypothesis, not a fact or ScopeRule)
+ InvariantSourceRefRecord / InvariantCounterexampleRefRecord (append-only provenance; counterexamples are context-bound)
+ ChainHypothesisRecord (append-only composition hypothesis; not an exploit, Evidence, Candidate, or Finding)
+ ResearchOpportunityRecord (append-only selected research direction; not Hypothesis truth and not authorization)
+ ResearchSelectionRecord (append-only selection decision; not Core ALLOW)
+ SnapshotRecord / SnapshotMemberRecord (immutable point-in-time references; not a full SoR copy)
+ ChangeEventRecord (append-only deterministic delta; not Evidence and not a vulnerability)
+ ResearchOrchestrationRecord (mutable checkpoint for one ResearchRun controller; not a Finding)
+ ResearchCycleRecord (append-only cycle history; not a message queue)
+ BudgetConsumptionRecord (append-only usage ledger; not IssuedBudget)
```

This is **not** a database copy of DOMAIN_MODEL.md.

## Owns

- persistence records and ports (Python implementation types, not wire contracts)
- PostgreSQL adapter (`postgres/`) using SQLAlchemy 2 Core + psycopg 3
- synchronous Unit of Work (explicit commit, otherwise rollback)
- Alembic-reviewed schema history

## Does not own

- Core authorization / ExecutionDecision
- Worker execution
- Evidence admission *authority* (Research owns semantics; Data only persists)
- Candidate / Verification *authority* (Research owns semantics; Data persists records and Candidate state writes that Research already decided)
- Finding / FindingProposal / Human Review *authority* (Research owns semantics; Data persists)
- Target Model / Differential / Invariant / Chain / Exploration / Temporal *authority* (Research owns semantics; Data persists opportunity, selection, snapshot, and change records only)
- Approval *authority* (Core owns semantics; Data persists the durable Approval record)
- ScopeRule matcher storage
- model routing, graphs, vectors

## Rules

- Core and Research must not import SQLAlchemy, psycopg, or Alembic.
- Workers must not write the SoR or import these repositories.
- Strix adapters must not write the SoR or import these repositories.
- ORM / SQLModel are not used. Table objects are not Domain entities.
- `metadata.create_all()` is not application startup.
- WorkerResult is UNTRUSTED EXECUTION OUTPUT. Inserting it does not create Observation or Evidence.
- Transition A (Application) is the only path that may persist Observation from a completed Worker invocation.
- Observation is not a vulnerability.
- `research_reasoning` is append-only untrusted reasoning provenance. It is not Observation, Evidence, or Hypothesis truth. `hypothesis_id` is nullable so rejected cycles can be stored without promoting a Hypothesis.
- `research_admission` is append-only research-process history. Rejected admissions have no `admitted_hypothesis_id`.
- `experiment_plan` is the immutable specification used for later assessment. It is not authorization state.
- `hypothesis_assessment` is append-only context-bound learning. It is not Evidence and does not mutate Hypothesis truth.
- `evidence` is append-only admitted Evidence. Inserting Observation or HypothesisAssessment does not create it.
- `evidence_admission` is append-only Evidence admission history. Rejected proposals have no `admitted_evidence_id`.
- `candidate` stores Candidate lifecycle state. Data does not choose OPEN/VALIDATED. VALIDATED is not a Finding.
- `candidate_admission` is append-only Candidate admission history. Rejected proposals have no `admitted_candidate_id`.
- `verification` is append-only Verification process history. Inserting it does not by itself change Candidate state.
- `finding_proposal` stores FindingProposal lifecycle state. Content is immutable after insert. APPROVED is not a Finding.
- `human_review` is append-only Human Review. It is not Core Approval and not a Finding.
- `approval` is append-only recorded Core Approval. AuditEvent is not a substitute for this record.
- `finding` is append-only accepted research result. Diagnostic plumbing is not a vulnerability. Inserting FindingProposal or Approval does not create it.
- `target_inference` is append-only INFERRED/HYPOTHESIZED target-model state. It is not Observation and cannot upgrade to OBSERVED.
- `differential_observation` is append-only comparison provenance. Inserting it does not create Evidence, Candidate, or Finding.
- `invariant_hypothesis` stores expected-behavior hypothesis status. It is not Observation, authorization, or a vulnerability. Status may change; source/counterexample refs are append-only.
- `chain_hypothesis` is append-only multi-step research composition. Inserting it does not dispatch a Worker and does not create Evidence, Candidate, or Finding.
- `research_opportunity` is append-only selected research direction. Inserting it does not authorize execution and does not create a Hypothesis.
- `research_selection` is append-only policy decision provenance. SELECT is not Core ALLOW.
- `snapshot` / `snapshot_member` are immutable point-in-time observation references. They are not a second SoR and not a vulnerability picture.
- `change_event` is append-only deterministic snapshot delta. Inserting it does not create Evidence, Candidate, or Finding. Snapshot retention/compaction is deferred and must never delete Evidence/Verification/Finding provenance.
- `research_orchestration` is the durable controller checkpoint for one ResearchRun. It is not AuditEvent workflow state and not a Finding.
- `research_cycle` is append-only cycle history. PostgreSQL is not a message broker.
- `budget_consumption` is append-only usage. IssuedBudget remains the immutable envelope. Replay of the same request/resource must not double-charge.
- WorkerResult stores first-class request-envelope provenance (`request_id`, correlation, capability/action, authorization decision reference, budget reference). `request_id` is unique (idempotency). Correlation is not JSON-only.
- IssuedBudget is immutable after insert (0 = no allowance).
- AuditEvent is append-only reconstructive history, not Evidence, not a log substitute, and not a dispatch queue.
- ExecutionAttempt is durable dispatch coordination for one intended Worker invocation. It is not Evidence and not a WorkerResult. `request_id` is unique.
- JSONB is only for untrusted/extensible WorkerResult bags and typed Observation/Audit payloads. It is not scope, authorization, Approval, Evidence, Finding, or budget authority.
- Connection URLs come from the environment (`ZEST_DATABASE_URL` / `ZEST_TEST_DATABASE_URL`). Use `postgresql+psycopg://…`. Passwords are not logged.
- Integration tests require an explicit `ZEST_TEST_DATABASE_URL`. They never default to the application URL, never infer `PG*` variables, and refuse SQLite, `postgres`/`template*` catalogs, and a database named `zest`. The test database name must contain `test`. Tests `TRUNCATE CASCADE` that database only.

Phase A local test cluster (existing WSL PostgreSQL binaries, user-space data dir, no sudo, not Docker architecture):

```
python scripts/start_wsl_test_postgres.py
$env:ZEST_TEST_DATABASE_URL = "postgresql+psycopg://zest_test@127.0.0.1:55432/zest_test"
uv run python -m unittest discover -s tests/integration -v
```

Python records here are **not** language-neutral architectural contracts. Worker wire truth remains `contracts/`.
