# Zest — Saldırı Dönemi Ana Entegrasyon Planı

**Belge sınıfı:** Mimari ana plan (vagon-vagon entegrasyon haritası)
**Hazırlayan:** Proje Mimarı / Akıl Hocası
**Tarih:** 2026-08-18
**Temel alınan kod:** master @ GATE 22 PASS (implementation SHA `ba24935d84245216011dc062fa12fbcccbefc9b5`)
**Kapsam:** Mevcut sistemin tamamı (457 Python dosyası, 54.400 LOC kaynak + 37.569 LOC test, 912 birim + 285 e2e/entegrasyon/kontrat testi, 54 mimari karar, 18 Alembic migration'ı) ile yeni saldırı beyninin **eksiksiz, dikişsiz** birleştirilmesi.

---

## 0. Bu belgenin vaadi

Bu plan, mevcut sistemin **hiçbir vagonunu sökmeden**, her yeni modülün hangi mevcut kancaya bağlanacağını dosya ve satır seviyesinde gösterir. "Vagonların birbirine bağlanması" burada soyut bir benzetme değil: her bölümde **kanca noktası (mevcut dosya:satır) → takılacak vagon (yeni modül) → kenetlenme protokolü (kontrat/test)** üçlüsü verilir.

Tanı doğrulandı: sistem "mükemmel reflekslere sahip bir organizma" — sinir sistemi (Core otoritesi), bağışıklık sistemi (epistemik disiplin), kas iskeleti (Worker containment) eksiksiz. Eksik olan: **avlanma organları** (dış algı), **saldırı kasları** (payload/mutation, saldırı sınıfları) ve **avcı beyni** (sınıf-bağımsız hipotez muhakemesi). Bu üçü, mevcut omurgaya 11 dikiş noktasından bağlanacak.

---

## 1. Mevcut sistemin doğrulanmış tam envanteri

Aşağıdaki her satır bu oturumda koddan yeniden doğrulanmıştır.

### 1.1 Kontrol düzlemi (Core) — 950 LOC, eksiksiz, DOKUNULMAZ

| Modül | Dosya | İşlev | Durum |
|---|---|---|---|
| Scope derleyici | `core/scope_compiler.py` (164) | Exact-host kural derleme + aday değerlendirme, fail-closed | ✅ çalışıyor, ⚠️ wildcard reddediyor (Bkz. Envanter Z1) |
| Scope semantiği | `core/scope.py` (69) | ALLOW/DENY/REQUIRE_HUMAN_REVIEW, belirsizlikte insan incelemesi | ✅ doğru tohum |
| Yetki | `core/authorization.py` | AuthorizationSource ACTIVE/EXPIRED/REVOKED | ✅ |
| Bütçe | `core/budget.py` | IssuedBudget/BudgetUsage/check_budget | ✅ |
| Onay | `core/approval.py` | APPROVE/REJECT, actor doğrulaması | ✅ |
| Yürütme | `core/execution.py` (171) | SideEffect LEVEL_0–3, ExecutionDecision | ✅ |
| Yetenek | `core/capability.py` | WORKER executor zorunluluğu, integration allowlist | ✅ |
| Enum'lar | `core/enums.py` | 20+ ReasonCode, ActorType, ScopeDecision | ✅ |

**Hüküm:** Core, dünyada eşi az bulunur bir otorite zinciri. Saldırı döneminde **tek satırı gevşetilmeyecek**; sadece *genişletilecek* (yeni ReasonCode, yeni kural türleri).

### 1.2 Epistemik omurga (Research) — eksiksiz, DOKUNULMAZ

- Observation → Evidence → Candidate → Verification → FindingProposal → Human Review → Finding zinciri: `research/evidence.py`, `candidate.py`, `verification.py`, `finding_proposal.py` + `application/finalize_finding.py`
- Migration zinciri: `a10_001_evidence_admission` → `a11_001_candidate_verification` → `a12_001_finding_acceptance`
- FindingProposal content-fingerprint + approval-subject bağlama (`finding_proposal.py:114-133`) — kurcalanmaya karşı mühürlü
- Epistemic durumlar: OBSERVED / DERIVED / INFERRED / HYPOTHESIZED / UNTRUSTED_EXTERNAL
- `FORBIDDEN_DISCOVERY_KEYS` (`discovery/types.py:10`) — discovery fact'lerine severity/vulnerability yazma yasağı
- Invariant mining (`invariant.py`), Chain Engine (`chain.py`, Karar 042), Differential Reasoning (`differential.py`, Karar 040), Temporal Intelligence (`temporal.py`, Karar 044) — hepsi mevcut ve test altında

**Hüküm:** Bu omurga, rakiplerin (XBOW dahil) çoğunda olmayan kanıt disiplinidir. Yeni saldırı sınıfları bu omurgaya **beslenecek**, omurgayı atlamayacak.

### 1.3 Capability Registry + Deney Derleyici — G18/G19, çalışıyor, GENİŞLETİLECEK

- `tools/registry.py`: JSON kontrat yükleme, fingerprint, `minimum_side_effect_level`, `RISK_UNDERSTATEMENT` fail-closed reddi
- `research/compiler.py` `compile_experiment_intent()`: bilinmeyen capability/action reddi, JSON Schema argüman doğrulama, risk aralığı zorlaması; efektif yan etki **registry'den** türetilir (modelden değil)
- Mevcut 6 capability kontratı (`resources/contracts/v1/capabilities/`): `diagnostic.echo`, `http.authorization.differential`, `http.state_transition`, `http.transaction` (read/mutate), `http.authentication` (login), `browser.page` (observe/navigate/interact)
- **Tamamında** `"loopback_only": true` + `requirements: ["loopback"]` (Envanter A4)

### 1.4 Worker yürütme düzlemi — containment mükemmel, ağ kilitli

- Çift kopya: `src/zest/worker_runtime/python/` ≡ `workers/python/zest_worker/` (byte-identical; tekil canonical + shim yapısı integrations'da olduğu gibi burada da korunmalı)
- Browser containment: Linux cgroup v2 (memory.max, pids.max) + Windows Job Object — çekirdek zorlamalı, G21'de Kali'de kanıtlı
- `http_transaction.py`: `ALLOWED_HOSTS = frozenset({"127.0.0.1"})` (satır 18), redirect STOP, "Not a scanner. Not a crawler."
- `http_authorization.py`: `ALLOWED_HOSTS` (satır 16), GET-only, MAX_REQUESTS=4, 4096 bayt yanıt tavanı
- `browser_page.py`: MAX_ATTEMPTED_REQUESTS=16
- Envelope deseni (`application/authorized_network_envelope.py`): Core'dan türeyen, Worker'ın ancak **sıkılaştırabileceği** değişmez zarf — **doğru dikiş zaten var**, yanlış olan zarfa hardcoded `loopback_only=True` basılması (`http_transaction_authorization.py:107`)

### 1.5 Keşif düzlemi (G22) — güçlü L2 organı, L1 yok

- 2.541 LOC, 13 kalıcı tablo (`a22_001_discovery_surface`), TX-A/TX-B iki-transaction dikişi
- AttackSurfaceGraph: rebuildable projection, `grants_scope() → False` (doğru), 10 node türü, 4 inference türü (ROUTE_TEMPLATE, OBJECT_TYPE, OBJECT_INSTANCE, SAME_AS), 7 goal türü
- Kimlik-farkındalı keşif (ANONYMOUS + identity variants), `DiscoveryBounds` (11 sert limit, konfigürasyondan)
- Strateji: `surface.discovery.v1` — **uygulama-içi** yüzey; DNS/CT log/subdomain/sertifika/servis/JS bundle/API spec keşfi **yok**

### 1.6 Model/AI düzlemi — altyapı zengin, canlı doğrulama yok

- `research/model_port.py`: ModelPort protokolü, ModelCallRequest/Result/Telemetry, hata sınıfları — `ContentPolicyBlockedError` dahil (sistem model reddini zaten öngörüyor)
- Adaptörler (`integrations/models/`): OpenAI, Anthropic, Gemini, `cli_session` (Codex CLI, 728 LOC), `local_runtime`, `external_agent`
- `budget_enforced_model.py`: her model çağrısı bütçe zorlamalı
- `routing.py` (546 LOC): runtime seçim politikası
- **GATE 04B = PENDING**: LIVE_MODEL_VALIDATED=False — ≥2 BENCHMARK_COMPATIBLE canlı runtime ile karşılaştırmalı koşu şartı henüz sağlanmadı
- Strix: `integrations/strix/adapter.py` — iskelet, uyutulmuş, `ALLOWED_STRIX_CAPABILITIES` kısıtlı

### 1.7 Benchmark/doğrulama altyapısı — sınıfının en iyisi, saldırı sınıfları için genişletilecek

- Güvenlik senaryoları: S01–S10 (BOLA), W01–W12 (workflow), R01–R11B+ (seçim), + araştırma benchmark senaryoları (prompt-injection tuzakları, hallucination trap, hypothesis poisoning dahil)
- `benchmark/holdout.py`: mühürlü holdout; `benchmark/leakage.py`: model-görünür blob sızıntı denetimi; metamorfik testler
- 8 e2e lab'ı (`tests/e2e/lab/`): http_idor_lab, http_workflow_lab, surface_discovery_lab, browser_page_lab… hepsi loopback fixture
- False-positive disiplini: vulnerable/secure/deceptive üçlüleri, `false_finding = 0`, INCONCLUSIVE meşru sonuç

### 1.8 Operatör arayüzü — yok denecek

- `interface/cli.py` (165 satır): sadece `status` + `export-source`
- `application/operator_status.py`: okuma modeli var, koşu başlatma/durdurma/onay komutları yok

### 1.9 Bulgu zinciri — kabul var, paketleme yok

- FindingProposal → Human Review → Finding mevcut ve mühürlü
- **Yok:** severity motoru, platform severity eşlemesi, PoC paketleme, rapor üretimi, duplicate tespiti, submission formatı

---

## 2. Onbir entegrasyon dikişi (kanca → vagon → kenetlenme)

Her dikiş: mevcut kancanın koordinatı, takılacak yeni vagon, ve ikisini birbirine kilitleyen kontrat/test protokolü.

### Dikiş 1 — Scope Dikişi (G1'in kalbi)

- **Kanca:** `core/scope_compiler.py` (wildcard reddi satır 47-48, 88-89; exact-host `_origin_matches` satır 150) + `core/enums.py` ScopeDecision
- **Vagon:** Scope Compiler v2 + ProgramResearchContext
  - Wildcard/subdomain semantiği: `*.example.com` kuralları (DERIVED host eşleşmesi; wildcard **kural** olabilir, wildcard **envelope** asla — envelope'deki `*` yasağı korunur)
  - Üçüncü sınıflandırma: IN_SCOPE / **UNKNOWN** / OUT_OF_SCOPE — UNKNOWN'a probing yok, census serbest
  - Program politikası domain data'sı: rate limit, yasaklı aksiyonlar (ör. "SMS yok" → ActionPolicy: DENY), bounty tablosu, raporlama formatı
  - Yeni ReasonCode'lar: `SCOPE_UNKNOWN_CLASSIFICATION`, `PROGRAM_POLICY_DENIED`, `RATE_LIMIT_BUDGET_DERIVED`
- **Kenetlenme:** Alembic `a23_001_program_scope` (program, scope_rule v2, program_policy, rate_limit_profile tabloları); 30+ kurnaz scope senaryosu test matrisi (alt-alan benzeri host, port oyunu, userinfo, path traversal, IDN/punycode, trailing-dot, casing); fail-closed regresyonu: mevcut 912 testin tamamı yeşil kalmalı

### Dikiş 2 — Envelope Dikişi (loopback kilidinin doğru açılımı)

- **Kanca:** `application/http_transaction_authorization.py:107` (`loopback_only=True` **hardcoded**) + `authorized_network_envelope.py`
- **Vagon:** `loopback_only` artık sabit değil; CompiledScope + ProgramPolicy'den **türetilir**. Loopback fixture'larında `true`, scope'taki gerçek hedeflerde `false`. Envelope'un değişmezliği ve "Worker ancak sıkılaştırır" kuralı aynen korunur
- **Kenetlenme:** Envelope türetim testleri (scope dışı host → envelope yok; UNKNOWN host → envelope yok; IN_SCOPE → envelope `loopback_only=false`); worker tarafında envelope-dışı host denemesi → EXECUTION_FAILED

### Dikiş 3 — Worker Ağ Dikişi

- **Kanca:** `workers/python/zest_worker/http_transaction.py:18`, `http_authorization.py:16` (`ALLOWED_HOSTS`), `browser_page.py` envelope denetimi
- **Vagon:** `ALLOWED_HOSTS` sabiti kalkar; denetim **envelope'a karşı** yapılır (host+port+scheme+path-prefix). Worker kendi kendine asla yetki üretmez — envelope yoksa çalışmaz. Bu, güvenlik modelini **gevşetmeden** açar: otorite hâlâ Core'da, Worker hâlâ fail-closed
- **Kenetlenme:** Worker red testleri (sahte envelope, genişletilmiş envelope, imzasız envelope → red); mevcut loopback lab'ları aynen geçmeli (regresyon)

### Dikiş 4 — Capability Kontrat Dikişi

- **Kanca:** `resources/contracts/v1/capabilities/*.json` + `tools/registry.py:19` (`SUPPORTED_REQUIREMENTS = {"loopback"}`) + `research/compiler.py:107`
- **Vagon:** Yeni requirement türleri: `scope_derived`, `oast_callback`, `program_policy`. Yeni capability aileleri (her biri JSON kontrat + fingerprint + SE sınıfı):
  - `sensor.dns`, `sensor.ctlog`, `sensor.http_head`, `sensor.archive`, `sensor.cert`, `sensor.port_banner` (L1 census, SE0, pasif/yarı-pasif)
  - `http.probe` (parametre madenciliği, method matrisi, SE0/SE1)
  - `mutation.fuzz` (bounded payload varyasyonu, SE1-SE3 aralıklı)
  - `oast.interaction` (callback dinleme, SE0; out-of-band kanıt)
  - `browser.session` (kimlikli SPA oturumu, SE1)
- **Kenetlenme:** Kontrat şema testleri; registry eski kontratları aynen kabul etmeli (geriye uyum); `RISK_UNDERSTATEMENT` reddi her yeni kontratta yeniden kanıtlanır

### Dikiş 5 — Avcı Beyni Dikişi (hardcoded-family duvarının yıkılması)

- **Kanca:** `research/selection.py:125` (`family_for_claim`: yalnız 2 sabit aile) ve `selection.py:~767` (`propose_experiment_options`: OBJECT_AUTHORIZATION + WORKFLOW_STATE_TRANSITION dışına çıkamıyor)
- **Vagon:** **HunterFamily Registry** — hipotez sınıfları kod değil, **veri** olur: `invariant_templates`, `prerequisites`, `validation_requirements`, `side_effect_class`, `proof_standard`, `false_positive_traps`. `HypothesisFamily.UNKNOWN` dışı her aile registry'den yüklenir; yeni sınıf eklemek = registry satırı + kontrat + evaluator + fixture üçlüsü, **kod cerrahisi değil**
- **Kenetlenme:** Alembic `a25_001_hunter_registry`; her aile için vulnerable/secure/deceptive fixture üçlüsü; `false_finding = 0` kapısı; registry olmadan hiçbir hunter pack planlanamaz (fail-closed)

### Dikiş 6 — Evaluator/Doğrulayıcı Dikişi

- **Kanca:** `research/evaluators/` (mevcut 5 evaluator) + `research/verification.py`
- **Vagon:** Sınıf başına bir evaluator + üç kademeli bağımsız doğrulama:
  - **V1 deterministik yeniden-üretim** (taze oturum, aynı girdi → aynı kanıt)
  - **V2 negatif kontrol** (secure fixture'da aynı deney → bulgu yok)
  - **V3 bağımsız model gözden geçirmesi** (farklı runtime, evidence'a kör başlangıç)
- **Kenetlenme:** V1/V2 olmadan FindingProposal admission reddi; V3 şartı SE2+ bulgularda; deceptive fixture `false_finding=0` kapısı

### Dikiş 7 — Algı/Sensör Dikişi (L1 Census)

- **Kanca:** `application/ports.py` (Protocol deseni) + `application/discovery/runner.py` + `research/discovery/types.py` (node/goal türleri)
- **Vagon:** Sensor/Acquisition Plane: hiçbir sensör domain truth yazamaz; hepsi `SensorObservation` üretir (UNTRUSTED_EXTERNAL → admission'dan geçerek fact olur). Sensör kataloğu: DNS/CT log/tarihsel URL (Wayback/Common Crawl)/sertifika/port-banner/teknoloji parmak izi + **APK analizi, reverse DMARC/CSP keşfi, 401-derinlik keşfi** (üçü de raporların dışında benim eklediğim şeritler)
- **Kenetlenme:** AttackSurfaceGraph v2 node aileleri (DOMAIN, HOSTNAME, CERT, SERVICE, TECH, JS_BUNDLE, API_SPEC…); `grants_scope()` **False kalır**; sensör çıktısının SoR'a yazımı yalnız TX-A/TX-B dikişinden

### Dikiş 8 — Mutation + OAST Dikişi (saldırı kasları)

- **Kanca:** Dikiş 4'ün yeni kontratları + `worker_runtime` executor iskeleti
- **Vagon:** 
  - **Mutation motoru:** payload sözlükleri sınıf başına registry'de; varyasyon bounded, sayaçlı, bütçe-tüketimli; her varyant bir Experiment (asılsız bombardıman yok — her atış hipoteze bağlı)
  - **OAST:** kendi kontrolümüzdeki callback uç noktası; kör SSRF/XSS/SQLi/XXE kanıtının tek meşru yolu; correlation-id ile deney↔callback bağlama
- **Kenetlenme:** OAST callback'i olmayan kör-sınıf bulgusu FindingProposal'a giremez; mutation bütçesi Core `budget.py`'den düşer; fixture lab'ları: kör-SSRF vulnerable/secure/deceptive üçlüsü

### Dikiş 9 — Bulgu→Para Dikişi

- **Kanca:** `finding_proposal.py` + `application/finalize_finding.py`
- **Vagon:** Severity motoru (internal P0–P3: etki × kanıt gücü × kapsam) + ProgramSeverityMapper (Bugcrowd VRT / HackerOne severity) + **PoC paketleyici** (yeniden-üretim adımları, request/response dökümü, zincir haritası, etki kanıtı — "etki kanıtı, yıkım değil") + duplicate önleme (platform başına bilinen-bulgu özetleri, SoR'a ham secret yazmadan)
- **Kenetlenme:** Paketlenmemiş bulgu "teslim edilebilir" sayılmaz; PoC adımları taze ortamda birebir koşulmalı

### Dikiş 10 — Operatör Dikişi

- **Kanca:** `interface/cli.py` (165 satır, 2 komut) + `application/operator_status.py`
- **Vagon:** Operatör konsolu: `program add/show`, `run start/stop/status`, `review queue/approve/reject`, `finding list/export`, `coverage show` (Coverage Debt matrisi: Asset × Identity × Family). Onay kuyruğu Core `approval.py`'ye bağlanır — CLI asla Core'u atlayamaz
- **Kenetlenme:** CLI komutlarının her biri mevcut application use-case'lerinden geçer; `status` çıktısına yeni gate bayrakları eklenir

### Dikiş 11 — Benchmark Dikişi (her gate'in hakemi)

- **Kanca:** `security_benchmark/scenarios.py` + `benchmark/holdout.py` + `tests/e2e/lab/`
- **Vagon:** Sınıf başına senaryo aileleri (XSS: S-X01…, SSRF: S-S01…, SQLi: S-Q01…) + gerçek-uygulama recall seti (Juice Shop, crAPI, DVWA — lab standardında, loopback fixture olarak) + metamorfik varyantlar + mühürlü holdout'a **sızıntı denetimiyle** ekleme
- **Kenetlenme:** Her gate kapanışı = ilgili senaryo ailesi `false_finding=0` + recall eşiği + 912+285 mevcut testin tamamı yeşil + Kali'de gerçek PostgreSQL ile koşu. Fabricated PASS yasak: PostgreSQL yoksa SKIP, asla PASS

---

## 3. Saldırı Dönemi GATE serisi (G1–G15) — her gate'in vagon bağlantı şeması

Önceki gateler (01–22) **altyapı dönemi** idi; bu seri **saldırı dönemi**. Numaralandırma yeniden 1'den başlar — kullanıcının kararı. Her gate: amaç / bağlandığı dikişler / kapanış standardı.

| Gate | Ad | Dikişler | Kapanış standardı (hepsi zorunlu) |
|---|---|---|---|
| **G1** | Scope Compiler v2 + ProgramResearchContext | 1, 2 | Wildcard/UNKNOWN/policy senaryo matrisi 30+; fail-closed regresyon; envelope türetim testleri; **scope kaydına TTL: süresi dolan kayıt otomatik REQUIRE_HUMAN_REVIEW'a düşer; platform API'lerinden (HackerOne/Bugcrowd scope endpoint'leri) periyodik senkron testi** |
| **G2** | Sensor/Acquisition Plane (L1 Census) | 7, 4 | Sensör kontratları; census fixture'ları; UNKNOWN'a probing yok kanıtı; SoR'a sensör yazamaz kanıtı |
| **G3** | AttackSurfaceGraph v2 | 7, 1 | Dış node aileleri; IN/UNKNOWN/OUT sınıflandırma; `grants_scope()=False` regresyonu |
| **G4** | HunterFamily Registry + HunterScore Scheduler | 5 | 2 mevcut ailenin registry'ye göçü (kod davranışı birebir aynı); 1 yeni aile pilot ekleme; seçim benchmark R-seti yeşil |
| **G5** | Authorization Hunter (tam matris) | 5, 6, 9 | Identity×tenant×object×operation×property×state matrisi; S-seti + yeni senaryolar; IDOR lab genişlemesi |
| **G6** | Mutation Motoru + OAST Altyapısı | 8, 4 | Bounded varyasyon kanıtı; bütçe tüketim kanıtı; callback↔deney korelasyonu; kör-SSRF üçlüsü; **`rate_limit_profile`'a karşı zorlama testi: limit aşımı denemesinde motor kendi kendine kesilir (programdan yasaklanma riski bu testle kapatılır)** |
| **G7** | Workflow Hunter + Auth/Session Lane | 5, 6 | W-seti + race (SE3, onaylı) fixture'ları; state-graph invariant çıkarımı |
| **G8** | API/Object Hunter | 5, 6 | BOLA/BOPLA/BFLA; documented-vs-observed envanter farkı; v1/v2/orphan uç fark analizi |
| **G9** | İlk Enjeksiyon Seti: SSRF(OAST) + Secret Exposure + XSS + Cloud Misconfig/Subdomain Takeover | 6, 8, 9 | Sınıf başına fixture üçlüsü; OAST kanıtlı kör bulgular; `false_finding=0` |
| **G10** | Bağımsız Validator V1/V2/V3 + Severity Motoru + Devre Kesici | 6, 9 | V1/V2 olmadan admission reddi; internal P0–P3 + VRT/H1 eşleme tablosu; severity regresyon seti; **saha devre kesicisi: bir ailenin gerçek dünyada INCONCLUSIVE/REJECTED oranı sapıtınca insan onayı olmadan otomatik throttle (kapatma yok), learning cycle'a bağlı telemetri testi** |
| **G11** | ImpactGraph / Attack-Path + Zincirleme | 5, 6 | Proof-backed edge zorunluluğu (`proof_id` olmayan edge yok); 2 zincir senaryosu (ör. SSRF→metadata→kredi→iç uç) lab'da; **adım-bazlı onay kanıtı: çok adımlı zincirde her adım kendi SE seviyesine göre ayrı Core kararı alır — tek "zinciri başlat" onayı tünel açamaz** |
| **G12** | Geniş Enjeksiyon Dalgası: SQLi/SSTI/LFI/RFI/Mass Assignment/JWT/CORS/GraphQL + DOM Taint + **AI/LLM Hedef Ailesi** | 6, 8 | Sınıf başına üçlü + metamorfik varyantlar; DOM kaynak→akıtma izleme lab'ı; **AI/LLM hedef şeridi kendi HunterFamily'si olarak: prompt injection, context leakage, tool-abuse — ModelPort altyapısı zaten var, fixture üçlüsü + recall kanıtıyla** |
| **G13** | Protocol/Parser Specialist | 4, 6 | Request smuggling/cache/desync — yalnız yüzey kanıtı destekliyorsa; SE3 onay akışı kanıtı |
| **G14** | PoC/Rapor Üretimi + Duplicate Ekonomisi + n-day Lane | 9 | Paketlenmiş rapor fixture'ı; duplicate önleme doğruluğu — **iç fingerprint + dış sinyal: platform disclosed-reports/bilinen-kategori taraması (API varsa API, yoksa program sayfası)**; sürüm→CVE eşleme (ana beyin değil, şerit) |
| **G15** | Continuous Change Hunter + Coverage Debt Canlı + Recall Konsolidasyonu | 11, 10 | ChangeEvent≠vulnerability kanıtı; kapsam borcu metriği canlı; tüm ailelerin recall skorlarının tek raporda konsolidasyonu (aile-bazlı recall kanıtları zaten her gate'te verilmiş olur — burada birleşik tablo) |
| **G16** | Exploratory Hypothesis Generator (keşifsel hipotez üreteci) | 5, 6 | Anomali substratı (G3 graf + temporal) üzerinden registry'de OLMAYAN aday hipotez üretimi; **model registry'ye asla doğrudan yazamaz — yeni aile taslağı insan onayına düşer**; üretilen her hipotez HYPOTHESIZED'dan başlar, aynı V1/V2/V3 + `false_finding=0` kapısından geçer; kanıtlanmamış yaratıcı zincir ImpactGraph'a edge olamaz; pilot fixture: kasıtlı "sıfırıncı-gün tarzı" lab zafiyeti — registry'de ailesi olmadan bulunmalı |

**Her gate'in değişmezleri:** vulnerable/secure/deceptive üçlüsü · `false_finding = 0` · taze-oturum yeniden-üretim · fabricated PASS yasak (PostgreSQL yoksa SKIP) · önceki tüm gatelerin testleri yeşil · Kali'de gerçek PostgreSQL + (gerekiyorsa) gerçek Chromium · maturity.py güncellemesi aynı commit'te · **her yeni HunterFamily kendi gate'inde recall kanıtı verir (Juice Shop/crAPI/DVWA alt kümesi — hiçbir aile G15'i beklemez)**.

---

## 4. Paralellik ve bağımlılık grafiği

```
G1 ──┬──> G2 ──> G3 ──┐
     │                ├──> G4 ──> G5 ──┬──> G7 ──> G8 ──┐
     └──> (Worker ağ  │      │         │                │
          dikişi 2-3) │      └──> G6 ──┴────────────────┼──> G9 ──> G10 ──> G11
                      │                                 │              │
                      └─────────────────────────────────┴──> G12 ──> G13
                                                                     G14 (G10'dan sonra)
                                                                     G15 (G3+G10'dan sonra)
```

- G1 her şeyin ön şartı (scope olmadan hiçbir hedef meşru değil)
- G6 (mutation+OAST), G9'dan **önce** — Claude raporunun aksine benim düzeltmem: injection şeridi mutation motoru olmadan açılamaz
- G11 (zincirleme), en az 4-6 doğrulanabilir sınıftan (G9+G12) sonra anlamlı
- G14 ekonomi şeridi, G10 severity motorundan sonra
- G16 (keşifsel hipotez üreteci), G3 + G4 + canlı gözlem akışı gerektirir; en erken anlamlı pilot G5 sonrası, tam gate sonda
- **Operasyon Modeli (kullanım kılavuzu) ayrı belgedir:** `Zest_Operasyon_Modeli.md` — bu planın inşaatı bittiğinde sistemin günlük kullanımını tanımlar

---

## 5. Korunan kutsallar (pazarlık edilemez)

1. **Core otoritesi:** scope/bütçe/onay/yan-etki kararları yalnız Core'da. Hiçbir hunter, sensör, model veya worker bunu atlayamaz
2. **Epistemik disiplin:** hiçbir model/worker Finding üretemez; zincir: Observation→…→Human Review→Finding
3. **Fail-closed:** belirsizlikte DENY/REQUIRE_HUMAN_REVIEW; `grants_scope()` sonsuza dek False
4. **Kanıt standardı:** "etki kanıtı, yıkım değil"; kör sınıflarda OAST korelasyonu şart
5. **False-positive disiplini:** her sınıf için üçlü fixture, `false_finding=0`
6. **SoR'da ham secret yok:** yalnız referans
7. **Fabricated PASS yasak:** altyapı yoksa SKIP
8. **Hipokrat yemini:** tüm kapasite yalnız yetkili scope içinde, private kullanım

---

## 6. "Saldırı yapan AI değil" cümlesinin doğru okunması

Claude raporunun kapanış cümlesi doğru ama **gözetimli kabul** edilmiştir: epistemik çerçeve, *saldırının yapılıp yapılmayacağını* değil, *bulgunun nasıl kanıtlanacağını* yönetir. Bu sistem RCE zincirleri dahil her saldırıyı **first-class bounded experiment** olarak koşar. Sulandırma yok: her yeni sınıf, tam kanıt standardıyla, tam etki hedefiyle, registry'ye tam hakimiyetle gelir. Fren yalnız scope'tur — yemin gereği — onun dışında fren yoktur.
