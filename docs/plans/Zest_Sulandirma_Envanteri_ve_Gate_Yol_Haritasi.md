# Zest — Sulandırma Envanteri ve Söküm/Yükseltme Rehberi

**Belge sınıfı:** Teşhis + cerrahi müdahale rehberi
**Tarih:** 2026-08-18
**Doğrulama:** Her madde bu oturumda master @ GATE 22 kodundan dosya:satır kanıtıyla yeniden doğrulanmıştır.
**Amaç:** ChatGPT döneminin mimariye yerleştirdiği tüm zayıflatılmış düşünceleri bulmak, teşhis etmek ve her birini **güvenlik modelini koruyarak** zirveye taşımanın yolunu göstermek.

**Reconcile durumu (2026-08-20):** Saldırı dönemi sulandırma cerrahisi
SD-G1–SD-G16 hattında tamamlandı ve SD-G16 seal commit'i
`113b99eb28dbf318dfeba67ee089aaa24885de31` olarak pushlandı. Son full QA:
`1542 passed, 9 skipped, 53 subtests passed`.

**Hüküm:** Bu envanterdeki ana söküm/yükseltme işleri `TAMAMLANDI`.
Eski teşhis metinleri korunur; aşağıdaki `Durum` notları bugünkü gerçeği
gösterir.

**Ayrı yeni-faz / formal doğrulama bayrakları:** `LIVE_MODEL_VALIDATED=false`,
`GATE_04B_STATUS=PENDING`, `GATE_21_STATUS=PENDING`,
`SECURITY_RESEARCH_VALIDATED=false`, `PRODUCTION_READY=false` ve
`SUBSCRIPTION_OAUTH_STATUS=NOT_IMPLEMENTED` hâlâ doğrudur. Bunlar sulandırma
cerrahisinin eksik işi değil; canlı model kalibrasyonu, formal Chromium/cgroup
seal, saha-validasyon ve production-readiness fazına taşınmış bağımsız
formalitelerdir.

---

## 0. Teşhis felsefesi: "korunacak öz" / "sökülecek zayıflık" ayrımı

ChatGPT'nin yaptığı şey çoğu yerde **doğru mühendisliği yanlış yere kilitlemekti**. Fail-closed davranışın kendisi kusur değil — kusur, fail-closed'un *hedefin kendisine* değil *yetkisizliğe* uygulanması gerekirken her şeye uygulanması. Bu yüzden her maddede üç bölüm var:

- **Teşhis:** zayıflatma nerede, ne yapıyor
- **Korunacak öz:** bu tasarımın hangi güvenlik değeri kesinlikle kalacak
- **Söküm/yükseltme:** zayıflığın nasıl kaldırılacağı ve hangi gate'te

Madde numaralandırması kategorilidir: **A** = ağ kilitleri, **B** = scope eksikleri, **C** = beyin kilitleri, **D** = kapasite kısıtları, **E** = doktrin kirleri, **F** = model/AI eksikleri, **G** = operatör yokluğu, **H** = kayıt eksikleri, **I** = algı körlüğü, **J** = ekonomi boşluğu.

---

## A. AĞ KİLİTLERİ — "loopback hapishanesi"

### A1. Envelope'a hardcoded loopback damgası — TEK KİLİT NOKTASI

- **Teşhis:** `src/zest/application/http_transaction_authorization.py:107` — `loopback_only=True` **sabit kodlanmış**. Scope ne derse desin, Core'un ürettiği yetki zarfı her zaman "yalnız loopback" mührü taşıyor. Sistemin gerçek hedefe çıkışını kapatan **tek satır** budur.
- **Korunacak öz:** Envelope deseninin kendisi — Core'dan türeyen, değişmez, Worker'ın ancak sıkılaştırabileceği zarf. Bu, dünyada az bulunur bir yetki mimarisi.
- **Söküm/yükseltme:** `loopback_only` sabit olmaktan çıkar; CompiledScope + ProgramPolicy'den **türetilir**. Loopback fixture → `true`; IN_SCOPE gerçek hedef → `false`; UNKNOWN/OUT_OF_SCOPE → envelope hiç üretilmez. **Gate G1.**

