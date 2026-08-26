# Zest — Persistent Runtime, Recovery & Operator Plan

**Belge sınıfı:** Operasyonel runtime / daemon / 24/7 planı  
**Ana belge:** `ZEST_MASTER_PLAN.md`
**Tarih:** 2026-08-21  
**Durum:** PLAN

---

## 0. Hedef

Zest araştırma motoru:
- browser kapanınca,
- Cursor kapanınca,
- SSH bağlantısı kopunca,
- dashboard yeniden başlatılınca,
- model runtime geçici olarak erişilemez olunca

ölmemelidir.

Gerçek runtime owner:

```text
zestd
```

olacaktır.

Ancak daemon yeni bir araştırma beyni olmayacaktır.

---

## 1. Ownership sınırları

### zestd owns

- process lifetime,
- runtime instance registration,
- run scheduling,
- lease acquire/renew/release,
- fencing enforcement orchestration,
- heartbeat,
- crash detection,
- recovery classification coordination,
- Preflight invocation,
- Worker process lifecycle/health,
- model runtime discovery/health,
- DB/disk/runtime health,
- Operator API host,
- SSE telemetry transport,
- notification event emission.

### zestd does NOT own

- hypothesis generation,
- opportunity selection,
- HunterFamily interpretation,
- experiment semantics,
- Evidence admission,
- Candidate validation,
- Finding decisions,
- scope,
- authorization,
- budget policy,
- side-effect execution itself.

Research semantics: `AutonomousResearchController`  
Authority: `Core`  
Execution: `WorkerPort`  
Truth: `PostgreSQL`

---

## 2. Dashboard kuralı

Dashboard/Operator Console:
- client'tır,
- run owner değildir,
- process supervisor değildir,
- authoritative config holder değildir,
- scope/budget override kaynağı değildir.

Dashboard yalnız:
- state görüntüler,
- lifecycle command yollar,
- approvals gönderir,
- health/timeline izler.

Closing browser = no-op for active run.

---

## 3. Runtime state correctness

Persistent daemon'dan önce state machine düzeltilir.

### Terminal states

Repo'daki gerçek enum isimleri audit sonrası authoritative alınır.

Semantik olarak terminal:
- completed family,
- operator cancelled/stopped,
- unrecoverable operational failure.

Terminal state sonrası:
- state değişmez,
- stop_reason değişmez,
- Finding/provenance geçmişi yeniden yazılmaz.

Operator command:
- auditlenir,
- state mutation yapmaz.

### desired action

Operator komutu ile actual runtime state ayrılabilir:

```text
desired_action = START | PAUSE | RESUME | CANCEL
current_state  = READY | RUNNING | PAUSED | ...
```

Supervisor reconcile eder.

Bu, aynı anda gelen PAUSE/RESUME/CANCEL yarışlarını deterministik hale getirir.

---

## 4. Durable ExecutionAttempt journal

Gerçek dünyaya giden her side-effecting call öncesinde durable intent gerekir.

Minimum alanlar:

```text
attempt_id
research_run_id
experiment_id
request_id
lease_epoch
capability_id
plan_fingerprint
authorization_decision_id
status
side_effect_level
started_at
dispatched_at
result_recorded_at
outcome_class
```

Önerilen phases:

```text
INTENT_COMMITTED
AUTHORIZATION_REQUESTED
AUTHORIZED
DISPATCHING
DISPATCHED
RESULT_RECORDED
FAILED_BEFORE_DISPATCH
UNKNOWN_OUTCOME
```

`request_id` unique.

Purpose:
- duplicate dispatch önleme,
- crash noktasını belirleme,
- unknown side-effect'i güvenli sınıflandırma,
- auditability,
- recovery.

---

## 5. Fenced run ownership

Lease tek başına yeterli değildir.

Run ownership:

```text
runtime_instance
+
owner_runtime_instance_id
+
lease_epoch
+
heartbeat_at
+
lease_until
```

`lease_epoch` monotonic fencing token'dır.

### Acquire

Yeni owner:
- run terminal değil,
- current lease expired/unowned,
- atomic CAS ile owner olur,
- epoch artar.

### Every authoritative mutation

Mutation:
- run id,
- owner runtime id,
- expected lease epoch

ile koşullandırılır.

0 row affected:
- ownership kaybedildi,
- daemon bu run için hemen yeni work üretmeyi keser.

### Worker dispatch

Dispatch path de current epoch ile bağlanır.

Old owner:
- yeni attempt yaratamaz,
- state değiştiremez,
- stale result'i authoritative path'e terfi ettiremez.

### DB time

Lease expiry wall-clock kararı için PostgreSQL time authoritative tercih edilir.

---

## 6. Heartbeat

Örnek başlangıç:
- heartbeat 30s,
- lease 90s.

