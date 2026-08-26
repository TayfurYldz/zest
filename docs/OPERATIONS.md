# Operations

Diagnostic operational helpers. This is not a DBA product and not a claim that Zest is production-ready for autonomous security research.

## PostgreSQL

Application database (operator status HEALTHY comes from this URL only):

- `ZEST_DATABASE_URL`

Isolated test database (reported separately as `TEST_POSTGRESQL`; never preferred over the application URL):

- `ZEST_TEST_DATABASE_URL` (must contain `test`; SQLite is not a substitute)

Commands:

```
python scripts/zest_db.py ping --test
python scripts/zest_db.py version --test
python scripts/zest_db.py migrate --test
```

Backup/restore remain operator procedures against PostgreSQL. Do not silently delete evidence-linked artifacts or SoR rows.

Connection health is `SELECT 1` only. Credentials and userinfo passwords are not logged or rendered.

## Operator status

```
python scripts/zest_status.py status
```

or, after install, from any working directory:

```
zest status
```

Output includes POSTGRESQL (application DB), TEST_POSTGRESQL (if configured), Worker, Model Runtimes, Strix, Auth, Orchestrator, Budget ledger, Reconciliation, Observability, GATE 04B, and maturity flags. It must not print secrets.

Worker HEALTHY requires a real diagnostic protocol probe (spawn → valid request → schema + correlation → clean exit). Codex `--version` is INSTALLED/VERSION_KNOWN only. `SUBSCRIPTION_OAUTH` is `NOT_IMPLEMENTED`. GATE 04B is PASS after authoritative qualification with two independent `BENCHMARK_COMPATIBLE` live ModelRuntime configurations executing the comparable full benchmark. Scripted baselines and Strix still do not count. Ordinary `zest status` remains passive and does not re-run or consume model quota.

## Codex readiness ladder

Documented CLI only. Tokens are not scraped. Codex is not auto-installed.

Multiple Codex CLI ModelRuntime configurations share one authenticated executable/session.
Models are operational configuration, not architecture.

```
ZEST_CODEX_MODELS=codex-cli-terra=gpt-5.6-terra,codex-cli-gpt55=gpt-5.5
```

or a model list that derives stable IDs:

```
ZEST_CODEX_MODELS=gpt-5.6-terra,gpt-5.5
```

Optional executable override: `ZEST_CODEX_EXECUTABLE`. Duplicate or empty entries fail closed.

Current diagnostic defaults (overrideable): `codex-cli-terra` → `gpt-5.6-terra`, `codex-cli-gpt55` → `gpt-5.5`.

`zest status` and ordinary `--discover` are **PASSIVE**. They may run `codex --version` and `codex login status` only. They must not run `codex exec` and must not consume model quota. Passive AUTH_READY is not `BENCHMARK_COMPATIBLE` and does not populate `available_model_configurations`.

Explicit live probe (consumes model quota; independent per configured model):

```
python scripts/run_research_benchmark.py --discover --live-probe
```

Each configuration is probed independently:

1. NOT_INSTALLED — executable missing
2. INSTALLED / VERSION_KNOWN — `codex --version` succeeded
3. AUTH_READY — `codex login status` exit 0
4. DIAGNOSTIC_READY / MODELPORT_COMPATIBLE — explicit LIVE `codex exec --ignore-user-config --ephemeral --sandbox read-only -m <model>`
5. BENCHMARK_COMPATIBLE — AUTH_READY and that model's request-consuming exec succeeded in this LIVE probe

Compatibility is not inferred across Codex configurations. Passive discovery and discovering two configs are not GATE 04B PASS. Usage-limit stderr maps to `RATE_LIMITED`, not a research-quality failure.

## Strix readiness

Executable/version is not HEALTHY. Sandbox/docker dependency must be ready. Harmless diagnostic ping only. No auto-install. Strix is not a Zest ModelRuntime.

## Source export

```
python scripts/export_source.py --output dist/zest-source.tar.gz
zest export-source --output dist/zest-source.tar.gz
```

Excludes `.git`, `.venv`, caches, coverage, runtime artifacts, and known credential/session files. Optional `--include-untracked-source` adds explicitly selected untracked source files only. Emits a SHA-256 manifest. Does not delete the developer's `.git` or `.venv`.

## Clean install

Mandatory for final GATE 13 PASS:

```
python scripts/clean_install_smoke.py
```

Builds a wheel (`python -m build` or `uv build`), installs it into an empty venv, changes CWD to an unrelated temp directory, then runs `zest status`, `ContractValidator()`, local diagnostic Worker probe, development benchmark fixture load, and runtime discovery with no repository root on `sys.path`. If neither build backend is available, the script exits `VALIDATION_PENDING` (code 3) instead of fabricating PASS.

## Gate validation commands

```
python -m compileall src tests scripts
python -m unittest discover -s tests/unit -q
python -m unittest discover -s tests/contract -q
python -m unittest tests.unit.test_architecture_boundaries -q
python scripts/run_research_benchmark.py --baseline GOOD_BASELINE --single-run-legacy
python -m unittest discover -s tests/integration -q
python scripts/clean_install_smoke.py
```

GATE 12/13 are PASS only after those suites actually run with 0 required skips. Do not fabricate PASS.

## GATE 14 — local security-research E2E

**GATE 14 status: PASS** (2026-08-17).

Validation environment:

- Kali Linux
- real PostgreSQL
- dedicated `ZEST_TEST_DATABASE_URL`
- Alembic head `a18_001_http_auth_class`
- `python -m unittest tests.e2e.test_gate14_security_lab`
- 19 E2E tests OK
- 0 skipped
- controlled localhost HTTP lab only
- no Codex / LLM / Strix

Proves: controlled authorized local security-research pipeline E2E for HTTP authorization differential / BOLA semantics.

Does **not** prove autonomous vulnerability discovery quality, real-world bug bounty performance, multi-model live validation, production readiness, or broad security-research validation. Do not set `SECURITY_RESEARCH_VALIDATED` or `PRODUCTION_READY`. GATE 04B remains PENDING.

```
python -m unittest tests.e2e.test_gate14_security_lab
```

If `ZEST_TEST_DATABASE_URL` is unset, the suite must SKIP, never fabricate PASS.

## GATE 15 — security ground-truth / false-positive benchmark

**GATE 15 status: PASS** (2026-08-17).

Authoritative environment:

- Kali Linux
- dedicated real PostgreSQL test database
- `ZEST_TEST_DATABASE_URL`
- Alembic head `a18_001_http_auth_class`
- GATE 14 regression: 19 OK, 0 skipped
- GATE 15 ground-truth benchmark: 21 OK, 0 skipped
- localhost-only security ground-truth lab
- no Codex / LLM / Strix

GATE 14 is a single controlled security-semantics E2E. GATE 15 is a multi-scenario ground-truth / false-positive benchmark on the same `http.authorization.differential` pipeline. GATE 04B is live model comparison. These gates do not imply each other.

GATE 15 proves only: controlled multi-scenario ground-truth / false-positive security benchmark passed for HTTP authorization differential semantics.

Preserved benchmark guarantees:

- true BOLA validated
- independent verification required
- `false_finding = 0`
- secure / public / delegated / shared cases produced no Finding
- deceptive 200 / insufficient evidence produced no Finding
- contradictory verification did not VALIDATE
- timeout became INCONCLUSIVE
- redirect boundary was not crossed
- out-of-scope target did not reach Worker
- Human/Core approval remained mandatory
- no ground-truth leakage

Does **not** prove autonomous vulnerability discovery quality, real-world bug bounty performance, multi-model live validation, production readiness, or broad security-research validation. Do not set `SECURITY_RESEARCH_VALIDATED` or `PRODUCTION_READY`. GATE 04B remains PENDING.

```
python -m unittest tests.e2e.test_gate15_security_ground_truth
```

If `ZEST_TEST_DATABASE_URL` is unset, the suite must SKIP, never fabricate PASS.

## GATE 16 — workflow / state-transition authorization

**GATE 16 status: PASS** (2026-08-17).

Authoritative environment:

- Kali Linux
- dedicated real PostgreSQL test database
- `ZEST_TEST_DATABASE_URL`
- Alembic head `a19_001_http_state_class`
- GATE 14 regression: 19 OK, 0 skipped
- GATE 15 regression: 21 OK, 0 skipped
- GATE 16 workflow/state-transition benchmark: 34 OK, 0 skipped
- localhost-only synthetic workflow lab
- no Codex / LLM / Strix
- no external network

GATE 14 = single BOLA E2E.
GATE 15 = BOLA false-positive ground truth.
GATE 16 = second vulnerability class: workflow/state-transition authorization + cross-class discrimination.

These gates do not imply live model validation or production readiness.

GATE 16 proves only: controlled workflow/state-transition authorization semantics plus cross-class discrimination against `HTTP_AUTHORIZATION_DIFFERENTIAL`.

Capability: `http.state_transition` (GET/POST, 127.0.0.1, exact origin, redirects disabled). Classification: `HTTP_STATE_TRANSITION_AUTHORIZATION`.

Does **not** prove autonomous vulnerability discovery quality, real-world bug bounty performance, multi-model live validation, production readiness, or broad security-research validation. Do not set `SECURITY_RESEARCH_VALIDATED` or `PRODUCTION_READY`. GATE 04B remains PENDING.

```
python -m unittest tests.e2e.test_gate16_state_transition_security
```

If `ZEST_TEST_DATABASE_URL` is unset, the suite must SKIP, never fabricate PASS.

## GATE 17 — autonomous multi-hypothesis research selection

**GATE 17 status: PASS** (2026-08-17).

Authoritative environment:

- Kali Linux
- dedicated real PostgreSQL test database
- `ZEST_TEST_DATABASE_URL`
- authoritative tested commit `48d807d`
- GATE 14 regression: 19 OK, 0 skipped
- GATE 15 regression: 21 OK, 0 skipped
- GATE 16 regression: 34 OK, 0 skipped
- GATE 17 autonomous research-selection benchmark: 57 OK, 0 skipped
- repo-wide: unit 586 OK, contract 2 OK, architecture 15 OK, integration 112 OK
- no Codex / LLM / Strix
- no external network
- no migration
- 0 `psycopg.Connection` ResourceWarnings

GATE 14 = single controlled BOLA security E2E.
GATE 15 = BOLA ground-truth / false-positive benchmark.
GATE 16 = workflow/state-transition security semantics + cross-class discrimination.
GATE 17 = autonomous multi-hypothesis research selection + adaptive closed-loop experiment choice.
GATE 04B = live model comparison.

These gates do not imply one another.

GATE 17 proves only: Controlled local multi-hypothesis closed-loop research selection and adaptive experiment choice were validated against the dedicated real PostgreSQL test database with truth-blind benchmark execution.

Hidden ground truth may grade results; it must not steer execution or promotion.

It does **not** prove general autonomous vulnerability discovery, real-world bug bounty performance, live model quality, broad security-research validation, or production readiness. Do not set `LIVE_MODEL_VALIDATED`, `SECURITY_RESEARCH_VALIDATED`, or `PRODUCTION_READY`. GATE 04B remains PENDING.

```
python -m unittest tests.e2e.test_gate17_autonomous_research_selection
```

If `ZEST_TEST_DATABASE_URL` is unset, the suite must SKIP, never fabricate PASS.

## GATE 18 — offensive substrate foundation

**GATE 18 status: PASS** (2026-08-17).

Authoritative environment:

- Kali Linux
- dedicated real PostgreSQL test database
- `ZEST_TEST_DATABASE_URL`
- authoritative tested commit `241e901fb2c6730ee293cca71942de45d3796282`
- Alembic head `a20_001_capability_plan_binding`
- migration round-trip: a19 → a20, a20 → a19, a19 → a20
- GATE 14 regression: 19 OK, 0 skipped
- GATE 15 regression: 21 OK, 0 skipped
- GATE 16 regression: 34 OK, 0 skipped
- GATE 17 regression: 57 OK, 0 skipped
- repo-wide: unit 627 OK, contract 2 OK, architecture 20 OK, integration 117 OK
- no Codex / LLM execution
- no external-network research
- no `psycopg` ResourceWarning observed

Canonical Worker capability registry contains only `diagnostic.echo`, `http.authorization.differential`, and `http.state_transition`. Codex remains ModelPort. Strix remains Integration. Neither compiles into a Worker ExperimentPlan. Core independently checks capability/action/version/fingerprint/risk. The Worker independently rejects mismatched definitions. Legacy NULL plans are never silently fingerprint-backfilled. Scope remains exact-host / default-deny / fail-closed for ambiguity. Redirects require reauthorization.

GATE 14 = single controlled BOLA security E2E.
GATE 15 = BOLA ground-truth / false-positive benchmark.
GATE 16 = workflow/state-transition security semantics + cross-class discrimination.
GATE 17 = autonomous multi-hypothesis research selection + adaptive closed-loop experiment choice.
GATE 18 = typed per-action capability/risk/scope/execution binding that is Core-verified, durable across restart, and Worker-rejected on definition mismatch.
GATE 04B = live model comparison.