### A2. Worker'larda sabit ALLOWED_HOSTS

- **Teşhis:** `workers/python/zest_worker/http_transaction.py:18` ve `http_authorization.py:16` — `ALLOWED_HOSTS = frozenset({"127.0.0.1"})`. Worker, envelope ne derse desin 127.0.0.1 dışına çıkamaz. (`src/zest/worker_runtime/python/` kopyaları byte-identical — iki kopya senkron tutulmalı.)
- **Korunacak öz:** Worker'ın kendi kendine yetki üretememesi; envelope'suz çalışmaması.
- **Söküm/yükseltme:** Sabit liste kalkar; denetim **envelope'a karşı** yapılır (host+port+scheme+path-prefix). Worker fail-closed kalır: envelope yoksa/uyuşmazsa EXECUTION_FAILED. Sahte/genişletilmiş/imzasız envelope red testleri. **Gate G1.**

### A3. Capability kontratlarında loopback şartı

- **Teşhis:** 6 kontratın 10 action'ının tamamında `"loopback_only": true` + `requirements: ["loopback"]` (`resources/contracts/v1/capabilities/*.json`). `tools/registry.py:19` — `SUPPORTED_REQUIREMENTS = frozenset({"loopback"})` — registry başka requirement **tanıyamaz**. `research/compiler.py:107` aynı sabitle doğruluyor.
- **Korunacak öz:** Requirement kavramının kendisi (kontratın çalışma ön şartını beyan etmesi) ve registry'nin bilinmeyen requirement'ı reddetmesi.
- **Söküm/yükseltme:** Requirement seti genişler: `scope_derived`, `oast_callback`, `program_policy`. Mevcut kontratlar geriye-uyumlu kalır; yeni kontratlar yeni requirement'ları beyan eder. **Gate G1 (registry genişlemesi), G2/G6 (yeni kontratlar).**

### A4. "Not a scanner. Not a crawler." doktrini

- **Teşhis:** Worker dosyalarının docstring'leri (`http_transaction.py` satır 1-3, `browser_page.py` satır 1, `http_authorization.py` satır 3) — bu cümleler kod değil **doktrin**: sistemin kimliğine "sen tarayıcı değilsin" diye işlenmiş. Kimi Code'un sınavında da görüldüğü gibi ekip bunu içselleştirmiş.
- **Korunacak öz:** "Rastgele internet taraması yapan kör araç olmama" ilkesi — doğru. Her istek hipoteze bağlı, scope-otoriteli, bütçeli olmalı.
- **Söküm/yükseltme:** Doktrin şuna evrilir: **"Bounded scanner. Scoped crawler. Her isteğin sahibi var."** Docstring'ler gate geçişlerinde güncellenir. **G1'den itibaren, her gate'te.**

---

## B. SCOPE DERLEYİCİ EKSİKLERİ — gerçek programları temsil edememe

### B1. Wildcard reddi

- **Teşhis:** `core/scope_compiler.py:47-48` ve `:88-89` — `"wildcard scope is not allowed"`. Gerçek bug bounty programları `*.example.com` yazar. Sistem bugün **hiçbir gerçek programı temsil edemez.**
- **Korunacak öz:** `ScopeRuleDefinition.__post_init__`'teki wildcard reddi *girdi doğrulaması* olarak — yani operatörün yanlışlıkla `*` yazması hâlâ hata olmalı; wildcard yalnız bilinçli kural türüyle.
- **Söküm/yükseltme:** Yeni kural türü: `host_pattern: "*.example.com"` — DERIVED subdomain eşleşmesi. Envelope'daki wildcard yasağı (`authorized_network_envelope.py:33`) **korunur**: derlenen envelope her zaman somut host taşır. **Gate G1.**

### B2. Exact-host tek eşleşme, UNKNOWN sınıfı yok

