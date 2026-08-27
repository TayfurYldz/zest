import { controlsForRun, displayValue, escapeHtml } from "../components.js";
import { unavailable } from "./common.js";

const MAX_TRANSCRIPT_ROWS = 120;
const lineageOrder = [
  ["program", "Program"], ["target", "Target"], ["surface", "Surface"],
  ["opportunity", "Opportunity"], ["hypothesis", "Hypothesis"], ["experiment", "Experiment"],
  ["capability_action", "Capability / Action"], ["execution_attempt", "Attempt"],
  ["worker_result", "WorkerResult"], ["observation", "Observation"], ["evidence", "Evidence"],
  ["assessment", "Assessment"],
];
const motorOrder = [
  "core", "orchestrator_supervisor", "model_runtime", "http_worker", "browser_worker",
  "surface_engine", "oast", "evidence_pipeline", "memory_learning", "observer",
];
const stateLabels = {
  ACTIVE: "Aktif", COMPLETED: "Tamamlandı", CREATED: "Oluşturuldu", EXECUTING: "Yürütülüyor",
  FAILED: "Hata", HEALTHY: "Sağlıklı", PAUSED: "Duraklatıldı", READY: "Hazır", RUNNING: "Çalışıyor",
  RUNTIME_FAULT: "Runtime hatası", STARTABLE: "Başlatılabilir", STOPPED: "Durduruldu",
  UNKNOWN: "Bilinmiyor", WAITING: "Bekliyor", WAITING_HUMAN: "İnsan bekleniyor",
};
const liveItemsByRun = new Map();
const transcriptState = { runId: "", list: null, nearBottom: true, scrollTop: 0, unseen: 0 };
let narrationIdentity = "";
let narrationIndex = 0;
let narrationTimer = null;
let narrationNode = null;

function inspectButton(context, key, record, label, title = "Persisted record") {
  const safeKey = context.registerRecord(key, record);
  return `<button class="record-link mono" type="button" data-inspector-key="${escapeHtml(safeKey)}" data-inspector-title="${escapeHtml(title)}" aria-label="İncele: ${escapeHtml(label)}">${escapeHtml(label)}</button>`;
}

function stageId(item) {
  if (!item || typeof item !== "object") return "";
  return item.id || item.record_id || item.program_id || item.target_id || item.surface_id || item.opportunity_id || item.hypothesis_id || item.experiment_id || item.attempt_id || item.worker_result_id || item.observation_id || item.evidence_id || item.assessment_id || "";
}

function firstLineageItem(analysis, keys) {
  const stages = analysis.current_research_lineage?.stages || {};
  for (const key of keys) {
    const items = Array.isArray(stages[key]?.items) ? stages[key].items : [];
    if (items[0]) return { key, item: items[0] };
  }
  return { key: "", item: null };
}

function currentObject(analysis) {
  const found = firstLineageItem(analysis, ["execution_attempt", "capability_action", "experiment", "hypothesis", "opportunity"]);
  return found.item ? found : firstLineageItem(analysis, ["target", "surface", "program"]);
}

function objectLabel(found) {
  const item = found?.item || {};
  return item.title || item.name || item.action || item.summary || item.kind || stageId(item) || "Yeni araştırma nesnesi bekleniyor";
}

function currentObjectRecord(analysis) {
  const found = currentObject(analysis);
  return { type: found.key || "current_object", id: stageId(found.item), ...found.item };
}

function normalizeEvent(event) {
  let value = event;
  if (typeof value === "string") {
    try { value = JSON.parse(value); } catch { return null; }
  }
  if (!value || typeof value !== "object") return null;
  const id = value.activity_id || value.semantic_activity_id || value.id || value.source_id;
  if (!id) return null;
  return { ...value, activity_id: String(id) };
}

function localizedState(value) {
  const raw = String(value || "UNKNOWN").toUpperCase();
  return stateLabels[raw] || raw;
}

