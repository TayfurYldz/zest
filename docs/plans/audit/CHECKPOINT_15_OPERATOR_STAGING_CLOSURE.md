# Research OS — Operator API / Console / Preflight Staging Closure

**Document class:** Qualification record  
**Date:** 2026-08-22  
**Status:** QUALIFIED for operator staging control-plane closure against persistent `research-osd`. Not systemd. Not machine-reboot. Not 24/7.

---

## A. Baseline

- branch: `campaign/canonical-mr5-mr6`
- old HEAD: `e51ea88e48520891ad955ae9382737bc11ffc515` (`research-osd-runtime-closed`, checkpoint 14)
- PG: `postgresql+psycopg://research_os_test@127.0.0.1:55432/research_os_test`
- Alembic head: `a42_001_preflight_report` (revises `a41_001_runtime_instance`)
- worktree at qualification: Checkpoint 15 sources added; sealed checkpoints 9–14 not rewritten

Earlier sealed chain unchanged:

| item | SHA |
|---|---|
| checkpoint 9 | `54b17a1` |
| MR-5 | `e8e78fe` |
| mutation id | `7d96f79` |
| MR-6A | `24b4a0d` |
| MR-6 | `297621b` |
| research-osd / checkpoint 14 | `e51ea88` |

## B. Before

Dashboard was a disposable HTTP UI for **commands**, but not a complete client:

- Lifecycle POST `/api/runs/:id/{start,pause,resume,cancel}` forwarded to research-osd when `RESEARCH_OSD_URL` was set.
- `GET /api/dashboard` still read PostgreSQL directly for programs, runs, and run detail.
- Operator API `GET /api/runs/:id` was a thin orchestration snapshot.
- Preflight existed in Application, ran on START, and was **not** durable or operator-visible.
- Health collapsed to `ok` / `pg_unavailable`.
- Errors were untyped `{ok:false, error: str}`.
- No first-class Preflight endpoints.

Dashboard did **not** own `LocalRunSupervisorRegistry` (Checkpoint 14). That remains true.

## C. After Process Model

```text
browser
  → dashboard (complete client; disposable)
       GET  /api/dashboard  → overlays research-osd console snapshot
       POST /api/runs/:id/{preflight,start,pause,resume,cancel}
  → HTTP 127.0.0.1
  → research-osd Operator API
  → current Preflight (persisted as evidence, never START authority)
  → reconstruct command from PostgreSQL SoR
  → lease / supervisor
  → ARC → Core → Worker
```

SSE is deferred. Dashboard polls REST every 3s. On reconnect the client fetches a full REST snapshot from research-osd.

## D. Preflight

Endpoints:

- `POST /api/runs/:id/preflight`
- `GET  /api/runs/:id/preflight/latest`

Durable table `preflight_report` (append-only):

- `preflight_report_id`, `research_run_id`, `runtime_instance_id`, `created_at`
- `release_version`, `configuration_fingerprint`
- `status` `READY_TO_START` | `NOT_READY`
- structured `checks` JSONB (no secrets)
- `authorizes_start` is always false in the API mapping

START always re-runs current Preflight. A stored PASS does not authorize a later START.

Stale-preflight PG proofs:

| change after PASS | START result |
|---|---|
| AuthorizationSource `EXPIRED` | `AUTHORIZATION_UNAVAILABLE` |
| budget exhausted | `BUDGET_EXHAUSTED` |
| Worker UNAVAILABLE | `WORKER_UNAVAILABLE` |
| model `AUTH_REQUIRED` | `MODEL_AUTH_REQUIRED` |

## E. Operator API

