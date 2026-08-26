# Zest Operations Notes

## SD-G8 — Coverage Debt

### Scope

SD-G8 builds a deterministic, LLM-free coverage-debt matrix that tells the
operator where the hunt is incomplete:

- **Coverage Debt Core** (`zest.research.coverage`): `CoverageCell`,
  `CoverageMatrix`, and `compute_coverage_debt(graph, registry, hypotheses_view)`.
  The matrix is indexed by `(node_canonical_key, identity_id, family_id)`.
- **Identity-agnostic boundary**: `HypothesisRecord` does not carry identity, so
  SD-G8 treats a hypothesis as covering all identity cells of its
  `(node, family)` pair. Per-identity binding is scheduled for SD-G9.
- **Scope partition**: only `IN_SCOPE` nodes produce debt cells.
  `UNKNOWN`/`OUT_OF_SCOPE` nodes are emitted as `NOT_APPLICABLE` and do not
  contribute to `total_debt`.
- **Determinism**: the matrix hash is computed from a canonical, sorted JSON
  serialization of all cells; permutations of the input produce the same hash.
- **Persistence**: `coverage_debt_snapshot` stores only
  `matrix_hash + cell_counts + total_debt`; the full matrix remains rebuildable
  from the ledger.

### Coverage States

| State | Meaning |
|-------|---------|
| `NOT_APPLICABLE` | Node is not an active hunt target (`UNKNOWN`/`OUT_OF_SCOPE`). |
| `UNTESTED` | No hypothesis exists for the cell. |
| `HYPOTHESIZED` | Hypothesis exists but has not passed V1. |
| `V1_PASSED` | Static scope/precondition/budget checks passed. |
| `V2_PASSED` | Passive/yielded evidence confirmed the hypothesis. |
| `V3_QUEUED` | Approved for active experiment (still pending execution). |
| `COVERED` | Hypothesis fully validated; no debt for this cell. |

### Registry Integration

- `CoverageDebtView` loads the latest enabled version of each `hunter_family`
  row (append-only versioning: higher `version` wins).
- `families_for_node` evaluates node kind + edge preconditions against the
  graph; only applicable families open cells.
- Hypothesis progress is read from `hypothesis` + `audit_event` tier events
  (`HYPOTHESIS_TIER_V1_PASSED`, `HYPOTHESIS_TIER_V2_PASSED`,
  `HYPOTHESIS_TIER_V3_QUEUED`, and rejection events).

### Operator Visibility

- CLI: `zest coverage --research-run-id <id>` prints total debt,
  per-family debt, per-state counts, top-10 nodes, and the matrix hash.
- Optional persistence: `CoverageDebtView.execute(..., persist=True)` writes a
  `coverage_debt_snapshot` record and returns the generated `snapshot_id`.

### Runbook

- `GATE_08_STATUS` stays `PENDING` until the independent architect audit seals
  the gate.
- Full suite commands are the same as SD-G7.

## SD-G9 — HunterScore Scheduler + Identity Binding

### Scope

SD-G9 closes the SD-G8 identity-agnostic boundary and adds a deterministic
priority queue so the operator can see what the system would hunt next:

- **Identity binding**: `HypothesisRecord` and `HuntV3QueueRecord` now carry an
  `identity_id` column (nullable for legacy rows). `GenerateHuntHypotheses`
  produces one hypothesis per `(node, identity, family)` tuple. Nodes without
  explicit identities use `ANONYMOUS`.
- **Identity expansion cap**: per node, expansion is capped at
  `MAX_IDENTITIES_PER_NODE = 8` to prevent combinatorial noise. When the cap is
  hit, an `IDENTITY_EXPANSION_CAPPED` audit event is written and the remaining
  identities stay `UNTESTED` for future cycles.