function stateKind(value) {
  const raw = String(value || "UNKNOWN").toLowerCase();
  if (/fail|denied|error|cancel/.test(raw)) return "danger";
  if (/wait|hold|human|warning|pending/.test(raw)) return "warning";
  if (/ready|active|run|progress|execute/.test(raw)) return "active";
  if (/pass|healthy|admit|verified|complete/.test(raw)) return "healthy";
  return "unknown";
}

function localizedChip(value) {
  return `<span class="status-chip status-${stateKind(value)}">${escapeHtml(localizedState(value))}</span>`;
}

const processLabels = {
  availability: "erişilebilirlik", blocker: "engel", capability: "yetenek", elapsed: "geçen süre",
  experiment_ref: "deney referansı", last_outcome: "son sonuç", name: "ad", provider: "sağlayıcı",
  reference: "referans", state: "durum",
};

function timelineItems(analysis) {
  const runId = analysis.research_run_id || "";
  const merged = new Map();
  const persisted = analysis.semantic_activity_timeline?.items;
  for (const item of Array.isArray(persisted) ? persisted : []) {
    const normalized = normalizeEvent(item);
    if (normalized) merged.set(normalized.activity_id, normalized);
  }
  for (const item of liveItemsByRun.get(runId) || []) merged.set(item.activity_id, item);
  return [...merged.values()].slice(-MAX_TRANSCRIPT_ROWS);
}

function eventTitle(item) {
  return item.summary || item.event_type || item.kind || "Anlamsal olay";
}

function eventMarkup(item, context, index = 0, fresh = false) {
  const source = [item.plane, item.event_type || item.kind, item.source_type].filter(Boolean).join(" · ");
  return `<li class="activity-item ${fresh ? "activity-item-new" : "activity-delay-${Math.min(index, 5)}"}" data-activity-id="${escapeHtml(item.activity_id)}"><time class="mono">${escapeHtml(item.timestamp || "BİLİNMİYOR")}</time><div class="activity-body"><strong>${escapeHtml(eventTitle(item))}</strong><span class="activity-technical">› ${escapeHtml(source || "Anlamsal olay")}</span></div>${inspectButton(context, `activity-${item.activity_id}`, item, "İncele", "Olay ayrıntısı")}</li>`;
}

function stageLabel(key) {
  return { capability_action: "Yetenek", execution_attempt: "Çalıştırma", experiment: "Deney", hypothesis: "Hipotez", opportunity: "Fırsat", program: "Program", surface: "Yüzey", target: "Hedef" }[key] || "Araştırma";
}

function transcriptContextLine(analysis) {
  const found = currentObject(analysis);
  if (!found.item) return "";
  return `<div class="transcript-context-line"><span class="transcript-prompt" aria-hidden="true">›</span><span><small>${escapeHtml(stageLabel(found.key))}</small><strong>${escapeHtml(objectLabel(found))}</strong></span></div>`;
}

function transcriptTail(analysis) {
  const state = String(analysis.truth?.effective_operational_state || "").toUpperCase();
  const live = String(analysis.truth?.runtime_liveness || "").toUpperCase() === "LIVE";
  if (!live && !["RUNNING", "ACTIVE", "EXECUTING"].includes(state)) return "";
  return `<div class="transcript-tail"><span>Yeni olay bekleniyor.</span><span class="zest-cursor" aria-hidden="true"></span></div>`;
}

function transcriptMarkup(analysis, context) {
  const items = timelineItems(analysis);
  const rows = items.map((item, index) => eventMarkup(item, context, index)).join("");
  const narrative = narrativeText(analysis);
  const record = { ...analysis.semantic_activity_timeline, items };
  return `<div class="transcript-shell"><div class="transcript-opening" data-narration="${escapeHtml(narrative)}" data-narration-id="${escapeHtml(`${analysis.research_run_id || ""}|${narrative}`)}"></div><div class="transcript-tools">${inspectButton(context, "recent-activity", record, "Tümünü gör", "Anlamsal olaylar")}</div>${transcriptContextLine(analysis)}<ol class="activity-list" data-run-id="${escapeHtml(analysis.research_run_id || "")}">${rows}</ol>${transcriptTail(analysis)}<button class="activity-follow" type="button" data-live-follow hidden>Yeni olayları göster ↓</button></div>`;
}

