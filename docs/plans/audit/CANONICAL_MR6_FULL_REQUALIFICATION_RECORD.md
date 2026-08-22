# Research OS — Canonical MR-6 Full Adversarial Re-Qualification

Date: 2026-08-22.
Branch: `campaign/canonical-mr5-mr6`.
This record attacks the MR-6A claim. It does not inherit PASS from MR-6A documentation.

This does **not** prove universal zero-day discovery or field performance.

## A. Repository

```
branch:     campaign/canonical-mr5-mr6
HEAD:       24b4a0d972225d44d97b94aad605cfd79ab1e227
            (pre-seal; this record is sealed as checkpoint 13)
PG:         postgresql+psycopg://research_os_test@127.0.0.1:55432/research_os_test
Alembic:    a40_001_mr6a_identity_anomaly (head)
worktree:   dirty only with this qualification evidence before the seal commit
```

Sealed chain (not rewritten):

| Item | SHA | Tag |
|---|---|---|
| Checkpoint 9 | `54b17a1257b5b5307b100e1e163de56915de541f` | `checkpoint-9-pair-4b-4c` |
| Canonical MR-5 | `e8e78fe24083cb0a2c7898d72928785b247f5d35` | `canonical-mr5-closed` |
| Mutation identity | `7d96f79b8dc1fcb26e5cf764c5626790d9197e12` | `mutation-cell-identity-closed` |
| MR-6 PARTIAL record | `6ac60f9af913aed686360cdee9e68117c61c95a0` | (no close tag) |
| MR-6A / checkpoint 12 | `24b4a0d972225d44d97b94aad605cfd79ab1e227` | `mr6a-arc-anomaly-closed` |

## B. Verdict

**CANONICAL_MR6_FULL_QA_PASS**

Research OS has one verified, autonomous, registry-external, ARC-connected
exploratory research path from a real durable `HTTP_AUTHORIZATION_DIFFERENTIAL`
Observation through a discriminating bounded experiment into canonical MR-5.

PASS does not mean: universal zero-day engine, all anomaly sources, live bug
bounty performance, GATE 04B, production, 24/7, or research-osd.

## C. Production Call Graph

```
WorkerResult
  → Observation (HTTP_AUTHORIZATION_DIFFERENTIAL)          PRODUCTION
  → admit_registry_external_anomaly_candidates             PRODUCTION
       (called inside SelectResearchOpportunities TX)
  → OpportunitySelectionCandidate                          PRODUCTION
  → SelectResearchOpportunities                            PRODUCTION
  → ResearchOpportunity (REGISTRY_EXTERNAL_EXPLORATORY)    PRODUCTION
  → AutonomousResearchController.step()                    PRODUCTION
  → ProposeResearchHypothesis._admit_registry_external     PRODUCTION
       (no ModelPort)
  → compile_identity_anomaly_experiment                    PRODUCTION
       → AuthorizationDifferentialCompiler
       → plan_authorization_differential
  → PreparePlannedExperiment / ExecutePlannedExperiment    PRODUCTION
       (fresh Core authorize)
  → WorkerPort                                             PRODUCTION
  → EvaluateExperimentFeedback                             PRODUCTION
  → PromoteOnAssessment                                    PRODUCTION
  → PromotionPipeline (sealed MR-5)                        PRODUCTION
  → FindingProposal                                        PRODUCTION
  → STOP before Human Review / Finding                     PRODUCTION
```

| Edge / adapter | Class |
|---|---|
| `ARC.step()` → `SelectResearchOpportunities` → admit | PRODUCTION |
| Fixture `ExecutePlannedExperiment` to produce the source Observation | TEST_ONLY invocation of PRODUCTION Worker/Core |
| `ExecuteExploratoryResearch` / `ExploratorySignalInput` / operator `compile_arguments` | LEGACY_MANUAL; not required for the qualified path |
| `ExecuteExploratoryHypothesis` | LEGACY_MANUAL |
| `ARC.run_managed_cycle` | CONDITIONAL; used by known-family `RunResearchSelection` and legacy exploratory adapters; **not** used by the qualified ARC.step() path |
| `DraftExploratoryHypothesis` | LEGACY_MANUAL; family-promotion tests only |
| `PromoteExploratoryFamily` | PRODUCTION family gate; HUMAN_OPERATOR only; not required to test exploratory hypothesis |
| Custom second `run_managed_cycle` lifecycle for exploratory | DEAD for the qualified path |

Critical answers:

1. ARC naturally discovers the exploratory Opportunity: **yes** (`admit` is inside Select, Select is inside `step()`).
2. `ExecuteExploratoryResearch` is **not** required for production MR-6.
3. No custom `run_managed_cycle` is a second exploratory lifecycle.
4. Anomaly producer stops at OpportunitySelectionCandidate. No execution authority.
5. `PromotionPipeline` remains the sealed MR-5 component.

## D. Real Anomaly Source

**source:** `HTTP_AUTHORIZATION_DIFFERENTIAL` Observation from normal Worker execution.

**durability:** persisted in PostgreSQL; `worker_result_id` is NOT NULL FK.

**same-run:** `observation.worker_result.research_run_id` must equal the admitting run.

Negative cases (no exploratory Opportunity, no Hypothesis, no Worker on the blocked path):

| Case | Result |
|---|---|
| Fake observation id | `REJECT_UNRESOLVED_SOURCE` |
| Observation from another run | `REJECT_CROSS_RUN` |
| Observation with missing WorkerResult | `REJECT_UNRESOLVED_SOURCE` |
| WorkerResult from another run | `REJECT_CROSS_RUN` |
| Incomplete identity context | `MISSING_COMPILER_SEMANTICS` |
| `registry_external=true` in payload | ignored; classification is computed |
| Secure cross-status ≠ 200 | `REJECT_NO_ANOMALY` |
| OUT_OF_SCOPE origin at **admission** | not origin-scope gated; Core denies at execute (`Worker=0`) |
| UNKNOWN origin missing `authorized_origin` | `MISSING_COMPILER_SEMANTICS` |
| UNKNOWN graph node (`scope_classification=UNKNOWN`) | OBJECT_AUTHORIZATION precondition is IN_SCOPE, so it does not own; may classify exploratory; Core still gates origin |

Admission is identity/same-run/compiler-semantics based. Scope is Core-time. Opportunity is not an execution token.

No raw secret material is stored in the source payload (actor/object/origin/status only).

## E. Opportunity / ARC Integration

Single next-action owner: `AutonomousResearchController`.

`admit_registry_external_anomaly_candidates` writes PENDING candidates only.
`SelectResearchOpportunities` is the sole reader that can admit a `ResearchOpportunity`.
`ARC.step()` is the sole production caller that then admits the exploratory Hypothesis and dispatches.

Dedup: structural identity is run+origin+actor+own+cross+strategy (not observation `source_id`). Same anomaly repeatedly → one candidate/opportunity. Concurrent admission → one authoritative opportunity (PostgreSQL unique + SAVEPOINT).

## F. Registry-External Classification

Computed, not asserted.

| Case | Result |
|---|---|
| Empty graph, unmatched identity differential | REGISTRY_EXTERNAL |
| IN_SCOPE HTTP_OPERATION matching OBJECT_AUTHORIZATION | KNOWN_FAMILY; no exploratory duplicate |
| Disabled family | not in `list_enabled()` / `families_for_node` skips `enabled=False`; treated as unmatched → exploratory. Intended: disabled = not owning. |
| Two matching families (name/id collision) | fail-closed to KNOWN_FAMILY (both IDs own) |
| Caller/model `registry_external=true` | no such production parameter; payload flag ignored |

Known-family matching is attempted first via `owning_identity_families(graph, enabled_registry)`.

## G. Hypothesis

- Deterministic origin `exh:{sha256(structural_identity)}`
- Same run; source_refs on the Opportunity
- Registry-external classification computed before admit
- HYPOTHESIZED; claim is `IDENTITY_ANOMALY_CLAIM` (not a vulnerability)
- No severity authority, no Evidence, no scope grant, no HunterFamily write
- ModelPort is not invoked on this path
- Same anomaly 10 cycles / concurrent ARC steps → one hypothesis
- Distinct identity context → separate candidate/hypothesis
- Different run → separate allowed
- Known-family explanation appearing later does not mint a second exploratory object; the already-admitted exploratory Hypothesis remains (not retracted)

## H. Discriminating Experiment

**Hypothesis A:** observed identity-dependent divergence may indicate an unmodelled authorization/state invariant.

**Alternative B:** benign representation or session variance (`IDENTITY_ANOMALY_ALTERNATIVE`; also `LEGITIMATE_PUBLIC_ACCESS` / delegated / shared in the evaluator).

**Control:** unauthenticated deny (401) and secure-control deny (403).

**Expected:** `HTTP_AUTHORIZATION_EXPECTED_OBSERVATION` — own object readable, cross-object returned with the other actor’s owner proven, unauthenticated denied, secure control denies.