These gates do not imply one another.

GATE 18 PASS means Zest can transform an admitted research intent into a typed, per-action capability-bound and scope-evaluated experiment whose risk level and capability definition are independently verified by Core, durably bound across restart, and independently rejected by the Worker if its executable definition does not match.

It does **not** prove autonomous vulnerability discovery, broad security-research capability, real-world bug bounty performance, live model quality, production readiness, crawler/browser/recon capability, or XBOW/Edra parity. Do not set `LIVE_MODEL_VALIDATED`, `SECURITY_RESEARCH_VALIDATED`, or `PRODUCTION_READY`. GATE 04B remains PENDING.

If `ZEST_TEST_DATABASE_URL` is unset, PostgreSQL-required suites must SKIP, never fabricate PASS.

## GATE 19 — authorized HTTP substrate

**GATE 19 status: PASS** (2026-08-17).

Implementation commit: `95c88bc`

Authoritative tested HEAD: `b442a672a7df86482d0f5a60eb156483b691d44c`

Authoritative environment:

- Kali Linux
- dedicated real PostgreSQL test database
- `ZEST_TEST_DATABASE_URL`
- no SQLite substitution
- no skipped tests treated as PASS
- Alembic head `a21_001_session_context`
- GATE 04B remains PENDING
- no live model validation
- no G21 implementation
- repo-wide: unit 676 OK, contract 2 OK, architecture 22 OK, integration 120 OK
- explicit G18 plan binding: 5 OK
- explicit G19 HTTP transaction: 2 OK
- GATE 14 regression: 19 OK, 0 skipped
- GATE 15 regression: 21 OK, 0 skipped
- GATE 16 regression: 34 OK, 0 skipped
- GATE 17 regression: 57 OK, 0 skipped
- only Alembic `path_separator` DeprecationWarning remains; no production-behavior failure

GATE 19 PASS means Zest can construct and execute typed, capability-bound, Core-authorized general HTTP experiments using bounded request methods, paths, queries, headers and bodies, while preserving exact scope evaluation, redirect reauthorization, capability fingerprint enforcement and Worker execution bounds.

GATE 19 does **not** prove:

- autonomous endpoint discovery
- crawler/recon capability
- browser automation
- arbitrary internet HTTP
- broad vulnerability discovery
- real-world bug bounty performance
- production readiness

Current limitations:

- loopback HTTP substrate only
- no HTTPS breadth claim
- no crawler
- no automatic endpoint discovery
- no browser state
- HTTP transaction does not by itself imply vulnerability semantics

Do not set `LIVE_MODEL_VALIDATED`, `SECURITY_RESEARCH_VALIDATED`, or `PRODUCTION_READY`. GATE 04B remains PENDING. GATE 20 remains PENDING at this closure.

If `ZEST_TEST_DATABASE_URL` is unset, PostgreSQL-required suites must SKIP, never fabricate PASS.

## GATE 20 — identity authentication session

**GATE 20 status: PASS** (2026-08-17).

Implementation commit: `e574306`

Authoritative tested HEAD: `b442a672a7df86482d0f5a60eb156483b691d44c`

Authoritative environment:

- Kali Linux
- dedicated real PostgreSQL test database
- `ZEST_TEST_DATABASE_URL`
- no SQLite substitution
- no skipped tests treated as PASS
- Alembic head `a21_001_session_context`
- GATE 04B remains PENDING
- no live model validation
- no G21 implementation
- repo-wide: unit 676 OK, contract 2 OK, architecture 22 OK, integration 120 OK
- explicit G18 plan binding: 5 OK
- explicit G19 HTTP transaction: 2 OK
- explicit G20 identity/session: 1 OK
- GATE 14 regression: 19 OK, 0 skipped
- GATE 15 regression: 21 OK, 0 skipped
- GATE 16 regression: 34 OK, 0 skipped
- GATE 17 regression: 57 OK, 0 skipped
- only Alembic `path_separator` DeprecationWarning remains; no production-behavior failure

GATE 20 PASS means Zest can establish and isolate authenticated sessions for explicitly configured identities and execute authorized HTTP experiments under the correct identity/session context without storing raw credential or session material in the authoritative research state.

GATE 20 does **not** prove:

- autonomous account discovery
- browser authentication
- arbitrary authentication mechanisms
- durable session-secret recovery after restart
- autonomous vulnerability discovery
- real-world bug bounty performance
- production readiness

Current limitations:

- `HTTP_FORM_LOGIN` is the bounded supported auth profile
- session material is process-local/non-durable
- missing session secret after restart requires reauthentication
- session ID alone is not authority
- raw password/cookie/token is not stored in SoR
- no browser login
- no G21 behavior

Do not set `LIVE_MODEL_VALIDATED`, `SECURITY_RESEARCH_VALIDATED`, or `PRODUCTION_READY`. GATE 04B remains PENDING. GATE 19 remains PASS.

If `ZEST_TEST_DATABASE_URL` is unset, PostgreSQL-required suites must SKIP, never fabricate PASS.

## GATE 21 — browser / application state

**GATE 21 status: PASS** (2026-08-26). Formal qualification is backed by real Chromium behavioral validation, authoritative delegated Linux cgroup v2 containment, restart/session fail-closed validation, and current PostgreSQL regression. The ordinary non-delegated E2E invocation may skip the cgroup enforcement cases; those skips were not used to award PASS.

Capability: `browser.page` actions `observe` (SE0), `navigate` (SE0), `interact` (SE1). Loopback HTTP only.

Browser extra (does not change the default G14–G20 install):

```
pip install -e ".[browser]"
playwright install chromium
```

Playwright browser installation is setup-time only. Zest runtime must not silently download Chromium.

Local validation:

```
python -m unittest discover -s tests/unit -q
python -m unittest discover -s tests/contract -q
python -m unittest tests.unit.test_architecture_boundaries -q
python -m unittest discover -s tests/integration -q
python -m unittest tests.e2e.test_gate14_security_lab
python -m unittest tests.e2e.test_gate15_security_ground_truth
python -m unittest tests.e2e.test_gate16_state_transition_security
python -m unittest tests.e2e.test_gate17_autonomous_research_selection
python -m unittest tests.e2e.test_gate21_browser_page
python -m unittest tests.e2e.test_gate21_linux_cgroup
python -m unittest tests.e2e.test_gate22_surface_discovery
```