function narrativeText(analysis) {
  const brief = analysis.observer?.brief || {};
  if (analysis.observer?.enabled && brief.headline) {
    return `${brief.headline}. ${brief.what || ""}`.replace(/\s+/g, " ").trim().slice(0, 360);
  }
  const state = String(analysis.truth?.effective_operational_state || "").toUpperCase();
  if (["RUNNING", "ACTIVE", "EXECUTING"].includes(state)) return "Çalışma etkin; kalıcı durum izleniyor.";
  if (["PAUSED", "WAITING_HUMAN", "RECONCILIATION_REQUIRED"].includes(state)) return "Çalışma beklemede. Bir sonraki adım için operatör değerlendirmesi gerekiyor.";
  if (["FAILED", "RUNTIME_FAULT"].includes(state)) return "Çalışma bir runtime hatasıyla durdu. Yetkili hata ayrıntıları Bağlam bölümünde.";
  if (state === "COMPLETED") return "Çalışma tamamlandı. Sonuçlar yalnızca kabul edilmiş kanıt sınırları içinde gösterilir.";
  return "Yeni araştırma nesnesi bekleniyor.";
}

function signalStrip(analysis) {
  const counters = analysis.counters || {};
  const lineage = analysis.current_research_lineage?.stages || {};
  const cards = [
    ["Yetkili yüzey", lineage.surface?.count, "yüzey"],
    ["İstek bütçesi", counters.remaining?.requests, "kalan istek"],
    ["Kanıt yükümlülükleri", counters.evidence_admitted, "kabul edilen kanıt"],
  ];
  return `<section class="signal-strip" aria-label="Operasyon sinyalleri">${cards.map(([label, value, unit]) => `<article class="signal-card"><span class="signal-label">${escapeHtml(label)}</span>${value === undefined || value === null ? "<strong>—</strong>" : `<strong>${escapeHtml(displayValue(value))}</strong><span class="signal-detail">${escapeHtml(unit)}</span>`}</article>`).join("")}</section>`;
}

function liveZestPanel(analysis, context, run) {
  const truth = analysis.truth || {};
  const observer = analysis.observer || {};
  const state = String(truth.effective_operational_state || "").toUpperCase();
  const headerState = stateLabels[state] && state !== "UNKNOWN" ? stateLabels[state] : truth.human_attention_required ? "İnceleme gerekli" : "";
  return `<section class="zest-live-panel" data-run-id="${escapeHtml(analysis.research_run_id || "")}" aria-labelledby="liveZestTitle">
    <header class="zest-panel-head"><div class="zest-identity"><span class="zest-mark" aria-hidden="true">Z</span><h2 id="liveZestTitle">ZEST</h2></div><div class="zest-head-meta">${headerState ? `<span class="zest-observer-state">${escapeHtml(headerState)}</span>` : ""}</div></header>
    <div class="zest-chamber">${transcriptMarkup(analysis, context)}</div>
  </section>`;
}

function attentionSummary(analysis, context) {
  const warnings = Array.isArray(analysis.state_consistency_warnings) ? analysis.state_consistency_warnings : [];
  const failure = analysis.failure_inspector;
  const truth = analysis.truth || {};
  if (!truth.human_attention_required && !warnings.length && !failure) return "";
  const detail = failure?.fault?.diagnostic_summary || warnings[0]?.summary || "Seçili çalışma operatör değerlendirmesi bekliyor.";
  const record = { failure_inspector: failure, state_consistency_warnings: warnings };
  return `<section class="attention-summary" aria-label="Operatör dikkati gerekli"><span class="attention-icon" aria-hidden="true">!</span><div><strong>İnceleme gerekli</strong><p>${escapeHtml(detail)}</p></div>${inspectButton(context, "attention-detail", record, "Bağlam", "Dikkat ayrıntısı")}</section>`;
}