- **Teşhis:** `_origin_matches` (scope_compiler.py:150) — birebir host eşleşmesi. Sonuç üç değerli: ALLOW / DENY / REQUIRE_HUMAN_REVIEW. Ama bug bounty gerçeği dört değerlidir: IN_SCOPE / UNKNOWN / OUT_OF_SCOPE / EXPLICITLY_EXCLUDED. UNKNOWN'a probing yasak ama census (pasif gözlem) serbest olmalı — bugün UNKNOWN diye bir kavram yok.
- **Korunacak öz:** `SCOPE_NOT_EXPLICITLY_ALLOWED → DENY` fail-closed'u; belirsizlikte REQUIRE_HUMAN_REVIEW.
- **Söküm/yükseltme:** ScopeClassification eklentisi + `SCOPE_UNKNOWN_CLASSIFICATION` ReasonCode'u. UNKNOWN: pasif sensör gözlemi serbest, aktif probing DENY. **Gate G1.**

### B3. Program politikası diye bir kavram yok

- **Teşhis:** Rate limit, yasaklı aksiyon ("SMS gönderme yok", "otomatik hesap oluşturma yok"), bounty tablosu, raporlama formatı, ödül kuralları — bunların hiçbiri sistemde veri olarak yok. Scope yalnızca URL kuralları.
- **Korunacak öz:** Core'un "tek otorite" olması — politika da Core verisi olacak, prompt değil.
- **Söküm/yükseltme:** ProgramResearchContext: program + scope_rule v2 + program_policy + rate_limit_profile + bounty_table tabloları (Alembic `a23_001_program_scope`). "SMS yok" → `ActionPolicy: DENY`; rate limit → Core `budget.py`'ye türetilmiş bütçe. **Gate G1.**

---

## C. BEYİN KİLİTLERİ — hardcoded-family duvarı

### C1. `family_for_claim` iki aileye kilitli

- **Teşhis:** `research/selection.py:125-131` — claim metni yalnızca `HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM` ve `HTTP_STATE_TRANSITION_CLAIM` tanıyor; gerisi `HypothesisFamily.UNKNOWN`. Sistem **tanımadığı her saldırı sınıfına kör.**
- **Korunacak öz:** UNKNOWN'un meşru bir durum olması (bilinmeyene aile uydurmama dürüstlüğü).
- **Söküm/yükseltme:** Aileler koddan veriye taşınır — HunterFamily Registry (invariant_templates, prerequisites, validation_requirements, side_effect_class, proof_standard, false_positive_traps). Yeni sınıf = registry satırı + kontrat + evaluator + fixture üçlüsü; kod cerrahisi değil. **Gate G4.**

### C2. `propose_experiment_options` aynı duvara çarpıyor

- **Teşhis:** `research/selection.py:~767` — deney seçenekleri yalnız OBJECT_AUTHORIZATION ve WORKFLOW_STATE_TRANSITION aileleri için üretiliyor. Yeni sınıf eklense bile seçici ona deney öneremez.
- **Korunacak öz:** Origin-binding kontrolleri (`origin_binds_object_context` vb.) — hipotez-deney bağının sıkılığı.
- **Söküm/yükseltme:** Seçim motoru registry-driven olur; her aile kendi option-generator şablonunu beyan eder. HunterScore = kanıt-değeri × yeni-bilgi × başarı-olasılığı × maliyet × politika-uyumu. **Gate G4.**

### C3. Evaluator seti 5 taneyle sabit

- **Teşhis:** `research/evaluators/` — diagnostic_echo, authorization_differential, http_authentication, http_transaction, state_transition. Başka değerlendirici yok.
- **Söküm/yükseltme:** Her HunterFamily kendi evaluator'unu getirir (V1 deterministik yeniden-üretim → V2 negatif kontrol → V3 bağımsız model gözden geçirmesi). V1/V2 olmadan FindingProposal admission reddi. **Gate G4 (iskelet) → her hunter gate'inde bir set.**

### C4. Payload/mutation motoru — TAMAMEN YOK

