# Zest — Ana Birleşik Yol Haritası

**Belge sınıfı:** Ana mimari ve uygulama yol haritası  
**Belge rolü:** `docs/plan/` altındaki en üst seviye plan  
**Tarih:** 2026-08-21  
**Durum:** PLAN — hiçbir madde tek başına PASS anlamına gelmez  
**Alt planlar:**
- `ZEST_HUNTER_RECONNECTION_PLAN.md`
- `ZEST_PERSISTENT_RUNTIME_PLAN.md`

---

## 0. Amaç

Zest'un hedefi basit bir tarayıcı üretmek değildir.

Hedef; yetkili bug bounty ve güvenlik araştırması kapsamında:

- hedef yüzeyini sürekli anlayan,
- uygulama ve iş akışı semantiğini modelleyen,
- bilinen ve registry-dışı hipotezler üreten,
- hipotezleri tipli ve tekrarlanabilir deneylere dönüştüren,
- her gerçek dünya aksiyonunu Core üzerinden yeniden yetkilendiren,
- Worker sonuçlarını doğrudan gerçek kabul etmeyen,
- kanıtı bağımsız doğrulama ve insan incelemesinden geçiren,
- günlerce kesintisiz çalışabilen,
- çökme/restart sonrasında yan etkiyi yanlışlıkla tekrarlamayan,
- zaman içinde kendi araştırma deneyiminden öğrenebilen

**otonom bir güvenlik araştırma sistemi** oluşturmaktır.

Bu plan kapasiteyi hafifletmez. Mevcut gelişmiş modüllerin gerçekten aynı avcı döngüsünde kullanılmasını ve bunun güvenilir bir 24/7 çalışma zamanı üzerinde koşmasını hedefler.

---

## 1. Değişmez mimari ilkeler

### 1.1 Otorite

- **Core** scope, authorization, policy, budget, side-effect ve approval otoritesidir.
- Model, Research, daemon, dashboard veya Worker Core'un yerine karar veremez.
- Her somut execution attempt, dispatch öncesinde güncel Core kararı alır.
- Önceden verilmiş insan onayı, güncel execution authorization yerine geçmez.
- Yeni/redirect edilen hedef veya asset scope açısından yeniden değerlendirilir.
- Belirsizlik yetki üretmez.

### 1.2 Araştırma

- **AutonomousResearchController (ARC)** tek araştırma-semantik koordinatörüdür.
- İkinci bir rakip `ResearchEngine` beyni oluşturulmayacaktır.
- Gerekirse ARC içindeki aşamalar küçük Application/Research servislerine ayrılır; ancak lifecycle ownership tek kalır.
- HunterFamily, CoverageDebt, exploratory reasoning, protocol specialist, mutation ve discovery ayrı araştırma döngüleri oluşturmaz; ARC'nin aynı lifecycle'ına beslenir.

### 1.3 Epistemik zincir

```text
Signal
  != WorkerResult
  != Observation
  != Assessment
  != Evidence
  != Candidate
  != Verification
  != FindingProposal
  != Finding
```

Canonical akış:

```text
ResearchOpportunity
→ Hypothesis
→ ExperimentPlan
→ Core Authorization
→ ExecutionAttempt
→ WorkerResult
→ Observation
→ HypothesisAssessment
→ Evidence Admission
→ Evidence
→ Candidate
→ Independent Verification
→ FindingProposal
→ Human Review
→ Core Approval
→ Finding
```

- Operational failure, hypothesis falsification değildir.
- `UNKNOWN_OUTCOME` başarısız deney değil, bilinmeyen sonuçtur.
- `INCONCLUSIVE` geçerli ve gerekli bir sonuçtur.
- Model iddiası tek başına Evidence değildir.
- WorkerResult tek başına Observation veya Evidence değildir.
- Finding her zaman insan incelemesinden geçer.

### 1.4 Kalıcılık

- PostgreSQL tek authoritative SoR'dur.
- Process memory yalnız cache veya çalışma tamponudur.
- Kritik lifecycle state, budget state, execution attempt, lease/fencing, approval ve epistemik kayıtlar restart sonrası geri yüklenebilir olmalıdır.
- Rebuildable projeksiyonlar authority haline gelmez.

### 1.5 Yürütme

- WorkerPort gerçek dünya yan etkisinin tek bounded execution sınırıdır.
- Worker kendini yetkilendiremez.
- Daemon Worker'a doğrudan araştırma kararı veremez.
- Model serbest-form execution authority değildir.
- Tipli capability + deterministik compiler + Core authorization hattı korunur.