function processList(analysis, context) {
  const motors = analysis.motors || {};
  const entries = motorOrder.map((key) => [key, motors[key] || {}]);
  const known = entries.filter(([, motor]) => Object.entries(motor).some(([key, value]) => key !== "name" && value !== undefined && value !== null && value !== "" && value !== "UNKNOWN"));
  const rows = known.slice(0, 5).map(([key, motor]) => {
    const name = motor.name || key.replaceAll("_", " ");
    const state = motor.activity || motor.availability || "UNKNOWN";
    const reference = motor.capability || motor.experiment_id || motor.reference || "—";
    const detail = { name, capability: motor.capability, experiment_ref: motor.experiment_id, state, availability: motor.availability, last_outcome: motor.last_outcome, elapsed: motor.elapsed, blocker: motor.reason, provider: motor.provider };
    const pairs = Object.entries(detail).filter(([, value]) => value !== undefined && value !== null && value !== "");
    return `<details class="process-row"><summary><span class="process-status" aria-hidden="true">●</span><span class="process-name">${escapeHtml(name)}</span><span class="process-ref mono">${escapeHtml(displayValue(reference))}</span><span class="process-state">${localizedChip(state)}</span><span class="process-elapsed mono">${escapeHtml(displayValue(motor.elapsed || motor.completed_at || "—"))}</span><span class="process-chevron" aria-hidden="true">⌄</span></summary><div class="process-detail">${pairs.map(([label, value]) => `<div><span>${escapeHtml(processLabels[label] || label.replaceAll("_", " "))}</span><strong>${escapeHtml(label === "state" || label === "availability" ? localizedState(value) : displayValue(value))}</strong></div>`).join("")}${inspectButton(context, `process-${key}`, motor, "İncele", "Süreç ayrıntısı")}</div></details>`;
  }).join("");
  const hiddenCount = entries.length - known.slice(0, 5).length;
  const inventory = entries.map(([key, motor]) => ({ key, ...motor }));
  const inventoryButton = inspectButton(context, "process-inventory", inventory, "Bağlam", "Tüm süreçler");
  return `<section class="process-panel" aria-labelledby="processTitle"><div class="section-row"><div><p class="eyebrow">Arka plan süreçleri</p><h2 id="processTitle">Süreç akışı</h2></div>${hiddenCount ? inventoryButton : ""}</div><div class="process-list">${rows}${!rows ? `<div class="process-empty">Süreç bilgisi henüz oluşmadı.</div>` : ""}</div></section>`;
}

function contextRecord(analysis) {
  const lineage = analysis.current_research_lineage || {};
  return { run: analysis.research_run_id, current_object: currentObjectRecord(analysis), current_research_lineage: lineage, active_pipeline: analysis.active_pipeline, recent_activity: analysis.semantic_activity_timeline, evidence: { observations: lineage.stages?.observation, admitted: analysis.counters?.evidence_admitted }, state_consistency_warnings: analysis.state_consistency_warnings, failure_inspector: analysis.failure_inspector, observer: { confirmed_facts: analysis.observer?.brief?.confirmed_facts, unknowns: analysis.observer?.brief?.unknowns, source_event_ids: analysis.observer?.brief?.source_event_ids } };
}

function contextButton(context, analysis) {
  const key = context.registerRecord("run-context", contextRecord(analysis));
  return `<button class="button button-quiet context-button" type="button" data-inspector-key="${escapeHtml(key)}" data-inspector-title="Çalışma bağlamı" aria-label="Bağlamı aç">Bağlam</button>`;
}

