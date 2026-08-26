# SD-G8 Plan — Coverage Debt Matrix

**Gate:** SD-G8 (Attack Period Gate 8 — NOT old infrastructure GATE 08).  
**Goal:** Build a deterministic `(Asset node × Identity × HunterFamily)` coverage-debt matrix that tells G9 scheduler where the hunt is incomplete, without adding attack capability, LLM calls, or diluted abstractions.

## Kutsal Checklist

- [K1] Debt computation is deterministic: same graph + registry + ledger → same matrix. No LLM involved.
- [K2] UNKNOWN/OUT_OF_SCOPE nodes are marked `NOT_APPLICABLE`; they do not enter the debt count.
- [K3] Debt is a proof counter with ids, not a percentage or score.
- [K4] Matrix is a read-only projection; persist only snapshot summary (hash + counts) if persisted at all.
- [K5] No existing tests deleted; contract md5s untouched; full suite stays green.

## Mevcut Zemin

- `AttackSurfaceNode` (`research/discovery/graph.py`) carries `kind`, `scope_classification`, `identity_ids`, `canonical_key`, `provenance_refs`.
- `HunterFamilyView` / `families_for_node` (`research/selection.py`) gives applicable families for a node.
- `summarize_attack_surface` / `summarize_graph` (`application/discovery/snapshot_views.py`) rebuild graph from ledger.
- Hypotheses live in `hypothesis` table; V1/V2/V3 decisions are in `audit_event` (subject=hypothesis, payload includes `family_id`, `tier`, `outcome`, `node_canonical_key`).
- CLI pattern in `interface/cli.py` (`status`, `census`, `budget` commands).

## Implementation Order

### P1 — Coverage Debt Core (`research/coverage/`)

| File | Change |
|------|--------|
| `src/zest/research/coverage/__init__.py` | Package marker. |
| `src/zest/research/coverage/types.py` | `CoverageState` enum: `UNTESTED`, `HYPOTHESIZED`, `V1_PASSED`, `V2_PASSED`, `V3_QUEUED`, `COVERED`, `NOT_APPLICABLE`. `CoverageCell` dataclass: `node_canonical_key`, `identity_id`, `family_id`, `state`, `missing_evidence` (tuple[str]). `CoverageMatrix` dataclass: `research_run_id`, `strategy_version`, `cells` (tuple[CoverageCell, ...]), `cell_counts` (dict[str,int]), `total_debt` (int), `matrix_hash` (str). |
| `src/zest/research/coverage/debt.py` | `compute_coverage_debt(graph, registry, hypotheses_view) -> CoverageMatrix`. Iterates IN_SCOPE nodes only. For each node, expands `identity_ids` (empty → `ANONYMOUS`). For each applicable family from `families_for_node`, resolves state from `hypotheses_view` (UNTESTED if no hypothesis; otherwise highest tier reached). Missing evidence is a tuple of reason strings/ids, not raw secrets. Returns deterministic matrix + hash. |

`hypotheses_view` shape (plain dataclass, no DB dependency):

```python
@dataclass(frozen=True)
class CoverageHypothesisView:
    hypothesis_id: str | None
    family_id: str
    node_canonical_key: str
    identity_id: str
    highest_tier: str  # UNTESTED | V1 | V2 | V3_QUEUED | COVERED
```

Mapping highest_tier → `CoverageState` is a pure function in `debt.py`.

### P2 — Application View + Operator CLI

| File | Change |
|------|--------|
| `src/zest/application/coverage/__init__.py` | Package marker. |
| `src/zest/application/coverage/hypothesis_view.py` | `build_coverage_hypothesis_view(uow, research_run_id) -> tuple[CoverageHypothesisView, ...]`. Reads hypotheses for the run, joins latest audit_event tier decisions per `(hypothesis_id, family_id, node_canonical_key)`. V3 queued state read from `hunt_v3_queue`. No LLM. |
| `src/zest/application/coverage/debt_view.py` | `CoverageDebtView` use-case: rebuild graph via `summarize_attack_surface`, load registry via `uow.hunter_families.list_enabled()`, build hypothesis view, call `compute_coverage_debt`, return `CoverageDebtSummary` dataclass. |
| `src/zest/interface/cli.py` | Add `"coverage"` to `choices`. Implement `_cmd_coverage(rest)` with `--research-run-id` required. Prints family-level debt table (UNTESTED count per family) and top-10 most-debt nodes. Uses `ZEST_DATABASE_URL`. |

### P3 — Snapshot Persistence (a32)

| File | Change |
|------|--------|
| `alembic/versions/a32_001_coverage_debt_snapshot.py` | New migration. Table `coverage_debt_snapshot`: `snapshot_id` PK, `research_run_id` FK, `matrix_hash` (SHA-256), `cell_counts` JSONB, `total_debt` int, `created_at` tz. Same discipline as `attack_surface_snapshot` (counts/hash only). |
| `src/zest/data/records.py` | `CoverageDebtSnapshotRecord` dataclass. |
| `src/zest/data/postgres/tables.py` | `coverage_debt_snapshot` table; add to `SPINE_TABLES` / `APPEND_ONLY_TABLES`. |
| `src/zest/data/ports.py` | `CoverageDebtSnapshotRepository` protocol. |
| `src/zest/data/unit_of_work.py` + `postgres/unit_of_work.py` | `coverage_debt_snapshots` attribute. |
| `src/zest/data/postgres/repositories.py` | `PostgresCoverageDebtSnapshotRepository`. |
| `src/zest/application/coverage/debt_view.py` | Optional `persist=True` parameter to write snapshot after computing. |

### P4 — Tests

| File | What |
|------|------|
| `tests/unit/research/coverage/test_debt.py` | Empty graph → empty matrix; NOT_APPLICABLE for UNKNOWN/OUT_OF_SCOPE; ANONYMOUS identity when `identity_ids` empty; determinism across 10 permutations; missing_evidence references are real ids; family applicability via mocked `families_for_node`. |
| `tests/integration/test_sd_g8_coverage_debt.py` | Fixture census → admission → graph → registry → generate hypothesis → validate tiers → compute matrix. Asserts hypothesized cell is not UNTESTED; CLI output matches ledger counts. PostgreSQL required. |
| `tests/unit/interface/test_cli_coverage.py` | `_cmd_coverage` argument parsing and happy-path output assertions using a stubbed use-case (optional, keeps CLI tested without DB). |
| `tests/unit/data/test_alembic_smoke.py` | Add `coverage_debt_snapshot` to SPINE table check. |

## Kapanış Standardı

- `pytest tests/unit tests/contract -q` green (≥1098).
- `pytest tests/integration -q` green (≥149).
- `pytest tests/e2e -q` green (155).
- `maturity.py`: `GATE_08_STATUS = "PENDING"` with docstring noting SD-G8 is NOT old GATE 08.
- `OPERATIONS.md`: SD-G8 section describing debt semantics, K1-K4, and CLI usage.
- No code changes before architect approval of this plan.