Değerler config/policy ile değişebilir; magic constant olarak mimariye gömülmez.

Heartbeat:
- research semantics değildir,
- run progress değildir,
- Evidence değildir.

Heartbeat failure:
- immediate research conclusion üretmez.

---

## 7. Crash recovery classifier

Recovery kararı explicit sınıfa dönüşür.

### SAFE_AUTOMATIC_RESUME

Durum:
- no in-flight side effect,
- previous result persisted,
- durable checkpoint known.

Action:
- fresh lease,
- reload SoR,
- continue.

### SAFE_RETRY_AFTER_REAUTHORIZATION

Durum:
- intent committed,
- dispatch olmadı.

Action:
- fresh lease,
- reload current policy,
- recompile if required,
- Core reauthorize,
- idempotency contract'a göre güvenli attempt oluştur.

### RECONCILIATION_REQUIRED

Durum:
- dispatch phase ambiguous,
- process died around Worker dispatch.

Action:
- run RECONCILING,
- no automatic retry,
- inspect durable attempt/worker artifacts/OAST/etc.

### HUMAN_REQUIRED

Durum:
- side effect may have happened,
- outcome unknown,
- high side-effect class.

Action:
- operator decision.

### OPERATIONAL_TERMINAL

Repeated/unrecoverable infrastructure failure.  
Research conclusion değildir.

---

## 8. DB outage behavior

PostgreSQL SoR ise DB yokken daemon yeni authoritative work üretemez.

DB disconnect:
- new Worker dispatch stop,
- new model research transitions stop,
- in-flight worker'ı "request geri alınabilir" varsayımıyla öldürme,
- result temporary bounded buffer'da tutulabilir ancak authority olmaz,
- reconnect sonrası lease yeniden doğrulanır,
- ambiguous transaction request_id ile reconcile edilir.

DB dönmezse:
- operational pause/failure,
- hypothesis falsified olmaz.

---

## 9. Worker failure behavior

Worker timeout:
- `UNKNOWN_OUTCOME` ihtimali değerlendirilir,
- network request hiç çıkmadığı kesin ise typed pre-dispatch failure olabilir,
- request çıkıp result gelmediyse blind retry yok.

Worker crash:
- same rule.

Invalid WorkerResult:
- schema fail,
- Observation yaratılmaz,
- operational error.

Late WorkerResult:
- terminal run state'i değiştirmez,
- attempt audit trail'e bağlanır,
- epistemic admission explicit policy'ye tabidir.

---

## 10. Model runtime failure behavior

Model errors:
- AUTH_FAILURE,
- RATE_LIMITED,
- UNAVAILABLE,
- CONTENT_POLICY_BLOCKED,
- MALFORMED_OUTPUT,
- STRUCTURED_OUTPUT_INCOMPATIBLE

gibi typed operational outcomes olmalıdır.

Model failure:
- vulnerability sonucu değildir,
- hypothesis falsification değildir,
- Evidence değildir.

Fallback:
- routing policy sınırları içinde,
- max fallback bounded,
- budget Core'dan geçer.

Rate-limit cooldown process memory'de kaybolmamalıdır.

---

## 11. Preflight

START öncesi fail-fast readiness report.

### Checks

#### Program
- AuthorizationSource active,
- scope valid,
- target classification valid,
- program policy present,
- persisted run configuration complete.

#### Database
- PostgreSQL reachable,
- migration/schema expected version,
- disk threshold acceptable.

#### Runtime ownership
- no competing live lease,
- runtime instance registered.

#### Worker
- required capability contracts,
- runtime healthy,
- browser required ise browser readiness.

#### Model
- selected ModelPort configuration exists,
- auth valid,
- structured output compatibility,
- not unusably rate-limited,
- call budget available.

#### Limits
- request budget,
- worker budget,
- model budget,
- orchestration bounds,
- side-effect ceiling.

### PreflightReport

Persist:
- run,
- timestamp,
- release version,
- config hash,
- checks,
- exact failure reasons,
- result.

Preflight runtime health yerine geçmez. START anının kanıtıdır.

---

## 12. Persistent supervisor

Mevcut local supervisor semantics korunup process-local registry'den çıkarılır.

Supervisor:
- run lease alır,
- ARC step loop'u schedule eder,
- persisted command/config reconstruct eder,
- pause/cancel desired action'ı gözler,
- heartbeat yeniler,
- exception'ı typed operational state'e çevirir,
- finally lease cleanup/reconciliation yapar.

Supervisor:
- research policy hesaplamaz,
- model prompt yazmaz,
- scope genişletmez,
- finding üretmez.

---

## 13. zestd startup sequence

