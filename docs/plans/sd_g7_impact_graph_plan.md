# SD-G7 Plan — ImpactGraph (Attack Period GATE 7)

> Status: pre-implementation, pending architect review  
> Scope: `~/zest`, branch `master`
> Constraint: no code is written until this plan is approved.

## 0. Context & non-goals

SD-G7 is the **Attack Period** gate 7. It is unrelated to the old infrastructure GATE 07.

Goal: every impact claim in a `FindingProposal` must be anchored to a chain of proof-backed impact nodes. A proposal whose impact claims lack an `ImpactChain` is rejected at admission with `IMPACT_CHAIN_MISSING`.

Non-goals:
- No new model LLM calls.
- No live network access.
- No change to the `Finding` creation rules after human review.
- No weakening of existing assertions or test functions.

## 1. Kutsal rules read-back

K1 No chainless impact: every impact claim in a proposal must reference a validated `ImpactChain`.
K2 Real proofs only: every chain node references existing ledger records (`evidence`, `observation`, `experiment`) via `proof_id`; missing proofs are rejected as hallucinated sources.
K3 No exaggeration: the claimed impact kind must not exceed the demonstrated capabilities of the referenced proofs.
K4 Scope confinement: chain nodes stay within the same program/research run boundary.
K5 Existing tests and contract copies are preserved.
K6 No raw secrets in chain payloads; proofs are references, not content.

## 2. Proposed file changes

### P1 — ImpactGraph core (`src/zest/research/impact/`)

| File | Change |
|------|--------|
| `src/zest/research/impact/__init__.py` | New package marker. |
| `src/zest/research/impact/types.py` | `ImpactKind` enum (`DATA_READ`, `DATA_WRITE`, `AUTH_BYPASS`, `STATE_CORRUPTION`, `ACCOUNT_TAKEOVER_PATH`, `EXTERNAL_CALLBACK`); `ImpactRelation` enum (`ENABLES`, `ESCALATES`, `CONFIRMS`); `ChainValidation` dataclass (`valid: bool`, `reason_codes: tuple[str, ...]`). |
| `src/zest/research/impact/chain.py` | `ImpactNode` (node_id, proof_refs, impact_kind, claim_text, scope_ref); `ImpactEdge` (from_node, to_node, relation); `ImpactChain` (ordered node list + edge list); constructor validates acyclicity, dangling edges, empty proof_refs. |
| `src/zest/research/impact/validator.py` | `ProofResolver` Protocol (`resolve(proof_id: str) -> ProofRecord | None`); `validate_chain(chain, resolver) -> ChainValidation` verifies every proof exists and every node has at least one proof. |
| `src/zest/research/impact/capability_map.py` | `DEMONSTRATED_CAPABILITY_TO_IMPACT_KIND` mapping (shelf data); `validate_impact_scope(impact_kind, proof_capabilities) -> ChainValidation` enforces K3. |

Research layer rules:
- `research` does not import `application`, `workers`, or `integrations`.
- IDs are received, not generated (same pattern as `SensorObservation`).

### P2 — Admission integration

| File | Change |
|------|--------|
| `src/zest/core/enums.py` | Add `IMPACT_CHAIN_MISSING` to `ReasonCode`. |
| `src/zest/research/finding_proposal.py` | Add `impact_claims: tuple[ImpactClaim, ...]` (optional) to `FindingProposalDraft`. Add `ImpactClaim` dataclass (`claim_text`, `impact_kind`, `chain_id`). In `admit_finding_proposal`, if `impact_claims` is non-empty, require each claim to carry a non-empty `chain_id`; otherwise `REJECTED_POLICY_CONFLICT` with `IMPACT_CHAIN_MISSING`. (Full chain validation is applied at application admission time because research layer has no DB access.) |
| `src/zest/application/impact/proof_resolver.py` | New. Implements `ProofResolver` by reading `uow.evidence`, `uow.observations`, `uow.experiments`. Returns a `ProofRecord` with `proof_id`, `demonstrated_capabilities`, `target_reference`, `research_run_id`. |
| `src/zest/application/impact/__init__.py` | New package marker. |
| `src/zest/application/submit_finding_proposal.py` | After `admit_finding_proposal` passes, if the draft carries impact claims, load each `ImpactChain` from `uow.impact_chains`, run `validate_chain` + `validate_impact_scope`; any failure → `ApplicationError` with reason code (no persistence). On success, persist the proposal as today plus optional impact claim references. |
| `src/zest/data/postgres/tables.py` | Add `impact_chain` and `impact_chain_node` tables (append-only). `impact_chain`: chain_id PK, research_run_id, program_id, graph_hash maybe null, created_at. `impact_chain_node`: node_id PK, chain_id FK, impact_kind, claim_text, scope_ref, proof_refs JSONB, ordering. `impact_chain_edge`: edge_id PK, chain_id FK, from_node_id, to_node_id, relation. |
| `alembic/versions/a31_001_impact_graph.py` | New migration creating the above tables; no data migration. |
| `src/zest/data/records.py` | Add `ImpactChainRecord`, `ImpactChainNodeRecord`, `ImpactChainEdgeRecord`. |
| `src/zest/data/postgres/repositories.py` | Add `ImpactChainRepository` with `get(chain_id)`, `insert(...)`, `list_for_run(...)`. |
| `src/zest/application/ports.py` | Add `impact_chains` to the `UnitOfWork` protocol if not already present. |

