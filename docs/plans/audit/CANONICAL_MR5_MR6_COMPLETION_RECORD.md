# Canonical MR-5 + MR-6 Completion Record — PromotionPipeline + exploratory hunting

Status: IMPLEMENTATION_COMPLETE. Targeted QA + campaign-wide adversarial QA executed.
Global maturity flags were **not** advanced.

Pair 4B+4C was sealed first as checkpoint 9 (see below). This record is the
B1+B2 implementation evidence. It is **not** a git seal unless the operator
asks for a commit.

## SHAs

| Item | SHA |
|---|---|
| Campaign baseline (Slices 3–7) | `d4b75fca11053376aea2380e70b390200c29a917` |
| Pair 4B+4C checkpoint (pushed) | `54b17a1257b5b5307b100e1e163de56915de541f` |
| Working tree for this pair | uncommitted on `campaign/majority-implementation` |

Pair 4B+4C targeted counts at seal: unit **1354** pass / 0 fail / **4** skip;
integration **191** pass / **1** pre-existing SD-G4 fail; G14–G17 isolated
**131** pass.

Numbering collision (do not merge gates):

| Document | “MR-5” means |
|---|---|
| `RESEARCH_OS_HUNTER_RECONNECTION_PLAN.md` | **PromotionPipeline** (this pair, B1) |
| Master-plan / lock Slice 7 | exploratory `diagnostic.echo` plumbing only |

Slice 7 `diagnostic.echo` PASS is preserved. Canonical exploratory hunting is
reconnection **MR-6** (B2).

## B1 — Canonical MR-5 PromotionPipeline

Durable chain (compose, do not rewrite Evidence/Candidate/Verification/
FindingProposal use-cases):

```
Assessment (CONSISTENT_WITH_PREDICTION)
→ EvidenceProposal / EvidenceAdmission (AdmitDiagnosticEvidence)
→ Evidence
→ Candidate (ProposeCandidateFromEvidence)
→ Independent Verification start (StartCandidateVerification)
→ AdvancePromotionPipeline (fresh Prepare + Execute + raw Evaluate)
→ AdmitDiagnosticEvidence on reproduction only (no second Candidate)
→ CompleteCandidateVerification
→ SubmitFindingProposal only if VALIDATED
```

Finding is **never** auto-created.

Unchanged last stage:

`FindingProposal → HumanReview → Core Approval → Finding`

ARC may trigger `on_assessment` after evaluate. ARC is not Candidate or
Finding authority. Independent verification Worker execution is `advance()`
and may continue after the ResearchRun / orchestration is terminal.

`SUPPORTED` is not a repo assessment enum; the eligible outcome is
`CONSISTENT_WITH_PREDICTION`. That outcome is not Evidence by itself —
admission is required. `INCONCLUSIVE`, `CONTRADICTS_PREDICTION`,
`EXECUTION_UNUSABLE`, unsupported strategy, and operational
`UNKNOWN_OUTCOME` / `INVOCATION_FAILED` / Core `DENY` do not auto-create
Candidate or FindingProposal. Reproduction evaluation uses raw
`EvaluateExperimentFeedback`, not `PromoteOnAssessment`, so a reproduction
CONSISTENT cannot recurse into a second promotion lifecycle.

Restart/idempotency: unique `promotion_run.assessment_id`; duplicate
`on_assessment` after VERIFYING+ is `SKIPPED_ALREADY_ATTEMPTED`; duplicate
`advance` reloads existing Verification/FindingProposal. Reproduction
experiment id is reserved before Worker dispatch. `ProposeCandidateFromEvidence`
reloads an existing Candidate for the same Evidence. `StartCandidateVerification`
is a no-op when already `VERIFYING`.

### B1 files / flow

- `src/research_os/application/promotion_pipeline.py` — `PromotionPipeline.on_assessment` / `.advance`, `PromoteOnAssessment`, `AdvancePromotionPipeline`
- `src/research_os/application/propose_candidate.py` — duplicate Evidence → existing Candidate
- `src/research_os/application/start_candidate_verification.py` — VERIFYING no-op
- `src/research_os/data/records.py` — `PromotionRunRecord`, `ALLOWED_PROMOTION_STAGES`
- `src/research_os/data/ports.py`, `unit_of_work.py`, postgres mapping/repositories/tables
- `alembic/versions/a38_001_promotion_run.py`
- `tests/support/fake_unit_of_work.py` — `promotion_runs`
- `tests/unit/application/test_promotion_pipeline.py`
- `tests/unit/application/test_candidate_lifecycle.py` (idempotency)
- `tests/integration/test_canonical_mr5_mr6.py` (`CanonicalPromotionPostgresTests`)
- Alembic-head assertions bumped to `a38_001_promotion_run`

