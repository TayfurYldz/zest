# Zest — Operasyon Modeli (Sistem Bittiğinde Kullanım Nasıl Olacak)

**Belge sınıfı:** Kullanım kılavuzu / operasyonel vizyon
**Tarih:** 2026-08-18
**Hedef kitle:** Sen — tek operatör, yetkili bug bounty araştırmacısı
**Amaç:** G1–G16 inşaatı bittiğinde bu sistemin **günlük hayatta nasıl kullanılacağını** somut olarak tanımlamak. Soyut mimari yok — sabah açtığında ne göreceğin, akşam ne kapatacağın.

---

## 0. Tek cümlelik cevap

**Sen sistemi çalıştırmazsın; sistem senin için avlanır, sen karar verirsin.** Senin işin üç şeye iner: (1) programı sisteme tanıtmak, (2) onay kuyruğundaki kanıtlanmış bulgu adaylarını yargılamak, (3) raporu platforma göndermek. Geri kalan her şey — recon, tarama, deney, doğrulama, önceliklendirme — otonomdur ama her yan etki senin yetkilendirdiğin scope'un içinde kalır.

---

## 1. Kurulum topolojisi (bir kere kurulur)

```
┌─────────────────────────────────────────────────────────┐
│  KALI MAKİNEN (senin donanımın)                          │
│  ├── PostgreSQL (SoR — tek gerçek kaynak)                │
│  ├── zest CLI (operatör konsolun)                 │
│  ├── Worker'lar (HTTP/browser executor'lar, cgroup'lu)   │
│  └── Model runtime'lar (API veya CLI oturumları)         │
└─────────────────────────────────────────────────────────┘
              │
┌─────────────────────────────────────────────────────────┐
│  VPS (ucuz bir sunucu — OAST callback uç noktası)        │
│  └── oast.senindomain.com — kör sınıf kanıtları buraya   │
│      düşer (SSRF/XSS/SQLi callback'leri)                 │
└─────────────────────────────────────────────────────────┘
```

İnternet erişimi gereken tek şey: hedef programların siteleri + OAST VPS'i. Başka dışa bağımlılık yok. Her şey private, hiçbir veri üçüncü tarafa gitmez (model API kullanıyorsan yalnızca prompt/gözlem metni gider — ham credential asla, SoR'da zaten referans olarak durur).

---

## 2. Bir programın yaşam döngüsü — baştan sona

### Faz 0: Programı sisteme tanıt (10 dakika, tek sefer)

Yeni bir HackerOne/Bugcrowd programı seçtin. Yapacağın tek şey:

```bash
zest program add
```

Sistem sana sorar: scope satırları (`*.example.com`, `api.example.com/app/*`), exclusion'lar, program kuralları (rate limit, yasaklı aksiyonlar, bounty tablosu). İki kaynaktan beslenir:

- **Senin girdiğin** kurallar (elle veya program sayfasından yapıştırarak)
- **Platform API senkronu** (G1): destekleyen platformlarda scope otomatik çekilir ve periyodik güncellenir; senkron ile senin kaydın çelişirse → `REQUIRE_HUMAN_REVIEW` (sen karar verirsin)

Core bunları derler: her kural somutlaşır, wildcard'lar açılır, çelişkiler sana sorulur. Sonra **sen onaylarsın** — bu onay Core'a yazılır. Bu andan itibaren sistem bu program için silahlanmıştır ama henüz tek istek atmamıştır.

### Faz 1: Dış census (otonom, saatler — sen uyurken)

```bash
zest run start --program example --phase census
```

Sensör düzlemi çalışır: DNS, CT log, subdomain, tarihsel URL'ler, sertifikalar, teknoloji parmak izleri, APK varsa indirilip API yüzeyi çıkarılır. **Tamamı pasif/yarı-pasif** — hedefe probing yok, bu yüzden UNKNOWN varlıklar da haritalanır. Sonuç AttackSurfaceGraph v2'ye düşer.

Sabah `zest coverage show --program example` dediğinde görürsün:

```
example.com programı — yüzey özeti
  847 hostname (412 IN_SCOPE, 389 UNKNOWN, 46 OUT_OF_SCOPE)
  23 teknoloji kümesi, 4 mobil uygulama, 11 API spec adayı
  UNKNOWN'ların en değerli 20'si: [liste — sen bunları programa
  sorabilir veya scope genişletme talebi açabilirsin]
```