- **HunterScore core** (`zest.research.scheduler`): deterministic score
  for every debt cell. Score components:
  - `state_weight`: `UNTESTED > HYPOTHESIZED > V1_PASSED > V2_PASSED > V3_QUEUED > COVERED`.
  - `family_success_bonus`: a bounded historical prior from supported/falsified
    hypothesis assessments. The prior is capped so one historically successful
    family cannot dominate the whole coverage matrix.
  - `family_exploration_bonus`: low-history or missing-history families receive
    a small deterministic exploration bump so novel families are not starved.
  - `freshness_bonus`: latest node activity, not first-seen age, drives hunt
    freshness. `first_seen_at` remains audit context; `latest_activity_at`
    handles old assets that changed recently.
  - `budget_suitability_bonus`: when the daily LLM budget is exhausted,
    V3-bound cells (`V2_PASSED`, `V3_QUEUED`) are penalized and cheap-path cells
    receive a small bonus.
- **Explainability**: every `HunterScore` carries a component breakdown
  (`explanation` tuple) so the ranking is never a black box.
- **Scheduler use case**: `RunHuntScheduler` rebuilds the coverage-debt matrix,
  scores all debt cells, selects the top N, and writes a
  `HUNT_SCHEDULE_RECOMMENDED` audit event. It does not write to the V3 queue.
- **Cycle intake**: `RunHuntCycle` can optionally consume a schedule; the V1/V2/V3
  tier gates and the `IN_SCOPE` V3 enqueue lock remain unchanged.

### Determinism Guarantees

- Same graph + registry + ledger + budget view + reference time always yields the
  same ranked list.
- Tie-break is deterministic: descending score, then ascending
  `(node_canonical_key, identity_id, family_id)`.

### Operator Visibility

- `RunHuntScheduler` writes `HUNT_SCHEDULE_RECOMMENDED` events with
  `matrix_hash`, `recommended_count`, and the top cells with their scores and
  state.
- The schedule can be consumed by `RunHuntCycle` for automated execution or kept
  as a recommendation only.
- SD-G9 seal includes starvation/lock-in regression tests proving bounded family
  prior, low-history exploration, and latest-activity freshness.

### Runbook

- `GATE_09_STATUS = "PASS"`.
- Seal evidence (2026-08-20): `1450 passed, 9 skipped, 44 warnings, 53 subtests
  passed` via full `pytest` against the local PostgreSQL integration database.
- Required commands:
  ```bash
  source .venv/bin/activate
  bash scripts/start_wsl_test_postgres.sh
  python -m pytest tests/integration/test_sd_g9_hunterscore_scheduler.py -q
  python -m pytest tests/unit tests/contract -q
  python -m pytest tests/integration -q
  python -m pytest tests/e2e -q
  python -m pytest -q
  ```

## SD-G10 — Independent Validator + Severity Engine + Circuit Breaker

### Scope

SD-G10 starts the attack-period validation/economy layer after HunterScore:

- **Independent validator**: required V1/V2/V3 tiers must pass before downstream
  admission. Missing tiers fail closed. `V3_QUEUED` is not a validator PASS.
- **Severity engine**: severity is downstream of validator PASS and IN_SCOPE
  status. It maps internal `P0`-`P3` to platform-style Bugcrowd/HackerOne
  labels, but does not write severity into Hypothesis, Observation, Evidence,
  Candidate, or early FindingProposal rationale.
- **Family circuit breaker**: rejected/inconclusive telemetry can throttle a
  family, but the breaker must never disable or delete a family.

### Runbook

- `GATE_10_STATUS = "PASS"`.
- SD-G10 is not old infrastructure `GATE 10 — Runtime / Strix Boundary
  Integrity`.
- Current P1 domain tests:
  ```bash
  python -m pytest tests/unit/research/validation tests/unit/test_maturity.py -q
  ```
- P1 evidence (2026-08-20): `1461 passed, 9 skipped, 53 subtests passed` via
  full `pytest`; Alembic deprecation warnings removed with `path_separator = os`.
- P2 application integration evidence (2026-08-20):
  - `SubmitFindingProposal` rejects security candidates without append-only
    validator tier PASS evidence through V3.
  - Diagnostic-only proposals stay exempt from the security validator gate.
  - Rejections write `FINDING_PROPOSAL_VALIDATION_REJECTED` audit events and
    return `REJECTED_VALIDATION_NOT_PASSED`.
  - Focused checks: `131 passed` for Gate14-Gate17 e2e, `31 passed` for
    SD-G10 finding-admission unit/integration coverage.
  - Full suite: `1465 passed, 9 skipped, 53 subtests passed`.
