# SD-G9 Plan — HunterScore Scheduler + Identity Binding

> Saldırı Dönemi GATE 9. Eski GATE 09 (Exploration/Temporal benchmark, a15) ile karıştırılmaz.

## P1 — Kimlik Bağlama (G8 D7 Notunun Kapanışı)

### 1.1 Veri Katmanı

| Dosya | Değişiklik | Gerekçe |
|-------|-----------|---------|
| `alembic/versions/a33_001_hypothesis_identity.py` | Yeni migration: `hypothesis` tablosuna `identity_id TEXT NULLABLE` ekle; down_revision `a32_001_coverage_debt_snapshot`. | Hipotez artık kimlik bağlı; mevcut satırlar NULL = eski agnostik semantik. |
| `src/zest/data/postgres/tables.py` | `hypothesis` Table'a `Column("identity_id", Text, nullable=True)` ekle. | SQLAlchemy metadata sync. |
| `src/zest/data/records.py` | `HypothesisRecord.identity_id: str | None = None` + post-init validasyon (None veya non-empty string). | Data-layer record. |
| `src/zest/data/postgres/mapping.py` | `hypothesis_from_row` identity_id map et. | Record rebuild. |
| `src/zest/data/postgres/unit_of_work.py` | Gerek yok; hypothesis repository zaten full-row insert yapıyor. | — |

### 1.2 Üretim Hattı

| Dosya | Değişiklik | Gerekçe |
|-------|-----------|---------|
| `src/zest/application/generate_hunt_hypotheses.py` | `GenerateHuntHypothesesCommand` aynı kalır. `execute`: her `(node, family)` için tek değil, `node.identity_ids` üzerinden her kimlik için ayrı `HypothesisRecord` üretir; identity_id record'a yazılır. Kimliksiz node → `ANONYMOUS`. `GenerateHuntHypothesesResult.hypothesis_sources` 4-tuple `(hypothesis_id, node_id, family_id, identity_id)` olur. `_generation_audit` payload'a `identity_id` ekler; `claim` içinde `{identity_id}` bağlamı varsa template expand edilir. | Hipotez = (node, family, identity) üçlüsü. |
| `src/zest/application/hunt_validation.py` | `ValidateHuntTiers.execute` hypothesis.identity_id okuyabilir; V3 queue record'a `identity_id` alanı eklenir (mevcut `HuntV3QueueRecord` zaten yok; a30 migration `hunt_v3_queue` tablosuna ekleme gerekebilir — Bkz. 1.3). | V3 kuyruk onay kapısı kimlik bağlamını taşır. |
| `src/zest/application/coverage/hypothesis_view.py` | `build_coverage_hypothesis_view`: `hypothesis.identity_id` doğrudan `CoverageHypothesisView.identity_id` olarak kullanılır; yalnızca audit event'ten `identity_id` fallback olarak çekilir (eski kayıtlar). | Kimlikli hipotezler artık yalnızca kendi hücrelerini etkiler. |
| `src/zest/research/coverage/debt.py` | `compute_coverage_debt`: identity_id None olan hipotez tüm hücrelere yayılır (G8 geriye uyumluluk); identity_id non-None ise yalnızca `(node, family, identity)` hücresine etki eder. Docstring D7 sınırıyla güncellenir. | G8 semantiği daraltılır; G9'dan önceki NULL kayıtlar bozulmaz. |
| `src/zest/research/coverage/types.py` | Gerek yok; `CoverageHypothesisView.identity_id: str | None` zaten mevcut. | — |

### 1.3 V3 Queue Kimlik Genişlemesi (Gerekirse)

`hunt_v3_queue` tablosunda `identity_id` yoksa a33 migration'a ekle:
- `op.add_column("hunt_v3_queue", sa.Column("identity_id", sa.Text(), nullable=True))`
- `HuntV3QueueRecord` ve mapping güncellenir.
- `_enqueue_v3` identity_id doldurur.

## P2 — HunterScore Formülü (Saf Katman)

### 2.1 Yeni Dosyalar

| Dosya | İçerik |
|-------|--------|
| `src/zest/research/scheduler/__init__.py` | Public export: `schedule`, `HunterScore`, `ScoredCell`, `ScoreExplain`, `FamilyStats`. |
| `src/zest/research/scheduler/types.py` | `ScoreExplain`, `FamilyStats`, `ScoredCell`, `HunterScoreMode` (FULL / CHEAP_ONLY) dataclass/enum'ları. |
| `src/zest/research/scheduler/score.py` | `schedule(matrix, family_stats, budget_view) -> tuple[ScoredCell, ...]`; sabit katsayılar; deterministik tie-break; `explain` dökümü. |

### 2.2 Formül (K1 — Açıklanabilirlik)

Her `UNTESTED`/`HYPOTHESIZED`/`V1_PASSED`/`V2_PASSED`/`V3_QUEUED` hücre için:

