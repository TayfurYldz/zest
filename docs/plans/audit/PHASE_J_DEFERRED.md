# Phase J — `research-osd`

Status: **QUALIFIED** for persistent local runtime ownership. See `PHASE_J_RESEARCH_OSD_QUALIFICATION.md`.

Not 24/7. Not PRODUCTION_READY. Not systemd/machine-reboot qualified.

## What landed

- Durable `runtime_instance` identity (new id every process start)
- `research-osd` process owns `LocalRunSupervisor` + existing CAS lease
- Per-run fenced UoW + lease-checked Worker dispatch
- Dashboard is a client (`RESEARCH_OSD_URL`) or read-only
- Recovery classifier does not treat expired lease as operational failure
- DISPATCHING / UNKNOWN_OUTCOME are not auto-retried

## What already existed (reused)

- `LocalRunSupervisor` tick/lease-renew/stop-on-loss
- Slice 1 lease/fencing on `research_orchestration`
- Slice 2 Preflight
- `ReconcileResearchRun` attempt classification
- `configuration_from_record` / ARC terminal immutability