- P3/P4/P5 seal evidence (2026-08-20):
  - `ScoreFindingSeverity` writes severity decisions only to append-only audit
    events, never into Hypothesis, Observation, Evidence, Candidate, or early
    FindingProposal records.
  - `EvaluateFamilyCircuitBreaker` reads family telemetry from the append-only
    ledger and can only `ALLOW` or `THROTTLE`; it never disables/deletes a
    family.
  - PostgreSQL SD-G10 integration covers V1/V2 missing, V3 queued, deterministic
    severity scoring, out-of-scope/validation-missing non-scoring, and
    throttle-without-disable telemetry.
  - Focused SD-G10 checks: `29 passed`.
  - Affected SD-G7/SD-G10/Gate14-Gate17 checks: `167 passed`.
  - Full suite: `1470 passed, 9 skipped, 53 subtests passed`.

## SD-G11 — Production Executor Fabric

### Scope

SD-G11 starts the Attack Muscle production executor fabric. This is **not** old
infrastructure `GATE 11 — Runtime Routing Integrity`, and it is **not** G21
browser/application-state maturity.

Current P1 slice:

- `BuildExecutorReplayManifest` reads persisted Experiment, ExecutionAttempt,
  WorkerResult, and Observation rows without redispatching a Worker.
- It produces a canonical replay manifest plus SHA-256 hash.
- Raw result, diagnostics, artifact descriptors, and observation payloads are
  represented by redacted digests only.
- Replay class is explicit: `DETERMINISTIC_REPLAY`,
  `ENVIRONMENT_SENSITIVE`, `HUMAN_REVIEW_REQUIRED`, or `NOT_REPLAYABLE`.
- Browser and stateful side-effect outputs are not treated as deterministic
  replay.

Current P2 slice:

- `BuildExecutorReplayBundle` wraps the replay manifest with a deterministic
  bundle hash.
- Durable ExperimentPlan rows are represented as request-template fingerprints;
  raw arguments are not copied into the bundle.
- WorkerResult response bodies, diagnostics, control signals, and artifact
  descriptors are represented by digests only.
- Screenshot/trace/response artifact presence is retained through descriptor
  kind + digest metadata.
- Replay controls fail closed: no automatic redispatch, Core authorization
  required, redirect reauthorization required, and human review required for
  high side-effect replay classes.

Current P3 slice:

- `AssessExecutorFabricExperiment` reads WorkerResult rows plus replay
  manifest/bundle hashes and emits deterministic fabric invariant assessments
  without redispatching Workers.
- `http.transaction` now supports HTTPS loopback transport while preserving
  Core-issued network envelope enforcement and redirect STOP behavior.
- The packaged Worker copy stays byte-for-byte synchronized with the runtime
  Worker implementation.
- PostgreSQL SD-G11 vertical slice covers HTTPS API execution, browser ledger
  environment-sensitive assessment, vulnerable/secure/deceptive workflow
  fixtures, scope-escape envelope blocking, and redirect reauthorization.

### Runbook

- SD-G11 status is `PASS`.
- G21 remains `PENDING`; local Chromium browser checks passed, but cgroup
  containment skipped without delegation and required mode fails closed.
- P1 evidence (2026-08-20): `6 passed` for replay manifest unit coverage plus
  PostgreSQL G19 ledger integration.
- P2 evidence (2026-08-20): `9 passed` for replay manifest, replay bundle, and
  PostgreSQL G19 ledger integration.
- P3 evidence (2026-08-20): `36 passed` focused HTTPS/worker/fabric checks,
  `104 passed` affected checks, and `1484 passed, 9 skipped, 53 subtests passed`
  full suite.
- P1 affected checks (2026-08-20): `35 passed, 5 skipped`.
- P1 full suite (2026-08-20): `1474 passed, 9 skipped, 53 subtests passed`.