**Disconfirming:** `HTTP_AUTHORIZATION_DISCONFIRMING_OBSERVATION` — cross-object denied, or returned object is not the other actor’s object (including PUBLIC).

**Compiler:** `AuthorizationDifferentialCompiler` → `plan_authorization_differential`.

**Durable context source:** observation payload fields `authorized_origin`, `actor`, `own_object`, `cross_object`, `mode` via `compiler_arguments_from_context`. Production path does **not** take operator `compile_arguments`. Query/body/headers are absent. Identities come from the durable source.

This is real A-vs-B discrimination in `HttpAuthorizationDifferentialEvaluator`, not merely “authorization-shaped fields.”

## I. Compiler → Core → Worker

Existing compiler/Core tests remain green. On the qualified path:

- Unknown capability / unknown action / extra arguments → compile reject
- Fabricated / cross-family mutation cell → blocked (checkpoint 11)
- Arbitrary query/body/headers → not in compiler arguments; extra args reject
- Risk understatement → `RISK_UNDERSTATEMENT`
- Expired AuthorizationSource / OOS compiled_scope / exhausted budget → Core DENY, Worker=0
- Opportunity/Hypothesis is not an execution token

## J. End-to-End Registry-External Lab

Independent requalification lab (`tests/integration/test_canonical_mr6_full_requalification.py`):

```
fixture Worker action
  → WorkerResult wr:a7d5f456-9ee0-44fc-aafd-5d10eb701814 (run-1)
  → Observation obs:a7d5f456-9ee0-44fc-aafd-5d10eb701814:HTTP_AUTHORIZATION_DIFFERENTIAL:...
  → anomaly candidate / ResearchOpportunity f2f1477ee340f39e… (same structural identity)
  → exploratory Hypothesis 8d302e0e-3917-492c-9c84-b0b736cd2376 (origin exh:b5742f54…)
  → Experiment faf28351613e6228…
  → ExecutionAttempt/request ea:7b28fd73… / ea:a201b6e6… (follow-up)
  → follow-up Observations obs:7b28fd73…, obs:a201b6e6…
  → Assessment 5b8d71dc… CONSISTENT_WITH_PREDICTION
  → Evidence 43ca4ab9…, 762a1964…
  → Candidate c882815b… VALIDATED
  → Verification 91fed048…
  → FindingProposal b52a05be…
  → Finding=0
```

IDs are from one reconstructable PostgreSQL run. No `ExploratorySignalInput`. No operator `compile_arguments`. No permanent HunterFamily used for discovery. No test-only promotion shortcut.

## K. Vulnerable / Secure / Deceptive

| Stage | Vulnerable | Secure | Deceptive |
|---|---|---|---|
| exploratory_opportunities | 1 | 0 | 1 |
| exploratory_hypotheses | 1 | 0 | 1 |
| experiments | 3 | 2 | 2 |
| observations | 3 | 1 | 2 |
| supporting_evidence | 2 | 0 | 0 |
| candidates | 1 | 0 | 0 |
| validated_candidates | 1 | 0 | 0 |
| finding_proposals | 1 | 0 | 0 |
| findings | 0 | 0 | 0 |
| enabled families | 16 (seed only) | 16 | 16 |

Secure: no exploratory promotion (cross-status 403 is not an anomaly).
Deceptive: hypothesis exists; PUBLIC control → CONTRADICTS (`LEGITIMATE_PUBLIC_ACCESS`); no VALIDATED candidate.

## L. False-Positive Ladder

| Level | Secure | Deceptive |
|---|---|---|
| false_exploratory_opportunity | 0 | 1 (acceptable noise: real 200 + owner confusion until control) |
| false_exploratory_hypothesis | 0 | 1 (acceptable) |
| false_supporting_evidence | 0 | 0 |
| false_candidate | 0 | 0 |
| false_validated_candidate | 0 | 0 |
| false_finding_proposal | 0 | 0 |
| false_finding | 0 | 0 |

A deceptive Hypothesis is acceptable. A deceptive validated Candidate is not. Hard requirement `false_finding = 0` holds.

## M. Operational Failure