- **Teşhis:** Kod tabanında fuzzing, payload sözlüğü, varyasyon üreteci, mutation — hiçbiri yok (tam taramada sıfır sonuç). Sistem "neye bakacağını" biliyor ama "ne göndereceğini" bilmiyor. Leaderboard avcısının ana silahı sistemde mevcut değil.
- **Söküm/yükseltme:** Mutation motoru: sınıf başına registry'de payload sözlükleri; her varyant ayrı Experiment (asılsız bombardıman yok — her atış hipoteze bağlı, bütçe-tüketimli, sayaçlı). **Gate G6 — Claude raporunun önerdiğinden ÖNE çekildi; injection şeridi bu motor olmadan açılamaz.**

### C5. OAST — TAMAMEN YOK

- **Teşhis:** Kör sınıfların (blind SSRF, blind XSS, blind SQLi, XXE) kanıtı için out-of-band callback altyapısı yok. Bu sınıflar bugün **kavramsal olarak imkânsız.**
- **Söküm/yükseltme:** Kendi kontrolümüzde callback uç noktası; correlation-id ile deney↔callback bağlama; OAST korelasyonu olmayan kör-sınıf bulgusu FindingProposal'a giremez. **Gate G6.**

### C6. Zincirleme motoru invariant seviyesinde, saldırı seviyesinde değil

- **Teşhis:** `research/chain.py` (Karar 042) invariant chain'leri biliyor; ama "SSRF → cloud metadata → kredi → iç uç → RCE" tarzı **saldırı yolu** grafiği yok.
- **Söküm/yükseltme:** ImpactGraph: her edge `proof_id` taşır; ham hipotez zincire giremez. En az 4-6 doğrulanabilir sınıf sonrası. **Gate G11.**

---

## D. KAPASİTE KISITLARI — lab'dan çıkamayan kaslar

| # | Kısıt | Konum | Etki | Yükseltme |
|---|---|---|---|---|
| D1 | `max_response_bytes` tavanı 4096 | `http_transaction.json` (read: max 4096) + worker `DEFAULT_MAX_RESPONSE_BYTES = 4096` | Gerçek JS bundle'lar, API yanıtları, HTML sayfaları **kesiliyor** — algı körlüğü | Program başına yapılandırılabilir tavan (ör. 1 MB), bütçeden düşülür. **G1/G4** |
| D2 | `timeout_ms` tavanı 2000 | `http_transaction.json` | Gerçek internet gecikmesinde neredeyse her istek TIMED_OUT | Yapılandırılabilir (ör. 10 sn), retry politikası zaten var. **G1** |
| D3 | `max_requests: 1` (http.transaction), `MAX_REQUESTS = 4` (authz diff) | kontrat + `http_authorization.py:19` | Diferansiyel analiz ve çok-adımlı probing imkânsız | Deney başına bounded N (hipotez gereksiniminden türetilir), Core bütçesinden düşer. **G4/G5** |
| D4 | Header tavanları: 8 header, 64/128 karakter | `http_transaction.py:48-50` | Gerçek auth header'ları, cookie'ler, JWT'ler taşınamıyor | Yapılandırılabilir tavanlar; şema kontratta güncellenir. **G4** |
| D5 | Path ≤ 256, query ≤ 16 parametre × 128 karakter | `http_transaction.json` şeması | Derin API path'leri, gerçek query string'ler reddediliyor | Tavanlar yükseltilir; fail-closed doğrulama korunur. **G4** |
| D6 | GET-only (authz differential) | `http_authorization.py` | POST-tabanlı BOLA/BOPLA görülemez | Method matrisi (G5 Authorization Hunter) |
| D7 | `MAX_ATTEMPTED_REQUESTS = 16` (browser) | `browser_page.py:42` | SPA derin keşfi 16 istekte boğuluyor | DiscoveryBounds'a taşınır, program başına ayarlanır. **G7** |
| D8 | Redirect STOP | tüm HTTP worker'lar | OAuth akışları, SSO, multi-hop API'ler izlenemiyor | `redirect: REAUTHORIZE` modu — her hop Core'dan yeniden yetki ister (G19'da tohumu var: "redirect reauthorization"). **G5/G7** |

