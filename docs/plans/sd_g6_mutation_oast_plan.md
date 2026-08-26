# SD-G6 PLAN — MUTATION ENGINE + OAST ÇEKİRDEĞİ + RATE-LIMIT ENFORCEMENT

> Saldırı Dönemi Gate 6. Eski altyapı dönemi GATE 06 (Experiment Planning) ile karıştırılmaz.

## 1. ÖZET

SD-G6 avcının dişlerini takar:

- **P0 — G5 Sertleştirmesi**: `_enqueue_v3` içine IN_SCOPE kod kilidi.
- **P1 — Mutation Engine**: Gözlemlenen HTTP yüzeyinden deterministik saldırı varyantları üreten saf research katmanı.
- **P2 — OAST Çekirdeği**: Loopback callback token üretimi + callback sorgulama portu.
- **P3 — Rate-Limit Enforcement**: `program_policy.rate_limit_profile` alanının Core authorize öncesi zorlanması.

Hiçbir varyant scope dışına üretilmez; OAST token'ları provenance taşır; canlı internet çağrısı testte yasaktır.

## 2. KUTSALLAR (İHLAL = GATE AÇIK KALIR)

- **K1** Varyant scope'a hapseder: hedef node'un `scope_classification`'ından koparılamaz.
- **K2** OAST token'ları `research_run_id + hypothesis_id + target_reference` bağlı; anonim token yasak.
- **K3** Rate-limit profili zorlanır; limit aşımı DENY + ReasonCode + ledger kaydı üretir.
- **K4** G5 mühür notu: `_enqueue_v3` OUT_OF_SCOPE/UNKNOWN node ile hata verir.
- **K5** Mevcut test fonksiyonu silinmez; kontrat kopyaları md5-identical kalır.
- **K6** Ham secret OAST payload veya varyant gövdelerine girmez.

## 3. MEVCUT ZEMİN

- `GATE_05_STATUS = "PASS"`; `GATE_06` henüz tanımlı değil.
- `pytest tests/unit tests/contract -q`: **1038 passed, 4 skipped**.
- `hunt_validation.py:_enqueue_v3` IN_SCOPE kontrolü içermiyor.
- `tools/registry.py`: `SUPPORTED_REQUIREMENTS = {"loopback", "scope_derived"}`.
- `tools/http_transaction_policy.py` + `http_transaction_authorization.py`: HTTP varyant argüman validasyonu hazır.
- `execute_planned_experiment.py:authorize()` Core decision öncesi ek kontrol noktası.
- `program_policy` tablosu `daily_llm_budget_microdollars` taşıyor; `rate_limit_profile` ayrı tablo.
- `ProgramPolicyView` şu an `rate_limit_profile` taşımıyor.
- `RateLimitProfileRepository` protokolü + Postgres implementasyonu mevcut.
- `ExperimentIntent → compile_experiment_intent → ExperimentPlan` yolu hazır.
- `BudgetConsumptionRecord` + `RecordBudgetConsumption` append-only ledger hazır.

## 4. DOSYA-BAZLI DEĞİŞİKLİK PLANI

### P0 — G5 Sertleştirmesi

**Dosya**: `src/zest/application/hunt_validation.py`
- **Fonksiyon**: `_enqueue_v3`
- **Değişiklik**: İlk satırda `node.scope_classification != ScopeClassification.IN_SCOPE` ise `HuntValidationTierError` yükselt.
- **Test**: `tests/unit/application/test_hunt_cycle.py` — yeni test: UNKNOWN node ve OUT_OF_SCOPE node ile V3 kuyruk denemesi → açık hata.

### P1 — Mutation Engine

**Dosya**: `src/zest/research/mutation/__init__.py`
- Modül init; dışa aktarılan tipler.

**Dosya**: `src/zest/research/mutation/types.py`
- **Sınıf**: `MutationVariant`
  - Alanlar: `variant_id`, `node_id`, `family_id`, `mutation_rule_id`, `target_reference`, `scope_classification`, `capability_id`, `action`, `arguments`, `provenance`.
  - `to_public_summary()` ile boyut sınırlı (≤ 2 KB), sır içermeyen audit payload.
- **Sınıf**: `MutationRule`
  - Alanlar: `rule_id`, `family_id`, `description`.