## SD-G12 — Broad Injection Wave

### Scope

SD-G12 starts the broad injection wave after the production executor fabric. This
is attack-period SD-G12, not old infrastructure `GATE 12` or old `GATE 13`.

Current P1 slice:

- HunterFamily seed rows now include SQLi, SSTI, LFI/RFI/path traversal, mass
  assignment, JWT crypto/claim confusion, CORS credential-exfiltration chain,
  GraphQL authorization/injection, DOM taint/client-side execution, and AI/LLM
  prompt-injection/context-leakage/tool-abuse families.
- These rows create coverage debt for input-bearing AttackSurfaceGraph nodes.
- They do not create Evidence, Candidates, Findings, Worker dispatches, severity,
  confidence, or vulnerability truth.
- Every SD-G12 seed remains IN_SCOPE-gated; P3 now promotes the broad-injection
  families to V3 plan admission.

Current P2 slice:

- `build_mutation_matrix` creates deterministic matrix plans from HunterFamily
  evidence requirements.
- Matrices enforce a 30-cell minimum, bounded maximum, explicit controls, and
  fail-closed unknown dimensions.
- Matrix planning does not create payload bodies, WorkerRequests, Evidence,
  Candidates, or Findings.

Current P3 slice:

- SD-G12 broad-injection families enqueue `mutation.matrix` / `plan` V3 records
  after V1+V2 pass.
- Queue arguments contain matrix metadata only: hash, version, cell count,
  dimension count, and control count.
- Worker dispatch remains forbidden until explicit operator/Core approval.
- Mutation-matrix V3 records are side-effect level 0 and carry no payload/body
  content.
- Legacy exposed API spec, unprotected hostname, and known-CVE surface families
  remain V2; old authorization/workflow V3 behavior is unchanged.

### Runbook

- SD-G12 status is `PASS`.
- P1 evidence (2026-08-20): `4 passed` focused, `39 passed` affected, and
  `1488 passed, 9 skipped, 53 subtests passed` full suite.
- P2 evidence (2026-08-20): `4 passed` focused, `46 passed` affected, and
  `1492 passed, 9 skipped, 53 subtests passed` full suite.
- P3 evidence (2026-08-20): `11 passed` focused, `29 passed` affected, and
  `1492 passed, 9 skipped, 53 subtests passed` full suite.

## SD-G13 — Protocol/Parser Specialist

### Scope

SD-G13 starts the protocol/parser specialist lane after the broad injection
wave. This is attack-period SD-G13, not old infrastructure `GATE 13`
operational readiness.

Current P1 slice:

- Protocol families now cover HTTP request smuggling/desync and HTTP cache
  poisoning/deception.
- These families require `protocol_surface_signals` on the AttackSurfaceGraph
  node before they create coverage debt or hypotheses.
- `build_protocol_parser_plan` creates deterministic `protocol.parser.v1`
  plans with required surface signals, dimensions, controls, bounded steps, and
  SHA-256 plan hashes.
- V3 admission queues `protocol.parser` / `plan` records only; no payload/body
  content is stored.
- Protocol parser queue records are side-effect level 3 and require SE3 before
  Worker dispatch.

Current P2 slice:

- `ApproveHuntV3Queue` moves V3 queue records to `APPROVED` only when a recorded
  human approval exists for `hunt-v3-queue:<queue_id>`.
- SE3 protocol parser queue records missing `approval_required=SE3` remain
  pending even if an approval record exists.
- Approval decisions are audited and do not dispatch Workers.

### Runbook

- SD-G13 status is `PASS`.
- P1 evidence (2026-08-20): `39 passed` focused and `50 passed` affected.
- P2 evidence (2026-08-20): `6 passed` focused with PostgreSQL integration.
- Seal evidence (2026-08-20): `74 passed` affected and
  `1507 passed, 9 skipped, 53 subtests passed` full suite.

## SD-G14 — Report, Duplicate Economics, and n-day Lane

### Scope

SD-G14 starts the report/duplicate/n-day operations lane after protocol/parser
specialists. This gate packages approved Findings for human review and platform
submission workflows; it does not submit reports automatically.