### Browser resource containment

The browser process tree runs under a kernel-enforced ceiling. There is no
unlimited fallback: if enforcement cannot be established the browser runtime is
NOT READY, `browser.page` fails closed with `START_FAILED`, and every other
Worker capability keeps working.

`max_memory_bytes` has one meaning on every host: the maximum aggregate memory
available to the contained Browser Worker plus its Chromium process tree.

| Host | Mechanism | Aggregate memory ceiling | PID ceiling |
| --- | --- | --- | --- |
| Linux | cgroup v2 owned child | `memory.max` over the whole contained tree | `pids.max` = `max_tasks` (tasks/threads, default 256) |
| Windows | Job Object | `JOB_OBJECT_LIMIT_JOB_MEMORY` / `JobMemoryLimit` across all job members | `ActiveProcessLimit` = `max_processes` (processes, default 32) |

These ceilings are not interchangeable. Linux `pids.max` counts tasks, including
threads. Windows `ActiveProcessLimit` counts processes. The containment
acknowledgement states the enforced kind and value; the Worker rejects a
mechanism/kind mismatch or a ceiling wider than its declared bound.

Windows additionally applies `JOB_OBJECT_LIMIT_PROCESS_MEMORY` with the same
value, which is stricter per process and never a substitute for the aggregate
bound. `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` remains set. The applied flags and
values are read back from the kernel with `QueryInformationJobObject` during the
containment handshake, so a job whose limits were not applied fails closed.

`memory.oom.group=1` and `memory.swap.max=0` are applied when the kernel exposes
them, so a cgroup OOM does not leave a partial Chromium tree running.

Ordering is enforced by a local handshake, not by timing: the Worker announces
its pid, blocks, and refuses to create Chromium until the parent has attached it
to the resource boundary and acknowledged the enforced values. An acknowledgement
that is missing, or wider than the Worker's declared limits, is rejected.

Linux requires a delegated, writable cgroup v2 subtree with the `memory` and
`pids` controllers. When the current subtree already owns processes and has no
delegation, start Zest under a delegated scope:

```
systemd-run --user --scope -p Delegate=yes -- python -m unittest tests.e2e.test_gate21_linux_cgroup
```

`ZEST_BROWSER_CGROUP_ROOT` may instead point at an already delegated
subtree inside `/sys/fs/cgroup`. It is host configuration; a WorkerRequest can
never influence the cgroup path.

On the authoritative validation host set `ZEST_REQUIRE_CGROUP_TESTS=1` so
an unavailable cgroup environment fails the suite instead of skipping it. A
skipped containment suite is never a pass.

GATE 21 does **not** claim:

- autonomous discovery
- crawler behavior
- bug bounty performance
- browser-based vulnerability discovery
- general internet browsing
- production readiness
- real-world security-research effectiveness

Alembic head after GATE 21 remained `a21_001_session_context`. GATE 22 appended `a22_001_discovery_surface`. GATE 21 added no migration.

If Chromium is unavailable, implementation may exist, but `GATE21_IMPLEMENTATION_READY_FOR_KALI` is FAIL. A skipped real-browser suite is not PASS.

Kernel memory and process enforcement is implemented on both supported hosts, and both bound the aggregate browser process tree. Linux cgroup enforcement is proved by `tests.e2e.test_gate21_linux_cgroup`, which must run on the authoritative Kali host; the Windows Job Object limits are proved by reading them back from the kernel in `tests.unit.platform_runtime.test_browser_resource_control`.

## GATE 22 — autonomous recon + attack surface graph

**GATE 22 status: PASS** (2026-08-18).

Authoritative tested implementation SHA: `ba24935d84245216011dc062fa12fbcccbefc9b5`

A later status-only commit may record this closure; it is not a second implementation SHA.

Authoritative environment:

- Kali Linux
- isolated PostgreSQL `zest_test`
- `ZEST_TEST_DATABASE_URL`
- no SQLite substitution
- Alembic head `a22_001_discovery_surface`
- `a22` → `a21` → `a22` round trip PASS
- G22 persistence: 3 OK / 0 skipped
- TX-B crash/replay: PASS, Worker redispatch = 0
- G22 hidden-lab: 2 OK / 0 skipped, real Chromium
- hidden-lab repeated successfully 3 additional times after the final fixture fix
- unit: 912 OK / 4 platform skips
- contract: 2 OK
- architecture: 26 OK
- integration: 123 OK / 0 skipped
- GATE 14: 19 OK
- GATE 15: 21 OK
- GATE 16: 34 OK
- GATE 17: 57 OK
- GATE 21 browser: 20 OK
- only skips: 4 Windows Job Object kernel tests on Kali (`the Job Object is a Windows mechanism`)
- GATE 04B remains PENDING
- no live model validation
- GATE 23 is not authorized

Formal claim:

Zest can autonomously build and maintain a bounded, provenance-rich, identity/state-aware attack-surface model of an authorized local target using real Browser/HTTP observations.

GATE 22 does **not** claim autonomous vulnerability discovery, bug-bounty capability, production readiness, generalized internet reconnaissance, or GATE 23.

Strategy: `surface.discovery.v1`. Existing `exploration.diagnostic.echo.v1` is unchanged. AttackSurfaceGraph is a rebuildable Research projection with no node/edge SoR tables.

Durable tables live in `a22_001_discovery_surface`: discovery_run_config, control_event, discovery_fact, discovery_fact_source, discovery_inference, discovery_inference_source, frontier_item, frontier_source, frontier_event, discovery_projection_receipt.

Projection is a deliberate two-transaction seam, not a shared commit with Transition A:

- TX A: WorkerResult persistence → Transition A Observation when applicable, or a typed ControlEvent (not Observation).
- TX B: project one Observation or ControlEvent → facts, typed sources, frontier, and `discovery_projection_receipt` in the same commit.

Crash after TX A and before TX B is recovered by replaying TX B from a missing receipt. Replay must not redispatch a Worker. Side-effectful `DISPATCHING` / `UNKNOWN_OUTCOME` remains not auto-retried.

G22 does not add shell, scanners, browser.evaluate, CDP, HAR, screenshots, wildcard scope, DNS widening, Neo4j, or NetworkX. G19 additive: none. G21 production capability/fingerprint is unchanged.

Hidden lab truth lives only in tests. ResearchContext must not receive route maps, vulnerability labels, or benchmark canaries.

Do not set `LIVE_MODEL_VALIDATED`, `SECURITY_RESEARCH_VALIDATED`, or `PRODUCTION_READY`. GATE 04B remains PENDING. GATE 23 is not authorized.