- **Protokol**: `MutationFamily`
  - `generate(node, provenance, variant_id_prefix) -> tuple[MutationVariant, ...]`

**Dosya**: `src/zest/research/mutation/families.py`
- Aile implementasyonları:
  - `ParamPollutionFamily`
  - `TypeJugglingFamily`
  - `BoundaryValueFamily`
  - `AuthHeaderVariationFamily`
  - `MethodOverrideFamily`
  - `ContentTypeConfusionFamily`
  - `IdOrTraversalCandidateFamily`
- Her aile: girdi gözlemden (EXACT_PATH/HTTP_OPERATION + parametre adayları) → deterministik varyant kümesi.
- Her varyant `provenance = {"node_id", "family_id", "mutation_rule_id"}` taşır.

**Dosya**: `src/zest/research/mutation/engine.py`
- **Fonksiyon**: `mutate_for_node(node, graph, *, variant_id_prefix: str) -> tuple[MutationVariant, ...]`
- `MutationEngine.mutate(node, graph, *, variant_id_prefix: str)` arayüzü.
- IN_SCOPE olmayan node için boş tuple döner (K1).
- ID üretimi research katmanında değil, application katmanında yapılır (F2).
- Determinizm testi: aynı girdi → aynı varyant seti.

**Dosya**: `src/zest/research/mutation/intent.py`
- **Yeni helper**: `mutation_variant_to_intent(variant, budget_id) -> ExperimentIntent`.

**Dosya**: `src/zest/application/record_mutation_variants.py` (yeni)
- **Use case**: `RecordMutationVariants`
  - `MutationEngine` ile varyant üretir.
  - Her varyantı `audit_event` ledger'ına `MUTATION_VARIANT_PLANNED` olarak yazar.
  - ID üretimi `application.identity.new_opaque_id()` ile yapılır.
  - Clock injection ile `occurred_at` yazar (D4).

**Testler**:
- `tests/unit/research/test_mutation_engine.py`
  - Her aile için determinizm testi.
  - UNKNOWN/OUT_OF_SCOPE node için boş varyant seti.
  - Provenance doğruluğu.
  - `MutationVariant → ExperimentIntent → ExperimentPlan` yolu validasyonu.

### P2 — OAST Çekirdeği

**Dosya**: `src/zest/research/oast/types.py`
- **Sınıf**: `OastToken`
  - Alanlar: `token_id`, `research_run_id`, `hypothesis_id`, `target_reference`, `expires_at`.
- **Protokol**: `OastPort`
  - `mint_token(...) -> OastToken`
  - `poll(token_id, *, now) -> tuple[OastCallback, ...]` (clock injection, D4).
- **Sınıf**: `OastCallback`
  - Alanlar: `callback_id`, `token_id`, `received_at`, `source_address`, `request_summary`.

**Dosya**: `tests/fixtures/oast/loopback.py`
- **Sınıf**: `LoopbackOastPort(OastPort)`
  - Bellek içi token/callback store; test/fixture sınırında, application identity kullanabilir.
  - Süresi dolmuş token'a `poll` çağrısı `OastTokenExpiredError` verir.

**Dosya**: `src/zest/application/admit_oast_callback.py`
- **Use case**: `AdmitOastCallback`
  - Callback'i evidence hattına `UNTRUSTED_EXTERNAL` olarak taşır.
  - Stale token reddi.
  - Provenance korunur.

**Testler**:
- `tests/unit/research/test_oast_core.py`
  - Token üretim yaşam döngüsü.
  - Callback eşleştirme.
  - Stale callback reddi.
  - Admission sonrası `UNTRUSTED_EXTERNAL` korunur.

**Fixture**: `tests/fixtures/oast/`
- Loopback fixture kayıtları (örn. `callback_001.json`, `stale_callback.json`).

### P3 — Rate-Limit Enforcement

**Dosya**: `src/zest/application/program_research_context.py`
- **Sınıf**: `ProgramPolicyView`
  - Yeni alan: `rate_limit_profile: RateLimitProfileRecord | None`
- **Fonksiyon**: `load_program_research_context`
  - Program'a ait rate limit profillerini `uow.rate_limit_profiles.list_for_program` ile çeker; ilkini view'a bağlar.
- **Fonksiyon**: `derive_loopback_only` dokunulmaz.