| endpoint | method | authority | response |
|---|---|---|---|
| `/health` | GET | process health, not research truth | nested database/worker/model readiness |
| `/api/programs` | GET | SoR read | sanitized program list |
| `/api/runs` | GET | SoR read | run list |
| `/api/runs/:id` | GET | SoR read | complete sanitized run detail |
| `/api/runs/:id/preflight` | POST | current Preflight + persist evidence | READY_TO_START / NOT_READY; not START authority |
| `/api/runs/:id/preflight/latest` | GET | last stored report | evidence of what WAS true |
| `/api/runs/:id/start` | POST | reconstruct from SoR + current Preflight + lease | orchestration tick |
| `/api/runs/:id/pause` | POST | SoR + supervisor stop | orchestration tick |
| `/api/runs/:id/resume` | POST | reconstruct from SoR | orchestration tick |
| `/api/runs/:id/cancel` | POST | SoR + supervisor stop | orchestration tick |
| `/api/console` | GET | composition of health + lists + details | dashboard snapshot |

Browser JSON must not supply target/scope/budget/bounds/question. Those keys are rejected (`INVALID_INPUT`). Empty `{}` is accepted.

Approval **writes** remain the existing dashboard Finding-review use cases. Run detail exposes a read-only pending queue. Mutation endpoints are not duplicated onto research-osd in this checkpoint.

## F. Dashboard Client Proof

- `dashboard.py` does not contain `LocalRunSupervisorRegistry` or `ResearchOsdRuntime`.
- `RESEARCH_OSD_URL` is required to attach run control.
- `collect_dashboard_payload` overlays programs/runs/run_details from `/api/console` when OSD is reachable.
- Payload includes `client_only: true`.
- Closing/restarting dashboard collection does not stop `research-osd` supervision (PG proof).

Program **bootstrap** still writes SoR through dashboard+`DATABASE_URL`. That is setup, not lifecycle ownership.

## G. Run Read Model

Sanitized fields include: `research_run_id`, program id/name, state, phase, cycle, stop/pause reason, lease health, latest Preflight summary, request/worker/model counts, hypothesis/experiment/observation/evidence/candidate/finding counts, pending approvals, timeline, timestamps, reconciliation block.

Redacted / absent: cookies, tokens, Authorization headers, model API keys, DB DSN/password, `SecretReference` values. `authorization_state` is the AuthorizationSource **state** enum (ACTIVE/EXPIRED/REVOKED), not an HTTP header.

## H. Runtime Health

Separated:

- process `ok` (daemon registered + PostgreSQL reachable)
- `ready_for_start` (DB + schema head + worker available now + model available now)
- nested `database` / `worker` / `model` with installed/configured/authenticated/compatible/`available_now`
- model `gate_04b` is displayed and explicitly **not** availability (`gate_04b_is_not_availability: true`)

A runtime may be process-healthy and still `AUTH_REQUIRED` / Worker unavailable. Tests prove `ready_for_start` is false in those cases.

## I. Reconciliation UX

When orchestration is `WAITING_HUMAN` (or `RECONCILIATION_REQUIRED`):

- classification / pause reason
- last known safe phase
- last attempt id
- side-effect ceiling (integer class, not payload)
- operator action: human review required; **do not auto-retry UNKNOWN_OUTCOME**
- `auto_retry: false`

Dashboard shows a Held status instead of START/RESUME.

## J. Lifecycle Commands

START / PAUSE / RESUME / CANCEL remain durable SoR commands.

Races (real PostgreSQL, 10×):

- two HTTP clients START → one supervisor / owner
- START + CANCEL → deterministic current state
- PAUSE then duplicate PAUSE → same state
- RESUME then duplicate RESUME → no duplicate owner
- dashboard reconnect reads SoR via OSD

## K. Client Authority Attacks

POST START with `target`, `scope`, `max_requests`, `side_effect_ceiling`, `budget`, `research_question` → `400 INVALID_INPUT`. Subsequent empty START uses persisted target/question/ceiling/budget. Client values do not appear in run detail.

## L. Dashboard Death / Reconnect

Active run stays supervised after dashboard payload collection is dropped. A new `collect_dashboard_payload` against the same OSD URL reconstructs the same SoR state.

## M. VDS Portability

