import { getAnalysis, getDashboard, postRunAction, semanticEventsUrl } from "./api.js";
import { createStore } from "./state.js";
import { escapeHtml } from "./components.js";
import { createInspector } from "./inspector.js";
import { appendLiveEvent, bind as bindMission, render as renderMission } from "./views/mission.js";
import { render as renderSurface } from "./views/surface.js";
import { render as renderResearch } from "./views/research.js";
import { render as renderExperiments } from "./views/experiments.js";
import { render as renderEvidence } from "./views/evidence.js";
import { render as renderAudit } from "./views/audit.js";
import { bind as bindSetup, render as renderSetup } from "./views/setup.js";

const views = [
  ["mission", "01", "Canlı Operasyon", "Seçili çalışmanın operasyonel görünümü", renderMission],
  ["research", "02", "Araştırma", "Niyet, doğrulama ve false-positive sınırları", renderResearch],
  ["execution", "03", "Programlar", "Planlar, denemeler ve worker sonuçları", renderExperiments],
  ["surface", "04", "Kanıtlar", "Kayıtlı yüzey bilgisi", renderSurface],
  ["evidence", "05", "Bulgular", "Observation, evidence ve inceleme sınırları", renderEvidence],
  ["authority", "06", "Politika", "Scope, authorization ve uzlaştırma", renderAudit],
  ["setup", "07", "Ayarlar", "Sınırları belirlenmiş yapılandırma", renderSetup],
];

const descriptions = new Map(views.map(([id, _index, title, description]) => [id, { title, description }]));
const store = createStore();
const records = new Map();
const nav = document.querySelector("#primaryNav");
const root = document.querySelector("#viewRoot");
const inspector = createInspector(document.querySelector("#inspector"), () => store.update({ inspector: null }));

function renderTruthStrip(analysis, selectedRun) {
  const element = document.querySelector("#truthStrip");
  element.hidden = true;
  element.innerHTML = "";
}

function translateState(value) {
  const labels = { RUNNING: "ÇALIŞIYOR", ACTIVE: "AKTİF", EXECUTING: "YÜRÜTÜLÜYOR", PAUSED: "DURAKLATILDI", WAITING: "BEKLİYOR", WAITING_HUMAN: "İNSAN BEKLENİYOR", STOPPED: "DURDURULDU", FAILED: "HATA", RUNTIME_FAULT: "RUNTIME HATASI", COMPLETED: "TAMAMLANDI", READY: "HAZIR", LIVE: "CANLI", UNKNOWN: "BİLİNMİYOR" };
  return labels[String(value || "UNKNOWN").toUpperCase()] || String(value || "BİLİNMİYOR");
}

function currentRun(snapshot, id) {
  return (snapshot?.database?.runs || []).find((run) => run.research_run_id === id) || null;
}

function registerRecord(key, record) {
  const safeKey = `${store.getState().selectedRunId}:${key}`;
  records.set(safeKey, record);
  return safeKey;
}

function updateNavigation(active) {
  const workspace = new Set(["mission", "execution", "research", "surface", "evidence"]);
  const labels = { mission: "Genel Bakış", execution: "Programlar", research: "Araştırma", surface: "Kanıtlar", evidence: "Bulgular", authority: "Politika", setup: "Ayarlar" };
  const button = ([id, index, title]) => `<button class="nav-item ${workspace.has(id) ? "nav-workspace" : "nav-system"}" type="button" data-view="${id}" aria-label="${escapeHtml(labels[id] || title)}" title="${escapeHtml(labels[id] || title)}" aria-current="${id === active ? "page" : "false"}"><span class="nav-index" aria-hidden="true">${index}</span><span class="nav-label">${labels[id] || escapeHtml(title)}</span></button>`;
  nav.innerHTML = `<div class="nav-group"><span class="rail-group-label">Workspace</span>${views.filter(([id]) => workspace.has(id)).map(button).join("")}</div><div class="nav-group nav-group-system"><span class="rail-group-label">System</span>${views.filter(([id]) => !workspace.has(id)).map(button).join("")}</div>`;
}