---

## 2. Birleşik hedef mimari

```text
                         OPERATOR
                            │
                    Operator Console
                            │
                     Operator API
                            │
                       zestd
            ┌───────────────┼────────────────┐
            │               │                │
        Preflight       Run ownership    Health/Recovery
            │               │                │
            └───────────────┴────────────────┘
                            │
             AutonomousResearchController
                [TEK RESEARCH LIFECYCLE]
                            │
       ┌────────────────────┼────────────────────┐
       │                    │                    │
 Surface/Graph       Coverage/Hunter       Exploratory
       │                    │                    │
       └──────────→ ResearchOpportunity ←────────┘
                            │
                        Hypothesis
                            │
                  ExperimentCompiler
          ┌─────────────────┼─────────────────┐
          │                 │                 │
 Authorization       Mutation/Injection   State/Protocol
  Compiler               Compiler           Compiler
          └─────────────────┼─────────────────┘
                            │
                    typed ExperimentPlan
                            │
                           Core
                            │
                    ExecutionAttempt
                            │
                       WorkerPort
                            │
                      WorkerResult
                            │
                      Transition A
                            │
                       Observation
                            │
                       Assessment
                            │
                    PromotionPipeline
                            │
 Evidence → Candidate → Verification → FindingProposal
                            │
                      Human Review
                            │
                       Core Approval
                            │
                         Finding
                            │
                       PostgreSQL SoR
```

`zestd` sistemi hayatta tutar.
ARC araştırır.  
Core izin verir.  
Worker uygular.  
PromotionPipeline doğrulamayı ve bulguya terfiyi koordine eder.  
PostgreSQL gerçeği saklar.  
İnsan Finding'i son kez yargılar.

---

## 3. İki çalışma hattı

### Track A — Hunter Reconnection

Amaç: mevcut gelişmiş araştırma organlarını tek ARC lifecycle'ına bağlamak ve plan-artifact seviyesindeki yetenekleri gerçek, tipli, Core-gated execution kapasitesine dönüştürmek.

Alt plan: `ZEST_HUNTER_RECONNECTION_PLAN.md`

Ana sonuçları:
- tek `ResearchOpportunity` giriş dili,
- Hunter/Coverage ile eski ARC döngüsünün birleşmesi,
- per-family/per-capability ExperimentCompiler,
- MutationMatrix → typed ExperimentPlan köprüsü,
- V3 approved → compile → fresh Core auth → execution,
- protocol step-by-step Core re-entry,
- durable PromotionPipeline,
- run-scoped exploratory strategy,
- OAST evidence path,
- field-validation ölçümleri.

### Track B — Persistent Runtime

Amaç: araştırma döngüsünü dashboard/Cursor/terminal ömründen ayırıp güvenli, restart edilebilir, 24/7 bir çalışma zamanı oluşturmak.

Alt plan: `ZEST_PERSISTENT_RUNTIME_PLAN.md`

Ana sonuçları:
- terminal-state correctness,
- durable execution-attempt journal,
- lease + fencing epoch,
- crash/recovery classifier,
- Preflight,
- persistent `zestd`,
- Operator API,
- stateless dashboard,
- SSE telemetry,
- systemd deployment,
- backup/restore,
- WSL2 staging ve native Ubuntu 24/7 hedefi.

---

## 4. Uygulama sırası — authoritative sequence

### PHASE 0 — Gerçeği sabitle

Amaç: yeni mimariyi yanlış repo varsayımlarının üzerine kurmamak.

Yapılacaklar:
- master + aktif qualification branch farkını çıkar,
- mevcut migrations, lifecycle states, orchestration objects, execution attempt tipleri, HunterFamily/V3/Mutation/Protocol yollarını koddan yeniden doğrula,
- eski planlarda "yok" denilen ama artık eklenmiş modülleri işaretle,
- hiçbir gate durumunu dokümandan miras alma; kod + test + migration kanıtıyla doğrula,
- gate isim çakışmalarını açıkça ayır: eski altyapı `GATE xx`, saldırı dönemi `SD-Gx`, bu planın `MR-*` ve `RT-*` kapanışları.

Kapanış:
- hiçbir mimari karar varsayıma dayanmıyor,
- branch/worktree durumu kayıtlı,
- "planlandı" ile "çalışıyor" ayrılmış.

### PHASE 1 — State correctness + execution journal