```
base_score = state_weight(state)  # UNTESTED=40, HYPOTHESIZED=35, V1_PASSED=30, V2_PASSED=20, V3_QUEUED=10
success_rate = supported / (supported + falsified + 1)
family_bonus = success_rate * 20
novelty_hours = (now - first_seen_at).hours  # node.created_at veya discovery_fact.created_at proxy
novelty_penalty = min(novelty_hours * 0.5, 20)  # yaşlandıkça düşük öncelik, max 20
budget_penalty = CHEAP_ONLY and family.validation_tier == "V3" ? -100 : 0
score = base_score + family_bonus - novelty_penalty + budget_penalty
```

- Tüm katsayılar `score.py` içinde büyük harfli sabitler.
- `ScoreExplain` her faktörün ham değerini ve katkısını taşır.
- Eşitlik tie-break: `(node_canonical_key, identity_id, family_id)` tuple sıralaması.

### 2.3 Girdiler

- `family_stats`: `dict[str, FamilyStats]`; `FamilyStats(supported: int, falsified: int, inconclusive: int)`.
- `budget_view`: `tuple[bool, ...]` yerine basit `cheap_path_only: bool` flag. Scheduler bütçe hesabını yapmaz; G4 view'ından gelen booleanı kullanır.

## P3 — Scheduler Use Case + Cycle Tüketimi

### 3.1 Yeni Use Case

| Dosya | İçerik |
|-------|--------|
| `src/zest/application/run_hunt_scheduler.py` | `RunHuntScheduler` + `RunHuntSchedulerCommand` + `RunHuntSchedulerResult`. Matrisi `CoverageDebtView` ile hesaplar/ya da parametre olarak alır. `family_stats` için audit eventlerden sayım yapar. `ProgramDailyBudgetUsage` ile bütçe modunu belirler. Üst N hücreyi `HUNT_SCHEDULE_RECOMMENDED` audit eventine yazar; V3 kuyruğuna doğrudan yazmaz. `no_op` = matris değişmemiş. |

### 3.2 Cycle Tüketim Dikişi

| Dosya | Değişiklik |
|-------|-----------|
| `src/zest/application/run_hunt_cycle.py` | `RunHuntCycleCommand`e `schedule: tuple[ScoredCell, ...] | None = None` ekle. Eğer `schedule` varsa, `GenerateHuntHypotheses` sadece schedule'daki `(node_id, family_id, identity_id)` hücreleri için hipotez üretir. Yoksa eski davranış (tüm graph). `RunHuntCycle` hâlâ V3 kuyruğuna yazar; onay kapısı değişmez. |

### 3.3 Audit Event

- Event type: `HUNT_SCHEDULE_RECOMMENDED`
- Payload: `research_run_id`, `node_canonical_key`, `identity_id`, `family_id`, `score`, `rank`, `explain` özeti.

## P4 — Test Planı

### 4.1 Unit

| Test | Dosya |
|------|-------|
| `HypothesisRecord` identity_id validasyonu | `tests/unit/data/test_records.py` (varsa) veya yeni `tests/unit/research/test_hypothesis_identity.py` |
| Identity-aware hipotez yalnız kendi hücresini kapatır; NULL hâlâ yayılır | `tests/unit/research/coverage/test_debt.py` (adaptasyon + yeni test) |
| `GenerateHuntHypotheses` identity başına ayrı kayıt üretir | `tests/unit/application/test_generate_hunt_hypotheses.py` (varsa) veya yeni |
| HunterScore determinizmi + explain | `tests/unit/research/scheduler/test_score.py` |
| Tie-break | `tests/unit/research/scheduler/test_score.py` |
| Bütçe CHEAP_ONLY → V3 aileleri sona atar | `tests/unit/research/scheduler/test_score.py` |
| Nöbet no-op (matris aynı) | `tests/unit/application/test_run_hunt_scheduler.py` |

### 4.2 Integration (PostgreSQL)

| Test | Dosya |
|------|-------|
| a33 migration smoke + head = a33 | `tests/unit/data/test_alembic_smoke.py` güncellemesi |
| Kimlik bağlı hipotez üretimi → coverage'da yalnız ilgili hücre HYPOTHESIZED | `tests/integration/test_sd_g9_hunterscore_scheduler.py` |
| Scheduler → `HUNT_SCHEDULE_RECOMMENDED` eventi + RunHuntCycle tüketimi | `tests/integration/test_sd_g9_hunterscore_scheduler.py` |
| Bütçe dolu programda cheap-path sıralaması | `tests/integration/test_sd_g9_hunterscore_scheduler.py` |

### 4.3 Head Güncellemeleri

Tüm integration/e2e testlerdeki `a32_001_coverage_debt_snapshot` head assertion'ları `a33_001_hypothesis_identity` olarak güncellenir (meşru alembic head bump).

## P5 — Kapanış

| Dosya | Değişiklik |
|-------|-----------|
| `src/zest/maturity.py` | `GATE_09_STATUS = "PASS"` + SD-G9 docstring (eski GATE 09 değildir notu). |
| `OPERATIONS.md` | SD-G9 bölümü: identity binding, HunterScore v2 formülü, scheduler/cycle ayrımı, test kanıtı. |

## Başlangıç Sırası

P1.1 → P1.2 → P1.3 (gerekirse) → P2 → P3 → P4 → P5.

Her aşamada `pytest tests/unit tests/contract -q` yeşil tutulur.
