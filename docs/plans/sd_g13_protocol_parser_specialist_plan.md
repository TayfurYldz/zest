# SD-G13 Plan — Protocol/Parser Specialist

**Status:** PASS
**Previous gate:** SD-G12 PASS (`0f3626b`)
**Do not confuse with:** old infrastructure `GATE 13` operational readiness.

## Purpose

SD-G13 introduces protocol/parser specialist lanes for request smuggling,
desync, and cache poisoning/deception without turning the system into a raw
parser attack runner. A protocol lane may become active only when the
AttackSurfaceGraph carries surface evidence for proxy/cache/protocol behavior.

## Non-Negotiables

- Protocol specialists are hypotheses, not findings.
- No protocol lane runs unless surface evidence supports the protocol.
- V3 admission queues a plan artifact only; Worker dispatch is forbidden until
  SE3 approval.
- Parser differences are recorded as hypotheses and plan metadata, not impact.
- Active execution remains Core-authorized per step.

## P1 — Surface-Gated Protocol Plan Admission

Files:

- `src/zest/research/selection.py`
- `src/zest/research/protocol/parser_plan.py`
- `src/zest/research/protocol/__init__.py`
- `src/zest/data/postgres/hunter_family_seed.py`
- `src/zest/application/hunt_validation.py`
- `tests/unit/research/test_hunter_family_registry.py`
- `tests/unit/data/test_sd_g13_hunter_family_seed.py`
- `tests/unit/research/test_sd_g13_protocol_parser_plan.py`
- `tests/unit/application/test_hunt_cycle.py`
- `tests/integration/test_sd_g5_hunt_cycle.py`

Families:

- HTTP request smuggling/desync
- HTTP cache poisoning/deception

Behavior:

- adds `required_attribute_any` preconditions so protocol families only match
  nodes with `protocol_surface_signals`;
- seeds V3 HunterFamily rows for the two SD-G13 protocol specialist families;
- builds deterministic `protocol.parser.v1` plans with required surface
  signals, dimensions, controls, steps, and SHA-256 plan hash;
- maps SD-G13 families to `protocol.parser` / `plan` V3 queue records;
- marks protocol parser plans side-effect level 3 and `approval_required=SE3`;
- stores only plan metadata in queue arguments and forbids Worker dispatch until
  SE3 approval.

Evidence:

- Focused checks (2026-08-20): `39 passed`.
- Affected checks (2026-08-20): `50 passed`.

## P2 — SE3 Queue Approval Gate

Files:

- `src/zest/application/hunt_v3_queue_approval.py`
- `tests/unit/application/test_sd_g13_hunt_v3_queue_approval.py`
- `tests/integration/test_sd_g13_protocol_queue_approval.py`

Behavior:

- binds human approval to a V3 queue subject `hunt-v3-queue:<queue_id>`;
- refuses to move SE3 protocol parser queue items out of `PENDING` without a
  recorded human `APPROVE`;
- rejects SE3 queue items missing `approval_required=SE3` even when a human
  approval exists;
- writes an audit event for approved and rejected queue approval decisions;
- does not dispatch Workers and does not create Evidence, Candidates, Findings,
  or protocol payloads.

Evidence:

- Focused checks (2026-08-20): `6 passed`.
- Affected checks (2026-08-20): `74 passed`.
- Full suite (2026-08-20): `1507 passed, 9 skipped, 53 subtests passed`.