function render() {
  const state = store.getState();
  const view = views.find(([id]) => id === state.activeView) || views[0];
  updateNavigation(view[0]);
  const metadata = descriptions.get(view[0]);
  document.querySelector("#viewTitle").textContent = metadata.title;
  document.querySelector("#viewDescription").textContent = metadata.description;
  const context = { ...state, snapshot: state.dashboardSnapshot, analysis: state.selectedAnalysis, registerRecord, refreshDashboard };
  root.innerHTML = state.dashboardError && !state.dashboardSnapshot ? `<div class="notice notice-danger">Dashboard kullanılamıyor: ${escapeHtml(state.dashboardError)}</div>` : view[0] === "setup" ? renderSetup(context) : view[4](context);
  if (view[0] === "setup") bindSetup(context);
  if (view[0] === "mission") bindMission(root);
  const selected = currentRun(state.dashboardSnapshot, state.selectedRunId);
  renderTruthStrip(state.selectedAnalysis, selected);
  renderTransport(state);
  document.querySelector("#contextSummary").textContent = selected ? (selected.program_id || selected.research_run_id || "Seçili çalışma") : "Seçili çalışma yok";
  const health = state.dashboardSnapshot?.operator?.database?.health || state.dashboardSnapshot?.database?.state || "UNKNOWN";
  const healthChip = document.querySelector("#healthChip");
  const healthIsRelevant = health && !/healthy|available|pass/i.test(String(health)) && String(health).toUpperCase() !== "UNKNOWN";
  const hasHealth = Boolean(healthIsRelevant);
  healthChip.hidden = !hasHealth;
  healthChip.textContent = hasHealth ? "Dikkat gerekli" : "";
  healthChip.className = "status-chip status-warning";
  const selector = document.querySelector("#runSelector");
  const runs = state.dashboardSnapshot?.database?.runs || [];
  selector.innerHTML = `<option value="">Seçili çalışma yok</option>${runs.map((run) => `<option value="${escapeHtml(run.research_run_id)}">${escapeHtml(run.research_run_id)}</option>`).join("")}`;
  selector.value = state.selectedRunId;
  if (state.analysisLoading) root.insertAdjacentHTML("afterbegin", `<div class="notice">Seçili çalışma analizi yükleniyor…</div>`);
  if (state.analysisError) root.insertAdjacentHTML("afterbegin", `<div class="notice notice-danger">Analiz kullanılamıyor: ${escapeHtml(state.analysisError)}</div>`);
}

let dashboardTimer = null;
let analysisTimer = null;
let analysisKey = "";
let eventSource = null;
let transportPollTimer = null;
let transportRunId = "";
let transportCursor = "";
let backgroundAnalysisRun = "";

function renderTransport(state) {
  const chip = document.querySelector("#transportChip");
  const value = state.transportState || "OFFLINE";
  const labels = { LIVE: "Canlı", RECONNECTING: "Yeniden bağlanıyor…", POLLING: "Yedek bağlantı · Polling", OFFLINE: "Bağlantı kesildi" };
  const classes = { LIVE: "active", RECONNECTING: "warning", POLLING: "info", OFFLINE: "unknown" };
  chip.hidden = value === "LIVE";
  chip.textContent = labels[value] || value;
  chip.className = `status-chip status-${classes[value] || "unknown"}`;
}

function stopLiveTransport() {
  if (eventSource) eventSource.close();
  eventSource = null;
  if (transportPollTimer) clearInterval(transportPollTimer);
  transportPollTimer = null;
  transportRunId = "";
}

function startLiveTransport(runId) {
  stopLiveTransport();
  transportCursor = "";
  store.update({ transportState: runId ? "RECONNECTING" : "OFFLINE", lastEventId: "" });
  if (!runId) return;
  transportRunId = runId;
  const poll = () => {
    const state = store.getState();
    if (state.selectedRunId === runId && state.transportState !== "LIVE" && !state.analysisLoading) {
      loadAnalysis(runId, { preserve: true });
    }
  };
  const enablePolling = () => {
    store.update({ transportState: "POLLING" });
    if (!transportPollTimer) transportPollTimer = setInterval(poll, 5000);
    poll();
  };
  if (typeof EventSource === "undefined") {
    enablePolling();
    return;
  }
  eventSource = new EventSource(semanticEventsUrl(runId));
  eventSource.onopen = () => {
    if (transportRunId !== runId) return;
    if (transportPollTimer) clearInterval(transportPollTimer);
    transportPollTimer = null;
    store.update({ transportState: "LIVE" });
  };
  const handleSemanticEvent = (event) => {
    if (transportRunId !== runId || store.getState().selectedRunId !== runId) return;
    const payload = (() => { try { return JSON.parse(event.data || "{}"); } catch { return null; } })();
    const lastEventId = event.lastEventId || payload?.activity_id || "";
    if (lastEventId && lastEventId === transportCursor) return;
    transportCursor = lastEventId;
    if (lastEventId) store.update({ lastEventId }, { notify: false });
    if (payload) appendLiveEvent(payload, registerRecord);
    loadAnalysis(runId, { preserve: true, background: true });
  };
  eventSource.onmessage = handleSemanticEvent;
  eventSource.addEventListener("semantic_activity", handleSemanticEvent);
  eventSource.onerror = () => {
    if (transportRunId !== runId) return;
    enablePolling();
  };
}