- terminal state'leri immutable yap,
- `stop_reason` write-once davranışı,
- operator command idempotency/no-op semantiği,
- durable `ExecutionAttempt` lifecycle,
- unique `request_id`,
- `INTENT_COMMITTED`, `AUTHORIZED`, `DISPATCHED`, `RESULT_RECORDED`, `UNKNOWN_OUTCOME` gibi açık execution phases,
- unknown side-effect replay'i yasakla.

Kapanış:
- terminal run state değişmiyor,
- dispatch öncesi durable attempt var,
- duplicate authoritative execution yok,
- worker timeout hypothesis'i falsified yapmıyor.

### PHASE 2 — Tek research lifecycle'a yeniden bağlanma

- ARC'nin opportunity-selection aşamasını tek giriş kapısına dönüştür,
- `UnifiedOpportunitySource` veya eşdeğer interface,
- SurfaceDiscovery, CoverageDebt/HunterScore ve exploratory source aynı `ResearchOpportunity` tipini üretir,
- HunterFamily yolu ARC dışındaki paralel lifecycle olmaktan çıkar,
- mevcut generator/falsifier kaldırılmaz,
- duplicate/competing hypothesis admission kuralları tanımlanır.

Kapanış:
- HunterScore tarafından seçilen fırsat ARC içinde gerçek Hypothesis'e dönüşebiliyor,
- eski Path A regression testleri bozulmuyor,
- iki ayrı "next action" otoritesi kalmıyor.

### PHASE 3 — Deterministik ExperimentCompiler katmanı

- generic planner fallback seviyesine indirilir,
- family/capability bazlı compiler registry,
- ilk compiler aileleri: AuthorizationDifferentialCompiler, StateTransitionCompiler, MutationMatrixCompiler, ProtocolStepCompiler ve gereken browser/session compiler,
- model "neyi denemeli" ve matrix cell seçimi yapabilir,
- compiler concrete typed arguments üretir,
- capability contract fingerprint ve side-effect minimumları korunur.

Kapanış:
- known HunterFamily için serbest-form model Worker args yolu yok,
- compiler output schema-validated,
- risk understatement mümkün değil.

### PHASE 4 — Fenced ownership + persistent daemon iskeleti

- `runtime_instance`,
- `owner_runtime_instance_id`,
- monotonic `lease_epoch`,
- `heartbeat_at`, `lease_until`,
- CAS-based acquire/renew/release,
- her authoritative orchestration mutation'da epoch doğrulaması,
- lease kaybeden daemon yeni iş dispatch edemez,
- `LocalRunSupervisor` semantiği persistent supervisor'a taşınır,
- dashboard process runtime owner olmaktan çıkar.

Kapanış:
- iki daemon aynı run'ı alamıyor,
- stale epoch yazımı reddediliyor,
- dashboard kapatılınca aktif run devam ediyor,
- daemon restart sonrası SoR'dan devam ediyor.

### PHASE 5 — Preflight + recovery

Preflight:
- authorization active,
- scope compiles,
- target allowed,
- persisted authoritative config complete,
- PostgreSQL/schema current,
- required capabilities ready,
- Worker health,
- ModelPort auth + structured-output compatibility,
- budget remaining,
- rate-limit cooldown,
- no conflicting lease.

Recovery:
- no in-flight side effect → `SAFE_AUTOMATIC_RESUME`,
- intent committed, not dispatched → `SAFE_RETRY_AFTER_REAUTHORIZATION`,
- dispatched, result unknown → `RECONCILIATION_REQUIRED`,
- side-effect outcome unknown → `HUMAN_REQUIRED`,
- repeated unrecoverable infra failure → explicit operational terminal state.

Kapanış:
- START yalnız başarılı preflight sonrası,
- stale health START yetkisi üretmiyor,
- unknown outcome kör retry olmuyor,
- recovered intent Core reauthorization olmadan dispatch edilmiyor.

### PHASE 6 — V3 + protocol + mutation execution bridges

V3:
```text
PENDING
→ HUMAN_APPROVED_FOR_COMPILATION
→ COMPILED
→ CORE_AUTHORIZATION_REQUESTED
→ AUTHORIZED_FOR_THIS_ATTEMPT
→ DISPATCHED
→ RESULT_RECORDED
```

Kurallar:
- approval != authorization,
- her attempt fresh Core check,
- daemon queue item'i doğrudan Worker'a göndermez,
- ARC/Application use-case ilgili compiler'ı çağırır.

Protocol:
- her step ayrı ExperimentPlan,
- her step ayrı Core auth,
- redirect/new target/unexpected state → STOP + re-evaluate.

Mutation:
- MutationMatrix plan/artifact,
- selected cell → deterministic compiler,
- bounded concrete variant,
- budget/rate-limit counted per experiment.