UNKNOWN listesi sana **para fırsatı** olarak sunulur: "Bu 20 host scope'ta değil ama aynı altyapıda görünüyor — program sayfasına soru sorup scope'a aldırabilirsin."

### Faz 2: Kimlik kurulumu (15 dakika, program başına)

Program hesap gerektiriyorsa: iki test hesabı açarsın (user-A, user-B — BOLA diferansiyeli için), credential'ları sisteme **referans** olarak kaydedersin (ham secret SoR'a girmez, G20'nin session binding'i devrede). Admin/normal/anon rolleri tanımlarsın. Bu kadar — sistem artık kimlik matrisini bilir.

### Faz 3: Otonom av (sürekli — sistemin asıl işi)

```bash
zest run start --program example
```

Bu komutla avcı döngü başlar ve **sen durdurana kadar sürer**:

1. **MAP:** Uygulama-içi keşif (G22 motoru + yeni sensörler) yüzeyi derinleştirir: endpoint'ler, formlar, parametreler, workflow'lar, API spec'ler, JS bundle'lardan çıkan gizli uçlar
2. **HYPOTHESIZE:** Hunter scheduler, registry'deki her aileyi yüzeyle çaprazlar. HunterScore hesaplar: kanıt-değeri × yeni-bilgi × olasılık × maliyet. Coverage Debt matrisi "hiç bakılmamış kareleri" (Asset × Identity × Family) yüksek puanlar
3. **PROBE:** En yüksek skorlu hipotez deneye çevrilir — Core her deneyi tek tek yargılar (scope ✓, bütçe ✓, SE seviyesi ✓, rate limit ✓). SE0/SE1 otomatik akar; SE2+ program politikası izin veriyorsa akar, vermiyorsa **sana onaya düşer**
4. **VALIDATE:** Bir şey bulursa V1 (taze oturumda yeniden üret) → V2 (negatif kontrol) → V3 (bağımsız model gözden geçirme) zincirinden geçer. Hepsini geçemeyen FindingProposal olamaz
5. **Yeniden planla:** Her sonuç (olumsuz dahil) grafiği ve skorları günceller; sistem "nerede hiç bakılmamış"ı bilerek ilerler

Sen bu sırada başka iş yaparsın. Rate limit'e yaklaşılırsa sistem kendi kendine yavaşlar; scope TTL dolarsa durup sana sorar; bir aile sahada saçmalamaya başlarsa devre kesici onu throttle eder (G10).

### Faz 4: Onay kuyruğu — senin günlük 30 dakikan

```bash
zest review queue
```

Karşında **kanıtlanmış** bulgu adayları durur — ham gürültü değil. Her satır:

```
[FP-0417] BOLA — /api/v2/orders/{id} — internal P1 → Bugcrowd P2
  Kanıt: user-A'nın siparişi user-B oturumuyla 200 OK + veri sızıntısı
  V1 ✓ (taze oturum, 3/3 tekrar)  V2 ✓ (kendi kaynağına 403)
  V3 ✓ (bağımsız model onayı)     OAST: —
  Etki: 2.3M sipariş kaydı numaralandırılabilir ID aralığında
  [a]onayla  [r]reddet  [d]detay  [i]inceleme iste
```

Sen `d` ile request/response dökümünü, zincir haritasını, PoC adımlarını incelersin. Onaylarsan → Finding olur. Reddedersen → sistem öğrenir (bu ailenin skoru düşer). Şüpheliysen → ek deney istersin.

**Kritik nokta:** Bu kuyruğa düşen her şey zaten `false_finding=0` disiplininden geçmiştir. Senin işin "bu gerçek mi?" değil, "bu raporlanmaya değer mi ve etkisi doğru çerçevelenmiş mi?" — yani insan yargısının gerçekten değerli olduğu kısım.

### Faz 5: Rapor — 5 dakika

```bash
zest finding export FP-0417 --platform bugcrowd
```

Sistem G14'ün paketleyicisiyle çıktı üretir: başlık, severity (platform formatına eşlenmiş), yeniden-üretim adımları (request/response dökümleriyle), etki kanıtı, zincir haritası, remediation önerisi. Göndermeden önce duplicate taraması: kendi geçmiş bulguların + programın disclosed raporları. "Bu kategori bu programda 3 ay önce raporlanmış ve kapatılmış" uyarısı gelirse zaman harcamazsın.

