# Zest — Hunter Reconnection & Research Capability Plan

**Belge sınıfı:** Araştırma mimarisi / saldırı kapasitesi entegrasyon planı  
**Ana belge:** `ZEST_MASTER_PLAN.md`
**Tarih:** 2026-08-21  
**Durum:** PLAN

---

## 0. Problem tanımı

Zest'ta güçlü araştırma organları bulunmaktadır veya saldırı dönemi boyunca inşa edilmiştir:

- SurfaceDiscovery / AttackSurfaceGraph,
- HunterFamily Registry,
- HunterScore / Coverage Debt,
- Mutation Engine / MutationMatrix,
- protocol specialists,
- exploratory hypothesis generator,
- ImpactGraph,
- independent verification,
- Evidence/Candidate/Finding zinciri.

Ana risk, bu organların ayrı ayrı gelişmiş olmasına rağmen aynı authoritative autonomous lifecycle içinde gerçek execution'a kadar birleşmemesidir.

Bu planın hedefi yeni bir framework yazmak değildir.

**Hedef: mevcut organları tek AutonomousResearchController lifecycle'ına bağlamak.**

---

## 1. Tek beyin kararı

Yeni bir rakip `ResearchEngine` lifecycle authority oluşturulmayacaktır.

Canonical owner:

```text
AutonomousResearchController
```

ARC:
- opportunity ingestion,
- hypothesis admission coordination,
- experiment selection,
- execution use-case coordination,
- assessment sonrası promotion trigger

aşamalarını koordine eder.

ARC'nin kendisi bütün domain mantığını içermez. God-class olmaması için alt servisler kullanılır:

```text
AutonomousResearchController
 ├─ OpportunitySource
 ├─ HypothesisAdmissionService
 ├─ ExperimentCompilerRegistry
 ├─ ExecutionCoordinator
 ├─ AssessmentCoordinator
 └─ PromotionPipeline trigger
```

Ancak "sonraki araştırma adımı nedir?" sorusunun birden fazla sahibi olmaz.

---

## 2. Canonical araştırma lifecycle

```text
Surface / Sensor / Graph
CoverageDebt / HunterScore
Exploratory anomaly sources
        │
        ▼
ResearchOpportunity
        │
        ▼
Hypothesis
        │
        ▼
ExperimentIntent
        │
        ▼
ExperimentCompiler
        │
        ▼
typed ExperimentPlan
        │
        ▼
Core Authorization
        │
        ▼
ExecutionAttempt
        │
        ▼
WorkerPort
        │
        ▼
WorkerResult
        │
        ▼
Transition A
        │
        ▼
Observation
        │
        ▼
HypothesisAssessment
        │
        ▼
PromotionPipeline
        │
        ├─ Evidence Admission
        ├─ Candidate
        ├─ Independent Verification
        ├─ FindingProposal
        ├─ Human Review
        └─ Core Approval
                │
                ▼
              Finding
```

Coverage ve target memory her döngü sonunda güncellenir.

---

## 3. ResearchOpportunity — ortak para birimi

Path A ve Hunter/Coverage yolu `Hypothesis` aşamasından önce birleşir.

`ResearchOpportunity` minimum semantik olarak şunları taşımalıdır:

- opportunity id,
- program/run,
- source type,
- target/surface references,
- relevant identity/state context,
- supporting observation/fact references,
- candidate HunterFamily ref (optional),
- novelty marker,
- coverage-debt rationale,
- expected information gain,
- estimated experiment cost class,
- required capability classes,
- provenance,
- created_at.

Taşımaması gereken authority alanları:
- authorization result,
- arbitrary severity,
- Finding,
- execution permission,
- model confidence as authority.

Sources:
- discovery,
- coverage debt,
- HunterScore,
- temporal change,
- target model contradiction,
- exploratory anomaly,
- OAST-derived anomaly,
- operator-supplied research question.

---

## 4. UnifiedOpportunitySource

Tek interface:

```text
OpportunitySource.propose(context) -> list[ResearchOpportunity]
```