### PHASE 7 — PromotionPipeline ve full finding closure

```text
Assessment
→ EvidenceProposal
→ EvidenceAdmission
→ Evidence
→ Candidate
→ IndependentVerification
→ FindingProposal
→ HumanReview
→ Core Approval
→ Finding
```

Kurallar:
- pipeline ARC tarafından tetiklenir fakat kendi durable transitions'ına sahiptir,
- ResearchRun tamamlandıktan sonra pending validation devam edebilir,
- Finding acceptance insan gate'ini kaldırmaz,
- impact yalnız proof-backed graph'tan gelir.

### PHASE 8 — Exploratory research'in gerçek execution yolu

- `TemporaryFamilyInstance` veya `ExploratoryStrategy`,
- run-scoped,
- permanent registry yazamaz,
- yeni Worker capability yaratamaz,
- side-effect ceiling yükseltemez,
- normal compiler/Core/evidence/verification/finding zincirini kullanır,
- permanent HunterFamily promotion yalnız human approval ile.

Exploratory inputs:
- graph anomaly,
- response-shape anomaly,
- temporal change,
- identity differential,
- unexpected workflow transition,
- OAST anomaly,
- target-model contradiction.

### PHASE 9 — OAST + session/browser + live model closure

OAST:
- callback token tek attempt'e bağlı,
- TTL/armed window,
- anti-spoofing,
- dedup,
- callback `UNTRUSTED_EXTERNAL` olarak başlar,
- doğrudan Evidence olmaz.

Session/browser:
- raw secret research SoR'a plaintext girmez,
- restart sonrası session yeniden kurulabilir,
- formal browser qualification ayrı gate'te kapanır.

Live model:
- GATE 04B ayrı kalır,
- karşılaştırmalı gerçek runtime kanıtı olmadan PASS yok,
- rate-limit/auth failure operational error olarak kalır.

### PHASE 10 — Operator API + Console + event stream

Operator API:
- program,
- run,
- preflight,
- pause/resume/cancel,
- approvals,
- V3 queue,
- candidates,
- findings,
- coverage,
- timeline,
- runtime health.

Console:
- stateless client,
- process ownership yok,
- authoritative config browser memory'de değil,
- SSE ile delta, REST ile full snapshot,
- secret göstermez.

### PHASE 11 — Staging, 24/7 ve deployment

Önce WSL2 staging:
- Linux-native layout,
- PostgreSQL WSL ext4 içinde,
- systemd,
- boot recovery,
- backup restore,
- 72 saat unattended qualification.

Sonra native Ubuntu:
- gerçek 24/7 hedef,
- aynı `/opt`, `/etc`, `/var/lib` layout,
- release pinning,
- config hash,
- upgrade/rollback,
- 30 gün unattended campaign.

Remote access:
- önce private network/VPN tercih edilir,
- public exposure gerekiyorsa ayrı hardening gate,
- Discord notification-only.

### PHASE 12 — Field validation ve öğrenme

Lab:
- vulnerable / secure / deceptive fixture setleri,
- ground-truth app campaign,
- false finding = 0,
- recall ölçülür.

Private realistic:
- unknown vulns,
- unique valid finding rate,
- cost/time-to-validation,
- registry-external success.

Authorized bug bounty:
- gerçek scope/policy,
- valid submission,
- reproducibility,
- duplicate ratio,
- operator intervention rate.

Maturity flag'leri yalnız kanıttan sonra ilerler.

---

## 5. Cross-track dependency map

```text
RT-0 terminal correctness ─────────────┐
RT-1 execution journal ────────────────┼──> RT-2 fencing/daemon
                                      │
MR-1 unified opportunity ─────┐        │
MR-2 compilers ───────────────┼──> MR-3 V3/mutation/protocol bridge
                              │
RT-2 fencing/daemon ──────────┼──────────────> safe live execution
RT-3 preflight/recovery ──────┘

MR-3 ──> MR-4 PromotionPipeline
MR-4 ──> MR-5 Exploratory
MR-2 ──> OAST/session/live-model qualification
RT operator stack + MR full lifecycle
        └──> STAGING
              └──> LAB VALIDATION
                    └──> AUTHORIZED FIELD VALIDATION
                          └──> PRODUCTION readiness evidence
```

---

## 6. Readiness kapıları

### INTEGRATION_READY
- tek ARC lifecycle,
- Hunter/Coverage path bağlı,
- production-quality ilk ExperimentCompilers,
- V3 consumer bridge,
- Mutation→ExperimentPlan bridge,
- PromotionPipeline,
- terminal states immutable,
- execution-attempt journal,
- fenced ownership tests green.