`tests.e2e.test_gate21_linux_cgroup` remains a closed G21 host concern unless G22 broke it. Do not run Codex, live-model, or GATE 04B probes.

If `ZEST_TEST_DATABASE_URL` is unset, PostgreSQL-required suites must SKIP, never fabricate PASS.

## GATE 01 — Attack Period (Scope Compiler v2 + ProgramResearchContext)

**GATE 01 status: PASS** (2026-08-19). Validated on Kali against isolated PostgreSQL; full suite: 1225 passed, 9 skipped.

Implementation scope:

- `core/scope_compiler.py`: wildcard `*.example.com` with multi-level subdomain match; apex `example.com` does not match; suffix attacks (`evil-example.com`, `example.com.evil.com`) and dotless suffixes are rejected; `UNKNOWN` classification denies active probing while allowing passive observation; expired rules fall back to `REQUIRE_HUMAN_REVIEW`.
- `core/enums.py`: `ScopeClassification` (`IN_SCOPE`, `UNKNOWN`, `OUT_OF_SCOPE`) and `ReasonCode.SCOPE_UNKNOWN_CLASSIFICATION`, `ReasonCode.SCOPE_EXPIRED`, `ReasonCode.PROGRAM_POLICY_DENIED`.
- Alembic `a23_001_program_scope`: `program`, `scope_rule_v2`, `program_policy`, `rate_limit_profile`, `bounty_table`.
- `application/program_research_context.py`: `ProgramResearchContext`, `ProgramPolicyView`, `load_program_research_context`, `derive_loopback_only`.
- `application/http_transaction_authorization.py`: `loopback_only` is no longer hardcoded to `True`; it is derived from `CompiledScope` + `ProgramPolicyView` (`loopback_fixture=True` or empty scope → `True`; real IN_SCOPE target → `False`).
- `application/execute_planned_experiment.py`: `ExecutePlannedExperimentCommand` carries an optional `program_policy`; passed to `authorize_http_transaction_plan`.
- `application/operator_status.py` + `interface/cli.py`: `GATE 01`, `GATE 21`, `GATE 22` are now surfaced in `zest status`.
- Worker runtime (`src/zest/worker_runtime/python/` and `workers/python/zest_worker/`): `ALLOWED_HOSTS` removed; dispatch is enforced against the envelope (scheme + host + port + path prefix). Missing or mismatched envelope → `EXECUTION_FAILED`.
- `tools/registry.py` + `research/compiler.py`: `SUPPORTED_REQUIREMENTS` extended with `"scope_derived"`; existing six contracts remain backward-compatible.
- Capability ceiling configurability: per-program `max_response_bytes` and `timeout_ms` with absolute caps (1 MB / 10 s). Budget authority remains in `core/budget.py`.

Current local validation (before authoritative Kali run):

- Alembic head `a23_001_program_scope`
- unit + contract: 947 OK / 4 skipped (Windows Job Object kernel tests skip on Linux/Kali)
- integration: 123 OK / 0 skipped
- e2e: 155 OK / 5 skipped
- `zest status` reports POSTGRESQL HEALTHY, TEST_POSTGRESQL HEALTHY, Worker HEALTHY

GATE 01 proves only: Zest can compile authorized program scope and policy into a fail-closed dispatch envelope that differentiates loopback fixtures from real IN_SCOPE targets, while keeping UNKNOWN targets observable-but-not-probed.

GATE 01 does **not** prove:

- autonomous vulnerability discovery
- arbitrary external internet targeting
- live bug-bounty performance
- platform sync to HackerOne/Bugcrowd (port + contract only in this gate; live adapter later)
- OAST callbacks or program-policy actions beyond `DENY`
- production readiness

Current limitations:

- loopback fixture remains the default when no program policy is loaded
- explicit program scope + policy are required for non-loopback dispatch
- UNKNOWN classification is fail-closed for active probes
- Worker cannot expand a Core-derived envelope

Do not set `LIVE_MODEL_VALIDATED`, `SECURITY_RESEARCH_VALIDATED`, or `PRODUCTION_READY`. GATE 04B remains PENDING. GATE 22 remains PASS.

Validation commands (to be run on authoritative Kali host with real PostgreSQL):

```
python -m compileall src tests scripts
python -m unittest discover -s tests/unit -q
python -m unittest discover -s tests/contract -q
python -m unittest tests.unit.test_architecture_boundaries -q
python -m unittest discover -s tests/integration -q
python scripts/run_research_benchmark.py --baseline GOOD_BASELINE --single-run-legacy
python scripts/clean_install_smoke.py
```

If `ZEST_TEST_DATABASE_URL` is unset, PostgreSQL-required suites must SKIP, never fabricate PASS.

## GATE 02 — Attack Period (Sensor/Acquisition Plane)

**GATE 02 status: PENDING.** Local implementation exists; formal PASS requires
Kali + real PostgreSQL validation. This is the Attack Period sensor plane
(SD-G2). It is **not** the old infrastructure GATE 02 (Bounded Research
Reasoning Cycle, `a8_001_research_reasoning`, closed 2026-08-16); those are
separate eras and must never be confused.

Implementation scope:

- Alembic `a24_001_sensor_plane`: `sensor_observation` table for raw,
  UNTRUSTED_EXTERNAL sensor records. This is not `discovery_fact`.
- `zest.research.sensor.types`: `SensorObservation`,
  `SensorCollectionResult`, `SensorPort`, `ScopeCensusView`. Sensors produce
  observations; they never write domain truth or generate authoritative IDs.
- `zest.research.sensor.{dns,ctlog,archive,cert,techfp}`: five
  passive/semi-passive sensors using fixture-based or already-collected data.
  No active probing, no banner grab, no live external calls in tests.
- `zest.application.sensor.runner`: `SensorAcquisitionRunner`
  coordinates sensors for one target under Core scope control and persists
  `SensorObservationRecord`s only.
- `zest.application.sensor.admit`: deterministic admission
  (`AdmitSensorObservations`) turns one observation into one `DiscoveryFact`,
  capped at `OBSERVED`, source marked `UNTRUSTED_EXTERNAL`, with an admission
  receipt in `discovery_fact_source`. Forbidden discovery keys are rejected;
  rejected observations produce no fact.
- `zest.interface.cli`: `zest census --research-run-id <id>
  --target <host> [--fixture-dir <dir>]` operator trigger; scope control is
  enforced by Core, CLI cannot bypass it.