async function refreshDashboard() {
  if (store.getState().dashboardLoading) return;
  store.update({ dashboardLoading: true });
  try {
    const snapshot = await getDashboard();
    const runs = snapshot.database?.runs || [];
    const selected = store.getState().selectedRunId;
    const next = runs.some((run) => run.research_run_id === selected) ? selected : (runs[0]?.research_run_id || "");
    store.update({ dashboardSnapshot: snapshot, dashboardLoading: false, dashboardError: "", selectedRunId: next });
    if (next !== selected) {
      startLiveTransport(next);
      if (next) await loadAnalysis(next);
    }
  } catch (error) {
    store.update({ dashboardLoading: false, dashboardError: error.message });
  }
}

async function loadAnalysis(runId, { preserve = false, background = false } = {}) {
  if (!runId) { analysisKey = ""; store.update({ selectedAnalysis: null, analysisError: "", analysisLoading: false }); return; }
  if (background && backgroundAnalysisRun === runId) return;
  if (analysisKey === runId && store.getState().analysisLoading) return;
  analysisKey = runId;
  backgroundAnalysisRun = background ? runId : "";
  if (!background) store.update({ ...(preserve ? {} : { selectedAnalysis: null }), analysisError: "", analysisLoading: true });
  try {
    const analysis = await getAnalysis(runId);
    if (store.getState().selectedRunId === runId) store.update({ selectedAnalysis: analysis, analysisLoading: false }, { notify: !background });
  } catch (error) {
    if (store.getState().selectedRunId === runId && !background) store.update({ analysisError: error.message, analysisLoading: false });
  } finally {
    if (backgroundAnalysisRun === runId) backgroundAnalysisRun = "";
  }
}

nav.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-view]");
  if (!button) return;
  store.update({ activeView: button.dataset.view });
});
document.querySelector("#runSelector").addEventListener("change", (event) => { analysisKey = ""; store.update({ selectedRunId: event.target.value, selectedAnalysis: null, analysisError: "" }); startLiveTransport(event.target.value); loadAnalysis(event.target.value); });
document.querySelector("#refreshButton").addEventListener("click", refreshDashboard);
document.querySelector("#mobileNavToggle").addEventListener("click", () => {
  const open = document.body.classList.toggle("mobile-nav-open");
  const toggle = document.querySelector("#mobileNavToggle");
  toggle.setAttribute("aria-expanded", String(open));
  toggle.setAttribute("aria-label", open ? "Menüyü kapat" : "Menüyü aç");
});
root.addEventListener("click", async (event) => {
  const recordButton = event.target.closest("button[data-inspector-key]");
  if (recordButton) {
    const record = records.get(recordButton.dataset.inspectorKey);
    if (record) inspector.open(recordButton.dataset.inspectorTitle || "Persisted record", record, "Sanitized HQ projection");
    return;
  }
  const actionButton = event.target.closest("button[data-run-action]");
  if (!actionButton) return;
  const runId = actionButton.dataset.runId;
  const action = actionButton.dataset.runAction;
  actionButton.disabled = true;
  try { await postRunAction(runId, action); await refreshDashboard(); await loadAnalysis(runId); }
  catch (error) { actionButton.insertAdjacentHTML("afterend", `<span class="control-status">${escapeHtml(error.message)}</span>`); }
  finally { actionButton.disabled = false; }
});
nav.addEventListener("click", () => {
  document.body.classList.remove("mobile-nav-open");
  const toggle = document.querySelector("#mobileNavToggle");
  toggle.setAttribute("aria-expanded", "false");
  toggle.setAttribute("aria-label", "Menüyü aç");
});

store.subscribe(render);
render();
refreshDashboard();
dashboardTimer = setInterval(refreshDashboard, 5000);
analysisTimer = setInterval(() => { const id = store.getState().selectedRunId; if (id && store.getState().transportState !== "LIVE" && !store.getState().analysisLoading) loadAnalysis(id, { preserve: true }); }, 10000);
window.addEventListener("beforeunload", () => { clearInterval(dashboardTimer); clearInterval(analysisTimer); stopLiveTransport(); });