| Failure | Result |
|---|---|
| Core DENY / OOS compiled_scope | Opportunity may exist; Worker=0; `CORE_BLOCKED` |
| Expired AuthorizationSource | Worker=0; `CORE_BLOCKED` |
| Budget exhausted after source | Worker=0 |
| Worker TIMED_OUT | exploratory hyp may exist; assessment `EXECUTION_UNUSABLE` (or inconclusive); not disconfirmation; no VALIDATED candidate |
| Worker PROCESS_FAILED | not hypothesis rejection (existing execute tests) |
| UNKNOWN_OUTCOME | ARC `_unknown_open` stops; no blind retry; Worker calls unchanged |
| Malformed WorkerResult | existing execute/ingest fail-closed |
| DB uniqueness races | SAVEPOINT; treated as durable no-op |

Operational failure ≠ unsupported hypothesis ≠ falsified hypothesis ≠ vulnerability proof.

## N. MR-5 Integration

**CANONICAL_MR5_CLOSED=YES**

Same Evidence admission, Candidate, Verification, FindingProposal objects.
No exploratory-specific bypass tables.
Finding remains HUMAN_OPERATOR review + Core finalization.
No autonomous Finding.

Targeted: `tests/integration/test_mr5_durability_seal.py`, `tests/integration/test_canonical_mr5_mr6.py` green.

## O. Permanent Family Gate

HUMAN_OPERATOR APPROVE only.
CONTROL_PLANE / model actor cannot write.
REJECT does not write.
Tampered `may_write` hard-fails.
Duplicate / concurrent 10× PostgreSQL → one permanent family version.
Name collision → `FAMILY_NAME_ALREADY_REGISTERED` / `ALREADY_PROMOTED`.
Testing exploratory hypothesis does **not** require permanent promotion.

## P. Concurrency

Real PostgreSQL, 10 repetitions of 2 concurrent `ARC.step()` on the same anomaly:

| Object | Authoritative count |
|---|---|
| exploratory Opportunity | 1 |
| exploratory Hypothesis | 1 |
| uncaught uniqueness races | 0 |
| duplicate Worker dispatch for same exploratory unit | 0 (cycle unique + origin unique) |

Concurrent family promotion 10× → one family row.
MR-5 durability concurrency remains green.

## Q. Restart

Fresh ARC/UoW instances after source Observation, Opportunity, Hypothesis, Experiment, WorkerResult, Assessment, Evidence, Candidate, Verification, FindingProposal: no duplicate exploratory identity; Finding=0.

**Limitation (not fabricated daemon recovery):** authorized-not-dispatched / DISPATCHING recovery is modeled by ARC resume + `_unknown_open`, not by research-osd. There is no persistent supervisor. State this exactly: some mid-dispatch crashes require the Phase J daemon to be a full operational owner. This does not block the qualified research path.

## R. Provenance / Research Trace

**SUFFICIENT_FOR_CURRENT_MR6**

Durable/audit state can answer:

- why anomaly was considered: `REGISTRY_EXTERNAL_ANOMALY_CANDIDATES_PROPOSED`
- why known families did not own it: empty owning set, or `REGISTRY_EXTERNAL_ANOMALY_ROUTED_KNOWN_FAMILY` when they did
- why exploratory hypothesis was admitted: `REGISTRY_EXTERNAL_HYPOTHESIS_ADMITTED` + origin `exh:`
- alternative explanation: Opportunity `unresolved_question` + plan disconfirming observation + evaluator `LEGITIMATE_*` reason codes (the in-memory `HypothesisChallenge` is not separately persisted; `del challenge` in `_persist_exploratory`)
- why AuthorizationDifferential was selected: hardcoded compiler for this source class
- expected vs disconfirming: experiment plan fields + assessment rationale
- final assessment: `hypothesis_assessment`

This is audit quality, not authority.

## S. Regression

| Checkpoint | Result |
|---|---|
| 9 Mutation / V3 / Protocol raw-exchange | green (`test_slice4b_4c_execution`, mutation identity) |
| 10 canonical MR-5 durability | green |
| 11 mutation-cell identity | green |
| 12 / MR-6A anomaly admission | green |

Targeted combined run: **111 passed**.

## T. Test Evidence

| Suite | Result |
|---|---|
| `python -m compileall src tests scripts` | OK |
| `pytest tests/unit -q` | **1428 passed**, 4 skipped |
| `pytest tests/integration -q` | **218 passed**, 2 failed (1 known SD-G4 + 1 requalification flake that was fixed; re-run of Full MR-6 file: 6 passed). After the provenance-list-order fix, Full MR-6 file is green. Integration remaining failure: SD-G4 only. |
| `pytest tests/e2e -q` | **152 passed**, 5 skipped, **4 failed** (cli_session isolation) |
| MR-6A + Full requalification | 16 passed (then 6 passed after isolation re-run) |
| Checkpoint 9/10/11/12 + architecture + family | 111 passed |