Current P1 slice:

- `build_finding_report_package` creates deterministic `report.package.v1`
  packages from approved Finding content.
- Packages include proof anchors, reproduction anchors, duplicate metadata, and
  safety metadata, but no raw payload/body content.
- Internal duplicate fingerprints normalize title, claim, and classification.
- External duplicate signals are advisory metadata only and do not change
  Finding truth.
- `PackageFindingReport` loads an approved Finding, builds the package, and
  records an audit event.

Current P2 slice:

- `evaluate_disclosed_report_duplicate_signal` normalizes external disclosed
  report or program-page duplicate signals.
- External signals get SHA-256 fingerprints and remain advisory
  `POTENTIAL_MATCH` metadata, not duplicate verdicts.
- Unrelated external signals return `NO_MATCH` without changing Finding truth.
- Provider metadata containing secret/raw request keys fails closed.

Current P3 slice:

- `match_nday_advisories` maps in-scope observed technology versions to
  provider-supplied advisory/CVE records.
- n-day output is `AFFECTED_VERSION_CANDIDATE` metadata only, not a Finding.
- Out-of-scope observations return no matches.
- Unsupported version/range formats fail closed instead of producing weak
  matches.

### Runbook

- SD-G14 status is `PASS`.
- P1 evidence (2026-08-20): `7 passed` focused, `53 passed` affected, and
  `1514 passed, 9 skipped, 53 subtests passed` full suite.
- P2 evidence (2026-08-20): `11 passed` focused, `57 passed` affected, and
  `1518 passed, 9 skipped, 53 subtests passed` full suite.
- P3 evidence (2026-08-20): `16 passed` focused and
  `74 passed, 10 subtests passed` affected.
- P4 evidence (2026-08-20): `17 passed` focused, `63 passed` affected, and
  `1524 passed, 9 skipped, 53 subtests passed` full suite.

## SD-G15 — Continuous Change, Live Coverage Debt, and Recall

### Scope

SD-G15 keeps the temporal lane from becoming a vulnerability oracle while
making coverage debt live for the operator. It also consolidates benchmark
recall across existing family scorecards without allowing averages to hide a
miss or false finding.

Current P1 slice:

- `assess_live_coverage_debt` compares durable coverage-debt snapshot summaries
  and reports count/total-debt deltas.
- `RefreshLiveCoverageDebt` persists a new coverage snapshot, attaches
  in-window ChangeEvents as context, and writes
  `LIVE_COVERAGE_DEBT_REFRESHED`.
- The audit payload explicitly carries `not_a_vulnerability`, `not_evidence`,
  `not_candidate`, and `not_finding`.
- The use-case does not create Evidence, Candidate, Finding, Approval,
  HumanReview, or V3 queue state.

Current P2 slice:

- `consolidate_recall_report` produces `recall.consolidated.v1` from object
  authorization, workflow authorization, and research-selection scorecards.
- Recall is reported per family as exact fractions.
- `weighted_average_allowed = false`; any family miss, false finding, or hard
  failure fails the consolidated gate.

### Runbook

- SD-G15 status is `PASS`.
- P1/P2 focused evidence (2026-08-20): `10 passed` including PostgreSQL vertical
  slice when `ZEST_TEST_DATABASE_URL` is configured.
- Discovery regression evidence (2026-08-20): `37 passed`.
- Affected evidence (2026-08-20): `44 passed`.
- Seal evidence (2026-08-20): `1534 passed, 9 skipped, 53 subtests passed`.

## SD-G16 — Exploratory Hypothesis Generator

### Scope

SD-G16 adds a registry-external exploratory lane. It can draft a new
human-reviewable family idea when sourced anomaly signals do not map to enabled
`HunterFamily` rows, but it cannot admit that family into the registry and it
cannot promote the idea into Evidence, Candidate, Finding, or ImpactGraph state.

- **Exploratory domain** (`zest.research.exploratory`):
  `ExploratorySignal`, `ExploratoryHypothesisDraft`, and
  `draft_registry_external_hypothesis`.