Composition:
- `SurfaceOpportunitySource`
- `HunterCoverageOpportunitySource`
- `ExploratoryOpportunitySource`
- `TemporalOpportunitySource`
- ileride `SourceAssistedOpportunitySource`

ARC kaynakları çağırır, normalize eder, deduplicate eder ve bounded seçim yapar.

Kurallar:
- kaynaklardan hiçbiri execution authority değildir,
- kaynaklardan hiçbiri Core authorization üretmez,
- HunterScore scheduler sadece priority önerir,
- full cross-product önceden materialize edilmez,
- opportunity selection her zaman run bounds içinde kalır.

---

## 5. HunterFamily'nin yeni yeri

HunterFamily bir lifecycle değildir. Research protocol datasıdır.

Bir family şunları tanımlar:

- prerequisites,
- relevant surface/object types,
- relevant identity/state dimensions,
- invariant templates,
- experiment strategy class,
- compiler id,
- validation requirements,
- proof standard,
- side-effect floor/ceiling,
- false-positive traps,
- evaluator id,
- coverage dimensions.

Known-family flow:

```text
ResearchOpportunity
→ HunterFamily match
→ family-specific Hypothesis
→ family compiler
→ ExperimentPlan
```

Registry:
- model tarafından kalıcı değiştirilemez,
- permanent change human-reviewed,
- code branching yerine data-driven kalır,
- unsupported compiler/evaluator ile active family yüklenemez.

---

## 6. ExperimentCompiler katmanı

### 6.1 Temel kural

AI:
- hedefe dair reasoning yapabilir,
- hangi hypothesis'i izlemek gerektiğini seçebilir,
- mutation matrix cell seçebilir,
- discriminating experiment önerisi üretebilir.

AI arbitrary Worker execution authority olamaz.

Concrete execution construction deterministik compiler'a aittir.

### 6.2 Compiler contract

```text
compile(
  hypothesis,
  research_context,
  family_definition,
  selected_strategy,
  authoritative_refs
) -> ExperimentPlan
```

Compiler:
- pure/deterministic olabildiğince,
- side-effect class'i family/capability registry'den alır,
- secrets'i değer olarak değil reference olarak taşır,
- target/scope authority üretmez,
- schema-validated typed plan üretir,
- normalized fingerprint üretir.

### 6.3 İlk compiler seti

#### AuthorizationDifferentialCompiler
Kullanır:
- identities,
- object ownership,
- operation,
- expected invariant,
- control/request pair.

#### StateTransitionCompiler
Kullanır:
- starting workflow state,
- action,
- expected/forbidden transition,
- identity,
- state readback strategy.

#### MutationMatrixCompiler
Kullanır:
- selected matrix cell,
- input vector,
- mutation family,
- encoding strategy,
- bounded variants,
- control request.

#### ProtocolStepCompiler
Kullanır:
- approved ProtocolPlan,
- one concrete step,
- transport/protocol state,
- previous step observation.

#### BrowserStateCompiler
Yalnız browser/session semantiği gereken yerde:
- page state,
- identity/session ref,
- bounded action,
- expected observation.

### 6.4 Generic planner

Silinmez.

Rolü:
- registry-external exploratory hypothesis,
- henüz compiler'ı olmayan research-only plan,
- düşük side-effect ceiling altında güvenli exploratory planning.

Known family için generic planner active execution yolu olmayacaktır.

---

## 7. Mutation Engine → real execution bridge

MutationMatrix executable payload değildir.

Canonical bridge:

```text
MutationMatrix
→ MatrixCellCandidate
→ AI/heuristic selection
→ MutationMatrixCompiler
→ ConcreteVariantSet
→ typed ExperimentPlan
→ Core
→ WorkerPort
```

Kurallar:
- her varyant bounded,
- max variants explicit,
- request budget tüketir,
- rate-limit profile'a tabidir,
- control sample olmadan semantic evaluator sonucu kabul edilmez,
- response anomaly doğrudan vulnerability değildir.

Coverage:
- "matrix hücresi test edildi" bilgisi actual completed attempt + usable observation üzerinden türetilir,
- planlandı diye covered sayılmaz.