- `core/enums.py`: `ScopeClassification.UNKNOWN` allows census; explicit
  `OUT_OF_SCOPE` denies even census. `ReasonCode.SENSOR_TIMEOUT`,
  `ReasonCode.SENSOR_FAILED`, `ReasonCode.CENSUS_ALLOWED`,
  `ReasonCode.CENSUS_DENIED`.
- `maturity.py`: `GATE_02_STATUS = "PENDING"` until authoritative validation.

Formal claim (upon PASS):

Zest can execute a Core-controlled, passive/semi-passive external census
of an authorized target using multiple sensors, persist raw observations as
UNTRUSTED_EXTERNAL, and deterministically admit them into the discovery ledger
as OBSERVED facts with provenance receipts, while keeping sensors unable to
write domain truth directly.

GATE 02 proves only: controlled sensor acquisition + deterministic admission
plumbing against PostgreSQL.

GATE 02 does **not** prove:

- autonomous vulnerability discovery
- active probing or live internet reconnaissance
- real bug-bounty performance
- production readiness
- old infrastructure GATE 02 behavior

Current limitations:

- Sensors are fixture-based or already-collected-data only in this gate.
- Live sensor execution is restricted to the operator-triggered `census` CLI
  command under Core scope control.
- No INFERRED/HYPOTHESIZED epistemic upgrade in admission; that is later work.

Validation commands (to be run on authoritative Kali host with real PostgreSQL):

```
python -m compileall src tests scripts
python -m unittest discover -s tests/unit -q
python -m unittest discover -s tests/contract -q
python -m unittest tests.unit.test_architecture_boundaries -q
python -m unittest discover -s tests/integration -q
python scripts/run_research_benchmark.py --baseline GOOD_BASELINE --single-run-legacy
python scripts/clean_install_smoke.py
```

If `ZEST_TEST_DATABASE_URL` is unset, PostgreSQL-required suites must SKIP, never fabricate PASS.

## GATE 03 — Attack Period (SurfaceGraph v2)

**GATE 03 status: PASS.** Sealed at `05ce3c0` + seal commit `f74677d`;
independent architect audit: 991 unit+contract passed; silent-drop eliminated;
`UNTRUSTED_EXTERNAL` preserved in graph; scope provenance mandatory. This is the
Attack Period SurfaceGraph v2 (SD-G3). It is **not** the old infrastructure
GATE 03 (Learning Cycle, `a9_001_learning_cycle`, closed 2026-08-16); those are
separate eras and must never be confused.

Implementation scope:

- `zest.research.discovery.types`: `AttackSurfaceNodeKind` extended with
  `DOMAIN`, `HOSTNAME`, `CERT`, `SERVICE`, `TECH`, `JS_BUNDLE`, `API_SPEC`;
  `AttackSurfaceEdgeKind` extended with `RESOLVES_TO`, `HOSTED_ON`, `SECURED_BY`,
  `RUNS`.
- `zest.research.discovery.graph`: `FACT_NODE_KIND` maps all seven
  SD-G2 sensor fact kinds; unmapped kinds raise `ResearchInputError` instead of
  being silently dropped. Sensor-sourced nodes preserve
  `TargetEpistemicStatus.UNTRUSTED_EXTERNAL` and carry `ScopeClassification`.
  Sensor-derived edges (HOSTNAME→ORIGIN, CERT→HOSTNAME, TECH→ORIGIN,
  JS_BUNDLE/API_SPEC→EXACT_PATH) are produced with provenance.
- `zest.application.sensor.admit`: `scope_classification` is a required
  keyword argument; no default UNKNOWN. It is stored in the admitted fact's
  attributes for graph projection.
- `zest.application.discovery.snapshot_views`: deterministic
  `summarize_attack_surface()` rebuilds the graph from the ledger and produces
  `AttackSurfaceSummary` with kind counts, identity coverage, scope
  classification counts, and `graph_hash`.
- Alembic `a27_001_attack_surface_snapshot`: `attack_surface_snapshot` table
  stores `research_run_id`, `strategy_version`, `node_count`, `edge_count`,
  `graph_hash`, `created_at`. Node/edge data remains rebuildable from the
  discovery ledger; the snapshot is a durable fingerprint, not a second copy.
- `maturity.py`: `GATE_03_STATUS = "PASS"` sealed by architect audit.

Formal claim (upon PASS):

Zest can rebuild a deterministic, scope-classified attack surface graph
from the discovery ledger including sensor-derived external census facts, persist
a hash/count snapshot, and query the graph by kind, identity, and scope
classification without granting scope, session, budget, or capability.

GATE 03 proves only: sensor-fact graph integration + deterministic snapshot
plumbing against PostgreSQL.

GATE 03 does **not** prove:

- autonomous vulnerability discovery
- active probing or live internet reconnaissance
- real bug-bounty performance
- production readiness
- old infrastructure GATE 03 behavior

Current limitations:

- Sensor-derived edge wiring uses explicit attributes/origin references only.
- Graph queries are read-only summaries; no planner consumes UNKNOWN nodes yet.
- Live sensor execution remains restricted to the operator-triggered `census`
  CLI command under Core scope control.

Validation commands (to be run on authoritative Kali host with real PostgreSQL):

```
python -m compileall src tests scripts
python -m unittest discover -s tests/unit -q
python -m unittest discover -s tests/contract -q
python -m unittest tests.unit.test_architecture_boundaries -q
python -m unittest discover -s tests/integration -q
python scripts/run_research_benchmark.py --baseline GOOD_BASELINE --single-run-legacy
python scripts/clean_install_smoke.py
```

If `ZEST_TEST_DATABASE_URL` is unset, PostgreSQL-required suites must SKIP, never fabricate PASS.

## GATE 04 — Attack Period (Token Economy Policy)

**GATE 04 status: PENDING.** Local implementation exists; formal PASS requires
Kali + real PostgreSQL validation. This is the Attack Period Token Economy
Policy (SD-G4). It is **not** the old infrastructure GATE 04/04B (Benchmark
Compatible policy); those are separate eras and must never be confused.

Implementation scope:

- `zest.core.pricing`: `MODEL_PRICE_TABLE` maps `model_id` to input/output
  microdollars per 1M tokens. `estimate_cost(model_id, tokens_in, tokens_out)`
  returns an `int` in microdollars. Unknown `model_id` raises
  `UnknownModelPriceError` (fail-closed: no money is spent on unpriced models).
- `zest.research.model_port.ModelCallResult`: carries
  `prompt_tokens`/`completion_tokens` (None if the provider does not report).