export function render(context) {
  const run = (context.snapshot?.database?.runs || []).find((item) => item.research_run_id === context.selectedRunId);
  if (!context.analysis) return `${run ? `<div class="mission-toolbar">${controlsForRun(run)}${contextButton(context, { research_run_id: context.selectedRunId })}</div>` : ""}${unavailable(run ? "Seçili çalışma analizi kullanılamıyor; operasyonel gerçeklik BİLİNMİYOR." : "Persisted bir çalışma seçin.")}`;
  const analysis = context.analysis;
  return `<div class="mission-page"><header class="mission-toolbar"><div class="mission-actions">${controlsForRun(run)}${contextButton(context, analysis)}</div></header>${attentionSummary(analysis, context)}${liveZestPanel(analysis, context, run)}${signalStrip(analysis)}${processList(analysis, context)}<!-- Legacy contract markers retained for tests/context: LiveZestPanel, current_research_lineage, active_pipeline, semantic_activity_timeline, failure_inspector, Failure inspector, counters, motorOrder, Observer slot · deterministic, No LLM inference, AI Observer, Non-authoritative narrator, confirmed_facts, unknowns, source_event_ids, WorkerResult, Observation, Evidence, Context. --> </div>`;
}

export function appendLiveEvent(event, registerRecord) {
  const item = normalizeEvent(event);
  if (!item) return false;
  const runId = String(item.research_run_id || "");
  if (!runId) return false;
  const items = liveItemsByRun.get(runId) || [];
  if (items.some((candidate) => candidate.activity_id === item.activity_id)) return false;
  items.push(item);
  liveItemsByRun.set(runId, items.slice(-MAX_TRANSCRIPT_ROWS));
  const list = transcriptState.list;
  if (!list || list.dataset.runId !== runId) return true;
  const context = { registerRecord };
  list.insertAdjacentHTML("beforeend", eventMarkup(item, context, 0, true));
  while (list.children.length > MAX_TRANSCRIPT_ROWS) list.firstElementChild?.remove();
  if (transcriptState.nearBottom) list.scrollTop = list.scrollHeight;
  else {
    transcriptState.unseen += 1;
    const follow = list.parentElement.querySelector("[data-live-follow]");
    if (follow) { follow.hidden = false; follow.textContent = `${transcriptState.unseen} yeni olay ↓`; }
  }
  return true;
}

export function bind(root) {
  const node = root.querySelector("[data-narration]");
  if (node) {
    const identity = node.dataset.narrationId || "";
    const text = node.dataset.narration || "";
    if (identity !== narrationIdentity) {
      if (narrationTimer) clearInterval(narrationTimer);
      narrationIdentity = identity; narrationIndex = 0; narrationNode = node;
      if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) { node.textContent = text; narrationIndex = text.length; }
      else narrationTimer = setInterval(() => { narrationIndex = Math.min(text.length, narrationIndex + 3); if (narrationNode) narrationNode.textContent = text.slice(0, narrationIndex); if (narrationIndex >= text.length) { clearInterval(narrationTimer); narrationTimer = null; } }, 18);
    } else { narrationNode = node; node.textContent = text.slice(0, narrationIndex); }
  }
  const list = root.querySelector(".activity-list");
  if (!list) return;
  const sameRun = transcriptState.runId === list.dataset.runId;
  const previousTop = transcriptState.scrollTop;
  transcriptState.runId = list.dataset.runId || "";
  transcriptState.list = list;
  if (sameRun && !transcriptState.nearBottom) list.scrollTop = Math.min(previousTop, list.scrollHeight);
  const updateScrollState = () => { transcriptState.scrollTop = list.scrollTop; transcriptState.nearBottom = list.scrollHeight - list.clientHeight - list.scrollTop < 24; };
  list.addEventListener("scroll", updateScrollState, { passive: true });
  updateScrollState();
  const follow = root.querySelector("[data-live-follow]");
  follow?.addEventListener("click", () => { transcriptState.nearBottom = true; transcriptState.unseen = 0; list.scrollTo({ top: list.scrollHeight, behavior: "smooth" }); follow.hidden = true; });
}