```text
1. Load immutable service config
2. Connect PostgreSQL
3. Verify schema
4. Register RuntimeInstance
5. Discover Worker runtimes
6. Discover Model runtimes
7. Start health monitor
8. Scan owned/orphaned runs
9. Recovery classifier
10. Acquire eligible leases
11. Start supervisors
12. Start Operator API
13. Start SSE/event delivery
```

DB/schema/ownership doğrulanmadan active work başlamaz.

---

## 14. Operator API

API stable Application boundary'dir.

Minimum resources:
- `/healthz`
- `/api/runtime`
- `/api/programs`
- `/api/runs`
- `/api/runs/{id}`
- `/api/runs/{id}/preflight`
- `/api/runs/{id}/start`
- `/api/runs/{id}/pause`
- `/api/runs/{id}/resume`
- `/api/runs/{id}/cancel`
- `/api/runs/{id}/timeline`
- `/api/approvals`
- `/api/v3-queue`
- `/api/candidates`
- `/api/findings`
- `/api/coverage`
- `/api/events`

Command payload authoritative run target/scope/bounds override etmez.  
Authoritative values PostgreSQL'dan reconstruct edilir.

---

## 15. Operator Console

Ana paneller:

### Program
- scope,
- exclusions,
- AuthorizationSource,
- policy,
- identities,
- rate limits.

### Run
- state,
- phase,
- current cycle,
- bounds,
- runtime owner,
- lease health,
- selected model,
- Worker health,
- preflight.

### Research
- surfaces,
- opportunities,
- hypotheses,
- experiments,
- observations,
- evidence,
- coverage debt.

### Review
- V3 approvals,
- reconciliation,
- Candidate verification,
- FindingProposal human review.

### Runtime
- DB,
- daemon,
- worker,
- model,
- OAST,
- browser,
- disk/log.

### Timeline
Human-readable projection from authoritative events.  
AuditEvent'i log çöplüğüne dönüştürmez.

---

## 16. Event delivery

Client live updates:
- SSE.

REST:
- authoritative full snapshot.

Reconnect:
1. client full state fetch,
2. last event cursor/sequence,
3. SSE deltas.

PostgreSQL LISTEN/NOTIFY internal wake-up için kullanılabilir ama client-facing SoR değildir.  
Kafka/NATS gerekmez.

---

## 17. Observability

Minimum structured logs:
- runtime_instance_id,
- research_run_id,
- cycle_id,
- experiment_id,
- attempt_id,
- lease_epoch,
- capability,
- event_type,
- error_class.

Metrics:
- cycle latency,
- worker latency,
- model latency,
- authorization denial count,
- rate-limit events,
- budget burn,
- unknown outcomes,
- reconciliation count,
- lease loss,
- supervisor restarts,
- queue depth,
- disk usage.

Alerts:
- stuck DISPATCHING,
- expired lease not reclaimed,
- DB unavailable,
- disk low,
- repeated Worker crash,
- all ModelPort runtimes unavailable,
- backup failure.

AuditEvent ≠ log event ≠ Evidence.

---

## 18. Secrets/session model

Research SoR:
- raw credential plaintext içermez.

Persist:
- SecretRef,
- identity reference,
- session metadata safe fields,
- expiry,
- provenance.

Secret material:
- isolated SecretPort/store,
- short-lived runtime material.

Restart:
- session reuse güvenli ve şifreli ise restore,
- değilse authentication capability ile yeniden kur.

"Session recovery" identity binding'i korur.

---

## 19. Deployment

### Staging — WSL2

WSL2:
- development/qualification/staging,
- real 24/7 production claim değildir.

Layout:

```text
/opt/zest/
  releases/
  current -> releases/<version>

/etc/zest/
  config
  environment/secrets refs

/var/lib/zest/
  artifacts
  runtime state
  backups metadata

/var/log/zest/
```

PostgreSQL:
- WSL ext4,
- `/mnt/c` üzerinde değil.

systemd:
- PostgreSQL native unit,
- `zestd.service`,
- dashboard ayrı process gerekiyorsa disposable service olabilir.

### Production — native Ubuntu

Aynı layout.  
Windows-specific path/code yok.

Before 24/7:
- machine reboot,
- daemon kill,
- PG restart,
- worker kill,
- network interruption,
- disk pressure

qualification.

---

## 20. Remote access

Single operator için öncelik:
- private VPN / Tailscale / WireGuard sınıfı erişim.

Public domain şart değil.

Public exposure seçilirse:
- TLS reverse proxy,
- strong auth,
- secure session,
- CSRF/origin protection,
- command audit,
- internal ports private,
- PostgreSQL never public.

Discord:
- notification-only başlangıçta,
- START/STOP authority yok.

---

## 21. Backup / restore

PostgreSQL:
- scheduled full backup,
- uygun olduğunda WAL/PITR,
- backup local tek diskte bırakılmaz.