- `zest.data.records`: `BudgetConsumptionRecord` gains `resource_metadata`
  JSONB; `ALLOWED_BUDGET_RESOURCE_TYPES` gains `MODEL_TOKENS_IN`,
  `MODEL_TOKENS_OUT`, `MODEL_ESCALATION_DECISION`.
- `zest.application.budget_enforced_model`: reserves `MODEL_CALL` on the
  research-run budget **before** invocation (existing K1 pattern), then records
  `MODEL_TOKENS_IN`/`MODEL_TOKENS_OUT` on the program-daily budget after the
  call. Replay with the same `request_id` is idempotent.
- `program_policy.daily_llm_budget_microdollars`: nullable integer in
  microdollars. `NULL` does **not** mean unlimited; it means the operator has
  not set a daily limit and live model calls are denied (fail-closed).
- `zest.application.program_daily_budget`:
  `AllocateProgramDailyBudget` creates the daily envelope;
  `ProgramDailyBudgetUsage` reads the append-only ledger and sums costs via
  `estimate_cost`; `CheckProgramDailyBudget` denies calls when the limit is
  reached or unset.
- `zest.research.routing`: `ModelPriceClass` (`cheap`/`expensive`);
  `TASK_PRICE_CLASS_POLICY`; default route is `cheap`; `expensive` is allowed
  only when the task class policy marks it or the previous cheap call returned
  `ESCALATION_NEEDED`. `monitoring` task class maps to `none` and produces zero
  model calls.
- `zest budget --program-id <id> [--date <iso>]`: read-only operator view
  of limit, spent, remaining, token counts, and recent call class distribution.
- Alembic `a28_001_token_economy`: adds
  `program_policy.daily_llm_budget_microdollars` and
  `budget_consumption.resource_metadata`; makes `issued_budget.research_run_id`
  nullable so program-daily cost envelopes do not require a research run.
- `maturity.py`: `GATE_04_STATUS = "PENDING"` until authoritative validation.

Cost unit note: 1 USD = 1_000_000 microdollars. The `daily_llm_budget_usd`
semantic from the policy is stored as `daily_llm_budget_microdollars` to keep
Core arithmetic integer-only and deterministic.

Formal claim (upon PASS):

Zest can enforce a per-program daily LLM cost ceiling, default to cheap
models, escalate to expensive models only on proven evidence, guarantee zero LLM
calls in monitoring tasks, and expose a read-only operator budget view sourced
entirely from the append-only consumption ledger.

GATE 04 proves only: cost accounting, daily budget enforcement, and routing
escalation plumbing against PostgreSQL.

GATE 04 does **not** prove:

- autonomous vulnerability discovery
- active probing or live internet reconnaissance
- real bug-bounty performance
- production readiness
- old infrastructure GATE 04/04B behavior

Current limitations:

- `MODEL_PRICE_TABLE` contains placeholder prices; live operational prices are
  set by operator configuration outside Core.
- `estimate_cost` uses integer microdollar arithmetic; sub-microdollar rounding
  is downward, so reported spend is a conservative lower bound.
- Program-daily budgets are independent of research-run model-call budgets;
  both must be satisfied for a call to proceed.

Validation commands (to be run on authoritative Kali host with real PostgreSQL):

```
python -m compileall src tests scripts
python -m unittest discover -s tests/unit -q
python -m unittest discover -s tests/contract -q
python -m unittest tests.unit.test_architecture_boundaries -q
python -m unittest discover -s tests/integration -q
python scripts/run_research_benchmark.py --baseline GOOD_BASELINE --single-run-legacy
python scripts/clean_install_smoke.py
```

If `ZEST_TEST_DATABASE_URL` is unset, PostgreSQL-required suites must SKIP, never fabricate PASS.

## GATE 05 — Attack Period (HunterFamily Registry + First Hunt Cycle)

**GATE 05 status: PASS.** Sealed at `310993d` + seal commit; independent
architect audit: 1036 unit passed, 4 skipped (Windows Job Object kernel tests on
Linux/Kali); zero deleted tests; C1 hardcoded-family lock broken; scope
confinement active on all 5 seed families via IN_SCOPE preconditions; V3 queue
approval-gated. This is the Attack Period HunterFamily Registry + First Hunt
Cycle (SD-G5). It is **not** the old infrastructure GATE 05 (Learning Cycle);
those are separate eras and must never be confused.

Implementation scope:

- Alembic `a29_001_hunter_family_registry`: `hunter_family` table (append-only
  versioning, composite PK `family_id` + `version`) and `hunt_v3_queue` table
  (PENDING/APPROVED/RUN/BLOCKED active-experiment queue). Five seed families:
  `OBJECT_AUTHORIZATION`, `WORKFLOW_STATE_TRANSITION`, `EXPOSED_API_SPEC`,
  `UNPROTECTED_HOSTNAME`, `TECH_KNOWN_CVE_SURFACE`.
- `zest.research.selection`: `HypothesisFamily` enum extended with
  SD-G5 families; `HunterFamilyView` read-only registry view;
  `families_for_node(node, graph, registry)` matches node kind, scope
  classification, and edge preconditions without UNKNOWN spam;
  `claim_from_template(node, family)` produces deterministic claim text.
- `zest.application.generate_hunt_hypotheses`:
  `GenerateHuntHypotheses` use case walks the graph, applies registry families,
  and persists `HypothesisRecord`s plus `HUNT_HYPOTHESIS_GENERATED` audit events.
  Default path is LLM-free.
- `zest.application.hunt_validation`: `ValidateHuntTiers` runs V1
  (static preconditions), V2 (passive evidence requirements), and V3
  (active-experiment enqueue). V3 is never reached unless V1 and V2 pass.
  Tier decisions are durable in `audit_event`; V3 items are inserted into
  `hunt_v3_queue` with state PENDING.
- `zest.application.run_hunt_cycle`: `RunHuntCycle` orchestrates one
  hunt cycle: generate → V1 → V2 → V3 queue. All state is in the append-only
  ledger; the cycle is stateless across invocations.
- `zest.application.sensor.admit`: TECH facts now carry a `technology`
  attribute from the sensor payload so the `TECH_KNOWN_CVE_SURFACE` claim
  template can render deterministically.
- `maturity.py`: `GATE_05_STATUS = "PASS"` sealed by architect audit.

Formal claim (upon PASS):

Zest can read the attack-surface graph, match nodes against a
versioned data-driven family registry, generate deterministic hypotheses,
run static + passive validation tiers, and enqueue approved active experiments
for V3 execution, all without LLM calls in the default path.