Sen metni okur, gerekirse rötuşlarsın, platforma gönderirsin. **Gönderme fişini sen çekersin** — sistem asla kendi kendine rapor göndermez.

### Faz 6: Sürekli nöbet (sen hiçbir şey yapmasan da)

Program aktif kaldıkça Continuous Change Hunter (G15) çalışır: yeni subdomain belirdi, JS bundle değişti, API'ye v3 eklendi, yeni parametre görüldü → değişiklik öncelik artışı yaratır, avcı o noktaya kayar. Sen ertesi sabah kuyrukta "dün gece deploy edilen `/api/v3/internal/export` ucu authorization matrisine takıldı" notunu bulursun.

---

## 3. Senin bir haftanın — önce ve sonra

| | Bugün (elle avcılık) | Sistem tamamlanınca |
|---|---|---|
| Recon | 1-2 gün, elle araç orkestrasyonu | Gece otonom, sabah özet |
| Yüzey takibi | Notların + ezber | Coverage Debt matrisi canlı |
| Deney tasarımı | Her testi sen kurarsın | Scheduler önerir, Core yargılar |
| Doğrulama | Elle tekrar, ekran görüntüsü | V1/V2/V3 otomatik, sen yargılarsın |
| Rapor yazımı | 1-3 saat/bulgu | 5 dakika + rötuş |
| Duplicate kontrolü | Platformda elle arama | Otomatik (iç + dış sinyal) |
| Senin zamanın | %80 mekanik, %20 yargı | %95 yargı, %5 mekanik |

---

## 4. Kontrol sende mi? — evet, dört kilit noktasında

1. **Program onayı:** hiçbir hedef, senin onaylamadığın scope'un dışına çıkamaz (Core fail-closed; UNKNOWN'a probing yok)
2. **Yan etki onayı:** SE2+ deneyler program politikası izin vermiyorsa senin onayına düşer; SE4 default-deny
3. **Bulgu onayı:** Finding yalnız senin onayınla doğar — model ve worker Finding üretemez (epistemik zincir)
4. **Gönderim:** raporu platforma sen gönderirsin

Bunun dışındaki her şey — binlerce istek, yüzlerce deney, onlarca doğrulama — senin uykunda, senin adına ama **senin kurallarınla** akar.

---

## 5. İnşaat sırasında kullanım nasıl olacak? (köprü dönem)

G1–G16 bitene kadar beklemezsin — her gate kullanılabilir bir şey teslim eder:

- **G1–G3 sonrası:** program tanıt + census + yüzey haritası → manuel avcılığın için bile bugünkü araçlarından üstün bir recon asistanı
- **G4–G5 sonrası:** authorization avcısı canlı → IDOR/BOLA kuyruğu çalışmaya başlar (bu tek başına en yüksek ödüllü kategori)
- **G6–G9 sonrası:** SSRF/secret/XSS bulguları akmaya başlar
- **G10+ sonrası:** tam döngü — kuyruk, severity, rapor
- **G16 sonrası:** kayıtlı olmayanı da avlayan sistem — leaderboard zirvesi kapısı

Yani değer eğrisi kademeli: **G5'te ilk para kazandıran sürüm, G10'da tam döngü, G16'da zirve adayı.**

---

## 6. G16'nın kullanımdaki farkı — somut örnek

Registry'deki aileler bilinen sınıfları yakalar. G16 şu sahneyi mümkün kılar:

Sistem bir API yanıtında tuhaflık görür: hata mesajında iç hostname sızıyor, timing tutarsız, response şekli grafiğin beklediğinden farklı. Hiçbir kayıtlı aile bunu claim etmez — ama G16 üreteci der ki: "Bu üç anomali birlikte, registry'de olmayan bir aday hipotez oluşturuyor: muhtemel internal proxy misconfiguration." Bu hipotez sana düşer; sen "kovalamaya değer" dersen sistem aynı V1/V2/V3 disipliniyle kanıtlar veya çürütür. Kanıtlanırsa **sen** yeni HunterFamily taslağını onaylarsın — registry senin saha zekânla büyür. Sistem avlandıkça akıllanır ve bu akıl **senin mülkün** olarak SoR'da birikir.

Bu, "çok hızlı bilinen-zafiyet tarayıcısı" ile "leaderboard #1 adayı" arasındaki farktır.