Artifacts:
- authoritative olup olmadıklarına göre sınıflandırılır,
- evidence-critical artifacts backup dahil.

Restore test:
- gerçek restore,
- stale lease temizleme/reconciliation,
- active run'lar kör resume edilmez,
- runtime instance identity yeniden oluşturulur.

Backup var demek restore kanıtı var demek değildir.

---

## 22. Release / upgrade / rollback

Run başlangıcında pin:
- engine/release version,
- config hash,
- contract fingerprints gerekiyorsa.

Upgrade:
- new release side-by-side,
- migration compatibility check,
- unsafe active run varsa pause/drain,
- symlink switch.

Rollback:
- schema backward compatibility doğrulanır,
- in-flight side-effect varsa kör process replacement yapılmaz.

Config change:
- active run'ın authoritative snapshot'ını sessizce değiştirmez.

---

## 23. Runtime implementation gates

### RT-0 — Terminal correctness

PASS:
- all terminal transitions immutable,
- stop_reason protected,
- command race tests.

### RT-1 — Execution journal

PASS:
- durable before dispatch,
- request id uniqueness,
- UNKNOWN_OUTCOME semantics,
- operational failure epistemic regression.

### RT-2 — Fenced ownership

PASS:
- two supervisors race: one owner,
- stale epoch write rejected,
- lease loss stops new dispatch.

### RT-3 — Persistent daemon

PASS:
- dashboard/Cursor death no effect,
- daemon owns supervisors,
- SoR reload after restart.

### RT-4 — Preflight

PASS:
- exact readiness report,
- START fail-fast,
- stale model/worker health not trusted.

### RT-5 — Recovery

PASS:
- pre-dispatch safe retry only after reauth,
- dispatched unknown → reconciliation,
- machine restart.

### RT-6 — Operator API/Console

PASS:
- client cannot override authority,
- duplicate START idempotency,
- pause/resume/cancel persisted,
- API no secrets.

### RT-7 — systemd staging

PASS:
- boot auto-start,
- PG→daemon dependency,
- 72h unattended staging,
- backup restore.

### RT-8 — native Ubuntu 24/7

PASS:
- deployment parity,
- long-run evidence as master maturity requires,
- upgrade/rollback,
- recovery drills.

### RT-9 — remote operator

PASS:
- private network or full hardened public boundary,
- audit,
- no internal port exposure,
- notification bridge isolated.

---

## 24. Qualification matrix

### Lifecycle
- READY→START→RUNNING
- RUNNING→PAUSE
- PAUSED→RESUME
- active→CANCEL
- terminal + CANCEL = no mutation
- duplicate START = no duplicate supervisor

### Ownership
- daemon A alive, daemon B acquire attempt
- lease expiry + takeover
- daemon A stalls then wakes
- stale epoch state write
- stale epoch Worker dispatch

### Recovery
- crash before dispatch
- crash exactly after dispatch
- crash after result persisted
- Worker timeout
- PG disconnect
- full machine reboot
- backup restore

### Model
- auth failure
- rate limit
- malformed output
- all runtimes unavailable

### Scope
- scope expires
- new redirect
- unknown host
- policy changes after V3 approval

### Epistemic
- Worker operational failure
- UNKNOWN_OUTCOME
- late WorkerResult
- no false Evidence/Finding

---

## 25. Hard-fail conditions

RT gate PASS değildir eğer:

- dashboard run owner ise,
- process memory authoritative run state ise,
- plain lease var fakat fencing yoksa,
- old owner stale write yapabiliyorsa,
- unknown outcome auto-retry ediliyorsa,
- Preflight "READY" stale cached auth'a dayanıyorsa,
- daemon research decision alıyorsa,
- daemon Core'u bypass edip Worker dispatch ediyorsa,
- terminal state mutate edilebiliyorsa,
- restart duplicate authoritative attempt üretiyorsa,
- backup restore test edilmemişken production-ready deniyorsa.

---

## 26. Son hedef

Operator:
- dashboard'u açar,
- programı seçer,
- preflight sonucunu görür,
- START der.

Bundan sonra:
- zestd run'ı sahiplenir,
- ARC araştırmayı yürütür,
- Core her concrete action'ı kontrol eder,
- Worker bounded şekilde çalışır,
- state PostgreSQL'da yaşar,
- model gerektiğinde çağrılır,
- browser kapansa bile run devam eder,
- daemon ölürse güvenli state'ten geri gelir,
- outcome bilinmiyorsa kendi kendine tahmin etmez,
- operator yalnız gerçekten karar gereken yerde devreye girer.

Bu runtime'ın amacı saldırı kapasitesini azaltmak değildir.

**Amaç: mevcut araştırma kapasitesini günlerce güvenilir, denetlenebilir ve tekrar üretilebilir biçimde sahada tutabilmektir.**