GATE 05 proves only: data-driven family registry + deterministic hunt-cycle
plumbing against PostgreSQL.

GATE 05 does **not** prove:

- autonomous vulnerability discovery
- active probing or live internet reconnaissance
- real bug-bounty performance
- production readiness
- old infrastructure GATE 05 behavior

Current limitations:

- V3 queue items are PENDING until a separate active-experiment approval gate.
- Registry is append-only operator/migration data; LLM cannot write to it.
- Optional LLM enrichment is budget-gated and not implemented in this gate.

Validation commands (to be run on authoritative Kali host with real PostgreSQL):

```
python -m compileall src tests scripts
python -m unittest discover -s tests/unit -q
python -m unittest discover -s tests/contract -q
python -m unittest tests.unit.test_architecture_boundaries -q
python -m unittest discover -s tests/integration -q
python scripts/run_research_benchmark.py --baseline GOOD_BASELINE --single-run-legacy
python scripts/clean_install_smoke.py
```

If `ZEST_TEST_DATABASE_URL` is unset, PostgreSQL-required suites must SKIP, never fabricate PASS.

## Maturity

- ARCHITECTURE_VALIDATED: architecture package complete
- DIAGNOSTIC_E2E_VALIDATED: yes after Gate 12/13 PASS on real PostgreSQL, process crash/restart, and clean install. Not live-model validation.
- LIVE_MODEL_VALIDATED: yes; GATE 04B authoritative paired live-runtime qualification passed on implementation baseline 014b1c0d88eeacdcf0e5a26330d9fe2de888f4fe
- SECURITY_RESEARCH_VALIDATED: no; GATE 14/15/16/17/18/19/20 PASS cover controlled local pipeline, ground-truth, cross-class, adaptive research-selection, capability/risk/scope substrate, bounded authorized HTTP transaction, and identity/session isolation validation, not broad or real-world security-research validation. GATE 22 PASS is not security-research validation.
- PRODUCTION_READY: no until operational and live-research gates that have not passed actually pass
- GATE 01: PASS (2026-08-19, Kali, isolated PostgreSQL, full suite 1225 passed / 9 skipped)
- GATE 02: PASS (Sensor/Acquisition Plane sealed at e2bf18b; independent architect audit: 977 unit+contract passed, boundary clean, admission per spec; not the old infrastructure GATE 02 reasoning cycle)
- GATE 03: PASS (Attack Period SurfaceGraph v2 sealed at 05ce3c0 + f74677d; independent architect audit: 991 unit+contract passed; not the old infrastructure GATE 03 learning cycle)
- GATE 04: PENDING (Attack Period Token Economy Policy implemented locally; formal PASS requires Kali + real PostgreSQL validation; not the old infrastructure GATE 04/04B benchmark policy)
- GATE 05: PASS (2026-08-19, sealed at 310993d + seal commit; independent architect audit: 1036 unit passed / 4 skipped; data-driven HunterFamily registry with 5 seed families; V1/V2/V3 tiers enforced; V3 queue approval-gated; not the old infrastructure GATE 05 learning cycle)
- GATE 14: PASS (2026-08-17, Kali, dedicated PostgreSQL, 19 E2E OK / 0 skipped)
- GATE 15: PASS (2026-08-17, Kali, dedicated PostgreSQL, GATE14 regression 19 OK / 0 skipped, GATE15 21 OK / 0 skipped)
- GATE 16: PASS (2026-08-17, Kali, dedicated PostgreSQL, GATE14 19 OK / 0 skipped, GATE15 21 OK / 0 skipped, GATE16 34 OK / 0 skipped)
- GATE 17: PASS (2026-08-17, commit 48d807d, Kali, dedicated PostgreSQL, GATE14 19 OK / 0 skipped, GATE15 21 OK / 0 skipped, GATE16 34 OK / 0 skipped, GATE17 57 OK / 0 skipped)
- GATE 18: PASS (2026-08-17, commit 241e901fb2c6730ee293cca71942de45d3796282, Kali, dedicated PostgreSQL, Alembic head a20_001_capability_plan_binding, unit 627 OK, contract 2 OK, architecture 20 OK, integration 117 OK, GATE14 19 OK / 0 skipped, GATE15 21 OK / 0 skipped, GATE16 34 OK / 0 skipped, GATE17 57 OK / 0 skipped)
- GATE 19: PASS (2026-08-17, implementation 95c88bc, authoritative tested HEAD b442a672a7df86482d0f5a60eb156483b691d44c, Kali, dedicated PostgreSQL, Alembic head a21_001_session_context, unit 676 OK, contract 2 OK, architecture 22 OK, integration 120 OK, GATE14 19 OK / 0 skipped, GATE15 21 OK / 0 skipped, GATE16 34 OK / 0 skipped, GATE17 57 OK / 0 skipped)
- GATE 20: PASS (2026-08-17, implementation e574306, authoritative tested HEAD b442a672a7df86482d0f5a60eb156483b691d44c, Kali, dedicated PostgreSQL, Alembic head a21_001_session_context, unit 676 OK, contract 2 OK, architecture 22 OK, integration 120 OK, GATE14 19 OK / 0 skipped, GATE15 21 OK / 0 skipped, GATE16 34 OK / 0 skipped, GATE17 57 OK / 0 skipped)
- GATE 21: PASS (2026-08-26, implementation baseline 014b1c0d88eeacdcf0e5a26330d9fe2de888f4fe; real Chromium 20/20, delegated Linux cgroup containment 7/7, restart/session semantics 3/3, current PostgreSQL integration regression 270/270)
- GATE 22: PASS (2026-08-18, authoritative tested implementation SHA ba24935d84245216011dc062fa12fbcccbefc9b5, Kali, isolated PostgreSQL zest_test, Alembic head a22_001_discovery_surface, a22→a21→a22 PASS, G22 persistence 3 OK / 0 skipped, TX-B replay PASS with Worker redispatch 0, hidden-lab 2 OK / 0 skipped plus 3 additional successful repeats, unit 912 OK / 4 Windows Job Object skips, contract 2 OK, architecture 26 OK, integration 123 OK / 0 skipped, GATE14 19 OK, GATE15 21 OK, GATE16 34 OK, GATE17 57 OK, GATE21 browser 20 OK)
- GATE 01: PASS (Scope Compiler v2 + ProgramResearchContext + program-policy-derived loopback fixture; Kali + isolated PostgreSQL, full suite 1225 passed / 9 skipped)