## U. Known Unrelated Failures

1. **SD-G4 token-economy dual CHECK** — `tests/integration/test_sd_g4_token_economy.py::test_cheap_call_records_tokens_and_deny_when_limit_reached` inserts `MODEL_TOKENS_IN` against `ck_budget_consumption_resource_type`. Pre-existing. Not an MR-6 defect.
2. **full-e2e cli_session isolation** — GATE 14/15/16/17 “no model runtime” still see `research_os.integrations.models.cli_session`. Pre-existing. Not an MR-6 defect.

## V. Hard-Fail Matrix

| Condition | Status |
|---|---|
| anomaly source caller-fabricated | FAIL closed (unresolved / missing WR) |
| cross-run source accepted | no |
| no normal ResearchOpportunity path | no; Select admits it |
| ARC does not select exploratory opportunity | no; `step()` Select owns it |
| manual ExecuteExploratoryResearch required | no |
| second scheduler exists for this path | no |
| known-family anomaly mislabeled exploratory | no |
| operator supplies production compile_arguments | no |
| experiment is random rather than discriminating | no |
| model controls executable payload | no; ModelPort unused |
| arbitrary capability invented | no |
| arbitrary mutation cell invented | no (checkpoint 11) |
| arbitrary protocol bytes/host | no |
| opportunity grants authority | no; Core still gates |
| Core bypass | no |
| WorkerResult direct Evidence | no |
| MR-5 bypass | no |
| deceptive fixture creates validated Candidate | no |
| deceptive fixture creates FindingProposal | no |
| permanent family without human approval | no |
| duplicate authoritative objects under concurrency | no |
| UNKNOWN_OUTCOME blind retry | no |
| Finding automatic | no |
| checkpoint 9/10/11/12 regression | no |
| maturity flags changed | no |

## W. Maturity

Unchanged:

```
LIVE_MODEL_VALIDATED=False
SECURITY_RESEARCH_VALIDATED=False
PRODUCTION_READY=False
GATE_04B=PENDING
GATE_21=PENDING
```

## X. Final Flags

```
MR6A_ARC_ANOMALY_ADMISSION_QUALIFIED=YES
CANONICAL_MR5_CLOSED=YES
MUTATION_CELL_IDENTITY_QUALIFIED=YES

MR6_FULL_EXPLORATORY_QUALIFIED=YES
CANONICAL_MR6_CLOSED=YES
```

## Y. Repository Seal

Checkpoint 13 + annotated local tag `canonical-mr6-closed`.
Prior checkpoints 9–12 were not amended. Not pushed unless requested.

## Z. Next Authorized Unit

**Phase J / research-osd: durable recovery of authorized-not-dispatched and DISPATCHING exploratory execution.**

Do not implement it in this seal. Full MR-6 qualifies the research path, not a 24/7 supervisor. The remaining honest gap is operational liveness of mid-dispatch crash recovery, not a second anomaly source and not GATE 04B.

## Close wording

Canonical MR-6 is closed for one autonomous registry-external, run-scoped exploratory research path driven by a real durable anomaly inside the normal ARC lifecycle. The path uses deterministic typed experiment compilation, fresh Core authorization, bounded Worker execution, and canonical MR-5 promotion. Exploratory reasoning grants no authority and permanent HunterFamily promotion remains Human Operator-gated. Finding remains Human Review + Core gated.

This does not prove universal zero-day discovery or field performance.

## Self-audit

1. Anomaly real and durable? YES
2. Source same-run? YES
3. ARC owned selection? YES
4. Known-family matching first? YES
5. Registry-external classification computed? YES
6. Hypothesis merely a hypothesis? YES
7. Experiment derived from durable context? YES
8. Alternative explanation represented? YES
9. Control present? YES
10. Deterministic compiler created exact plan? YES
11. Core authorized fresh? YES
12. Model avoided payload authority? YES
13. WorkerResult remained untrusted? YES
14. Canonical MR-5 handled promotion? YES
15. Deceptive stayed below validated Candidate? YES
16. Finding remained human? YES
17. Family promotion remained human? YES
18. Concurrency stayed unique? YES
19. Restart avoided replay of exploratory identity / Finding? YES (daemon mid-dispatch recovery deferred)
20. Sealed checkpoints green? YES
21. Avoided manual exploratory adapter to fake production? YES
22. Avoided calling one lab “universal zero-day”? YES