- **Application use case** (`DraftExploratoryHypothesis`): loads the enabled
  registry read-only, persists one `HypothesisRecord`, and writes an
  `EXPLORATORY_HYPOTHESIS_DRAFTED` audit event.
- **Human approval boundary**: every draft carries
  `requires_human_family_approval=true` and
  `may_write_hunter_registry=false`; registry mutation remains a separate
  operator-approved workflow.

### Validation Boundaries

- Every generated draft starts at `HYPOTHESIZED`.
- Required gates are fixed as `HYPOTHESIZED`, `V1`, `V2`, `V3`, and
  `FALSE_FINDING_ZERO`.
- Model novelty claims such as `N4_ZERO_DAY` are retained only as advisory
  metadata; the normalized `novelty_basis` remains `UNCLASSIFIED`.
- Direct vulnerability/exploit/evidence/finding truth claims are rejected at the
  research-domain boundary.
- The use case does not create HunterFamily, Evidence, Candidate,
  FindingProposal, HumanReview, Approval, Finding, ImpactGraph, or V3 queue
  state.

### Runbook

- SD-G16 status is `PASS`.
- Focused evidence (2026-08-20): `8 passed`.
- Affected evidence (2026-08-20): `84 passed`.
- Seal evidence (2026-08-20): `1542 passed, 9 skipped, 53 subtests passed`.
- PostgreSQL availability remains a QA requirement for full sealing; missing
  PostgreSQL is SKIP/PENDING evidence, not PASS evidence.

## SD-G7 — ImpactGraph

### Scope

SD-G7 makes every impact claim in a FindingProposal traceable to a chain of
proof artifacts from the ledger. A chain without proofs cannot be admitted.

- **ImpactChain core** (`zest.research.impact`): `ImpactNode`,
  `ImpactEdge`, and `ImpactChain` with structural validation and a
  demonstrated-capability scope rule.
- **Admission integration**: `SubmitFindingProposal` rejects proposals whose
  `impact_claims` reference a missing chain, an invalid chain, or a chain whose
  claimed impact kinds exceed what the referenced proofs actually demonstrate.
- **Persistence**: `impact_chain`, `impact_chain_node`, and `impact_chain_edge`
  tables store chains append-only; `finding_proposal.impact_chain_ids` links
  proposals to their chains.

### Impact Kinds and Demonstrated Capabilities

Simple (union) mapping:

| Demonstrated capability | Allowed impact kinds |
|-------------------------|----------------------|
| `READ_OTHER_OBJECT` | `DATA_READ`, `AUTH_BYPASS` |
| `WRITE_OTHER_OBJECT` | `DATA_WRITE`, `STATE_CORRUPTION` |
| `WORKFLOW_TRANSITION_WITHOUT_AUTH` | `STATE_CORRUPTION`, `AUTH_BYPASS` |
| `AUTHENTICATED_AS_USER` | `AUTH_BYPASS` |
| `PRIVILEGE_ESCALATION_EVIDENCE` | `AUTH_BYPASS` |
| `OAST_CALLBACK_RECEIVED` | `EXTERNAL_CALLBACK` |

Composite (AND) requirements:

| Impact kind | Required capability sets |
|-------------|--------------------------|
| `ACCOUNT_TAKEOVER_PATH` | `{AUTHENTICATED_AS_USER, PRIVILEGE_ESCALATION_EVIDENCE}` OR `{AUTHENTICATED_AS_USER, CROSS_ACCOUNT_SESSION_ASSUMPTION}` |

Unknown capabilities contribute nothing (fail-closed empty set).

### Validation Rules

1. Every `ImpactNode` must reference at least one resolvable `proof_id`.
2. Every `proof_id` must exist in the ledger (`evidence`, `observation`, or
   `experiment`) and belong to the same `research_run_id` as the chain.
3. The chain must be acyclic and every edge must connect nodes inside the chain.
4. The node's `impact_kind` must be in the allowed set derived from its proofs'
   demonstrated capabilities (simple union + composite AND rules).