### P3 — Impact scope rule (K3)

| File | Change |
|------|--------|
| `src/zest/research/impact/capability_map.py` | Shelf mapping: each demonstrated capability (e.g. `READ_OTHER_OBJECT`, `WORKFLOW_TRANSITION_WITHOUT_AUTH`, `OAST_CALLBACK_RECEIVED`, `AUTHENTICATED_AS_USER`) maps to an allowed set of `ImpactKind`. Unknown capability → empty allowed set (fail-closed). `ACCOUNT_TAKEOVER_PATH` requires both `AUTHENTICATED_AS_USER` and `PRIVILEGE_ESCALATION_EVIDENCE`. `DATA_WRITE` requires a write capability; read-only proofs cannot support it. |
| `src/zest/application/impact/proof_resolver.py` | Derive `demonstrated_capabilities` from the proof record’s evaluation strategy + observed facts (not from claim text). |

### P4 — Tests + operations

| File | Purpose |
|------|---------|
| `tests/unit/research/impact/test_chain.py` | Cycle rejection, dangling edge rejection, empty proof_refs rejection, valid chain acceptance. |
| `tests/unit/research/impact/test_validator.py` | Hallucinated proof_id rejection, valid proof resolution, scope mismatch rejection. |
| `tests/unit/research/impact/test_capability_map.py` | Read-only proof cannot claim `DATA_WRITE` or `ACCOUNT_TAKEOVER_PATH`; allowed mappings pass; unknown capability fails closed. |
| `tests/unit/research/test_finding_proposal.py` | New tests: proposal with impact claims missing chain_id → `IMPACT_CHAIN_MISSING`; proposal without impact claims still passes (backward compatibility). |
| `tests/integration/test_sd_g7_impact_graph.py` | Fixture evidence → chain persistence → proposal admission with chain PASS; missing chain RED; exaggerated impact RED; append-only chain table verification. |
| `src/zest/maturity.py` | Add `GATE_07_STATUS = "PENDING"`; docstring paragraph noting SD-G7 = ImpactGraph, NOT old GATE 07. |
| `OPERATIONS.md` | Add SD-G7 section: chain semantics, K3 capability mapping, admission hook, test runbook. |

## 3. Execution order

1. P1 core (types/chain/validator/capability_map) — unit tests green.
2. P3 capability map tests — green.
3. P2 alembic + repositories + records + ports + application resolver + admission hook — integration tests green.
4. P4 maturity + operations + final full suite.

## 4. Regression guard

- `pytest tests/unit tests/contract -q` must stay green after every sub-phase.
- `pytest tests/integration -q` must be green before final push.
- No existing test function is deleted.
- Contract copies remain md5-identical.
- `GATE_07_STATUS` stays `PENDING` until independent architect audit.

## 5. Open questions for architect

1. Should `ImpactChain` be created by a separate application use-case (e.g. `RegisterImpactChain`) before `SubmitFindingProposal`, or created inline during submission? Proposal: separate use-case keeps proof registration distinct from proposal admission.
2. Should `graph_hash` from SD-G3 be stored on `impact_chain` table for cross-audit, or kept null for now? Proposal: nullable column, populated if the chain was derived from a graph snapshot.
3. Should old finding proposal proposers (`propose_diagnostic_finding_proposal`, etc.) produce empty `impact_claims` (backward compatible), or should diagnostic proposals explicitly declare no impact? Proposal: empty tuple to preserve existing behavior.