### STAGING_READY
- zestd persistent,
- Preflight,
- recovery classification,
- dashboard disposable client,
- API lifecycle controls,
- systemd,
- PostgreSQL backup,
- daemon and machine restart tests.

### RESEARCH_VALIDATION_READY
- OAST gereken sınıflar için hazır,
- authenticated session/browser qualification,
- exploratory run-scoped path,
- lab fixtures,
- full epistemic chain,
- live model usable,
- GATE 04B kendi bağımsız şartına göre tamamlanmışsa ilgili flag ilerler.

### 24_7_READY
- 72h unattended,
- no process-local authoritative state,
- disk/log/health observability,
- restore tested,
- rate-limit cooldown persisted,
- lease expiry/recovery stress tested.

### PRODUCTION_READY
Bu belge kendi başına flag set etmez.

Minimum:
- native Ubuntu,
- uzun süreli unattended evidence,
- upgrade/rollback,
- backup restore,
- run release/config pinning,
- authorized field campaign,
- no unresolved critical lifecycle correctness defect,
- formal maturity rulesin tamamı.

---

## 7. Test doktrini

Her değişiklik:
1. bounded,
2. rollbackable,
3. explicit acceptance criteria'lı,
4. eski regression suite'i koruyan,
5. PostgreSQL gereken yerde gerçek PostgreSQL kullanan,
6. browser gereken yerde gerçek browser qualification yapan,
7. missing dependency'yi PASS gibi göstermeyen,
8. false-positive discipline'i zayıflatmayan

bir change set olmalıdır.

Özellikle:
- terminal immutability,
- duplicate START,
- two-daemon race,
- stale fencing epoch,
- crash between intent and dispatch,
- crash after dispatch before result,
- model auth/rate-limit failure,
- worker timeout,
- DB outage,
- out-of-scope redirect,
- V3 approval then scope change,
- PromotionPipeline restart,
- OAST late callback,
- secret/session restart behavior,
- false finding = 0,
- independent reproduction.

---

## 8. Eski planlarla ilişki

`Zest_Saldiri_Donemi_Entegrasyon_Plani.md` vizyon ve capability kapsamı için korunur.

Bu yeni plan:
- saldırı kapasitesini kaldırmaz,
- SD-G1–G16'yı yeniden icat etmez,
- mevcut saldırı dönemi organlarının **execution/lifecycle bağlantılarını tamamlar**,
- production runtime track'i ile aynı sıraya yerleştirir.

`Zest_Operasyon_Modeli.md` son kullanıcı vizyonu olarak korunur:
> operatör programı tanımlar, sistem avlanır, operatör yargılar.

Yeni runtime planı bu vizyonun dashboard/Cursor/terminal ömründen bağımsız çalışmasını sağlar.

---

## 9. Cursor uygulama kuralı

Bu üç plan Cursor/Codex promptlarının kaynak dokümanıdır.

Her implementation prompt:
- yalnız tek phase/slice seçer,
- önce mevcut kodu doğrular,
- planı körlemesine uygulamaz,
- mevcut implementation varsa yeniden yazmaz,
- architectural invariants listesini prompt içine taşır,
- production file ownership'i tek implementerde tutar,
- testleri değişiklikten önce tanımlar,
- yeni migration gerekiyorsa additive/reversible yapar,
- gate'i yalnız kanıtla kapatır.

---

## 10. Son hedef

Zest tamamlandığında:
- tek research lifecycle ile düşünecek,
- birden fazla algı kaynağından fırsat toplayacak,
- bilinen HunterFamily'leri disiplinli şekilde kullanacak,
- registry dışı anomalilerden yeni hipotez üretecek,
- deterministik compiler'larla gerçek deney kuracak,
- Core sınırlarını hiçbir zaman aşmayacak,
- Worker'larla bounded execution yapacak,
- bütün sonuçları kanıt zincirinden geçirecek,
- günlerce bağımsız çalışacak,
- crash/restart sonrasında aynı işi yanlışlıkla tekrarlamayacak,
- operatöre yalnız gerçekten değerli karar noktalarını bırakacak,
- her araştırma yolculuğunu gelecekteki araştırma zekâsının hammaddesi olarak saklayacaktır.

Amaç "çok test atan sistem" değildir.

**Amaç: az yanlış, çok derin, yeniden üretilebilir, kanıtlanmış ve ekonomik olarak değerli bulgu üreten otonom araştırmacı.**