ARC already wrapped evaluate with `PromoteOnAssessment` (Slice 5). This pair
extends that hook through Candidate+VERIFYING; verification Worker stays on
`advance()`.

## B2 — Canonical MR-6 full exploratory hunting

Slice 7 `compile_exploratory_hypothesis` / `ExecuteExploratoryHypothesis`
(`diagnostic.echo` + generic planner) is unchanged. Family-promotion gate is
unchanged: permanent `hunter_family` write still requires `HUMAN_OPERATOR`
approval.

New path:

```
graph/temporal/identity/workflow/response anomaly
→ DraftExploratoryHypothesis (run-scoped, registry-external)
→ ResearchOpportunity (kind OTHER, mode EXPLORATION)
→ compile_exploratory_research (typed compilers; never diagnostic.echo)
→ ExperimentPlan
→ ARC start + run_managed_cycle (same Prepare/Execute/Evaluate)
→ Observation → Assessment → canonical PromotionPipeline.advance
```

Model query/body/header/raw keys are stripped. Missing typed fields →
`BLOCKED_MISSING_SEMANTICS`. Compilers reused by **calling** them
(`AuthorizationDifferentialCompiler`, `StateTransitionCompiler`,
`MutationMatrixCellCompiler`, `ProtocolStepCompiler`), not by writing
`hunter_family`. Exploratory Result is not Evidence/Candidate/Finding;
those appear only through MR-5 after admission + independent verification.

### B2 files / flow

- `src/research_os/research/exploratory_research_compile.py`
- `src/research_os/application/execute_exploratory_research.py`
- `src/research_os/research/exploratory.py` — `GRAPH_STRUCTURE_ANOMALY`, `IDENTITY_ANOMALY`, `WORKFLOW_ANOMALY`
- `tests/unit/research/test_exploratory_research_compile.py`
- `tests/unit/application/test_execute_exploratory_research.py`
- `tests/integration/test_canonical_mr5_mr6.py` (`CanonicalExploratoryPostgresTests`)

HTTP authorization lab modes: `vulnerable` | `secure_only` | deceptive
(compile as `vulnerable`, worker returns 200 with actor as cross-object
owner so the evaluator does not prove IDOR).

## Reused architecture

- Core `evaluate_execution` + compiled scope (sole auth/scope/budget/side-effect authority)
- ARC = sole research lifecycle owner; no second Worker dispatcher
- `AdmitDiagnosticEvidence`, `ProposeCandidateFromEvidence`, `StartCandidateVerification`, `CompleteCandidateVerification`, `SubmitFindingProposal`
- `PreparePlannedExperiment` / `ExecutePlannedExperiment` / Transition A ingest
- Typed compilers from Pair 4 / 4B / 4C
- Human Review + Core Approval use-cases untouched (never auto-APPROVE, never bypassed)
- Slice 7 exploratory plumbing + `PromoteExploratoryFamily` gate unchanged

## Schema changes

Additive Alembic `a38_001_promotion_run` revising `a37_001_impact_edge_proof`.

Table `promotion_run`: mutable stage machine, unique `assessment_id`.
Stages: `EVIDENCE_REJECTED`, `EVIDENCE_ADMITTED`, `CANDIDATE_REJECTED`,
`CANDIDATE_OPEN`, `VERIFYING`, `REPRODUCTION_EXECUTED`, `VERIFIED`,
`PROPOSAL_RECORDED`, `STOPPED`.

Authoritative Evidence/Candidate/Verification/FindingProposal rows remain
in their own tables. Finding creation path unchanged.

## Targeted QA

### MR-5

- Assessment CONSISTENT → admitted Evidence → Candidate (`VERIFYING`)
- rejected / INCONCLUSIVE / CONTRADICTS / EXECUTION_UNUSABLE / unsupported strategy → no Candidate
- Candidate → independent verification via `advance()`
- verification reject/inconclusive → no FindingProposal
- VALIDATED diagnostic → FindingProposal; Finding = 0; Human Review = 0
- duplicate invocation → no duplicate authoritative records
- crash/reload between stages (new pipeline instance; REPRODUCTION_EXECUTED resume)
- ResearchRun/orchestration terminal (`COMPLETED` / `MAX_CYCLES_REACHED`) → pending promotion still advances
- HumanReview / Core Approval unchanged