**Ortak ilke:** Bu tavanların hiçbiri "güvenlik" değil **lab sabitidir**. Güvenlik, scope+bütçe+envelope üçlüsünden gelir; bayt sayısından değil. Tavanlar yapılandırılabilir olur, bütçe her zaman Core'dan düşer.

---

## E. DOKTRİN KİRLERİ — kimliğe işlemiş korkular

| # | Kir | Konum | Doğru halef |
|---|---|---|---|
| E1 | "Not a scanner. Not a crawler." | Worker docstring'leri | "Bounded scanner. Scoped crawler. Her isteğin sahibi var." |
| E2 | "Autonomous != unbounded"ın "autonomous ≈ hiç" diye okunması | `autonomous_research_controller.py` başlığı; G22'nin "does not claim autonomous vulnerability discovery" reddiyeleri | Otonomi, otorite zinciri içinde tam hız: Core izin verdiği sürece durmaksızın avlanma |
| E3 | Her gate'in "does not prove..." litanyosu | `maturity.py` docstring, OPERATIONS.md | Dürüstlük korunur; ama her gate'e "bu gate ŞUNU kanıtlar" pozitif iddiası eklenir (G18'de var: güzel örnek) |
| E4 | Strix'in uyutulması | `integrations/strix/adapter.py` — iskelet, `ALLOWED_STRIX_CAPABILITIES` kısıtlı | Strix, Sensor Plane'in bir sensörü olarak uyanır: çıktısı UNTRUSTED_EXTERNAL, SoR'a admission'dan geçerek girer. **G2** |
| E5 | `ContentPolicyBlockedError`'un son durak olması | `research/model_port.py:55` | Model reddi runtime hatası olarak yönetilir; routing başka runtime'a düşer (routing.py zaten çok-runtime'lı); hunter prompt'ları scope yetki bağlamını taşır. Kalıcı çözüm: private kullanımda kendi model-runtime konfigürasyonun. **GATE 04B + G4** |

---

## F. MODEL/AI EKSİKLERİ

- **F1. GATE 04B PENDING:** `maturity.py:72` — ≥2 BENCHMARK_COMPATIBLE canlı runtime ile karşılaştırmalı koşu hiç yapılmamış. LIVE_MODEL_VALIDATED=False. **Çözüm:** İki canlı runtime konfigürasyonu (ör. iki farklı CLI/API runtime), benchmark koşusu, `run_research_benchmark.py` ile. Bu, saldırı dönemi G4'ten **önce** kapatılmalı — avcı beyni canlı model olmadan kördür.
  - **Durum (2026-08-20 reconcile):** `YENI_FAZ_FORMALITESI`. SD-G4
    saldırı dönemi içinde model ekonomisi/routing/bütçe disiplinini kapattı;
    model iddiaları SD-G16'da registry ve truth üretmekten kesin biçimde
    ayrıldı. Ancak `LIVE_MODEL_VALIDATED=false` ve `GATE_04B_STATUS=PENDING`
    hâlâ bilinçli olarak korunuyor; bu canlı-runtime kalibrasyon fazının işi.
- **F2. Hunter rolü yok:** ModelRole (model_port.py:20) mevcut ama avcıya özgü rol/şema seti (hipotez üretimi, kanıt değerlendirme, zincir muhakemesi) tanımlı değil. **G4.**
- **F3. MCP bağlantıları yok:** Dış araç/bilgi kaynaklarına MCP üzerinden erişim yok. Sensor Plane protokolü (Dikiş 7) bunun doğal yuvası. **G2.**

---

## G. OPERATÖR YOKLUĞU

- **G1 (madde):** `interface/cli.py:143` — `choices=("status", "export-source")`. Operatör sistemi **başlatamıyor, durduramıyor, onaylayamıyor, bulgu göremiyor.** Bug bounty pratiğinde bu, sistemin kullanılamaz olması demek.
- **Çözüm (Gate G10-G15 bandı, ama minimal sürüm G5'te):** `program add/show` · `run start/stop/status` · `review queue/approve/reject` (Core approval'a bağlı) · `finding list/export` · `coverage show` (Coverage Debt: Asset × Identity × Family). CLI asla Core'u atlayamaz — her komut application use-case'inden geçer.

---

## H. KAYIT EKSİKLERİ

- **H1.** `maturity.py`'de GATE_21 ve GATE_22 sabitleri **yok** (Kimi sınavında G21'i yakaladı; G22'yi ben ekledim). OPERATIONS.md'de PASS yazıyor ama maturity bayrağı tanımsız.
  - **Durum (2026-08-20 reconcile):** `TAMAMLANDI`. `maturity.py` artık
    `GATE_21_STATUS="PENDING"` ve `GATE_22_STATUS="PASS"` sabitlerini ve
    `maturity_mapping()` girdilerini içeriyor.
- **H2.** `maturity.py` docstring'i G20'de bitiyor — G21/G22 açıklamaları eklenmemiş.
  - **Durum (2026-08-20 reconcile):** `TAMAMLANDI`. `maturity.py`
    docstring'i G21 ve G22 ayrımını içeriyor; G21 formal PASS bekliyor, G22
    PASS.
- **Çözüm:** Her yeni gate ile maturity sabiti + docstring paragrafı aynı commit'te gelir; kapanış standardının parçası yapıldı (Ana Plan §3 değişmezleri).

---

## I. ALGI KÖRLÜĞÜ — görülmeyen yüzeyler

Mevcut keşif (G22) yalnız uygulama-içi (L2). Eksik şeritlerin tamamı:

1. **L1 External Census:** DNS, CT log, subdomain, tarihsel URL (Wayback/Common Crawl), sertifika, port/banner, ASN — **hiçbiri yok.** → G2
2. **JS bundle analizi:** endpoint/secret/parametre çıkarımı — yok → G2/G8
3. **API spec keşfi:** OpenAPI/GraphQL introspection/WebSocket/Swagger — yok → G8
4. **Parametre madenciliği:** hidden/bound parametre keşfi — yok → G8 (http.probe kontratı)
5. **APK analizi:** mobil uygulamadan API yüzeyi çıkarımı (web'den görünmeyen uçlar) — yok → G2
6. **Reverse DMARC/CSP keşfi:** aynı infrastruktürdeki kardeş varlıklar — yok → G2
7. **401-derinlik keşfi:** unauthenticated yüzeyin ardındaki authenticated harita — yok → G2/G5
8. **Teknoloji parmak izi → n-day eşleme:** yok → G14
9. **AI/LLM hedef şeridi:** prompt injection, context leakage, tool abuse, model API'leri — yok → **G12'de kendi HunterFamily'si olarak** (Claude'un "hiçbir gate'te yok" itirazı bu revizyonla güçlendirilerek kapatıldı)
10. **Continuous change:** yeni uç/parametre/sürüm değişim avcısı (temporal.py tohumu var, avcı yok) → G15

---

## J. EKONOMİ BOŞLUĞU — bulgu paradan kopuk

- **J1. Severity motoru yok:** internal P0–P3 + platform eşleme (Bugcrowd VRT / HackerOne) — G10
- **J2. PoC/rapor paketleme yok:** yeniden-üretim adımları, request/response dökümü, zincir haritası — G14
- **J3. Duplicate ekonomisi yok:** ilk bulan kazanır; bilinen-bulgu kontrolü ve benzerlik taraması — G14
- **J4. Coverage Debt metriği yok:** "hiç bakılmamış yüzey" ölçülmüyor; HunterScore'un yeni-bilgi bileşeni kör — G15

---

## K. GÜÇLÜLER — DOKUNULMAYACAK LİSTESİ (tam taramada doğrulandı)

Bunlar sulandırılmamış, tam tersine zirvede inşa edilmiş; saldırı döneminde **tek satırına dokunulmayacak:**

1. Core otorite zinciri (scope→authorization→budget→approval→execution→capability, 950 LOC)
2. Epistemik pipeline + FindingProposal fingerprint/approval-subject mühürleme
3. `RISK_UNDERSTATEMENT` fail-closed reddi (compiler)
4. Browser containment (cgroup v2 / Job Object) — çekirdek zorlamalı
5. TX-A/TX-B iki-transaction projection dikişi
6. `grants_scope() → False` (AttackSurfaceGraph asla yetki vermez)
7. `FORBIDDEN_DISCOVERY_KEYS` — discovery fact'lerine severity/vulnerability yasağı
8. Benchmark altyapısı: sealed holdout + leakage denetimi + metamorfik testler + prompt-injection tuzak senaryoları
9. 912 birim + 285 e2e/entegrasyon/kontrat testi, Kali'de gerçek PostgreSQL disiplini, fabricated PASS yasağı
10. SoR'da ham secret yasağı (yalnız referans)
11. 54 mimari kararın kayıt disiplini
12. Model bütçe zorlaması (budget_enforced_model)

---

## L. CERRAHİ SIRALAMA — tek bakışta

```
G1  [TAMAMLANDI] : A1, A2, A3(registry), B1, B2, B3, D1, D2, H1, H2    → kilit açılımı
                   + scope TTL (bayat kayıt → REQUIRE_HUMAN_REVIEW)
                   + platform API scope senkronu (HackerOne/Bugcrowd)
G2  [TAMAMLANDI] : I1, I5, I6, I7(kısım), E4, F3                       → dış algı
G3  [TAMAMLANDI] : I1'in grafik yansıması                              → yüzey modeli
G4  [TAMAMLANDI] : C1, C2, C3(iskelet), D3, D4, D5, F1, F2, E2, E3     → beyin kilidi kırma
G5  [TAMAMLANDI] : D6, D8, G(minimal CLI), I7(kısım)                   → authorization avcısı
G6  [TAMAMLANDI] : C4, C5 + rate_limit_profile zorlama testi           → saldırı kasları
G7  [TAMAMLANDI] : D7                                                  → workflow avcısı
G8  [TAMAMLANDI] : I2, I3, I4                                          → API/object avcısı
G9  [TAMAMLANDI] : E1                                                  → ilk enjeksiyon seti
G10 [TAMAMLANDI] : J1 + saha devre kesicisi (aile throttle)            → validator + severity
G11 [TAMAMLANDI] : C6 + adım-bazlı onay kanıtı                         → zincirleme
G12 [TAMAMLANDI] : I9 (AI/LLM kendi HunterFamily'si)                   → geniş enjeksiyon
G13 [TAMAMLANDI] : —                                                   → protokol uzmanı
G14 [TAMAMLANDI] : J2, J3, I8 + dış duplicate sinyali                  → ekonomi
G15 [TAMAMLANDI] : I10, J4, G(tam CLI) + recall konsolidasyonu         → sürekli av + ölçüm
G16 [TAMAMLANDI] : keşifsel hipotez üreteci (registry-dışı avcılık)    → yaratıcılık organı
```

**Revizyon notu (2026-08-18, ikinci tur):** Claude'un 7 maddesinden 6'sı tamamen, 1'i (AI/LLM) güçlendirilerek kabul edildi; Bölüm B'deki "kayıtlı olmayan şeyi bulamama" yapısal teşhisi G16 olarak plana girdi. Recall kanıtı artık G15'i beklemez — her aile kendi gate'inde kanıtlar.

**Kapanış notu (2026-08-20):** Bu yol haritası SD-G16 ile kapandı. Sulandırma
cerrahisinin açık operasyonel maddesi kalmadı. Bundan sonraki açık bayraklar
canlı model doğrulama, production readiness, saha-validasyon ve legacy formal
browser/cgroup seal konularıdır; bunlar yeni faz olarak ele alınmalıdır.

Her maddenin cerrahisi, Ana Plan'daki dikiş haritasındaki kenetlenme protokolüyle mühürlenir: fixture üçlüsü, `false_finding=0`, taze-oturum yeniden-üretim, 912+285 testin tamamı yeşil, Kali doğrulaması, maturity güncellemesi.

**Son söz:** Bu envanterdeki hiçbir madde "güvenliği gevşet" demiyor. Her madde, ChatGPT'nin *korkudan kilitlediği* bir kapıyı, Core otoritesinin *anahtarıyla* açıyor. Kilit kalıyor — anahtar bizde.