---

## 8. V3 queue → execution bridge

V3 bir ayrı motor değildir; yüksek-risk/aktif experiment admission kapısıdır.

State örneği:

```text
PENDING
→ APPROVED_FOR_COMPILATION
→ COMPILED
→ AUTHORIZATION_REQUESTED
→ AUTHORIZED
→ ATTEMPT_COMMITTED
→ DISPATCHED
→ RESULT_RECORDED
```

Alternatif terminal/ara durumlar:
- REJECTED,
- EXPIRED,
- CORE_DENIED,
- RECONCILING,
- CANCELLED_BEFORE_DISPATCH.

Invariants:
- human approval execution token değildir,
- approval sonrası scope/budget/policy tekrar Core tarafından kontrol edilir,
- item id → plan id → attempt id provenance zinciri korunur,
- APPROVED ama hiç dispatch edilmemiş item recovery'de recompile + reauthorize edilebilir,
- DISPATCHED ama sonuç bilinmiyorsa blind retry yok.

Consumer:
ARC'nin yönettiği Application use-case.

Daemon yalnız "work exists" sinyali verebilir; queue semantics yorumlamaz.

---

## 9. Protocol specialist integration

Protocol specialists:
- surface evidence olmadan aktive olmaz,
- ProtocolPlan üretir,
- planın tamamı bir defalık execution izni değildir.

Flow:

```text
ProtocolPlan
→ human admission when policy requires
→ step 1 compiler
→ Core
→ Worker
→ Observation
→ precondition check
→ step 2 compiler
→ Core
→ Worker
...
```

Her adım:
- yeni target/redirect kontrolü,
- budget,
- rate-limit,
- side effect,
- prior state expectations

bakımından yeniden değerlendirilir.

Unexpected observation sequence'i durdurur ve re-plan yapar.

---

## 10. PromotionPipeline

ARC Assessment'ta durmamalı; fakat Evidence→Finding mantığı ARC içine gömülmemeli.

Ayrı durable Application service:

```text
PromotionPipeline
```

Trigger:
- new eligible HypothesisAssessment,
- pending Candidate verification,
- verification result,
- human review result.

Flow:

```text
SUPPORTED Assessment
→ EvidenceProposal
→ Evidence Admission
→ Evidence
→ Candidate
→ Independent Verification
→ FindingProposal
→ HumanReview
→ Core Approval
→ Finding
```

`UNSUPPORTED`:
- hypothesis path kapanabilir,
- negative knowledge üretir,
- Evidence yok.

`INCONCLUSIVE`:
- Finding yok,
- yeniden deney için future opportunity üretilebilir,
- operational reason ve semantic uncertainty ayrılır.

### 10.1 Independent verification

Verification producer, original generator ile aynı tek otorite değildir.

Minimum:
- fresh reproduction,
- control/negative comparison,
- provenance validation,
- family-specific proof standard.

Model review varsa:
- farklı runtime tercih edilir,
- source evidence'e blind start uygulanabilir,
- model review tek başına validation değildir.

### 10.2 ImpactGraph

Finding impact:
- free-text model şişirmesi değildir,
- proof-backed edge'lerden oluşur,
- edge leaf'leri Evidence/verified facts'e bağlanır,
- unproven chain Finding severity yükseltmez.

---

## 11. Exploratory research — registry dışı avcılık

Amaç:
Sistemin Nuclei-benzeri dev bir family scanner'a dönüşmesini önlemek.

`ExploratoryStrategy` özellikleri:
- run-scoped,
- ephemeral,
- source anomaly references zorunlu,
- permanent registry write yok,
- capability creation yok,
- Core bypass yok,
- side-effect ceiling raise yok,
- same evidence/verification gates,
- full provenance.

Flow:

```text
anomaly cluster
→ exploratory ResearchOpportunity
→ exploratory Hypothesis
→ discriminating experiment strategy
→ compatible deterministic compiler
→ Core
→ Worker
→ normal epistemic chain
```

Permanent family promotion:

```text
Repeated validated exploratory pattern
→ CandidateResearchPattern / HunterFamilyDraft
→ Human review
→ benchmark fixtures
→ registry admission
```

Yani sistem yeni şey deneyebilir; ancak kendi kalıcı yetki/araştırma protokolünü tek başına değiştiremez.

---

## 12. Coverage Debt v2

Başlangıç:
`Asset × Identity × HunterFamily`

Genişletilebilecek boyutlar:
- workflow state,
- auth state,
- identity pair,
- input-vector class,
- method,
- content type,
- protocol,
- target change epoch.

Ancak global Cartesian matrix üretme.

Her HunterFamily ilgili dimension setini declare eder.

Örnek:
- authorization family: asset + identity pair + object class + operation + state,
- injection family: input vector + content type + parser context,
- desync family: service + HTTP version + intermediary topology + method,
- workflow family: identity + state + transition.

Coverage cell lazy hesaplanır.

Debt score:
- unexploredness,
- information gain,
- historical yield,
- freshness,
- target importance,
- experiment cost,
- rate-limit cost,
- duplicate likelihood

gibi sinyalleri birleştirebilir.

Score authority değildir. Scheduler suggestion'dır.

---

## 13. OAST research semantics

OAST callback:
- dış dünyadan gelen untrusted signal'dır,
- tek başına Evidence değildir.

Binding:
- token,
- program,
- run,
- experiment/attempt,
- issued_at,
- expires_at,
- expected channel,
- one-time/limited reuse policy.

Callback:

```text
UNTRUSTED_EXTERNAL
→ correlation validation
→ Observation
→ family evaluator
→ EvidenceProposal
```

Late callback:
- loglanır,
- run terminal ise state değiştirmez,
- policy izin verirse future research opportunity olabilir.

Blind family proof standard OAST gerektiriyorsa OAST olmadan FindingProposal yaratılmaz.

---

## 14. Business/application semantics

Injection breadth sistemin tek zekâ ölçütü değildir.

Dedicated semantic context aşağıdaki bilgileri birleştirmelidir:
- actor/identity,
- object ownership,
- workflow state,
- operation,
- property,
- tenant,
- trust boundary,
- application invariant,
- business effect.

ResearchOpportunity ve Hypothesis bu semantik bağlamı referanslayabilmelidir.

Öncelik:
- yalnız "parametre var mı" değil,
- "bu parametre hangi iş kuralını etkiliyor" sorusu.

---

## 15. Discriminating experiment intelligence

Bir experiment yalnız "payload denemesi" değildir.

İyi experiment:
- competing hypotheses arasında ayrım yapar,
- control içerir,
- mümkünse tek belirsizliği izole eder,
- expected observation önceden yazar,
- failure mode ile hypothesis falsification'ı ayırır,
- bilgi kazancı yüksek ve maliyeti bounded'dır.

ARC'nin experiment selection politikası:
- exploitability skoru yerine information gain + coverage + cost + proof value dengesi kurar.

---

## 16. Research Trace Corpus için hazırlanma

Her trajectory saklanmalıdır:

- context,
- opportunity,
- selected hypothesis,
- rejected alternatives,
- experiment intent,
- why selected,
- execution outcome,
- observation,
- assessment,
- validation,
- human review,
- duplicate/triage outcome,
- failure reason.

Özellikle "neden başarısız oldu?" saklanır.

Bu veri ileride Adaptive Research Intelligence için temel olacaktır.

AI learning:
- research preference öğrenebilir,
- authority öğrenemez.

---

## 17. Attack Period kapsamıyla bağ

Eski saldırı dönemi planındaki aşağıdaki organlar korunur:
- Scope Compiler v2,
- Sensor/Acquisition Plane,
- AttackSurfaceGraph,
- HunterFamily Registry,
- Authorization/Workflow/API hunters,
- Mutation + OAST,
- injection breadth,
- ImpactGraph,
- independent validator,
- protocol specialists,
- report/duplicate economics,
- continuous change,
- exploratory hypothesis generator.

Bu plan bunları tekrar sıfırdan yapmayı emretmez.