5. All node `scope_ref`s must stay within the same program/run boundary.
6. `SubmitFindingProposal` rejects any `impact_claims` whose registered chain
   belongs to a different research run than the proposal (cross-run provenance
   is not allowed).

### Use Cases

- `RegisterImpactChain`: persists a validated chain; performs structural
  validation + run-binding check (proofs must resolve within the chain's run).
- `SubmitFindingProposal`: rejects chains registered under a different run,
  re-validates the chain structurally and against demonstrated capabilities,
  then persists the proposal with `impact_chain_ids`.

### Runbook

- `GATE_07_STATUS` stays `PENDING` until the independent architect audit seals
  the gate.
- Full suite commands are the same as SD-G6.

## SD-G6 — Mutation Engine + OAST Core

### Scope

SD-G6 adds the attacker's planning teeth while keeping every tooth inside the
scope muzzle:

- **Mutation Engine** (`zest.research.mutation`): deterministic
  variation generation from observed `HTTP_OPERATION` / `EXACT_PATH` nodes.
- **OAST Core** (`zest.research.oast`): out-of-band callback token
  lifecycle for blind-vulnerability proof-of-concepts.
- **Rate-limit enforcement**: `program_policy.rate_limit_profile` is enforced by
  `ExecutePlannedExperiment` before any Core execution decision.

### Mutation Families

| Family | Rule | Description |
|--------|------|-------------|
| `param_pollution` | duplicate_param / array_param | Duplicate or array-style query parameters. |
| `type_juggling` | type_juggling | Numeric/boolean/float-like string values. |
| `boundary_value` | boundary | Boundary values for numeric/identifier parameters. |
| `auth_header_variation` | auth_header | Authorization and forwarding header variants. |
| `method_override` | override | Method override via header or query parameter. |
| `content_type_confusion` | content_type | Content-Type confusion for mutating requests. |
| `id_or_traversal` | traversal | Path traversal / identifier manipulation candidates. |

All variants are plans only; execution still flows through the existing
`capability → envelope → approval` gate. Variants are confined to
`IN_SCOPE` nodes; `UNKNOWN` and `OUT_OF_SCOPE` nodes produce no variants.

### Audit Payload Rules

- Each variant's `to_public_summary()` produces a size-bounded payload
  (≤ 2 KB total) with no secrets, no full response bodies, and no raw token
  values.
- Forbidden argument keys: `token`, `secret`, `password`, `api_key`,
  `private_key`, `session`, `cookie`.

### OAST Token Lifecycle

1. `AdmitOastCallback` expects the token to exist in `oast_token` table.
2. The callback timestamp is checked against `expires_at`.
3. Expired callbacks are rejected (`OAST_TOKEN_EXPIRED`) but still audited.
4. Valid callbacks are persisted as `sensor_observation` records with
   `sensor_id = "oast.loopback"` and `epistemic_status = UNTRUSTED_EXTERNAL`.
5. `AdmitSensorObservations` then admits the observation as a `DiscoveryFact`
   with `source_status = UNTRUSTED_EXTERNAL`, preserving the epistemic boundary.

### Rate-limit Enforcement

- `RateLimitProfile` is stored in `rate_limit_profile` table.
- `ProgramResearchContext.policy.rate_limit_profile` exposes it to dispatch.
- `ExecutePlannedExperiment._check_rate_limit` counts authorized attempts in the
  rolling window and returns `RATE_LIMIT_DENIED` before Core execution.
- Clock is injected; production code never calls `datetime.now()` directly.

### Live Infrastructure Boundary

- All OAST tests use the `LoopbackOastPort` fixture; live internet callbacks are
  forbidden in tests.
- Real OAST callback infrastructure (HTTP ingress, token DNS, etc.) is a
  separate operational track and is not opened by this gate.

### Runbook

- To run the full SD-G6 test suite on Kali with real PostgreSQL:
  ```bash
  source .venv/bin/activate
  python -m pytest tests/unit tests/contract -q
  python -m pytest tests/integration -q
  python -m pytest tests/e2e -q
  ```
- `GATE_06_STATUS` stays `PENDING` until the independent architect audit seals
  the gate.