**Dosya**: `src/zest/core/rate_limit.py` (yeni)
- **Sınıf**: `RateLimitCheck`
  - `allowed: bool`, `reason_code: ReasonCode`, `next_allowed_at: datetime | None`
- **Fonksiyon**: `check_rate_limit(profile, recent_attempts, now) -> RateLimitCheck`
  - `max_requests_per_window` / `window_seconds` bazlı.
  - Limit aşımında `ReasonCode.PROGRAM_POLICY_DENIED` veya yeni `RATE_LIMIT_DENIED` (eğer eklenirse).

**Dosya**: `src/zest/core/enums.py`
- **Değişiklik**: Yeni `ReasonCode.RATE_LIMIT_DENIED` ekle (mevcut assert'leri etkilemez; yeni testler kullanır).

**Dosya**: `src/zest/application/execute_planned_experiment.py`
- **Fonksiyon**: `authorize`
  - Core `evaluate_execution` çağrısından ÖNCE rate-limit kontrolü ekle.
  - Aşış varsa `ResearchLoopOutcome` DISPATCH_DENIED ile döner.
  - Audit event + ledger kaydı (BudgetConsumptionRecord `resource_type="RATE_LIMIT_DENIED"` veya audit event yeterli).

**Testler**:
- `tests/unit/application/test_execute_planned_experiment.py` (varsa) yeni testler.
- `tests/unit/core/test_rate_limit.py` yeni.
- Saat mock'lu deterministik test; sleep yok.

### P4 — Integration + Maturity

**Dosya**: `tests/integration/test_sd_g6_mutation_oast.py`
- PostgreSQL'li uçtan uca:
  - attack surface node → mutation varyant üretimi → `audit_event` ledger.
  - OAST loopback callback → `sensor_observation` → evidence admission.

**Dosya**: `tests/integration/test_sd_g6_rate_limit.py`
- PostgreSQL'li uçtan uca: pencere dolu → `ExecutePlannedExperiment` → `RATE_LIMIT_DENIED`.

**Dosya**: `src/zest/maturity.py`
- Yeni: `GATE_06_STATUS = "PENDING"`
- Docstring paragrafı ekle: "SD-G6 = Mutation Engine + OAST Core + Rate-Limit Enforcement; eski GATE 06 değildir."

**Dosya**: `OPERATIONS.md`
- SD-G6 bölümü: mutation aileleri listesi, OAST token yaşam döngüsü, rate-limit zorlaması, loopback-only test garantisi.

**Alembic**: `alembic/versions/a30_001_oast_token.py`
- Yeni tablo: `oast_token`
  - `token_id`, `research_run_id`, `hypothesis_id`, `target_reference`, `expires_at`, `created_at`.
- Varyantlar ayrı tabloya yazılmaz; `RecordMutationVariants` bunları `audit_event` payload'ına yazar (SoR şişmesin).
- OAST callback'leri için kalıcı tablo yok; callback'ler `sensor_observation` olarak kaydedilir ve mevcut admission zincirinden geçer.

## 5. UYGULAMA SIRASI

1. **P0**: `_enqueue_v3` IN_SCOPE kilidi + unit testi.
2. **P3**: `ProgramPolicyView` rate_limit_profile + `core/rate_limit.py` + `execute_planned_experiment.py` kontrolü + unit testleri.
3. **P1**: Mutation engine modülü + unit testleri.
4. **P2**: OAST core + loopback implementasyonu + unit testleri.
5. **P4**: Integration testleri + `maturity.py` PENDING + `OPERATIONS.md` SD-G6 bölümü.
6. Her aşamada `pytest tests/unit tests/contract -q` yeşil kalır.
7. Son: Kali + PostgreSQL'de full suite yeşil; commit + push.

## 6. KAPANIŞ STANDARDI

- `GATE_06_STATUS = "PENDING"` + docstring.
- `OPERATIONS.md` SD-G6 bölümü.
- Unit+contract: 1076+ PASS, sıfır silinen test.
- Integration: 143+ PASS; mutation varyant → ledger; OAST loopback → evidence; rate-limit DENY uçtan uca.
- E2E: 155 PASS, 5 skipped.
- Mühür bende: push + bağımsız denetim olmadan PASS yazılmaz.
