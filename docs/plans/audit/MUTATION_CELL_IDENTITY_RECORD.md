# Mutation Cell Identity Prerequisite Record

Date: 2026-08-22.
Branch: `campaign/canonical-mr5-mr6`.
Parent: checkpoint 10 `e8e78fe24083cb0a2c7898d72928785b247f5d35` (`canonical-mr5-closed`).

```
MUTATION_CELL_IDENTITY_QUALIFIED=YES
CANONICAL_MR5_CLOSED=YES
MR6_FULL_EXPLORATORY_QUALIFIED=NO
```

This is **not** Canonical MR-6 qualification. It closes the provenance defect
where complete caller `dimension_values` could compile a fabricated
`selected_cell_id`.

## Required invariant

`selected_cell_id` must resolve to an actual cell in the rebuilt
authoritative MutationMatrix for the requested HunterFamily. Complete
dimensions alone do not manufacture cell identity.

```
known cell_id → deterministic compiler path
unknown / fabricated cell_id → BLOCKED_UNKNOWN_CELL
  Worker = 0, no ExecutionAttempt, no Observation, no coverage, no Evidence, no Candidate
```

## Failure distinction

| Case | Outcome |
|---|---|
| Unknown / transplanted / wrong matrix_hash | `BLOCKED_UNKNOWN_CELL` |
| Known cell, incomplete authoritative semantics or missing origin | `BLOCKED_MISSING_SEMANTICS` |

## Explicitly unchanged

- Model may select among admitted cells; it may not define new cells.
- Catalog/compiler owns payload semantics (query/body/headers ignored).
- Core remains execution authority. Planned/compiled ≠ covered.
- Nine mutation HunterFamilies remain supported.
- Maturity flags unchanged.
- MR-6 exploratory attack capability remains unqualified.