- `OsdSettings` from env: database URL, bind host, API port, heartbeat, lease TTL, environment name, release version, log path, model/worker config refs
- Documented Linux paths: `/opt/research-os/`, `/etc/research-os/`, `/var/lib/research-os/`, `/var/log/research-os/`
- Example: `config/research-osd.env.example` (no secrets)
- No Windows-only paths, Cursor paths, `/home/tayfur`, or shell-profile dependency
- Alembic path is repo-relative
- **No systemd unit, no enable, no deploy** (Checkpoint 16)

## N. Security

- Operator API default bind `127.0.0.1`; `0.0.0.0` rejected
- No public unauthenticated lifecycle API
- PostgreSQL not exposed
- Typed JSON errors; no traceback HTML on Operator API
- Secrets redacted on every JSON response
- Remote operator access deferred to VPN/reverse proxy in Checkpoint 16

## O. Regression

| suite | result |
|---|---|
| compileall src tests scripts | OK |
| pytest tests/unit | 1459 passed, 4 skipped |
| pytest tests/integration | 238 passed, 1 failed (SD-G4 known) |
| pytest tests/e2e | 156 passed, 5 skipped (cli_session isolation known) |
| Checkpoint 15 dedicated PG | 13 passed including 10× races + stale Preflight + payload attacks |

Checkpoint 9–14 sealed SHAs were not rewritten. Finding remains Human Review + Core gated.

## P. Tests

| case | result |
|---|---|
| Preflight API report + persist | PASS |
| stale Preflight (auth/budget/worker/model) | PASS |
| lifecycle commands | PASS |
| payload override attacks | PASS |
| daemon health/readiness | PASS |
| sanitized run detail | PASS |
| dashboard client-only | PASS |
| dashboard death/reconnect | PASS |
| multi-client races 10× | PASS |
| reconciliation visibility | PASS |
| no secret output | PASS |
| local bind default | PASS |

## Q. Known Debt

Separate from this unit:

1. SD-G4 `MODEL_TOKENS_IN` dual CHECK
2. e2e `cli_session` isolation skips
3. application DB stamp at older ancestor
4. SSE live transport (deferred; REST poll is enough)
5. approval **mutation** on research-osd (read-only queue now; dashboard still calls existing use cases)
6. systemd / machine-reboot / VPN operator access (Checkpoint 16)
7. leftover hyp/exp rows without orchestration checkpoints still block **attach** Preflight via existing `INTEGRITY_ERROR` reconcile (unchanged research semantic)

## R. Hard Fail Matrix

| condition | result |
|---|---|
| dashboard constructs LocalRunSupervisorRegistry | no |
| dashboard builds operational ARC owner | no |
| dashboard death kills active run | no |
| stale Preflight authorizes START | no |
| client POST changes persisted scope/target/budget/bounds | no |
| API leaks secrets | no |
| API binds publicly by default | no |
| health says ready when auth/Worker unavailable | no |
| duplicate START creates duplicate owner | no |
| reconciliation ambiguity auto-retries | no |
| daemon loses fencing | no |
| research-osd becomes research brain | no |
| Human Finding gate changes | no |
| checkpoint 9–14 regress | no |
| maturity flags change | no |

## S. Maturity

`src/research_os/maturity.py` unchanged.

## T. Final Flags

```
OPERATOR_STAGING_CLOSURE_QUALIFIED=YES
PHASE_J_RESEARCH_OSD_QUALIFIED=YES
CANONICAL_MR5_CLOSED=YES
CANONICAL_MR6_CLOSED=YES
```

PASS does **not** mean:

```
SYSTEMD_STAGING_READY
MACHINE_REBOOT_QUALIFIED
24_7_READY
REMOTE_OPERATOR_READY
SECURITY_RESEARCH_VALIDATED
PRODUCTION_READY
GATE 04B PASS
```

## U. Seal

See checkpoint 15 commit and optional tag `operator-staging-control-closed`.

Do not call this staging-ready. VDS systemd/reboot proof is Checkpoint 16.

## V. Next Authorized Unit

**CHECKPOINT 16 — VDS systemd + machine-reboot staging qualification**

Do not start it here.