Repo audit'i her organ için şu sınıflandırmayı yapmalıdır:
- EXISTING_AND_CONNECTED,
- EXISTING_BUT_DISCONNECTED,
- PLANNING_ONLY,
- PARTIAL_EXECUTION,
- FORMALLY_QUALIFIED,
- MISSING.

Yalnız eksik bridge uygulanır.

---

## 18. Implementation slices

### MR-0 — Architecture reconnect audit

PASS:
- Path A/B gerçek call graph ile gösterilmiş,
- current V3/mutation/protocol/promotion bağlantıları doğrulanmış,
- yeniden yazılacak değil yalnız bağlanacak noktalar listelenmiş.

### MR-1 — Unified Opportunity

PASS:
- Hunter/Coverage opportunity ARC tarafından consume ediliyor,
- old ARC opportunity flow regression green,
- single next-action owner.

### MR-2 — Compiler Registry

PASS:
- ilk minimum compiler seti,
- known family generic planner'ı bypass ediyor,
- schema/fingerprint/risk tests,
- deterministic fixtures.

### MR-3 — Mutation execution bridge

PASS:
- selected matrix cell → actual typed plan,
- bounded requests,
- control/evaluator integration,
- coverage actual completion'a bağlı.

### MR-4 — V3 consumer + protocol step bridge

PASS:
- approved item compile oluyor,
- fresh Core authorization,
- one attempt only,
- crash states correct,
- protocol step-wise reauth.

### MR-5 — PromotionPipeline

PASS:
- Assessment→Evidence→Candidate→Verification→FindingProposal,
- restart-safe,
- human gate retained,
- false promotion tests.

### MR-6 — Exploratory execution

PASS:
- registry-external lab case,
- no registry mutation,
- normal Core/Worker/evidence chain,
- negative/deceptive fixtures.

### MR-7 — OAST/session/browser integration

PASS:
- callback correlation,
- anti-spoof/expiry/dedup,
- authenticated research restart behavior,
- browser formal qualification gereken gates.

### MR-8 — Field validation

PASS criteria doküman tarafından değil benchmark/field campaign tarafından belirlenir.

Ölç:
- surface recall,
- vuln recall,
- false finding,
- reproducibility,
- time-to-validation,
- cost/valid finding,
- coverage closure,
- registry-external hypothesis yield.

---

## 19. Hard-fail koşulları

Aşağıdakilerden biri varsa ilgili MR gate PASS değildir:

- ikinci research lifecycle authority,
- Hunter/V3 yolu ARC dışında Worker dispatch ediyor,
- model direct Worker payload authority,
- approval Core authorization yerine kullanılıyor,
- planned matrix cell covered sayılıyor,
- WorkerResult direct Evidence,
- operational failure falsified hypothesis,
- UNKNOWN_OUTCOME auto retry,
- unverified Candidate Finding oluyor,
- permanent HunterFamily model tarafından yazılıyor,
- false_finding > 0 kabul ediliyor,
- missing PostgreSQL/browser/model prerequisite PASS gibi gösteriliyor.

---

## 20. Son hedef

Bu plan tamamlandığında Zest:

- discovery ve coverage'dan aynı opportunity dilinde düşünecek,
- known family disiplinini exploratory yaratıcılıkla aynı lifecycle'da birleştirecek,
- generic planner'a bağımlı kalmadan tipli experiment kuracak,
- mutation ve protocol planlarını gerçek execution'a bağlayacak,
- V3 queue'yu gerçek ama fresh-authorized deneylere dönüştürecek,
- Assessment'tan Finding'e kadar durable promotion yapacak,
- registry dışı pattern'leri güvenli biçimde araştırabilecek,
- sonuçları Research Trace Corpus'a aktaracak,
- hiçbir aşamada Core ve epistemik sınırları zayıflatmayacaktır.

**Amaç daha çok saldırı sınıfı listelemek değil; mevcut ve gelecekteki bütün araştırma organlarını tek avcı zihninde gerçekten kullanılabilir hale getirmektir.**
