# Zest — Küresel Otonom Güvenlik Araştırması Rekabet İstihbaratı

**Rapor tarihi:** 2026-08-20  
**İncelenen depo:** TayfurYldz/zest
**İncelenen HEAD:** [1f008265f1633ff74cc0c6d7156cc415bc542ca5](https://github.com/TayfurYldz/zest/commit/1f008265f1633ff74cc0c6d7156cc415bc542ca5), 2026-08-19
**Çalışma modu:** Kesinlikle salt-okunur; depoda dosya, commit, dal veya PR değişikliği yapılmadı.  
**Değişken veri kesiti:** Pazar ve liderlik tabloları 2026-08-20 itibarıyladır.

## Kanıt işaretleri

- **[OLGU]** Birincil kaynakta veya depoda doğrudan görülen bilgi.
- **[BEYAN]** Ürün sahibi, araştırmacı ya da platformun kendi iddiası.
- **[ÇIKARIM]** Birden çok olgudan türetilen teknik sonuç.
- **[BİLİNMİYOR]** Kamuya açık kanıt bulunmayan alan.
- **Güven:** Yüksek / Orta / Düşük / Yalnızca üretici beyanı.

Bu raporda “tamamlanmış tasarım kabiliyeti”, depodaki niyet ve planların başarıyla gerçekleştirilmiş varsayımsal son halini; “mevcut kabiliyet”, HEAD’deki kaynak, test ve migrasyonları; “kanıtlanmış kabiliyet” ise laboratuvar, bağımsız tekrar veya gerçek saha kanıtını ifade eder. Plan, uygulama ve doğrulama eş anlamlı değildir.

# 1. Yönetici Özeti

Zest’un doğru sınıfı “AI vulnerability scanner” değildir. Tamamlanmış hali; program politikasını, kapsamı, bütçeyi ve yan etkileri model dışı bir otoriteyle yöneten; gözlem–kanıt–aday–bulgu ayrımını koruyan; hedefin davranışını çok kimlikli, zamansal ve nedensel olarak öğrenen; deney seçip çalıştıran; sonuçları bağımsız doğrulama ve insan kabulünden geçiren **kanıt kontrollü otonom güvenlik araştırma işletim sistemi**dir.

Ana hüküm:

1. **Mimari disiplin bakımından** Zest, kamuya açık rakiplerin çoğundan daha açık ve güçlü bir epistemik zincir tarif ediyor. WorkerResult’ın gerçek sayılmaması, kapsam otoritesinin modelde olmaması, Evidence admission, bağımsız Verification ve insan Finding kabulü gerçek ayrıştırıcılardır.
2. **Bugünkü saha kabiliyeti bakımından** XBOW, Zest’un çok önündedir. XBOW’un HackerOne üretim hedeflerinde doğrulanmış bulgu hacmi, hedef seçimi, klon-dedup, paralel ajan yürütme ve validator yaklaşımı gerçek saha kanıtıdır. Zest’ta SECURITY_RESEARCH_VALIDATED ve PRODUCTION_READY hâlâ false’dur: [maturity.py](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/src/zest/maturity.py).
3. **Kaynak/binary araştırmasında** Project Glasswing/Mythos ayrı bir ligdedir. Bu, siyah-kutu bug bounty ile aynı ürün kategorisi değildir; ancak Zest’un gelecekteki source-assisted hattının çıtasını belirler.
4. **Kurumsal ağ, kimlik ve saldırı yolu kanıtında** NodeZero ve Pentera daha olgundur; bunların birincil problemi bug bounty hedef seçimi veya target-specific business logic değildir.
5. **İnsan üstünlüğünün merkezi** araç kullanımı değil; hedefin “normal” davranışını, sahiplik ve rol beklentilerini, organizasyon niyetini ve tuhaflığı ekonomik bağlamla birlikte sezmesidir. Zest’un mevcut Target Model + Differential + Invariant + Temporal bileşimi bu semantik dünya modelini henüz tamamlamaz.
6. **G16 tek başına yeterli değildir.** Registry dışı anomaliyi hipoteze çevirmek değerlidir; fakat yeni açık sınıfı keşfetmek için beklenti öğrenimi, sahiplik/rol/iş amacı modeli, karşı-olgusal deney sentezi, açık-uçlu soyutlama/family induction, çeşitlilik araması ve validator kaçış analizi gerekir.
7. **En önemli mevcut tasarım riski HunterScore’dur.** Aile başarı bonusu 10 × (supported − falsified) ile sınırsız büyür ve 0–50 arası coverage-state ağırlığını kolayca ezer. “Tazelik” en son değişime değil, node’un en eski sensor gözlemine göre hesaplanır. Bu iki özellik başarılı ailelere kilitlenme ve yeni değişiklikleri ıskalama riski yaratır: [score.py](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/src/zest/research/scheduler/score.py), [run_hunt_scheduler.py](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/src/zest/application/run_hunt_scheduler.py).
8. **P0 mimari engel saptanmadı.** Ana otorite ve kanıt sınırları tutarlı. Ancak P1 olarak semantik dünya modeli, üretim sınıfı executor/protokol hattı, açık-uçlu keşif mimarisi, hedef-portföy ekonomisi, source-assisted IR ve gerçek saha ölçümü öne çekilmelidir.

# 2. Araştırma Metodolojisi

## 2.1 Depo incelemesi

GitHub bağlantısı üzerinden varsayılan dal ve HEAD doğrulandı. Kök anayasa belgeleri, mimari kararlar, domain modeli, teknik gereksinimler, operasyon belgeleri, saldırı dönemi planları, G1–G9 uygulama/test zinciri, araştırma çekirdeği, uygulama use-case’leri, worker runtime, veri kayıtları, Alembic migrasyonları, benchmark ve unit/integration/e2e testleri incelendi.

Birincil depo kaynakları:

- [PROJECT_STRUCTURE.md](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/PROJECT_STRUCTURE.md)
- [DOMAIN_MODEL.md](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/DOMAIN_MODEL.md)
- [TECHNICAL_REQUIREMENTS.md](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/TECHNICAL_REQUIREMENTS.md)
- [TECHNICAL_DECISIONS.md](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/TECHNICAL_DECISIONS.md)
- [REPOSITORY_LAYOUT.md](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/REPOSITORY_LAYOUT.md)
- [IMPLEMENTATION_PLAN.md](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/IMPLEMENTATION_PLAN.md)
- [Operasyon modeli](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/docs/plans/Zest_Operasyon_Modeli.md)
- [Saldırı dönemi entegrasyon planı](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/docs/plans/Zest_Saldiri_Donemi_Entegrasyon_Plani.md)
- [Sulandırma envanteri ve gate yol haritası](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/docs/plans/Zest_Sulandirma_Envanteri_ve_Gate_Yol_Haritasi.md)

## 2.2 Dış araştırma

Kaynak sırası: resmi dokümantasyon/kod/paper/platform sıralaması → araştırmacının kendi yazısı → röportaj/teknik analiz → yalnızca tamamlayıcı topluluk kaynağı. Kapalı kaynak ürünlerde kamuya açık olmayan mimari uydurulmadı.

Değişen liderlik tablosu verileri tarih damgasıyla kaydedildi. Dinamik veya oturum gerektiren sayfalarda tam Top-100 listesinin tekrarlanabilir şekilde alınamadığı yerlerde isim uydurmak yerine PUBLIC EVIDENCE NOT FOUND denildi.

## 2.3 Puan rubriği

0 = yok; 1 = kavramsal; 2 = planlanmış/çok sınırlı; 3 = temel uygulama; 4 = olgun; 5 = gerçek dünyada ölçekli gösterim.  
Her hücre A/P biçimindedir: **A = mimari/ürün kabiliyeti, P = kamuya açık veya ampirik kanıt**. Soru işareti kamu kanıtı olmadığını gösterir.

## 2.4 Sınırlamalar

- Zest özel depodur; sonuçlar belirtilen HEAD anına aittir.
- Kapalı kaynak rakiplerde açıklanmayan veri modeli, prompt, model, maliyet ve validator tasarımı bilinmemektedir.
- Vendor sayıları bağımsız doğrulanmadıkça vendor beyanı olarak tutuldu.
- Hiçbir canlı üçüncü taraf hedef taranmadı veya prob edilmedi.

# 3. Zest Nihai Mimari Rekonstrüksiyonu

## 3.1 Anayasal çekirdek

[OLGU, yüksek güven] Core; request → policy → scope → budget → execution zincirinin tek otoritesidir. AI karar önerir; yetki vermez. Worker’lar tek yan-etki katmanıdır ve çıktı güvenilmez kabul edilir. Bu sınır, tamamlanmış sistemin güvenlik omurgasıdır: [DOMAIN_MODEL.md](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/DOMAIN_MODEL.md), [TECHNICAL_DECISIONS.md](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/TECHNICAL_DECISIONS.md).

## 3.2 Gerçek intended zincir

Program ve kapsam alınır → Scope Compiler yetkili ağ zarfını üretir → sensor gözlemleri güvenilmeyen dış veri olarak toplanır → deterministik normalizasyon Observation/Artifact oluşturur → SurfaceGraph v2 provenance ve scope ile yeniden kurulur → ProgramResearchContext kimlik, oturum, politika ve bütçeyi bağlar → Coverage Debt node × identity × HunterFamily matrisini çıkarır → scheduler araştırma fırsatlarını sıralar → HunterFamily veya registry-dışı keşif hipotez önerir → Generator ve Falsifier rakip açıklamalar üretir → discriminating experiment seçilir → deney tekrar Core authorization’a girer → HTTP/Browser/specialist worker kontrollü yan etki yürütür → WorkerResult, Observation’a normalleştirilir → Research yalnızca açık admission ile Evidence önerir → bağımsız Verification sonucu Candidate lifecycle’a taşır → FindingProposal hazırlanır → human review ve Core approval sonrası Finding oluşur → ImpactGraph kanıt bağlı zinciri kurar → temporal/change intelligence yeni araştırma fırsatları üretir.

Bu zincirin kritik kuralı:

**WorkerResult ≠ Observation ≠ Evidence ≠ Candidate ≠ FindingProposal ≠ Finding.**

## 3.3 Epistemik hedef modeli

Target Model’de bilgi OBSERVED, DERIVED, INFERRED ve HYPOTHESIZED olarak ayrılır. Differential Engine actor/role/session/resource/state/action/input/time eksenlerinde fark arar; Invariant Engine beklenen davranışı ve karşı örneği taşır; Chain Engine yalnızca proof reference’lı kenarlarla sınırlı arama yapar; Temporal Intelligence snapshot/change ayrımı kurar. Bu, “cevap güven verici görünüyor” ile “kanıtlandı” arasındaki boşluğu bilinçli olarak kapatır.

## 3.4 Veri ve bellek

PostgreSQL source of record’dur. Reasoning, admission, assessment ve audit kayıtları append-oriented’tır; projections yeniden üretilebilir. Research Memory gerçekliğin kendisi değil, retrieval abstraction’dır. LLM sohbeti kalıcı durum değildir. Bu tercih, uzun süreli hedef hafızası ve neden-sonuç denetimi için doğrudur; ancak projection/retention maliyeti saha yükünde ayrıca kanıtlanmalıdır.

## 3.5 Tamamlanmış operasyon modeli

Nihai operasyon vizyonu: operatör program/kapsam ekler; sistem census yapar; iki veya daha fazla hesap/rol bağlar; MAP → HYPOTHESIZE → PROBE → VALIDATE döngüsünü kesintisiz çalıştırır; bulguyu insan kuyruğuna paketler; duplicate kontrolü ve rapor üretir; hedef değişince yeniden avlanır. Bu, mevcut kodun tamamı değildir; [operasyon modeli](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/docs/plans/Zest_Operasyon_Modeli.md) ile tamamlanmış tasarım varsayımıdır.

# 4. Zest Organ/Engine Haritası

| Organ | Mevcut çekirdek | Nihai görev | Otorite sınırı |
|---|---|---|---|
| Control Plane | Policy, ScopeCompiler v2, budget, approvals | Her deneyi fail-closed yetkilendirme | Model kapsam veya yetki basamaz |
| Sensor Plane | DNS, CT, archive, certificate, tech fixture-adapter’ları | Canlı pasif/aktif census ve runtime gözlem | Sensor verisi UNTRUSTED_EXTERNAL |
| SurfaceGraph v2 | Domain, hostname, cert, service, tech, JS bundle, API spec | Zengin, zamansal, kimlikli hedef modeli | Graph scope/session/budget/capability veremez |
| ProgramResearchContext | Program, scope, identity/session bağlamı | Deneyler arası tutarlı yetkili bağlam | Core tarafından üretilir |
| HunterFamily Registry | 5 seed family, V1/V2/V3 kademeleri | Kanıt gereksinimli uzman araştırma aileleri | V3 aktif onaylıdır; model registry yazamaz |
| Research Brain | generator/falsifier, selection, differential, invariant, target/causal | Rakip hipotez ve ayırt edici deney | Doğrudan yan etki yok |
| Mutation Engine | 7 deterministik mutation family | Protokol/semantik/fuzz deney üretimi | Her mutasyon yeniden Core’a girer |
| OAST | Loopback doğrulanmış core | DNS/HTTP(S)/SMTP/LDAP callback hizmeti | Callback kanıtı admission ister |
| HTTP/Browser Workers | Yerel yetkili HTTP ve Playwright akışı | HTTPS, SPA, auth, API/protokol uzmanları | Tek yan-etki katmanı, OS/process izolasyonu |
| Evidence Pipeline | normalize, admit, verify, candidate | Sahte pozitif kontrollü bulgu terfisi | Human + Core olmadan Finding yok |
| ImpactGraph | Proof-backed, cross-run kilitli | Çok adımlı business impact zinciri | Kanıtsız edge reddedilir |
| Coverage Debt | Node × identity × family matris | Bilinen ve bilinmeyen araştırma borcu | Kimliksiz genelleme engellenmeli |
| HunterScore | State + family history + first-seen freshness + budget | Bilgi getirisi/değer/yenilik odaklı portföy planlama | Bugünkü formül yalnız tavsiye üretir |
| Temporal Intelligence | Snapshot/change modelleri | Deployment ve davranış değişiminde yeniden av | Değişim kendi başına açık değildir |
| Model Runtime/Routing | Provider-neutral port, role/routing kuralları | Ayrık creator/critic/validator model alaşımı | Model kendi rolünü seçmez |
| Data/Memory | PostgreSQL, append-only ledger, projections | Uzunlamasına hedef ve çapraz hedef öğrenme | Retrieval truth değildir |
| Interface/Operations | status, census, budget, coverage CLI | program/run/review/finding/report/duplicate operasyonu | İnsan kabul noktası korunur |

# 5. Mevcut vs Planlanan vs Doğrulanmış Kabiliyet Matrisi

Kaynak doğruluk çıpası: [maturity.py](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/src/zest/maturity.py). Mevcut kodda ARCHITECTURE_VALIDATED ve DIAGNOSTIC_E2E_VALIDATED true; LIVE_MODEL_VALIDATED, SECURITY_RESEARCH_VALIDATED ve PRODUCTION_READY false’dur. Saldırı dönemi G1–G8 PASS, G9 PENDING’dir.

| Kabiliyet | Mevcut HEAD | Planlanan nihai hal | Doğrulama |
|---|---|---|---|
| Scope Compiler v2 | Uygulandı | Program politikasıyla sürekli scope | G1 PASS |
| Authorized network envelope | Uygulandı | Tüm executor’lara ağ düzeyi sınır | G1 PASS |
| Sensor plane | 5 sensör, fixture protokolü | Canlı census/adapters | G2 laboratuvar PASS; canlı Internet kanıtı yok |
| SurfaceGraph v2 | 7 node kind + provenance | Semantik/runtime/source katmanları | G3 PASS |
| Token economy | Fail-closed fiyat/bütçe/escalation | Portföy düzeyi ROI ve model alaşımı | G4 PASS |
| HunterFamily registry | 5 seed family | Geniş uzman aileler + insan onaylı expansion | G5 PASS |
| Mutation | 7 deterministik aile | Gramer/semantik/protokol/race mutasyonları | G6 çekirdek PASS |
| OAST | Loopback core | Dağıtık çok-protokollü servis | G6 yerel PASS |
| ImpactGraph | Proof-backed chain | Cross-target causal chain analytics | G7 PASS |
| Coverage Debt | Node × identity × family | Semantik ve registry-dışı borç | G8 PASS |
| HunterScore | Deterministik, açıklanabilir | Değer + bilgi getirisi + yenilik + ekonomi | Uygulandı; G9 bağımsız seal PENDING |
| Browser | Playwright engine/containment | Gerçek HTTPS/SPA/SSO/anti-bot | Eski G21 formal PASS bekliyor |
| Authorization diff | Yerel HTTP lab | Çok rol/tenant/ownership | G14/G20/G22 yerel PASS |
| Workflow research | Yerel state benchmark | Uzun, asenkron ve dağıtık workflow | G16/G17 yerel PASS |
| Live models | Port/routing var | En az iki gerçek config, calibration | LIVE_MODEL_VALIDATED false |
| Security research | Lab benchmark’leri | Yetkili saha ve bug bounty sonucu | SECURITY_RESEARCH_VALIDATED false |
| Source-assisted | Karar düzeyinde gelecek | AST/CFG/dataflow → runtime proof | Plan; uygulanmış değil |
| Mobile/binary | Yok | Uzman hatlar ima ediliyor | Kanıt yok |
| Reporting/duplicate | Operasyon vizyonunda | Platform-ready paket/duplicate ekonomi | Tam iş akışı yok |
| Continuous operation | Bileşenleri var | Event/change-triggered 24/7 av | Saha kanıtı yok |

**Doküman sürüklenmesi:** Bazı eski planlar mutation/OAST/Coverage Debt’i yok sayıyor; HEAD’de G6/G8 vardır. Kök kural dosyasındaki “architecture/design phase; production code yazma” ifadesi de geniş uygulama gövdesiyle çelişmektedir. Sonuç: plan belgeleri tarihsel kanıt olarak kullanılmalı, maturity ve HEAD güncel gerçeklik için üstün tutulmalıdır.

# 6. Küresel Otonom Güvenlik Rakip Manzarası

| Sistem | Asıl problem | En güçlü kamu kanıtı | Zest ile kıyas | Güven |
|---|---|---|---|---|
| XBOW | Siyah-kutu/white-box web ve API otonom pentest, bug bounty | HackerOne üretim hedefleri ve triage sonuçları | En yakın doğrudan saha rakibi | Yüksek; mimari ayrıntı orta |
| Horizon3 NodeZero | Enterprise network, identity, cloud, Kubernetes attack path | Resmi ürün dokümanı, gerçek exploit/path ve re-test | Kısmi rakip; bug bounty semantiği farklı | Yüksek |
| Project Glasswing/Mythos | Kaynak, binary, variant ve exploit araştırması | Anthropic teknik eval’leri ve partner programı | Gelecek white-box hattına rakip | Orta; büyük ölçüde Anthropic beyanı |
| Pentera | Adversarial exposure validation, internal/external/cloud | Canlı üretim test, chain, remediation/re-test | Enterprise validation rakibi | Orta-yüksek |
| Terra | Continuous agentic web/network/AI pentest + human-on-loop | Resmi agent architecture ve guardrail açıklaması | Human-agent operasyonunda yakın | Orta; saha metriği sınırlı |
| RunSybil | Black-box continuous application security | Resmi ürün beyanı | Potansiyel doğrudan rakip | Düşük/üretici beyanı |
| Picus | BAS + attack path/exposure validation | Resmi platform ve otonom pentest mimarisi | Enterprise validation; önemli yapı fikri | Orta |
| Ridge Security | Policy-outside-model, evidence validation | Resmi mimari beyan | Otorite/evidence yaklaşımı benzer | Düşük-orta |
| Synack Sara | AI pentest + insan ağı | Resmi ürün sayfası | Hybrid delivery rakibi | Orta |
| Strix | Açık kaynak multi-agent app pentest | Kod, PoC, browser/proxy/tooling | En erişilebilir uygulama karşılaştırıcısı | Yüksek |
| Shannon | Source-aware web/API + live exploit | Açık kaynak 5-faz pipeline ve safety doc | White-box app hattına yakın | Yüksek |
| CAI | Açık bug-bounty-ready agent framework | Kod/paper/CTF ve PoC | Araştırma çerçevesi; prod OS değil | Orta-yüksek |
| PentAGI | Self-hosted multi-agent execution/memory | Açık kod ve flow dokümanı | Tool executor/orchestration karşılaştırıcısı | Yüksek; saha sonucu düşük |
| PentestGPT | Task-tree/context management | USENIX paper + repo | Planlama/memory araştırma referansı | Yüksek |
| Incalmo | Çok-hostlu network red teaming | IEEE S&P paper/MHBench | Uzun-horizon network karşılaştırıcısı | Yüksek |

**Bölgesel araştırma:** Avrupa merkezli CAI ve çeşitli bağımsız projeler teknik olarak değerlidir. Türkiye merkezli, Zest ile aynı kapsama ve kamuya açık teknik/saha kanıtına sahip bir sistem için güvenilir birincil kanıt bulunamadı. Bu “rakip yok” anlamına değil, **PUBLIC EVIDENCE NOT FOUND** anlamına gelir.

# 7. Ayrıntılı XBOW Analizi

## Bilinen

- [OLGU] XBOW, 2025’te önce PortSwigger/PentesterLab ve kendi benchmark’larını; sonra white-box OSS zero-day; ardından HackerOne siyah-kutu üretim hedeflerini kullandığını açıkladı: [The Road to Top 1, 2025-06-24](https://xbow.com/blog/top-1-how-xbow-did-it).
- [OLGU] Program scope/policy parse sürecinde LLM + manuel kürasyon; hedef puanında WAF, status, redirect, auth form, endpoint sayısı ve teknoloji sinyalleri; klon ortamlar için SimHash + screenshot imagehash kullandı.
- [OLGU] Discovery ve validation ayrıdır. XSS için browser execution gibi deterministik validator; bazı alanlarda ayrı LLM validator kullanılır.
- [BEYAN, kısmen platform sonucu ile destekli] Yaklaşık 1.060 submission’ın 130’u resolved, 303’ü triaged, 208’i duplicate, 209’u informative, 36’sı N/A; insan ekibi discovery’ye değil, policy uyumu için pre-submission review’a katıldı.
- [BEYAN] 2026 mimarisi binlerce kısa ömürlü ajan, kalıcı coordinator, taze context ve deterministik doğrulama kullanıyor; 48 adımlı chain örneği yayımlandı: [1,060 Autonomous Attacks, 2026-03-02](https://xbow.com/blog/we-ran-1060-autonomous-attacks).

## Çıkarılan mimari

Persistent coordinator → dar amaçlı kısa ömürlü agent → tool/browser/exploit runtime → ayrı validator → portfolio-level target selector. Taze ajan context’i uzun context çürümesini azaltır; coordinator global durumu tutar. Bu, Zest’un Controller + per-experiment Core admission yaklaşımıyla uyumludur; fakat XBOW saha ölçeğini kanıtlamıştır.

## XBOW’un asıl rekabet avantajları

1. **Target economics:** Hangi programa ve varlığa ne zaman compute harcanacağını çözer.
2. **Asset deduplication:** Klon staging’leri bir portföy olarak işler.
3. **Agent variance:** Dar amaçlı yeni bağlamlarla çıkmazdan yeniden başlar.
4. **Validator yatırım döngüsü:** Discovery kadar doğrulama altyapısı da ürünün merkezidir.
5. **Gerçek veri:** Program kararları, duplicate/informative sonuçları ve exploit trace’leri model/strateji geliştirme sinyali olur.
6. **Semantik normal davranış:** IDOR doğruluğu için rol bazlı login/browsing ile “normal” davranış öğrenme yönüne geçti: [IDOR high-accuracy analysis](https://xbow.com/blog/xbow-finds-idors-high-accuracy-ambiguous-context).

## Bilinmeyen

İç veri modeli, gerçek model/router bileşimi, validator false-negative oranı, maliyet, destructive-action policy ayrıntıları, scope violation sayısı, field recall ve insan pre-submission reddetme oranı kamuya açık değildir.

## Zest dersi

XBOW’u geçmek için daha fazla vulnerability template değil; semantik normal model, portföy ekonomisi, kısa-ömürlü farklılaştırılmış araştırmacılar, validator çeşitliliği ve gerçek saha geri-besleme döngüsü gerekir. Zest’un epistemik modeli daha açıklanabilir olabilir; fakat saha girdisi olmadan bu mimari üstünlük sonuç üstünlüğüne dönüşmez.

# 8. Ayrıntılı Horizon3 / NodeZero Analizi

NodeZero’nun hedefi “HackerOne’da bug bulmak” değil, enterprise ortamında gerçekten hangi path’in domain/credential/cloud/business impact’e ulaştığını kanıtlamaktır.

- [OLGU] Intelligent Scope bulunduğu /16’dan organik genişlemeyi; endpoint-only kapsam ise chain’i kapatmayı tarif eder: [Deployment Strategy](https://docs.horizon3.ai/portal/deployment_strategy/).
- [OLGU] AD/Entra credential doğrulandıktan sonra BloodHound collector ve ephemeral Neo4j 4.4 kullanarak karmaşık path arar: [BloodHound docs](https://docs.horizon3.ai/portal/features/bloodhound/).
- [OLGU] Context score; exploitability, downstream impact ve business context’i CVSS’den ayrı işler: [Glossary](https://docs.horizon3.ai/knowledge_base/glossary/).
- [OLGU] WebApp; crawling, authentication, SPA, REST/SOAP/GraphQL, request/response/screenshot kanıtı ve web’den identity/infrastructure chain’i vurgular: [NodeZero WebApp](https://horizon3.ai/nodezero/webapp/).
- [OLGU] Campaign/Insights zaman içinde açık attack path, remediation ve re-test takibi yapar: [Insights](https://docs.horizon3.ai/insights/).

**Zest’a aktarılabilir fikirler:** deployment perspective’in scope semantiğine dahil edilmesi; credential/identity ilişki grafiği; crown-jewel/business impact; chain choke-point; remediation sonrası path-level revalidation.

**Aktarılmaması gereken varsayım:** Enterprise credential graph’ı, target-specific web business logic’in yerine geçmez. NodeZero’daki graph başarısını doğrudan bug bounty novelty puanı saymak yanlış olur.

# 9. Ayrıntılı Project Glasswing Analizi

Glasswing, bug bounty platformundan çok frontier model destekli kaynak/binary vulnerability research ve savunma programıdır.

- [BEYAN, teknik eval ayrıntılı] Mythos Preview’ın OSS-Fuzz corpus’unda yaklaşık 7.000 entry point koşusunda 595 tier-1/2 crash ve 10 ayrı tam control-flow hijack ürettiği; OS/browser zero-day ve complex exploit geliştirdiği açıklandı: [Mythos Preview technical assessment, 2026-04-07](https://www.anthropic.com/research/mythos-preview).
- [BEYAN] İlk yaklaşık 50 partnerin 10.000’den fazla high/critical flaw bulduğu ve programın yaklaşık 150 yeni organizasyona genişlediği açıklandı: [Expanding Project Glasswing, 2026-06-02](https://www.anthropic.com/news/expanding-project-glasswing).
- [OLGU/BEYAN] 45 ajan + ayrı VM + ortak forum + peer review + arbiter deneyinde coordinated Mythos swarm 266, coordinated Opus 41 vulnerability buldu; bağımsız ve koordineli arama yalnız 12 ortak bulguda kesişti: [Patterns and problems in emerging multiagent systems, 2026-08](https://www.anthropic.com/research/multiagent-systems).
- [OLGU] Anthropic ayrıca exploit kabiliyetini deterministik capability ladder ile ölçen ExploitBench/ExploitGym/SCONE yaklaşımını yayımladı: [Exploit evals](https://www.anthropic.com/research/exploit-evals).

**Önemli karşı ders:** Çok ajan tek başına çeşitlilik değildir. Aynı model/context ajanları aynı hataya yığılabilir; koordinasyon, conformity, collusion ve epistemic trust problemleri doğurur. Zest model alaşımı, bağımsız validator, kaynak güven skoru ve çeşitlilik bütçesi kullanmalıdır.

**Karşılaştırma hükmü:** Glasswing, source/binary novelty ve exploit synthesis’te daha güçlü kanıt sunuyor. Zest ise scope/program policy, runtime evidence provenance ve human finding acceptance’ı daha açık bir sistem kontratına dönüştürüyor. Gelecek üstünlük formülü “Source suggests; runtime proves” olabilir; bugün o hat uygulanmış değildir.

# 10. Diğer Ticari Rakipler

## Pentera

Pentera internal, external, cloud/hybrid ortamında exploitable path’i güvenli biçimde doğrulama, proven risk önceliği ve otomatik remediation/re-test akışı sunuyor: [Pentera Platform](https://pentera.io/pentera-platform/), [AI-powered exposure validation](https://pentera.io/ai-powered-exposure-validation/). Güçlü yanı production-safe, tekrarlanabilir enterprise validation’dır; target-specific novelty hakkında kamu kanıtı sınırlıdır.

## Terra Security

Terra yüzlerce uzman agent, exploit proof ve Human-on-the-Loop guardrail yaklaşımını açıklar: [Agent Architecture](https://www.terra.security/agent-architecture), [AI pentesting guardrails, 2026-08-06](https://www.terra.security/blog/ai-pentesting-guardrails). TORCH, operatör sezgisini ajan izleriyle birleştiren doğru bir ürün fikridir. Ancak kapalı mimari, field recall/FP ve bug bounty kanıtı kamuya açık değildir.

## RunSybil

RunSybil sürekli black-box application/infrastructure testing ve “elite researcher reasoning” iddia eder: [RunSybil](https://www.runsybil.com/). Kamuya açık teknik mimari, benchmark ve program-owner doğrulaması sınırlı olduğundan güçlü sonuçlar **yalnızca üretici beyanı** olarak kalmalıdır.

## Picus, Ridge ve Synack

Picus signal layer → coordinator → specialized agents → validation → remediation/revalidation mimarisini açıklıyor: [Autonomous Pentesting](https://www.picussecurity.com/resource/blog/what-is-an-autonomous-pentesting-platform). Ridge, policy’nin model dışında ve sonuçların evidence-gated olmasını vurguluyor: [Ridge Security](https://ridgesecurity.ai/). Synack, AI pentest’i vetted human network ile hibritliyor: [Synack AI Pentesting](https://www.synack.com/platform/ai-pentesting/). Bunlar Zest’un otorite ve human-review kararlarını doğrulayan pazar sinyalleridir; özgün novelty kanıtı değildir.

# 11. Açık Kaynak Otonom Güvenlik Projeleri

| Proje | Temel mimari | Güçlü fikir | Sınırlama / ders |
|---|---|---|---|
| [Strix](https://github.com/usestrix/strix) | Multi-agent, browser, interception proxy, shell, custom exploit runtime | Black/grey/white-box, API contract, PoC, CI | Geniş araç icrası; kamu benchmark ve coverage semantics sınırlı |
| [Shannon](https://github.com/KeygraphHQ/shannon) | Pre-recon → recon → 5 parallel analysis → 5 exploit → report | Source-to-live exploit; yalnız working PoC raporu | Safety doc production’da mutasyon/silme ve prompt injection riski uyarır |
| [CAI](https://github.com/aliasrobotics/CAI) | Uzman agent framework + human oversight | Bug-bounty-ready açık araştırma | Tool/command injection advisories, worker isolation ihtiyacını gösterir |
| [PentAGI](https://github.com/vxcontrol/pentagi) | Researcher/Developer/Executor, Docker sandbox, vector memory | Self-hosted provider-neutral execution | Erken alfa; business logic ve field validation sınırlı |
| [PentestGPT](https://github.com/greydgl/pentestgpt) | Pentesting Task Tree; reasoning/generation/parsing | Context loss azaltma | İlk tasarım human-in-loop; saha otonomisi sınırlı |
| [AutoPenBench](https://github.com/lucagioacchini/auto-pen-bench) | Docker benchmark + milestones | Progress-rate, başarısızlık analizi | Container task ≠ üretim bug bounty |
| [CVE-Bench](https://github.com/uiuc-kang-lab/cve-bench) | 40 critical CVE, application-specific graders | Gerçek CVE exploit ölçümü | Known-vulnerability benchmark novelty ölçmez |
| [Incalmo](https://arxiv.org/abs/2501.16466) | Planner + high-level task abstraction + expert services | 40 multi-host environment’ta 37 critical asset | Network red team; web business semantics değil |

Shannon’ın [safety dokümanı](https://github.com/KeygraphHQ/shannon/blob/main/docs/safety.md) özellikle önemlidir: proof-by-exploitation bile hatalı rapor üretebilir; kaynak dosyası prompt injection taşıyabilir; üretimde state mutasyonu riski vardır. Zest’un untrusted worker result, process isolation ve human acceptance kararları bu gerçek saldırı yüzeyine karşı yerindedir.

# 12. AI Güvenlik Araştırma Literatürü

| Çalışma | Sonuç | Zest dersi |
|---|---|---|
| [PentestGPT, USENIX Security 2024](https://www.usenix.org/conference/usenixsecurity24/presentation/deng) | Üç self-interacting module context loss’u azaltır | Durable task/claim graph sohbet özetinden üstündür |
| [NYU CTF Bench, NeurIPS 2024](https://proceedings.neurips.cc/paper_files/paper/2024/hash/69d97a6493fbf016fff0a751f253ad18-Abstract-Datasets_and_Benchmarks_Track.html) | 200 challenge, altı kategori, tool-using evaluation | Kategori çeşitliliği gerekir; CTF saha eşdeğeri değildir |
| [CyBench](https://cybench.github.io/) | 40 professional CTF + subtasks | Sadece binary success yerine ara ilerleme ölç |
| [CyberSecEval 3](https://ai.meta.com/research/publications/cyberseceval-3-advancing-the-evaluation-of-cybersecurity-risks-and-capabilities-in-large-language-models/) | Autonomous offensive ops dahil risk/capability suite | Capability ve safety birlikte ölçülmeli |
| [AutoPenBench, EMNLP Industry 2025](https://aclanthology.org/2025.emnlp-industry.114/) | 33 task; milestone progress | Uzun görevde partial progress ve recovery ölç |
| [CVE-Bench, ICML 2025](https://proceedings.mlr.press/v267/zhu25i.html) | En iyi agent framework en fazla %13 | Lab iddialarını gerçek CVE grader’ıyla sertleştir |
| [Incalmo, IEEE S&P 2026](https://users.ece.cmu.edu/~lbauer/papers/2026/sp2026-incalmo.pdf) | 40 ağdan 37 critical asset; 12–54 dk, 15 dolar altı | High-level domain service abstraction model yükünü azaltır |
| [Can LLMs Hack Enterprise Networks?](https://dl.acm.org/doi/10.1145/3766895) | Gerçekçi AD assumed-breach ve stochastic timing | Retry, stochastic outcome ve time dependency first-class olmalı |
| [PrimeVul/ICSE 2025](https://www.computer.org/csdl/proceedings-article/icse/2025/056900a469/215aWRJLUZy) | Eski vuln benchmark’larında leakage/noisy label | Temporal split, dedup ve truth-blind holdout şart |
| [Glasswing multiagent study](https://www.anthropic.com/research/multiagent-systems) | Koordineli ve bağımsız arama tamamlayıcı; ajanlar aynı hataya yığılabilir | Ajan sayısı değil epistemik/stratejik çeşitlilik ölçülmeli |

Literatürün ortak sonucu: Model puanı tek başına zayıf öngörücüdür. Scaffolding, domain abstraction, external state, deterministic graders, retry/recovery ve benchmark hygiene sonucu dramatik değiştirir. Bu, Zest’un “AI reasons; system decides; tools execute; evidence proves” ayrımını destekler.

# 13. Elite Bug Bounty Liderlik Tablosu Analizi

## 13.1 Anlık kesit

**YesWeHack, 2026 Q3, 2026-08-20:** SecurityReapers 1.752 puanla birinci; Edra 1.708; YoyoDavelion 1.503; Vozec 1.275; rabhi 1.165. Sayfa ilk 25’i yayımlıyor: [YesWeHack Ranking](https://yeswehack.com/ranking).

**Intigriti, son 90 gün, 2026-08-20:** 1ucky1ucke 3.006, fatman 2.297, mrdesoky0 2.039; ilk 20 görünür: [Intigriti Leaderboard](https://app.intigriti.com/leaderboard?ninetydays=true&severity=1).

**HackerOne:** Oturum gerektiren leaderboard’un tam güncel isim listesi tekrarlanabilir biçimde alınamadı. Resmi formül Reputation × Signal Percentile × Impact Percentile; pozitif reputation, negatif olmayan signal ve sıfır code-of-conduct ihlali eligibility koşuludur. Günlük 08:30 UTC güncellenir: [HackerOne 90-Day Leaderboard docs, 2026-03-26](https://docs.hackerone.com/en/articles/8456917-90-day-leaderboard).

**Bugcrowd:** Dinamik liderlik sayfası tam sıralamayı açık metin olarak sunmadı. 2026’da platform, accuracy, submission volume ve zaman içi consistency ile yüksek doğruluklu araştırmacılara priority queue bypass getirdi: [Bugcrowd, 2026-06-08](https://www.bugcrowd.com/blog/introducing-priority-queue-bypass-a-new-way-to-recognize-top-hackers/).

## 13.2 Liderlik tablosunun neyi ölçtüğü

Liderlik tablosu “salt teknik deha” değildir. Geçerli rapor oranı, impact, program erişimi, invite kalitesi, zamanlama, duplicate riski, rapor profesyonelliği ve sürdürülebilir çalışma birleşimidir. HackerOne formülü ve Bugcrowd priority badge politikası, Zest scheduler’ının sadece coverage state ve family success kullanmasının ekonomik olarak eksik olduğunu gösterir.

## 13.3 Top-10/50/100 inceleme sınırı

Top-10 ve görünür Top-25/20 kesitleri incelendi; geçmiş ve güncel platform yazılarıyla metodoloji örnekleri seçildi. Her Top-50/100 üyenin açık teknik izi yoktur. Kamu kanıtı olmayan kişilere uzmanlık veya davranış atfetmek yerine ayrıntılı profiller yalnız yeterli birincil kanıt bulunan araştırmacı/takımlarla sınırlandı.

# 14. SecurityReapers Vaka Çalışması

## Doğrulanabilir kamu izi

- [OLGU] 2026 Q3 YesWeHack tablosunda 1. sıradadır: [ranking](https://yeswehack.com/ranking).
- [BEYAN] Şirket web sitesi “manual-first”, chained/business-impact flaw, bug bounty ve Synack Red Team deneyimi vurgular: [Security Reapers](https://securityreapers.com/).
- [BEYAN] Metodoloji; reconnaissance → threat modeling → discovery → safe exploitation → privilege/lateral movement → impact validation → reporting → remediation → retest zinciridir: [Methodology](https://securityreapers.com/methodology/).
- [BEYAN] Kurucular Muhammad Zeeshan ve Muhammad Usman olarak açıklanmıştır: [About](https://securityreapers.com/about/).

## Güvenilirlik uyarısı

Sitedeki 750+ vulnerability, 120+ program ve 200+ assessment rakamlarının altında açıkça “Placeholder metrics — updatable” ifadesi vardır. Bu yüzden bu rakamlar performans kanıtı olarak kullanılmamalıdır. YesWeHack birinciliği yüksek güvenli platform kanıtıdır; şirket içi toplam metrikler düşük güvenlidir.

## Mental algoritma çıkarımı

- **[PUBLICLY STATED]** Automation başlangıç noktasıdır; ana değer manual-first threat modeling ve chain’dir.
- **[PUBLICLY STATED]** Teknik exploit, business impact’e çevrilmeden iş bitmiş sayılmaz.
- **[ÇIKARIM, orta güven]** Takım; geniş yüzeyde her şeye eşit süre ayırmak yerine crown-jewel, abuse case ve chain potansiyeli yüksek alanları seçer.
- **[BİLİNMİYOR]** Kullandıkları özel araçlar, kelime listeleri, scheduler, AI/LLM yöntemi ve duplicate modeli için yeterli kamu kanıtı yoktur.

Zest’a aktarılacak esas fikir araç değil; **threat actor + asset value + abuse case + chain potential** dörtlemesinin scheduler ve semantic target model’e girmesidir.

# 15. Önde Gelen Hunter Metodoloji Profilleri

## 15.1 Rabhi — kalıcı metodoloji ve farklılaşma

YesWeHack’in all-time #1 olarak tanıttığı rabhi, reconnaissance’ı temel; uzmanlaşma, az görünür JS parametreleri, güncel bypass teknikleri, sezgi, disiplinli günlük çalışma ve iyi program iletişimini başarı nedeni olarak açıklar: [Rabhi blueprint, 2025-09-16](https://www.yeswehack.com/community/rabhi-root-bug-bounty-blueprint).

Kodlanabilir davranış: önce maksimum hedef bağlamı; kalabalığın test ettiği görünür input yerine gizli ilişki/parametre; uzman family derinliği; günler sonra yeniden dönme; duplicate’i öğrenme maliyeti sayma.

## 15.2 Shubham Shah — ısrar ve sürekli öğrenme

Shah, başarının yalnız valid bug olmadığını; hedef/teknoloji hakkında edinilen bilginin uzun vadeli sermaye olduğunu, persistence ve sürekli güncel research okumanın esas olduğunu belirtir: [So, you want to get into bug bounties?, 2022-11-26](https://shubs.io/so-you-want-to-get-into-bug-bounties/).

Kodlanabilir davranış: negatif sonuçları bağlamlı knowledge olarak sakla; “no finding”i boşa harcanmış run sayma; yeni public research’i family/experiment adayına dönüştür.

## 15.3 Orange Tsai — mimari semantik uyumsuzluk

Orange Tsai’nin Apache “Confusion Attacks” araştırması, modüller aynı alanı farklı yorumladığında yeni attack surface doğduğunu; tekil bug kalıbından ziyade komponent ilişkileri ve semantic ambiguity üzerinde çalıştığını gösterir: [Confusion Attacks, 2024-08-09](https://orange-tw.blogspot.com/2024/08/confusion-attacks-en.html).

Kodlanabilir davranış: aynı field’ın proxy/router/ACL/filesystem/handler katmanlarındaki anlamını karşılaştır; normalization ve ownership sınırlarında çelişki ara; local gadget’ları chain primitive olarak kaydet.

## 15.4 Bugcrowd elite takım — bilişsel çeşitlilik

sw33tLie, bsysop ve godiego; sabit roller yerine lead/follow geçişi, no-ego adaptasyon ve tek asenkron iletişim hattı kullandıklarını açıklar: [Bugcrowd Hacker Spotlight, 2026-02-10](https://www.bugcrowd.com/blog/hacker-spotlight-meet-an-elite-hacking-team/).

Kodlanabilir davranış: aynı modelin kopyalarını çoğaltmak yerine farklı specialist priors; ortak kanıt panosu; bulgu sahibinden bağımsız challenger; rolün göreve göre değişmesi.

## 15.5 Bugcrowd 2026 topluluk sinyali

2.000’den fazla hacker anketinde %72 takımın daha iyi sonuç verdiğini, %61 işbirliğinde daha fazla critical bulduğunu, ideal takımın çoğunlukla 3–4 kişi olduğunu; %82’nin AI kullandığını bildirdi: [Inside the Mind of a Hacker 2026](https://www.bugcrowd.com/blog/inside-the-mind-of-a-hacker-2026/). Bu öz-beyan anketidir; yine de “human-augmented” modelin pazar normu olduğunu gösterir.

# 16. Ortak Elite-Hunter Kalıpları

1. Recon “asset list” değil, hedefin sosyal/teknik haritasıdır.
2. Başarılı avcı görünür input’tan çok az kalabalık ilişkiyi seçer.
3. Normal davranışı rol, sahiplik, tenant ve zamanla öğrenir.
4. İki hesap yalnız diff için değil, beklenen iş kuralını anlamak için kullanılır.
5. Tuhaf response tek başına açık değil, daha pahalı araştırma için sinyaldir.
6. Küçük primitive’ler business impact zincirine çevrilir.
7. Negatif sonuç zihinsel pattern library’yi büyütür.
8. Yeni program/deployment/change anı duplicate riskini düşürür.
9. Uzmanlaşma depth üretir; ekip çeşitliliği blind spot’u azaltır.
10. Rapor kalitesi, ilişki ve platform signal’i bir sonraki hedef erişimini etkiler.

# 17. Kodlanmaya Değer İnsan Mental Algoritmaları

| Mental algoritma | Kanıt türü | Makine karşılığı |
|---|---|---|
| “Önce normal nedir?” | XBOW IDOR + elite workflow | Role/ownership/tenant normal-behavior model |
| “Herkes nereye bakıyor; kimsenin bakmadığı neresi?” | Rabhi public statement | Crowding/duplicate prior + obscure-surface score |
| “Bu alan iki katmanda farklı mı yorumlanıyor?” | Orange Tsai public research | Cross-layer semantic consistency engine |
| “Zayıf primitive hangi crown-jewel’a bağlanır?” | SecurityReapers public method | ImpactGraph + asset-value + chain potential |
| “Bu başarısızlık bağlama mı özgü?” | Shubs + repo negative knowledge | Context-bound negative-knowledge ledger |
| “Yeni deployment eski varsayımı bozdu mu?” | Leaderboard economics inference | Change-triggered re-hunt and freshness |
| “Bir başkası bulgumu çürütebilir mi?” | Bugcrowd team + Glasswing peer review | Independent challenger/arbiter |
| “Bu yolun fırsat maliyeti nedir?” | Platform ranking formulas | Expected value / information gain / cost scheduler |

**Otomatikleştirilmemesi gereken davranışlar:** kapsam dışına yaratıcı biçimde taşma; program niyetini keyfi yorumlama; gerçek kullanıcı verisini gereksiz okuma; destructive impact’i ispat uğruna artırma; belirsiz durumda otomatik submission; modelin kendi bulgusunu kendi onaylaması.

# 18. Zest vs Elite İnsan Hunter

| Aşama | Elite insan üstünlüğü | Tamamlanmış Zest üstünlüğü |
|---|---|---|
| Program seçimi | Pazar sezgisi, ilişki, kalabalık bilgisi | Tüm portföyü sürekli puanlama |
| Recon | Organizasyon ve ürün anlamı | 24/7 exhaustive census, provenance |
| Surface model | Tuhaf ilişkiyi sezme | Eksiksiz graph, kimlik ve zaman matrisi |
| Hipotez | Yeni soyutlama ve analogy | Binlerce kontrollü rakip hipotez |
| Semantik | İş amacı, ownership ve “should” | Öğrenilmiş normal davranışın tutarlı uygulanması |
| Deney | Yaratıcı, az örnekli deneme | Deterministik, paralel, tekrarlanabilir experiment |
| Başarısızlık | Sezgiyle pivot | Tam negative ledger ve no-repeat |
| Chain | Narrative/business impact | Kanıt bağlı exhaustive bounded path search |
| Verification | Ambiguity judgment | Bağımsız reproduction ve exact artifact |
| Reporting | Program yöneticisiyle iletişim | Tutarlı PoC, evidence package, regression |
| Öğrenme | Zengin ama unutkan pattern library | Uzunlamasına ve çapraz-hedef ölçülebilir hafıza |

Makine insanı taklit etmek yerine unutmazlık, paralellik, cross-identity completeness, temporal diff, reproducibility ve negatif bilgi yoğunluğu üzerinden kazanmalıdır.

# 19. Engine-by-Engine Rekabet Matrisi

Hücreler **A/P** biçimindedir. RO-C = mevcut Zest; RO-F = tamamlanmış intended design. XBOW/NodeZero/Glasswing puanları yalnız kamuya açık kabiliyet ve kanıta dayanır. Elite sütunu, yeterli kamu izi bulunan üst düzey insan/takım iş akışını temsil eder; tek bir kişinin her alanda 5 olduğu iddia edilmez.

| # | Boyut | RO-C | RO-F | XBOW | NodeZero | Glasswing | Elite | Güven |
|---:|---|---:|---:|---:|---:|---:|---:|---|
| 01 | Attack-surface discovery | 3/1 | 5/0 | 5/5 | 5/5 | 1/1 | 5/5 | Yüksek |
| 02 | External recon | 3/1 | 5/0 | 5/5 | 4/5 | 1/1 | 5/5 | Yüksek |
| 03 | Web crawling | 2/1 | 4/0 | 5/5 | 4/4 | 0/0 | 4/5 | Orta-yüksek |
| 04 | SPA understanding | 2/1 | 4/0 | 4/4 | 4/4 | 0/0 | 4/4 | Orta |
| 05 | API discovery | 2/1 | 5/0 | 4/4 | 4/4 | 1/1 | 5/5 | Orta |
| 06 | JS intelligence | 1/0 | 4/0 | 4/4 | 2/2 | 1/1 | 5/5 | Orta |
| 07 | Authentication handling | 3/2 | 5/0 | 5/5 | 5/5 | 1/1 | 5/5 | Yüksek |
| 08 | Session handling | 3/2 | 5/0 | 5/5 | 5/5 | 1/1 | 5/5 | Yüksek |
| 09 | Multi-account reasoning | 3/2 | 5/0 | 5/5 | 4/5 | 1/1 | 5/5 | Orta-yüksek |
| 10 | Authorization testing | 3/2 | 5/0 | 5/5 | 5/5 | 2/2 | 5/5 | Yüksek |
| 11 | Business-logic reasoning | 2/1 | 4/0 | 4/4 | 2/3 | 1/1 | 5/5 | Orta |
| 12 | Workflow/state reasoning | 3/2 | 5/0 | 4/4 | 4/4 | 1/1 | 5/5 | Orta-yüksek |
| 13 | API object reasoning | 3/2 | 5/0 | 5/5 | 3/4 | 1/1 | 5/5 | Orta-yüksek |
| 14 | Mutation/fuzzing | 3/1 | 4/0 | 5/5 | 4/4 | 5/4 | 5/5 | Orta |
| 15 | Payload generation | 3/1 | 4/0 | 5/5 | 5/5 | 5/5 | 5/5 | Orta-yüksek |
| 16 | OAST/blind support | 3/1 | 4/0 | 5/5 | 3/4 | 2/2 | 5/5 | Orta |
| 17 | Injection coverage | 1/1 | 4/0 | 5/5 | 4/5 | 4/4 | 5/5 | Orta-yüksek |
| 18 | Cloud attack surface | 0/0 | 3/0 | 2/2 | 5/5 | 1/1 | 5/5 | Yüksek |
| 19 | Identity attack paths | 2/1 | 4/0 | 2/3 | 5/5 | 1/1 | 5/5 | Yüksek |
| 20 | Exploit chaining | 3/2 | 5/0 | 5/5 | 5/5 | 5/5 | 5/5 | Yüksek |
| 21 | Novel hypothesis generation | 1/0 | 4/0 | 5/5 | 3/3 | 5/4 | 5/5 | Orta |
| 22 | Registry-independent discovery | 1/0 | 3/0 | 5/4 | 3/3 | 5/4 | 5/5 | Orta |
| 23 | Causal reasoning | 3/2 | 4/0 | 4/4 | 4/5 | 5/4 | 5/5 | Orta |
| 24 | Differential reasoning | 4/3 | 5/0 | 5/5 | 4/5 | 5/4 | 5/5 | Yüksek |
| 25 | Negative controls | 4/3 | 5/0 | 5/5 | 5/5 | 5/5 | 5/5 | Yüksek |
| 26 | False-positive control | 4/3 | 5/0 | 5/5 | 5/5 | 4/4 | 5/5 | Yüksek |
| 27 | Independent reproduction | 4/3 | 5/0 | 5/5 | 5/5 | 5/5 | 5/5 | Yüksek |
| 28 | Evidence provenance | 4/3 | 5/0 | 3/4 | 5/5 | 3/3 | 3/5 | Orta-yüksek |
| 29 | Scope discipline | 4/3 | 5/0 | 5/5 | 5/5 | 4/4 | 5/5 | Yüksek |
| 30 | Program-policy awareness | 4/3 | 5/0 | 5/5 | 2/3 | 1/1 | 5/5 | Yüksek |
| 31 | Rate limiting | 3/2 | 5/0 | 5/5 | 5/5 | 3/3 | 4/5 | Orta |
| 32 | Cost control | 4/3 | 5/0 | 5/5 | 4/4 | 3/3 | 3/5 | Orta |
| 33 | Model routing | 3/2 | 4/0 | 5/4 | 4/4 | 4/4 | 2/2 | Düşük-orta |
| 34 | Memory/learning | 4/3 | 5/0 | 5/4 | 5/5 | 4/4 | 4/5 | Orta |
| 35 | Negative knowledge | 4/3 | 5/0 | 5/4 | 4/4 | 4/4 | 4/5 | Orta |
| 36 | Temporal intelligence | 3/2 | 5/0 | 5/5 | 5/5 | 4/4 | 5/5 | Orta-yüksek |
| 37 | Continuous monitoring | 1/0 | 5/0 | 5/5 | 5/5 | 4/4 | 3/5 | Yüksek |
| 38 | Change detection | 3/2 | 5/0 | 5/5 | 5/5 | 4/4 | 5/5 | Orta-yüksek |
| 39 | Coverage measurement | 4/3 | 5/0 | 4/4 | 5/5 | 3/3 | 2/4 | Orta |
| 40 | Scheduler intelligence | 3/2 | 4/0 | 5/5 | 5/5 | 4/4 | 5/5 | Orta |
| 41 | Exploration/exploitation | 3/2 | 4/0 | 5/5 | 4/5 | 5/4 | 5/5 | Orta |
| 42 | Source-assisted research | 1/0 | 4/0 | 4/4 | 1/1 | 5/4 | 4/5 | Yüksek |
| 43 | Static analysis | 0/0 | 3/0 | 4/4 | 1/1 | 5/4 | 4/5 | Orta |
| 44 | AST/dataflow | 0/0 | 4/0 | 4/4 | 0/0 | 5/4 | 4/5 | Orta |
| 45 | Binary analysis | 0/0 | 2/0 | 2/2 | 1/1 | 5/4 | 5/5 | Orta-yüksek |
| 46 | Mobile/APK research | 0/0 | 3/0 | 2/2 | 1/1 | 3/3 | 5/5 | Orta |
| 47 | Protocol specialists | 1/0 | 4/0 | 4/4 | 5/5 | 5/4 | 5/5 | Orta |
| 48 | Reporting | 1/0 | 5/0 | 5/5 | 5/5 | 3/3 | 5/5 | Yüksek |
| 49 | Duplicate avoidance | 1/0 | 4/0 | 5/5 | 1/2 | 1/1 | 5/5 | Orta-yüksek |
| 50 | Human review | 3/2 | 5/0 | 4/5 | 4/5 | 4/4 | 5/5 | Yüksek |
| 51 | Autonomous operation | 3/2 | 5/0 | 5/5 | 5/5 | 5/4 | 0/0 | Yüksek |
| 52 | Scale | 2/1 | 4/0 | 5/5 | 5/5 | 5/4 | 2/5 | Orta-yüksek |
| 53 | Cost efficiency | 3/1 | 4/0 | 5/4 | 5/4 | 4/3 | 2/4 | Orta |
| 54 | Real-world validation | 1/0 | 4/0 | 5/5 | 5/5 | 5/4 | 5/5 | Yüksek |
| 55 | Bug bounty evidence | 0/0 | 0/0 | 5/5 | 1/1 | 1/1 | 5/5 | Yüksek |

**Okuma notu:** RO-F sütunundaki 4 veya 5, “tasarım tamamlanırsa mimari olarak mümkün” demektir; saha kanıtı olmadığı için P çoğunlukla 0’dır. Bu tablo güzellik yarışması değil, belirsizlikleri görünür kılan karar aracı olmalıdır.

# 20. Vulnerability-Class Coverage Matrisi

| Sınıf | RO mevcut | RO nihai | Kamu lideri / referans | Kritik not |
|---|---|---|---|---|
| BOLA/IDOR/BFLA/BOPLA | Object authorization family + identity diff | Ownership/role/property semantic model | XBOW + elite humans | Normal davranış öğrenilmeden false positive/negative riski |
| Auth/session | Session binding, local labs | OAuth/OIDC/SAML/MFA/device flows | XBOW/NodeZero | Gerçek IdP ve cross-domain session eksik |
| Workflow/business logic | Workflow family, invariant/diff | Long async workflow + semantic intent | Elite humans | İnsan hâlâ açık ara güçlü |
| XSS/DOM | Genel browser/mutation altyapısı | DOM sink/source + browser validator | XBOW/Strix | Dedicated DOM/JS IR yok |
| SQL/NoSQL/command/SSTI | Seed family değil | Injection specialist lane | XBOW/Strix | Mevcut derinlik düşük |
| SSRF/XXE/blind RCE | OAST loopback/mutation | Multi-protocol OAST + chain | XBOW | Production callback service yok |
| Request smuggling/desync | Yok | Protocol specialist | Elite/özel araştırma | HTTP parser differential gerekir |
| Cache poisoning/deception | Yok | Cache/proxy semantic specialist | XBOW/humans | Layered normalization model gerekir |
| Race/concurrency | Temel temporal/workflow kavramı | Distributed barrier/retry/probabilistic proof | Glasswing/humans | Deterministic single replay yetmez |
| File upload/path traversal | Mutation adayları | File/polyglot/parser pipeline | XBOW/Glasswing | Content transform chain gerekir |
| GraphQL/gRPC/WebSocket/SOAP | API_SPEC node sınırlı | Stateful protocol adapters | NodeZero WebApp/elite | Graph and subscription semantics yok |
| JWT/OAuth/OIDC/SAML | Session modeli genel | Dedicated identity protocol state machines | NodeZero/humans | Token audience/issuer/delegation semantics gerekli |
| Cloud/IAM/Kubernetes | Yok | Ayrı cloud/identity lane | NodeZero/Pentera | Web family registry’ye zorlanmamalı |
| Mobile/APK | Yok | MobSF/Frida destekli mobile lane | Elite mobile hunters | Binary/client ↔ backend ilişki modeli gerekli |
| Source memory safety | Yok | AST/CFG/dataflow/fuzz/runtime | Glasswing | App black-box hattından ayrı capability |
| Binary exploitation | Yok | Ghidra/angr/harness/exploit eval | Glasswing | P2 uzmanlaşma; web hedefinden önce değil |

# 21. Recon Kabiliyet Matrisi

| Recon işi | RO mevcut | Eksik semantik | Önerilen biçim |
|---|---|---|---|
| DNS/subdomain | Sensor fixture protokolü | Canlı provider güveni, wildcard/poison kontrolü | Integrate + normalize |
| CT/certificate | G2 sensörü + graph | Issuer/history/hostname cluster | Native temporal projection |
| Archive | G2 sensörü | Route/parameter diff ve deleted feature memory | Native change intelligence |
| HTTP reachability/tech | Fingerprint sensörü | HTTPS/WAF/redirect/auth form/clone group | httpx benzeri entegrasyon + native facts |
| Crawling | Browser çekirdeği | SPA state graph, forms, hidden routes | Katana/Playwright entegrasyonu + native graph |
| JS intelligence | JS_BUNDLE node | AST, source maps, endpoint/secret/schema, dynamic import | Native JS IR + Semgrep/CodeQL adapter |
| API | API_SPEC node | Schema inference, object/action/property model | OpenAPI/Postman ingest + runtime inference |
| Mobile | Yok | APK/IPA routes, deep links, cert pinning, backend map | MobSF/Frida integration, native relation graph |
| Cloud | Yok | Asset/account/IAM/trust/crown-jewel | Ayrı provider adapters ve identity graph |
| Clone/dedup | Yok | Same-code/same-UI/same-backend cluster | SimHash/imagehash/TLS/API similarity |
| Change monitoring | Temporal model | Deploy signal, newly exposed asset, policy change | Event-driven re-census/re-hunt |

ProjectDiscovery’nin [Subfinder/httpx/Katana araçları](https://docs.projectdiscovery.io/opensource) bu satırların hazır primitives’idir. Zest bunların tümünü yeniden yazmamalı; güvenilmeyen çıktıyı kendi Observation/Artifact şemasına almalı, scope ve provenance’ı dışarı bırakmamalıdır.

# 22. Business Logic / Semantik İstihbarat Karşılaştırması

Zest’un Invariant, Differential, Target/Causal Model ve identity matrix’i güçlü bir iskelet sunuyor. Fakat business logic için üç ayrı anlam katmanı eksik:

1. **Ontoloji:** User, owner, delegate, approver, tenant, resource, entitlement, monetary value, irreversible action nedir?
2. **Normatif beklenti:** Kim, hangi state’te, hangi action’ı hangi koşulda yapabilmelidir?
3. **İş amacı:** Teknik olarak izin verilen davranış ürünün ekonomik/güvenlik niyetine aykırı mı?

XBOW’un IDOR doğruluğu için “normal” rol davranışı öğrenmeye geçmesi bu eksikliğin saha kanıtıdır. SecurityReapers threat modeling ve impact’i; rabhi gizli JS parametrelerini; elite insanlar organizasyon anlatısını kullanır. Tamamlanmış Zest’un yalnız response diff ve counterexample ile yetinmesi, semantik olmayan bir anomaly scanner’a dönüşme riski taşır.

Önerilen organ: **Semantic World Model**. Bu model iddia üretir, yetki vermez; evidence değildir. Observed UI/API/source behavior’dan role, ownership, workflow precondition ve value-flow ilişkileri çıkarır; belirsizlik taşır; her normatif iddia karşı-olgusal runtime experiment ile test edilir.

# 23. Otonom Akıl Yürütme Karşılaştırması

| Özellik | Zest | XBOW | Glasswing swarm | İnsan |
|---|---|---|---|---|
| Global state | PostgreSQL/graph/ledger | Persistent coordinator [beyan] | Forum + per-agent VM | Zihinsel/notlar |
| Yerel görev | Bounded experiment | Kısa ömürlü narrow agent | Ajanın seçtiği proje/alan | Esnek |
| Challenge | Generator/Falsifier | Ayrı validator | Peer review + arbiter | Takım arkadaşı |
| Otorite | Core, model dışı | Safety checker/network scope [beyan] | Program safeguards | Etik/program kuralları |
| Çeşitlilik | Model routing planı | Model alloys [beyan] | Aynı modelde conformity gözlendi | Doğal deneyim çeşitliliği |
| Recovery | Durable attempts/negative knowledge | Fresh agent yeniden başlar | Paralel/koordineli yeniden dağılım | Sezgi |
| Terfi | Explicit admission/verification/human | Validator + human submission review | Arbiter/disclosure | Triage |

Zest’un teorik avantajı, her experiment’in Core’a yeniden girmesi ve state’in model context’inden bağımsız olmasıdır. Dezavantajı, çok küçük bounded experiment’lerin büyük causal narrative’i parçalamasıdır. Çözüm scope’u gevşetmek değil; coordinator’ın plan graph’ını durable tutması, lokal ajanlara minimal sufficient context vermesi ve cross-experiment hypothesis identity’yi korumasıdır.

# 24. Yeni Vulnerability Discovery Karşılaştırması

## Açık cevap

**Bugünkü Zest:** Registry’de tanımlanmamış yeni sınıfı güvenilir biçimde keşfettiğine dair kanıt yoktur. Beş seed HunterFamily ve mevcut deterministic mutation, bilinen sınıfları sistematik araştırır.

**Tamamlanmış mevcut yol haritası:** G16, graph + temporal anomaly’den registry dışı hipotez üretip insan onayıyla yeni family’ye dönüşmeyi amaçlar. Bu, registry tunnel vision’ını azaltır; ancak tek başına “yeni vulnerability class” kabiliyeti için yeterli değildir.

## Eksik bilişsel mimari

1. Normal-behavior ve business-intent öğrenimi.
2. Cross-layer semantic inconsistency detection.
3. Mechanism induction: gözlemlerden yeni causal primitive soyutlama.
4. Counterfactual experiment synthesis.
5. Novelty search ve archive/peer similarity ile “daha önce görülmedi” kalibrasyonu.
6. Diverse proposer ensembles: farklı model, araç ve representation.
7. Validator escape analysis: verifier’ın bilmediği şekillerde gerçek bug üretebilen yollar.
8. Human family curation: model registry’ye doğrudan yazmaz.

XBOW’un production diversity’si ve Glasswing’in kaynak/binary araması, novelty’nin benchmark family listesinden değil, çok zengin hedef context’i ve başarısızlık geri beslemesinden geldiğini gösterir.

## Test edilmesi gereken hipotez

G16 için başarı metriği “registry dışı hipotez sayısı” değil:

- Human reviewer tarafından yeni mekanizma olarak kabul oranı,
- Bilinen family’ye sonradan map edilemeyen valid finding oranı,
- Hidden target’ta novelty-adjusted precision,
- Aynı anomaly’den farklı causal explanation çeşitliliği,
- False family creation ve duplicate family oranı olmalıdır.

# 25. Exploit Chaining Karşılaştırması

Zest ImpactGraph’ın kanıtsız edge reddi, “sequence ≠ causality” kuralı ve cross-run kilidi güçlüdür. XBOW’un 48-step SSRF → GDAL/VRT → pixel exfiltration zinciri yaratıcı execution derinliği; NodeZero credential/identity path’i enterprise graph derinliği; Glasswing dört-vulnerability browser chain’i source/binary derinliği gösterir.

Eksik olan yalnız graph search değildir:

- chain primitive’in precondition/postcondition standardı,
- side-effect ve irreversibility maliyeti,
- session/identity/host boundary transferi,
- partial proof ve temporal validity window,
- business impact goal state,
- chain minimization ve safe reproduction,
- aynı primitive’in farklı protokol executor’ları arasında taşınması.

Zest’un doğru yönü proof-backed chain’dir. Yanlış yön, chain score’u yalnız uzunluk veya severity toplamına indirgemektir.

# 26. Validation / False Positive Karşılaştırması

Zest’un epistemik pipeline’ı tasarımsal olarak en güçlü yanıdır. XBOW discovery/validator ayrımı ve browser XSS check’i bunun saha eşidir. NodeZero gerçek path’i çalıştırır; Shannon yalnız working PoC raporlamayı hedefler fakat kendi safety dokümanı hâlâ insan review gerektiğini söyler.

“false_finding = 0” tek başına sağlıklı hedef değildir. Dört metrik birlikte izlenmelidir:

1. Submission precision / program triage acceptance.
2. Validator false-negative / escape rate.
3. Reproduction stability ve environment sensitivity.
4. Human rejection reason distribution.

Rare race/temporal bug için byte-identical replay şartı yanlış olabilir. Bağımsız verification; eşdeğer causal effect, belirli zaman penceresi, N-deneme başarı aralığı ve negative control ile probabilistic kanıt kabul edebilmelidir. Otorite zayıflatılmaz; kanıt semantiği genişletilir.

# 27. Continuous Hunting Karşılaştırması

XBOW ve enterprise platformlar sürekli çalışmayı ürün iddiası/operasyon sonucu olarak sunar. NodeZero Insights campaign ve remediation trend’i; Pentera re-test; Terra code-change-aligned continuous test vurgular. Zest temporal model ve change event’e sahip olsa da uçtan uca always-on program/run/review/report operasyonu saha kanıtına ulaşmamıştır.

Continuous hunting için gerekli event contract:

- Yeni hostname/cert/service/route/schema,
- JS bundle/source diff,
- policy/scope değişimi,
- auth/role/entitlement değişimi,
- deployment fingerprint,
- yeni public CVE/research primitive,
- önceki negative knowledge’ı geçersiz kılan environment change,
- remediation ve regression,
- leaderboard/program launch sinyali.

Her event tüm taramayı başlatmamalı; hangi invariant/hypothesis/coverage cell’in stale olduğunu deterministik olarak işaretlemelidir.

# 28. White-Box / Source-Assisted Karşılaştırma

Önerilen zincir:

Source snapshot → language frontend → AST/CFG/DFG/CPG → route/controller/middleware/ORM/UI map → source hypothesis → runtime target binding → controlled experiment → independent verification → evidence.

Temel ilke: **SOURCE SUGGESTS. RUNTIME PROVES.**

CodeQL global dataflow, olası source-to-sink akışını modeller: [CodeQL Data Flow](https://codeql.github.com/docs/writing-codeql-queries/about-data-flow-analysis/). Joern AST, control-flow ve data-flow’u Code Property Graph’ta birleştirir: [Joern CPG](https://docs.joern.io/code-property-graph/). Shannon source analysis’i browser/CLI exploit ile doğrular; Glasswing source/binary üzerinde çok daha yüksek novelty hedefler.

Zest için rekabet avantajı, statik bulguyu doğrudan Candidate yapmamak olacaktır. Source claim yalnız HYPOTHESIZED/INFERRED Target Model düğümü üretmeli; route ve deployment fingerprint ile runtime karşılığı bağlanmadan Evidence’a terfi etmemelidir.

Bu hat büyük moat olabilir; çünkü source provenance + runtime proof + negative path + human disposition birleşimi zamanla benzersiz bir causal verification corpus üretir.

# 29. Operasyonel Ölçek Karşılaştırması

| Ölçek konusu | Zest mevcut | Rakip kanıtı | Gerekli eşik |
|---|---|---|---|
| Parallel target | Worker topology tasarımı | XBOW binlerce app/agent [beyan] | 100+ program, backpressure, isolation |
| Durable state | PostgreSQL/ledgers güçlü | NodeZero campaigns, XBOW coordinator | Multi-month retention ve replay |
| Tool isolation | Process/OS containment | Strix/Shannon Docker; Glasswing VM/agent | Per-experiment capability token |
| Browser fleet | Yerel Playwright | XBOW/NodeZero/Terra production browser | HTTPS, anti-bot, deterministic profile |
| OAST | Loopback | Interactsh/XBOW production blind tests | HA callback, correlation, abuse control |
| Human queue | Minimal CLI/review | XBOW pre-submit; Terra on-loop | SLA, sampling, escalation, batch review |
| Data volume | Zengin record/table/migrations | Kapalı | Partition/retention/projection benchmark |
| Recovery | Attempts/audit | XBOW fresh agents, NodeZero auto-heal | Crash-safe resume, idempotent side effects |

Bugünkü sistemde “geniş domain model” ile “dar saha executor” arasında dengesizlik vardır. Veri modelinin olgunluğu, HTTPS/SPA/protocol execution ve operator throughput’tan öndedir.

# 30. Maliyet / Model Ekonomisi Karşılaştırması

Zest G4’te fail-closed price table, daily budget ve escalation kurdu. Bu doğru temel; fakat “token ucuzluğu” bug bounty ekonomisi değildir.

Gerçek objective çok amaçlıdır:

Expected utility = valid finding olasılığı × impact/bounty/değer × novelty × time advantage − model/tool/infra maliyeti − duplicate riski − program/policy riski − human review maliyeti.

Bu tek sihirli scalar’a indirgenmemelidir. Pareto frontier ve hard constraints kullanılmalıdır. XBOW target selector’ın WAF/auth/endpoint/technology ve clone grouping sinyalleri; HackerOne Reputation × Signal × Impact formülü; Bugcrowd accuracy/consistency badge’i bu objective’in dış kanıtlarıdır.

Model routing önerisi:

- Ucuz model: parsing, normalization, broad candidate generation.
- Farklı sağlayıcı/representation: challenger ve semantic critic.
- Pahalı model: yüksek expected information gain veya V3.
- Deterministik kod: scope, schema, validator, exact diff.
- Local model: hassas source veya yüksek hacimli düşük risk.
- “Content policy blocked” sonucu başka modele kaçma nedeni değildir; policy outcome olarak kaydedilir.

# 31. Zest Gizli Avantajları

1. **Epistemik tür sistemi:** Observation/Evidence/Finding ayrımı, LLM plausibility’sini gerçeklikten ayırır.
2. **Core authority separation:** Scope, budget, identity ve side effect modelin elinde değildir.
3. **Untrusted worker boundary:** Tool output, prompt injection ve parser hatası gerçeğe dönüşmez.
4. **Append-only reasoning/admission ledger:** Sonradan “neden inandık?” sorusu cevaplanabilir.
5. **Independent verification + human acceptance:** Discovery ile karar rolü ayrıdır.
6. **Coverage Debt:** “Neyi test etmedik?” first-class state’tir; çoğu agent yalnız bulunan şeyleri gösterir.
7. **Context-bound negative knowledge:** Global “yok” genellemesi yerine koşullu başarısızlık taşır.
8. **Scope-aware autonomy:** Controller özgürce hareket etse de her experiment yetki kapısından geçer.
9. **Proof-backed ImpactGraph:** Narrative chain’in kanıtsız şişmesini engeller.
10. **Benchmark bilinci:** Visible/hidden split, holdout ve truth-blind düşüncesi depoda anayasal seviyededir.

Bu avantajlar yalnız tasarım övgüsü değildir; Zest’un [domain modeli](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/DOMAIN_MODEL.md) ve [teknik kararlarında](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/TECHNICAL_DECISIONS.md) açık kontratlardır.

# 32. Zest Gizli Zayıflıkları

## 32.1 HunterScore exploit lock-in riski

[OLGU] State ağırlığı UNTESTED 50’den V3_QUEUED 10’a iner. Family bonus 10 × (supported − falsified) ve sınırlandırılmamıştır. Altı net destek +60 verir; tüm coverage state farkını ezer: [score.py](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/src/zest/research/scheduler/score.py).

[ÇIKARIM, yüksek güven] Geçmişte kolay valid üreten family sürekli üste çıkar; az denenmiş novel family compute alamaz. Coverage quantity, bilgi getirisi ve business value’nun önüne geçebilir.

## 32.2 Tazelik semantik hatası

[OLGU] Freshness map, node provenance’ındaki **en erken** sensor observation’ı seçer. Score bunu first_seen_age_hours olarak kullanır: [run_hunt_scheduler.py](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/src/zest/application/run_hunt_scheduler.py).

[ÇIKARIM, yüksek güven] Yıllardır var olan node bugün değişse bile “eski” görünür. Yeni deployment ve yeni evidence için latest_changed_at/separate change freshness gerekir; first_seen korunmalı ama hunt freshness değildir.

## 32.3 Scheduler eksik objective’leri

Asset value, program reward, duplicate probability, target crowding, novelty, expected information gain, validator cost, safety risk, chain potential ve human queue yükü yoktur. Budget yalnız tamamen tükendiğinde V3’e −20, ucuz yola +5 uygular.

## 32.4 Registry tünel görüşü

Beş seed family başlangıç için doğru; ancak Coverage Debt node × identity × family olduğu için sistem “registry’nin dışında neyi bilmediğini” ölçmez. G16 bu boşluğu tanır fakat cognitive mechanism henüz kavramsaldır.

## 32.5 False-negative körlüğü

“Zero false finding” hedefi precision’ı yükseltirken rare temporal, anti-bot, one-shot ve environment-sensitive bug’ları öldürebilir. Validator kaçış benchmark’ı yoksa sistem kendi körlüğünü başarı sanır.

## 32.6 Context fragmentation

Her experiment’in yeniden Core’a girmesi güvenlidir; fakat semantik narrative parçalanırsa model, uzun workflow ve chain’de neden denediğini kaybedebilir. Durable hypothesis/plan graph ve context pack kalite metriği gerekir.

## 32.7 Executor derinliği

Mevcut HTTP/browser laboratuvar odaklıdır; geniş HTTPS, gerçek SSO, anti-bot, browser fingerprint, WebSocket/gRPC/GraphQL subscription, email/DNS callback, mobile ve cloud execution saha kanıtı yoktur.

## 32.8 Tasarım–saha dengesizliği

Çok zengin kayıt/migrasyon/ledger katmanı birkaç günde hızla büyümüştür; production latency, partitioning, data retention, re-projection ve operator ergonomisi kanıtlanmadan soyutlama borcu oluşabilir.

## 32.9 Human review darboğazı

Evidence rigor arttıkça reviewer başına paket boyutu büyür. Relevance, replay, causal summary, privacy redaction ve risk-based sampling yoksa güvenli insan kapısı throughput’u durdurur.

## 32.10 Doküman drift’i

Eski anayasal/planning cümleleri mevcut kodu yanlış tarif ediyor. Bu yalnız estetik sorun değil; ajanlar ve insanlar yanlış maturity durumunu öğrenebilir. Çözüm kök dokümanı sessizce “düzeltmek” değil, generated current-state manifest ve tarih damgalı decision supersession’dır.

# 33. Eksik Organlar

| Organ | Neden organ düzeyinde | Öncelik |
|---|---|---|
| Semantic World Model | Business intent, role, ownership, tenant ve value-flow olmadan novelty yüzeysel kalır | P1 |
| Portfolio Intelligence | Program/asset/time/duplicate/bounty ekonomisini yönetir | P1 |
| Open-Ended Discovery Lab | G16 için mechanism induction, counterfactual synthesis, diversity search | P1 |
| Source Causal IR | AST/CFG/DFG/route/auth/ORM/UI ilişkisini runtime’a bağlar | P1 |
| Production Executor Fabric | HTTPS/browser/API/protocol/OAST/race workers’ı capability token ile ölçekler | P1 |
| Validator Science | Precision kadar FN/escape/calibration ve probabilistic verification | P1 |
| Change Intelligence Bus | Event’i stale hypothesis/coverage cell’e bağlar | P1 |
| Field Evidence Program | Yetkili saha, bug bounty, human disposition, safety ve cost metrics | P1 |
| Report/Duplicate Operations | Submission-ready paket, duplicate prior, reviewer SLA | P1 |
| Cloud/Identity Graph | IAM/trust/credential/K8s chain | P2 |
| Mobile/Binary Labs | APK/IPA/native ve compiled target uzmanlığı | P2 |

# 34. Aşırı Mühendislik Riski Taşıyan Alanlar

1. **Şema ve ledger genişliği:** Saha query pattern’leri oluşmadan her kavramı kalıcı tabloya çevirmek migration/retention yükü yaratabilir.
2. **Gate çoğalması:** Gate yalnız ölçülebilir risk azaltıyorsa değerlidir. Benzer property’yi tekrar sınayan gate’ler merge/experiment hızını düşürür.
3. **Deterministik her şey eğilimi:** Determinism replay ve authority için şarttır; novelty generation için çeşitlilik ve stochastic search kontrollü biçimde gerekir.
4. **Graf her probleme çözüm değildir:** Runtime target graph, code CPG ve enterprise identity graph aynı şemaya zorlanmamalı; typed bridges kullanılmalıdır.
5. **Family ayrıntısı:** Her payload veya CWE’yi HunterFamily yapmak registry explosion üretir. Family mekanizma/claim/evidence seviyesinde kalmalıdır.
6. **Mükemmel provenance’ın marjinal maliyeti:** Her ara tokenı saklamak yerine karar, tool action, normalized observation, admission ve hash lineage saklanmalıdır.

Kaldırılması gereken çekirdek yoktur. Aşırı mühendislik çözümü otoriteyi veya evidence gate’i azaltmak değil; representation’ı ölçülebilir query/use-case ile gerekçelendirmektir.

# 35. Competitive Moat Analizi

| Moat | Savunulabilirlik | Neden |
|---|---|---|
| Field-validated causal trace corpus | Çok yüksek | Target davranışı + action + evidence + disposition rakibin satın alamayacağı veri |
| Context-bound negative knowledge | Yüksek | Ne denendi/ne zaman/niçin başarısız eşsiz compute tasarrufu |
| Human disposition + validator escape corpus | Yüksek | Precision ve recall’ı birlikte geliştirir |
| Longitudinal target semantic memory | Yüksek | Yıllar içindeki role/workflow/change ilişkileri |
| Private truth-blind benchmark suite | Orta-yüksek | Contamination-resistant gelişim ölçümü |
| HunterFamily/evidence contract library | Orta | Kopyalanabilir; saha istatistiği eklenince güçlenir |
| Scope/policy architecture | Orta | İyi tasarım kopyalanabilir fakat güvenilir operasyon kaydı zaman alır |
| Model/prompts | Düşük | Sağlayıcı ve model ilerlemesiyle hızla emtia |
| Tool wrapper koleksiyonu | Çok düşük | Kolay kopyalanır ve bakım borcu yaratır |

En iyi moat “en iyi prompt” değil, **kanıtlanmış araştırma hafızasıdır**.

# 36. İçe Aktarılmaya Değer Dış Fikirler

| Kaynak fikir | Neden önemli | RO karşılığı/gap | Eylem |
|---|---|---|---|
| XBOW target scoring + clone dedup | Compute ve duplicate ekonomisi | Scheduler’da yok | Portfolio Intelligence native |
| XBOW fresh narrow agents | Context rot ve dead-end azaltma | Bounded experiment var, variance yok | Diverse ephemeral researcher pool |
| XBOW separate validators | Discovery ≠ truth | Zaten güçlü | Validator diversity/escape ile derinleştir |
| NodeZero perspective/scope | Başlangıç konumu path’i değiştirir | Scope var, perspective az | Scope modeline vantage point |
| NodeZero identity/credential graph | Enterprise chain | Yok | P2 ayrı typed graph |
| Terra Human-on-the-Loop steering | Güvenli high-risk decision | Human review son aşamada | Ara experiment steering/approval |
| Glasswing peer review + arbiter | Tamamlayıcı search | Generator/Falsifier var | Model/representation diversity |
| Glasswing exploit ladder | Binary success’ten zengin ölçüm | Benchmarks var | Capability/proof ladder |
| Shannon source→runtime PoC | Static sinyali doğrular | Future lane | Source Causal IR |
| Incalmo expert services | LLM düşük seviye komutta kaybolmaz | Workers var | Typed high-level security actions |
| Orange cross-layer confusion | Yeni mekanizma keşfi | Differential var, semantic layer yok | Cross-layer meaning consistency |
| Rabhi off-path JS parametre | Duplicate avoidance ve novelty | JS IR yok | Hidden surface/obscurity prior |
| Bugcrowd accuracy reputation | Human queue güveni | Finding review var | Researcher/validator calibration |
| PrimeVul temporal split | Leakage’i azaltır | Hidden split prensibi var | Strict chronological/dedup benchmark |

# 37. Zest’un Reddetmesi Gereken Dış Fikirler

1. LLM çıktısını doğrudan finding veya evidence saymak.
2. Modelin scope/policy/rate/budget’i kendisinin yorumlayıp genişletmesi.
3. Tek büyük ajan ve tek sınırsız context ile bütün engagement’ı yürütmek.
4. Aynı modelin discovery ve validation’ı kendi kendine onaylaması.
5. Nuclei/Burp/ProjectDiscovery/ffuf/sqlmap araçlarını kontrolsüz wrapper zoo’ya çevirmek.
6. Sadece CWE/OWASP coverage sayısını araştırma kalitesi sanmak.
7. “Zero false positive” uğruna validator false-negative ölçmemek.
8. Public benchmark skorunu real-world novelty kanıtı saymak.
9. Model sayısını epistemik çeşitlilik sanmak.
10. Scope güvenliği gerekçesiyle insanı tamamen kaldırmak veya insan var diye ağ/policy guardrail’ını gevşetmek.

# 38. Önceliklendirilmiş Gap Yol Haritası

## Öncelik hükmü

**P0 saptanmadı.** Core authority, worker isolation ve evidence promotion zincirinde sistemi baştan tasarlatacak bir mimari engel görülmedi. Aşağıdaki P1’ler, sistemi “iyi kontrollü araştırma altyapısı”ndan “saha rekabetçisi otonom araştırmacı”ya taşıyan kabiliyetlerdir.

## GAP-P1-01 — Semantic World Model

- **Gap:** Role, ownership, tenant, entitlement, asset value, business intent ve normal-behavior modeli.
- **Evidence:** XBOW IDOR doğruluğu için normal rol davranışını öğrenme; SecurityReapers threat modeling; rabhi’nin görünmeyen işlev/parametre araması.
- **Competitor/hunter:** XBOW, SecurityReapers, elite human hunters.
- **RO current equivalent:** Target Model, Differential Engine, Invariant Engine, ProgramResearchContext.
- **Final-plan equivalent:** G16 ve business-logic/workflow hunter’ları.
- **Neden yetersiz:** Davranış farkını görür ama “kim neden bunu yapabilmeli?” normunu açıkça temsil etmez.
- **Recommended subsystem:** Research Brain altında native Semantic World Model; Data’da uncertain typed relations.
- **Dependencies:** G1 scope, G3 graph, identity/session, temporal snapshots.
- **Expected benefit:** BOLA/BFLA/BOPLA ve target-specific logic için daha yüksek precision/novelty.
- **Complexity:** Çok yüksek.
- **Priority:** P1.
- **Validation:** Hidden çok-tenant business-flow suite; ownership/role norm doğruluğu; unseen workflow valid finding precision; human semantic-disagreement rate.

## GAP-P1-02 — Production Executor Fabric

- **Gap:** Gerçek HTTPS, SPA, browser profile, SSO, API ve stateful protocol execution.
- **Evidence:** XBOW ve NodeZero production browser/auth; Strix proxy/browser/API contract; Shannon live exploitation.
- **Competitor/hunter:** XBOW, NodeZero WebApp, Strix, Shannon.
- **RO current equivalent:** HTTP/Browser workers, Playwright, process/cgroup containment, authorized envelope.
- **Final-plan equivalent:** Specialist HTTP/browser/API/protocol lanes.
- **Neden yetersiz:** Yerel lab başarıları production anti-bot, cross-domain auth, asynchronous flow ve protocol state’i kanıtlamaz.
- **Recommended subsystem:** Platform/Worker Runtime altında capability-token’lı executor fabric.
- **Dependencies:** Core authorization, SecretRef, session ledger, rate limiter, artifact store.
- **Expected benefit:** Lab–saha boşluğunu en hızlı kapatan kabiliyet.
- **Complexity:** Çok yüksek.
- **Priority:** P1.
- **Validation:** Yetkili staging’de HTTPS/SPA/OAuth/GraphQL/WebSocket suite; scope-escape sıfır; deterministic replay; browser fleet reliability.

## GAP-P1-03 — G16 Open-Ended Discovery Architecture

- **Gap:** Registry dışı mechanism induction, counterfactual synthesis ve diversity search.
- **Evidence:** Glasswing coordinated/independent aramanın tamamlayıcılığı; Orange semantic confusion; XBOW production novelty.
- **Competitor/hunter:** Glasswing, XBOW, Orange Tsai.
- **RO current equivalent:** Generator/Falsifier, anomaly substrate, G16 planı.
- **Final-plan equivalent:** Registry-external exploratory hypothesis.
- **Neden yetersiz:** “Anomali → hipotez” yeni mechanism/family üretiminin nasıl kalibre edileceğini açıklamaz.
- **Recommended subsystem:** Research altında Open-Ended Discovery Lab; human-curated Family Proposal.
- **Dependencies:** Semantic World Model, Target/Causal Model, benchmark harness, diverse model routing.
- **Expected benefit:** Sophisticated scanner tünelinden çıkış.
- **Complexity:** Araştırma düzeyinde çok yüksek.
- **Priority:** P1.
- **Validation:** Unseen mechanism benchmark; registry-external valid finding; family novelty/duplicate/rejection ölçümleri.

## GAP-P1-04 — Portfolio Intelligence ve HunterScore v2

- **Gap:** Asset value, information gain, novelty, duplicate/crowding, time advantage, cost ve safety Pareto planlama.
- **Evidence:** XBOW target signals/clone dedup; HackerOne ranking formülü; Bugcrowd accuracy badge; YesWeHack timing.
- **Competitor/hunter:** XBOW ve leaderboard liderleri.
- **RO current equivalent:** Coverage Debt + state/family/freshness/budget HunterScore.
- **Final-plan equivalent:** Exploration/exploitation scheduler ve duplicate economics.
- **Neden yetersiz:** Unbounded family bonus lock-in yaratır; first_seen değişim tazeliği değildir; program ekonomisi yoktur.
- **Recommended subsystem:** Strategic Intelligence altında portfolio planner; scalar yerine hard filters + Pareto frontier.
- **Dependencies:** Change events, program metadata, human dispositions, target similarity.
- **Expected benefit:** Aynı compute ile daha yüksek valid-impact ve daha az duplicate.
- **Complexity:** Yüksek.
- **Priority:** P1.
- **Validation:** Historical off-policy replay; shadow scheduler A/B; novelty-adjusted valid value per dollar/hour; starvation tests.

## GAP-P1-05 — Source Causal IR

- **Gap:** AST/CFG/DFG/CPG, route/controller/middleware/ORM/UI map ve runtime binding.
- **Evidence:** Glasswing, Shannon, CodeQL, Joern.
- **Competitor/hunter:** Glasswing/Mythos ve Shannon.
- **RO current equivalent:** Source-assisted karar prensibi; uygulanmış IR yok.
- **Final-plan equivalent:** Authorized source lane.
- **Neden yetersiz:** Source claim’in hangi deployed route ve runtime behavior’a ait olduğu modellenmiyor.
- **Recommended subsystem:** Ayrı Source Intelligence plane + typed bridges to Target Model.
- **Dependencies:** Repo snapshot provenance, build/deploy fingerprint, language frontends, worker isolation.
- **Expected benefit:** Derin auth/dataflow/variant açıkları; güçlü causal moat.
- **Complexity:** Çok yüksek.
- **Priority:** P1.
- **Validation:** Source-suggested/runtime-proven precision; interprocedural hidden cases; deployment mismatch rejection; novel variant yield.

## GAP-P1-06 — Validator Science ve Recall

- **Gap:** Validator false-negative, escape, calibration ve probabilistic verification ölçümü.
- **Evidence:** XBOW ayrı validator’ları; Shannon’ın PoC’ye rağmen insan review uyarısı; rare race/timing doğası.
- **Competitor/hunter:** XBOW, Shannon, Glasswing exploit graders.
- **RO current equivalent:** Independent Verification, strict evidence, human review.
- **Final-plan equivalent:** False-finding=0 disiplini.
- **Neden yetersiz:** Precision optimize edilirken kaçırılan gerçek açık görünmez.
- **Recommended subsystem:** Validation altında validator ensemble, escape corpus ve verifier observability.
- **Dependencies:** Ground truth benchmark, human dispositions, equivalence/probability evidence schema.
- **Expected benefit:** Güveni korurken recall artışı.
- **Complexity:** Yüksek.
- **Priority:** P1.
- **Validation:** Seeded validator-evasion suite; blinded human adjudication; precision/recall/calibration birlikte.

## GAP-P1-07 — Race ve Temporal Experiment Coordinator

- **Gap:** Barrier-synchronized distributed requests, jitter, retry distribution ve time-window proof.
- **Evidence:** Glasswing race exploit beyanı; enterprise testlerde stochastic/timing dependencies; elite race research.
- **Competitor/hunter:** Glasswing, specialist humans, akademik AD testleri.
- **RO current equivalent:** Temporal Intelligence, workflow experiments, rate limiter.
- **Final-plan equivalent:** Race specialist.
- **Neden yetersiz:** Tek deterministik replay concurrency bug’ını ne üretir ne güvenilir doğrular.
- **Recommended subsystem:** Worker Runtime’ta Race Coordinator; Research’te probabilistic assessment.
- **Dependencies:** Executor fabric, monotonic clock, high-resolution artifact, safety budget.
- **Expected benefit:** High-impact workflow/race coverage.
- **Complexity:** Yüksek.
- **Priority:** P1.
- **Validation:** Controlled race benchmark; false concurrency control; reproducibility confidence intervals.

## GAP-P1-08 — Production OAST Service

- **Gap:** HA DNS/HTTP(S)/SMTP/LDAP callback, correlation, tenant isolation ve abuse control.
- **Evidence:** Interactsh çok-protokollü OOB altyapısı: [Interactsh overview](https://docs.projectdiscovery.io/opensource/interactsh/overview).
- **Competitor/hunter:** ProjectDiscovery/Interactsh, XBOW blind findings.
- **RO current equivalent:** G6 loopback OAST core.
- **Final-plan equivalent:** Collaborator-style production service.
- **Neden yetersiz:** Loopback validation, Internet callback güvenilirliği ve operasyon güvenliğini kanıtlamaz.
- **Recommended subsystem:** Platform integration; native correlation/evidence admission.
- **Dependencies:** Domain/TLS, secret isolation, retention/privacy, rate/abuse policy.
- **Expected benefit:** SSRF/XXE/blind injection/RCE/email flow.
- **Complexity:** Orta-yüksek.
- **Priority:** P1.
- **Validation:** Multi-protocol authorized lab; callback loss/duplication; tenant leakage; scope token forgery tests.

## GAP-P1-09 — Field Evidence ve Anti-Contamination Benchmark Programı

- **Gap:** Gerçek yetkili hedefler, chronological hidden suites, human disposition ve safety/cost ölçümü.
- **Evidence:** XBOW benchmark’tan HackerOne’a geçti; CVE-Bench en iyi agent için %13; PrimeVul leakage sorunu.
- **Competitor/hunter:** XBOW, CVE-Bench, PrimeVul.
- **RO current equivalent:** Visible/hidden benchmark ve old/new gates.
- **Final-plan equivalent:** Live-model/security-research validation.
- **Neden yetersiz:** Local truth fixture production diversity, duplicate ve policy pressure’ı temsil etmez.
- **Recommended subsystem:** Independent Evaluation Program; benchmark verisi research runtime’dan ayrılmış.
- **Dependencies:** Legal authorization, sealed graders, versioned environments, telemetry.
- **Expected benefit:** Gerçek capability ve regression gerçeği.
- **Complexity:** Çok yüksek, operasyonel.
- **Priority:** P1.
- **Validation:** Zaten programın kendisi; third-party rerun ve signed benchmark manifests.

## GAP-P1-10 — Operator, Reporting ve Duplicate Operations

- **Gap:** Program/run/review/finding UX, evidence minimization, duplicate prior, submission-ready report ve human SLA.
- **Evidence:** XBOW pre-submission review; Bugcrowd queue economics; elite hunters’da rapor iletişimi.
- **Competitor/hunter:** XBOW, Bugcrowd top researchers, SecurityReapers.
- **RO current equivalent:** status/census/budget/coverage CLI ve human review domain.
- **Final-plan equivalent:** Finding packaging, duplicate check, continuous operation.
- **Neden yetersiz:** Teknik candidate değer üretmez; doğru kişiye doğru kanıtı zamanında taşımazsa backlog olur.
- **Recommended subsystem:** Interface/Application altında Review Workbench ve Report Packager.
- **Dependencies:** Artifact redaction, replay bundle, program templates, similarity index.
- **Expected benefit:** Daha kısa review/submit süresi, daha az duplicate/N/A, daha iyi field feedback.
- **Complexity:** Orta-yüksek.
- **Priority:** P1.
- **Validation:** Reviewer time, report rejection reason, duplicate rate, time-to-submit, evidence completeness.

## GAP-P2-11 — Cloud/Identity/Kubernetes Lane

- **Gap:** IAM, credential, trust, cloud resource ve K8s path.
- **Evidence:** NodeZero ve Pentera’nın olgun enterprise path’i.
- **RO current equivalent:** Web identity/session; cloud graph yok.
- **Final-plan equivalent:** Cloud/protocol specialists.
- **Neden yetersiz:** Web ownership ile IAM trust aynı ontoloji değildir.
- **Recommended subsystem:** Ayrı typed graph + provider adapters; ImpactGraph’a proof bridge.
- **Dependencies:** P1 executor/semantic/validation.
- **Expected benefit:** Enterprise ve hybrid kapsam.
- **Complexity:** Çok yüksek.
- **Priority:** P2.
- **Validation:** GOAD/cloud/K8s authorized ranges; path proof; cleanup/re-test.

## GAP-P2-12 — Mobile/Binary Research Labs

- **Gap:** APK/IPA/native, dynamic instrumentation, reverse engineering ve exploit harness.
- **Evidence:** MobSF, Frida, Ghidra ve Glasswing.
- **RO current equivalent:** Yok.
- **Final-plan equivalent:** Mobile/binary specialist vizyonu.
- **Neden yetersiz:** Mevcut web graph ve worker’ları compiled/native semantiği taşımaz.
- **Recommended subsystem:** Ayrı labs; [MobSF](https://mobsf.github.io/docs/), [Frida](https://frida.re/docs/home/), [Ghidra](https://ghidra-sre.org/) entegrasyonları.
- **Dependencies:** Source Causal IR, artifact isolation, specialized benchmarks.
- **Expected benefit:** Yeni hedef sınıfları; web backend ile client chain.
- **Complexity:** Çok yüksek.
- **Priority:** P2; web saha yeterliliğinden sonra.
- **Validation:** APK/IPA hidden suites, binary capability ladder, runtime proof.

# 39. Zest’un XBOW-Sınıfı Sistemleri Geçmesi İçin Gerekenler

XBOW seviyesine ulaşmak için:

1. Gerçek HTTPS/SPA/auth production executor.
2. Program scope/policy ingest ve network-enforced guardrail’in saha ispatı.
3. Target portfolio selector, clone dedup ve launch/change timing.
4. En az onlarca vulnerability mechanism’de browser/protocol/OAST validator.
5. Kısa ömürlü, farklı prior/model/representation kullanan researcher pool.
6. Semantic normal-behavior ve ownership/role modeli.
7. Duplicate/informative/N/A/human-rejection feedback loop.
8. Binlerce hedefte isolation, backpressure, cost ve review throughput.
9. Public/third-party doğrulanabilir bug bounty sonucu.

XBOW’u aşmak için bunlara ek olarak:

- Daha güçlü evidence provenance ve causal replay,
- Context-bound negative knowledge,
- Coverage Debt’in registry-dışı belirsizliği ölçmesi,
- Source-assisted causal IR,
- Validator recall/escape bilimi,
- Cross-target semantic pattern mining,
- Güvenli human-guided new-family induction gerekir.

Üstünlük “daha çok submission” değil; aynı veya daha yüksek valid-impact üretirken daha az scope/policy ihlali, daha düşük duplicate/N/A, daha iyi recall, daha düşük maliyet ve daha güçlü yeniden üretilebilirliktir.

# 40. Zest’un Elite İnsan Hunter’ları Geçmesi İçin Gerekenler

İnsanı her tekil hedefte “daha yaratıcı” olmaya çalışarak geçmek yanlış objective’tir. Zest şu machine-native eksenlerde üstün olmalıdır:

- Her identity × resource × action × state hücresini eksiksiz izlemek.
- Yıllarca negatif ve pozitif bilgiyi unutmayıp change ile invalidate etmek.
- Binlerce target’ta aynı yeni primitive’i saatler içinde test etmek.
- Ajanlar arası bağımsızlık ve adversarial validation.
- Cross-target parser/semantic confusion örüntülerini keşfetmek.
- Human pattern library’yi açık hypothesis/evidence contract’a çevirmek.

İnsan hâlâ program politikası belirsizliği, etik etki, gerçek business intent, social/organizational context, tamamen yeni abstraction ve iletişimde karar mercii kalmalıdır. Hedef insanı silmek değil; insana yalnız yüksek entropili kararı bırakmaktır.

# 41. Kamu Bilgisinden Henüz Belirlenemeyenler

1. XBOW’un gerçek model/router bileşimi, maliyet/bulgu ve validator recall’u.
2. XBOW insan pre-submission review’unun kaç bulguyu reddettiği/değiştirdiği.
3. NodeZero/Pentera/Terra’nın target-specific web business-logic recall’u.
4. Glasswing 10.000+ high/critical sayısının bağımsız triage ve unique-root-cause dağılımı.
5. Kapalı rakiplerin durable memory, negative knowledge ve provenance şeması.
6. SecurityReapers özel araçları, AI kullanımı ve placeholder olmayan toplam metrikleri.
7. HackerOne/Bugcrowd tam güncel Top-100 isim/metodoloji eşlemesi.
8. Zest’un gerçek model latency, cost, calibration ve refusal davranışı.
9. Zest data plane’in aylar süren üretim hacmindeki maliyeti.
10. Zest’un yetkili saha precision/recall/duplicate/impact performansı.

# 42. Nihai Teknik Hüküm

## 42.1 Otuz zorunlu soruya açık cevap

### 1. Zest tam olarak neye dönüşüyor?

Politika ve kanıt sınırları model dışında tutulan, sürekli hedef öğrenen ve kontrollü deney yapan bir **otonom güvenlik araştırma işletim sistemi**ne.

### 2. Her planlanan subsystem uygulanırsa hangi sınıfta olur?

Autonomous bug bounty researcher + continuous offensive intelligence platform + human/AI research laboratory hibriti olur.

### 3. Hangi rakipler gerçekten kıyaslanabilir?

Siyah-kutu web/bug bounty için XBOW; human-on-loop continuous app pentest için Terra; source-assisted app için Shannon/Glasswing; enterprise path için yalnız ilgili alt sistemlerde NodeZero/Pentera.

### 4. Pazarlama benzerliğine rağmen hangileri doğrudan kıyaslanamaz?

NodeZero/Pentera’nın enterprise identity/network problemi ve Glasswing’in source/binary zero-day problemi, Zest’un ana bug bounty/web hedefiyle bire bir aynı değildir.

### 5. Attack-surface understanding’de en güçlü kim?

Bug bounty dış yüzey/portföyünde XBOW; enterprise identity/cloud graph’ında NodeZero; epistemik/provenance tasarımında tamamlanmış Zest.

### 6. Exploit execution’da en güçlü kim?

Web production kanıtında XBOW; enterprise chain’de NodeZero; source/binary exploit derinliğinde Glasswing/Mythos.

### 7. Business-logic reasoning’de en güçlü kim?

Kamu kanıtına göre elite insanlar. XBOW yaklaşmaktadır; Zest’un tamamlanmış tasarımında bile Semantic World Model eksiktir.

### 8. Novel vulnerability discovery’de en güçlü kim?

Source/binary’de Glasswing; production web/bug bounty’de XBOW ve elite humans. Zest henüz kanıt sunmuyor.

### 9. Attack chaining’de en güçlü kim?

Her domain’de farklı lider: XBOW web, NodeZero enterprise identity, Glasswing binary/browser, insanlar target-specific business narrative. Zest proof discipline’de güçlü ama saha derinliği düşük.

### 10. False-positive discipline’de en güçlü kim?

Mimari kontrat olarak Zest; saha sonucu olarak XBOW/NodeZero. Zest’un recall/false-negative’i ayrıca kanıtlanmalıdır.

### 11. Evidence/provenance’da en güçlü kim?

Kamuya açık tasarım ayrıntısına göre Zest.

### 12. Continuous hunting’de en güçlü kim?

Kamu saha kanıtında XBOW ve enterprise campaign/re-test tarafında NodeZero/Pentera; Zest plan düzeyinde.

### 13. Source-assisted vulnerability research’te en güçlü kim?

Glasswing/Mythos; açık kaynak web karşılaştırıcısı Shannon.

### 14. Elite insanlar neyi hâlâ daha iyi yapıyor?

Business intent, ownership/role expectation, weirdness, target economics, etik etki, yeni abstraction ve belirsiz rapor iletişimi.

### 15. Zest tipik insan akışından neyi daha iyi modelliyor?

Eksiksiz ledger, explicit epistemic states, cross-identity coverage, context-bound negative knowledge, deterministic replay ve scope/budget authority.

### 16. En büyük eksik organ nedir?

Semantic World Model; onu Portfolio Intelligence ve Open-Ended Discovery Lab izler.

### 17. Yol haritasında gereksiz veya yanlış öncelikli ne var?

Çekirdek organ gereksiz değil. Fakat mobile/binary/cloud breadth, web saha executor’ı, semantik model ve field evidence’den önce gelirse yanlış öncelik olur. “False finding = 0” tek KPI olmamalıdır.

### 18. Ne daha erkene çekilmeli?

Semantic World Model, production executor, HunterScore v2, source causal IR’in küçük dikey dilimi, validator recall ve field evaluation.

### 19. Ne daha sonraya atılmalı?

Geniş binary/mobile protocol evreni ve tüm cloud provider kapsamı; önce dar web/API hattı sahada üstün olmalı.

### 20. Ne tamamen kaldırılmalı?

Core/evidence organlarından hiçbiri. Scheduler’dan unbounded family dominance ve first_seen-as-freshness semantiği kaldırılmalı; “zero FP tek başarı metriği” kaldırılmalıdır.

### 21. Sophisticated scanner olma riski var mı?

Evet. Registry + deterministic mutation + family-success scheduler, G16 ve semantic induction derinleşmezse bu riski doğrudan üretir.

### 22. Mimari gerçekten novel discovery destekliyor mu?

Kısmen. Anomaly, causal, differential, temporal ve G16 gerekli iskeleti sağlar; yeni mechanism induction ve semantic world model olmadan yeterli değildir.

### 23. XBOW düzeyi bug bounty performansını bugün ne engelliyor?

Production executor derinliği, target economics, semantic normal model, validator library, agent-scale operations, report/duplicate workflow ve gerçek field data eksikliği.

### 24. XBOW’u aşmayı ne engelliyor?

Yukarıdakilere ek olarak source/runtime causal corpus, registry-dışı novelty, validator recall ve uzunlamasına cross-target öğrenme henüz yok.

### 25. O seviyeye ulaşıldığının güvenilir kanıtı ne olur?

En az iki çeyrek yetkili gerçek programlarda; bağımsız triage edilen unique valid bulgu, impact, duplicate/N/A, precision/recall estimate, scope/safety incident, cost ve human-review metriklerinin yayımlanması.

### 26. Hangi benchmark’lar oluşturulmalı?

Unseen multi-tenant business logic; validator escape; clone/dedup; temporal deployment; race; OAuth/OIDC/SAML; GraphQL/WebSocket; OAST; source-to-runtime; registry-external mechanism; safe-scope; long-horizon recovery ve anti-contamination chronological holdout.

### 27. Hangi gerçek dünya metrikleri ölçülmeli?

Valid unique finding/target/hour/dollar; critical/high yield; duplicate/informative/N/A; human rejection; validator FN estimate; time-to-first-valid; coverage staleness; scope denial/violation; destructive side effect; replay success; reviewer minutes; learning transfer.

### 28. Hangi public hunter metodolojileri kodlanmalı?

SecurityReapers threat-model→chain→impact; rabhi recon/specialization/off-path arama; Orange cross-layer semantic confusion; shubs context-bound persistence; Bugcrowd küçük-diverse-team/challenger modeli.

### 29. Hangi hunter davranışları otomatikleştirilmemeli?

Kapsamı “yaratıcı” genişletme, gerçek veride gereksiz impact, destructive proof, belirsiz policy’yi tek taraflı yorumlama, otomatik public disclosure/submission ve self-approval.

### 30. Principal architect olarak sonraki 10 en yüksek değerli değişiklik ne olur?

1. Semantic World Model dikey dilimi.
2. HTTPS/SPA/auth production executor gate’i.
3. HunterScore v2: first_seen/change ayrımı, bounded family prior, Pareto portfolio.
4. G16 mechanism-induction benchmark ve human family-proposal akışı.
5. Validator recall/escape benchmark.
6. Source Causal IR: tek dil + route/auth/dataflow → runtime proof dikeyi.
7. Production OAST + blind evidence admission.
8. Race/temporal probabilistic verification.
9. Field evidence programı ve signed metric dashboard.
10. Review/report/duplicate workbench.

## 42.2 Tek cümlelik nihai hüküm

**Tamamlanmış Zest, kontrol ve kanıt mimarisi bakımından dünya sınıfı bir otonom araştırma sistemi olabilecek kadar tutarlıdır; fakat XBOW’u veya elite insanları geçmesi, daha fazla family eklemekten değil semantik dünya modeli, üretim yürütme derinliği, açık-uçlu mekanizma keşfi, hedef-portföy ekonomisi ve gerçek saha veri döngüsünü başarıyla kurmaktan geçer.**

# 43. Kaynak Ekleri

## 43.1 Zest birincil depo kanıtı

1. [İncelenen HEAD commit — 2026-08-19](https://github.com/TayfurYldz/zest/commit/1f008265f1633ff74cc0c6d7156cc415bc542ca5)
2. [PROJECT_STRUCTURE.md](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/PROJECT_STRUCTURE.md)
3. [DOMAIN_MODEL.md](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/DOMAIN_MODEL.md)
4. [TECHNICAL_REQUIREMENTS.md](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/TECHNICAL_REQUIREMENTS.md)
5. [TECHNICAL_DECISIONS.md](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/TECHNICAL_DECISIONS.md)
6. [REPOSITORY_LAYOUT.md](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/REPOSITORY_LAYOUT.md)
7. [IMPLEMENTATION_PLAN.md](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/IMPLEMENTATION_PLAN.md)
8. [Zest operasyon modeli](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/docs/plans/Zest_Operasyon_Modeli.md)
9. [Saldırı dönemi entegrasyon planı](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/docs/plans/Zest_Saldiri_Donemi_Entegrasyon_Plani.md)
10. [Sulandırma envanteri/gate yol haritası](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/docs/plans/Zest_Sulandirma_Envanteri_ve_Gate_Yol_Haritasi.md)
11. [Maturity truth source](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/src/zest/maturity.py)
12. [HunterScore formula](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/src/zest/research/scheduler/score.py)
13. [RunHuntScheduler/freshness](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/src/zest/application/run_hunt_scheduler.py)
14. [Scheduler types](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/src/zest/research/scheduler/types.py)
15. [Scheduler tests](https://github.com/TayfurYldz/zest/blob/1f008265f1633ff74cc0c6d7156cc415bc542ca5/tests/unit/research/scheduler/test_score.py)

## 43.2 Ticari/kapalı sistem kaynakları

16. XBOW, [The Road to Top 1 — 2025-06-24](https://xbow.com/blog/top-1-how-xbow-did-it)
17. XBOW, [1,060 Autonomous Attacks — 2026-03-02](https://xbow.com/blog/we-ran-1060-autonomous-attacks)
18. XBOW, [IDOR high-accuracy methodology](https://xbow.com/blog/xbow-finds-idors-high-accuracy-ambiguous-context)
19. XBOW, [Validation benchmarks repository](https://github.com/xbow-engineering/validation-benchmarks)
20. Horizon3, [NodeZero WebApp](https://horizon3.ai/nodezero/webapp/)
21. Horizon3, [Deployment Strategy](https://docs.horizon3.ai/portal/deployment_strategy/)
22. Horizon3, [BloodHound integration](https://docs.horizon3.ai/portal/features/bloodhound/)
23. Horizon3, [Glossary: attack path/context/impact](https://docs.horizon3.ai/knowledge_base/glossary/)
24. Horizon3, [Insights and longitudinal revalidation](https://docs.horizon3.ai/insights/)
25. Anthropic, [Mythos Preview technical assessment — 2026-04-07](https://www.anthropic.com/research/mythos-preview)
26. Anthropic, [Exploit evals — 2026-05-22](https://www.anthropic.com/research/exploit-evals)
27. Anthropic, [Expanding Project Glasswing — 2026-06-02](https://www.anthropic.com/news/expanding-project-glasswing)
28. Anthropic, [Patterns and problems in multiagent systems — 2026-08](https://www.anthropic.com/research/multiagent-systems)
29. Pentera, [Pentera Platform](https://pentera.io/pentera-platform/)
30. Pentera, [AI-powered exposure validation](https://pentera.io/ai-powered-exposure-validation/)
31. Terra, [Agent Architecture](https://www.terra.security/agent-architecture)
32. Terra, [Guardrails — 2026-08-06](https://www.terra.security/blog/ai-pentesting-guardrails)
33. RunSybil, [Product overview](https://www.runsybil.com/)
34. Picus, [Autonomous penetration testing architecture](https://www.picussecurity.com/resource/blog/what-is-an-autonomous-penetration-testing-platform)
35. Ridge Security, [Platform overview](https://ridgesecurity.ai/)
36. Synack, [AI Pentesting](https://www.synack.com/platform/ai-pentesting/)

## 43.3 Açık kaynak ve akademik kaynaklar

37. [Strix repository](https://github.com/usestrix/strix)
38. [Shannon repository](https://github.com/KeygraphHQ/shannon)
39. Shannon, [Safety, scope and limitations](https://github.com/KeygraphHQ/shannon/blob/main/docs/safety.md)
40. [CAI repository](https://github.com/aliasrobotics/CAI)
41. [PentAGI repository](https://github.com/vxcontrol/pentagi)
42. PentAGI, [Flow execution architecture](https://github.com/vxcontrol/pentagi/blob/main/backend/docs/flow_execution.md)
43. [PentestGPT repository](https://github.com/greydgl/pentestgpt)
44. USENIX, [PentestGPT — 2024](https://www.usenix.org/conference/usenixsecurity24/presentation/deng)
45. NeurIPS, [NYU CTF Bench — 2024](https://proceedings.neurips.cc/paper_files/paper/2024/hash/69d97a6493fbf016fff0a751f253ad18-Abstract-Datasets_and_Benchmarks_Track.html)
46. [CyBench](https://cybench.github.io/)
47. Meta, [CyberSecEval 3 — 2024-07-23](https://ai.meta.com/research/publications/cyberseceval-3-advancing-the-evaluation-of-cybersecurity-risks-and-capabilities-in-large-language-models/)
48. ACL Anthology, [AutoPenBench — 2025](https://aclanthology.org/2025.emnlp-industry.114/)
49. ICML/PMLR, [CVE-Bench — 2025](https://proceedings.mlr.press/v267/zhu25i.html)
50. IEEE S&P, [Incalmo — 2026](https://users.ece.cmu.edu/~lbauer/papers/2026/sp2026-incalmo.pdf)
51. ACM TOSEM, [Can LLMs Hack Enterprise Networks? — 2026](https://dl.acm.org/doi/10.1145/3766895)
52. IEEE/ACM ICSE, [Vulnerability Detection with Code LMs/PrimeVul — 2025](https://www.computer.org/csdl/proceedings-article/icse/2025/056900a469/215aWRJLUZy)

## 43.4 Hunter, leaderboard ve ekonomi kaynakları

53. YesWeHack, [2026 Q3 Ranking — erişim 2026-08-20](https://yeswehack.com/ranking)
54. Intigriti, [90-day Leaderboard — erişim 2026-08-20](https://app.intigriti.com/leaderboard?ninetydays=true&severity=1)
55. HackerOne, [Leaderboards documentation — 2026-03-26](https://docs.hackerone.com/en/articles/8456255-leaderboards)
56. HackerOne, [90-Day Leaderboard formula — 2026-03-26](https://docs.hackerone.com/en/articles/8456917-90-day-leaderboard)
57. Bugcrowd, [Priority queue bypass — 2026-06-08](https://www.bugcrowd.com/blog/introducing-priority-queue-bypass-a-new-way-to-recognize-top-hackers/)
58. Bugcrowd, [Inside the Mind of a Hacker 2026 — 2026-01-27](https://www.bugcrowd.com/blog/inside-the-mind-of-a-hacker-2026/)
59. Bugcrowd, [Elite team spotlight — 2026-02-10](https://www.bugcrowd.com/blog/hacker-spotlight-meet-an-elite-hacking-team/)
60. Bugcrowd, [Experience vs methodology — 2026-06-22](https://www.bugcrowd.com/blog/experience-vs-methodology-how-hackers-make-decisions/)
61. SecurityReapers, [Company overview](https://securityreapers.com/)
62. SecurityReapers, [Methodology](https://securityreapers.com/methodology/)
63. SecurityReapers, [About](https://securityreapers.com/about/)
64. YesWeHack, [Rabhi blueprint — 2025-09-16](https://www.yeswehack.com/community/rabhi-root-bug-bounty-blueprint)
65. Shubham Shah, [Bug bounty mindset — 2022-11-26](https://shubs.io/so-you-want-to-get-into-bug-bounties/)
66. Orange Tsai, [Confusion Attacks — 2024-08-09](https://orange-tw.blogspot.com/2024/08/confusion-attacks-en.html)

## 43.5 Capability building-block kaynakları

67. ProjectDiscovery, [Open-source tool map](https://docs.projectdiscovery.io/opensource)
68. ProjectDiscovery, [Katana](https://docs.projectdiscovery.io/opensource/katana/overview)
69. ProjectDiscovery, [Interactsh](https://docs.projectdiscovery.io/opensource/interactsh/overview)
70. GitHub, [CodeQL data-flow analysis](https://codeql.github.com/docs/writing-codeql-queries/about-data-flow-analysis/)
71. Joern, [Code Property Graph](https://docs.joern.io/code-property-graph/)
72. [Frida dynamic instrumentation](https://frida.re/docs/home/)
73. [MobSF documentation](https://mobsf.github.io/docs/)
74. [Ghidra SRE framework](https://ghidra-sre.org/)
75. OWASP, [API Security Top 10 2023](https://owasp.org/API-Security/editions/2023/en/0x11-t10/)
76. OWASP, [Top 10:2025](https://owasp.org/Top10/2025/)

## 43.6 Kapanış kanıt notu

En güçlü Zest sonuçları depo kontratlarından; en güçlü rakip sonuçları platform dokümanı, açık kod veya program-owner/benchmark sonucundan çıkarıldı. XBOW ve Glasswing’in sayısal sonuçları ayrıntılı teknik yayınlara dayansa da nihai olarak ilgili üreticilerin beyanıdır; bağımsız, ham trace/triage dataset’i kamuya açık değildir. Bu nedenle rapor, “tasarımsal olarak güçlü”, “uygulanmış”, “laboratuvarda doğrulanmış” ve “sahada kanıtlanmış” ifadelerini sistematik olarak ayrı tutar.