### MR-6

- registry-external vulnerable lab fixture → Evidence + Candidate + Verification through MR-5
- matching `secure_only` fixture → no Finding / no FindingProposal
- deceptive fixture → `false_finding = 0`
- exploratory hypothesis uses non-diagnostic compiler (`http.authorization.differential`)
- Core DENY → Worker count 0
- model query/body/header cannot become Worker args
- no permanent registry mutation during research
- HTTP_AUTHORIZATION_DIFFERENTIAL FindingProposal still requires existing V1/V2/V3 validation audit (not weakened)

## Full repository QA (exact)

| Suite | PASS | FAIL | SKIP |
|---|---|---|---|
| unit (`tests/unit`) | **1373** | 0 | **4** |
| integration (`tests/integration`, real PostgreSQL) | **195** | **1** | 0 |
| e2e G14–G17 isolated | **131** | 0 | 0 |
| e2e complete package (`tests/e2e`) | **152** | **4** | **5** |
| architecture/boundary | included in unit | 0 | — |

The integration failure is the **pre-existing**
`tests/integration/test_sd_g4_token_economy.py::test_cheap_call_records_tokens_and_deny_when_limit_reached`
(`ck_budget_consumption_resource_type` / `MODEL_TOKENS_IN`). Not caused by
this pair.

The complete-package e2e failures are the **pre-existing** `cli_session`
module-isolation failures on G14–G17 `no_model` tests when the entire e2e
package is collected together. Isolated G14–G17 remains **131 passed**.

Covered in those suites (no new regression):

- G14–G17
- SD-G6 (`test_sd_g6_mutation_oast.py`, `test_sd_g6_rate_limit.py`)
- SD-G12 mutation matrix unit (`test_sd_g12_mutation_matrix.py`) + 4B/4C 9-cell execution
- SD-G13 protocol queue (`test_sd_g13_protocol_queue_approval.py`) + protocol step execution
- PromotionPipeline unit + postgres
- exploratory vulnerable/secure/deceptive fixtures
- restart/idempotency
- Core DENY / Worker count 0
- UNKNOWN_OUTCOME / operational failure not retried and not treated as VALIDATED
- WorkerResult ≠ Evidence
- approval ≠ authorization
- false_finding = 0 on deceptive/secure fixtures
- duplicate authoritative record audit

Previous Slice 7 unit baseline after 4B+4C was 1354 passed / 4 skipped.
This pair adds 19 unit tests net (1373). Integration baseline was 191 passed
+ 1 pre-existing fail; this pair adds 4 postgres tests (195).

## Adversarial findings

- G17 `_snapshot` previously took `candidates[0]` ordered by `candidate_id`.
  After MR-5, a run can have a leftover VERIFYING Candidate from a later
  cycle plus a VALIDATED Candidate. UUID order made hidden-GT scoring miss
  `VALIDATED`. Harness now selects the most advanced Candidate. Production
  promotion rules were not weakened.
- HTTP_AUTHORIZATION_DIFFERENTIAL still cannot SubmitFindingProposal without
  the existing validation-audit view. Diagnostic VALIDATED can. That is
  existing class policy, not a new bypass.

## Unresolved gaps

- Reconnection **MR-7** (OAST/session/browser formal qualification) is open
- Reconnection **MR-8** (field validation / benchmark campaign) is open
- `DispatchApprovedV3Queue` is still not an ARC poller / second research brain
- SD-G4 token-economy CHECK constraint remains pre-existing
- Full e2e package `cli_session` isolation remains pre-existing
- Phase J / `research-osd` not started

## Qualification

```
CANONICAL_MR5_QUALIFIED = YES
CANONICAL_MR6_QUALIFIED = YES
HUNTER_RECONNECTION_COMPLETE = NO
```

HUNTER_RECONNECTION_COMPLETE is NO because MR-7 and MR-8 remain. MR-5 and
MR-6 are qualified on the evidence above.

Human Review is not removed. Core Approval is not bypassed. Global maturity
flags are unchanged.
