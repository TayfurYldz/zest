import { getAnalysis, getDashboard, postRunAction, semanticEventsUrl } from "./api.js";
import { createStore } from "./state.js";
import { escapeHtml, statusChip } from "./components.js";
import { createInspector } from "./inspector.js";
import { render as renderMission } from "./views/mission.js";
import { render as renderSurface } from "./views/surface.js";
import { render as renderResearch } from "./views/research.js";
import { render as renderExperiments } from "./views/experiments.js";
import { render as renderEvidence } from "./views/evidence.js";
import { render as renderAudit } from "./views/audit.js";
import { bind as bindSetup, render as renderSetup } from "./views/setup.js";

const views = [
  ["mission", "01", "Mission / Live", "Selected-run operational cockpit", renderMission],
  ["research", "02", "Research", "Intent, verification, and false-positive boundaries", renderResearch],
  ["execution", "03", "Execution", "Plans, attempts, and worker outcomes", renderExperiments],
  ["surface", "04", "Surface", "Persisted surface intelligence", renderSurface],
  ["evidence", "05", "Evidence", "Observation, evidence, and review boundaries", renderEvidence],
  ["authority", "06", "Authority", "Scope, authorization, and reconciliation", renderAudit],
  ["setup", "07", "Program Setup", "Bounded configuration bootstrap", renderSetup],
];

const descriptions = new Map(views.map(([id, _index, title, description]) => [id, { title, description }]));
const store = createStore();
const records = new Map();
const nav = document.querySelector("#primaryNav");
const root = document.querySelector("#viewRoot");
const inspector = createInspector(document.querySelector("#inspector"), () => store.update({ inspector: null }));

function renderTruthStrip(analysis, selectedRun) {
  const element = document.querySelector("#truthStrip");
  if (!selectedRun) {
    element.innerHTML = `<div class="truth-strip-empty"><span class="section-label">Truth strip</span><strong>NO RUN SELECTED</strong><span class="muted">Choose a persisted run to load authoritative state.</span></div>`;
    return;
  }
  const truth = analysis?.truth;
  if (!truth) {
    element.innerHTML = `<div class="truth-strip-empty"><span class="section-label">Truth strip</span><strong>${escapeHtml(selectedRun.research_run_id)}</strong><span class="muted">Analysis unavailable · operational truth is UNKNOWN</span>${statusChip("UNKNOWN")}</div>`;
    return;
  }
  const items = [
    ["effective state", truth.effective_operational_state],
    ["lifecycle", truth.persisted_lifecycle_state],
    ["phase", truth.current_phase],
    ["runtime", truth.runtime_liveness],
    ["attention", truth.human_attention_required ? "REQUIRED" : "NONE"],
  ];
  element.innerHTML = `<div class="truth-strip-heading"><span class="section-label">Truth strip</span><span class="mono">${escapeHtml(truth.research_run_id || selectedRun.research_run_id)}</span></div><div class="truth-strip-values">${items.map(([label, value]) => `<div class="truth-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value ?? "UNKNOWN"))}</strong></div>`).join("")}</div>`;
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
  nav.innerHTML = views.map(([id, index, title]) => `<button class="nav-item" type="button" data-view="${id}" aria-label="${escapeHtml(title)}" aria-current="${id === active ? "page" : "false"}"><span class="nav-index">${index}</span>${escapeHtml(title)}</button>`).join("");
}

function render() {
  const state = store.getState();
  const view = views.find(([id]) => id === state.activeView) || views[0];
  updateNavigation(view[0]);
  const metadata = descriptions.get(view[0]);
  document.querySelector("#viewTitle").textContent = metadata.title;
  document.querySelector("#viewDescription").textContent = metadata.description;
  const context = { ...state, snapshot: state.dashboardSnapshot, analysis: state.selectedAnalysis, registerRecord, refreshDashboard };
  root.innerHTML = state.dashboardError && !state.dashboardSnapshot ? `<div class="notice notice-danger">Dashboard unavailable: ${escapeHtml(state.dashboardError)}</div>` : view[0] === "setup" ? renderSetup(context) : view[4](context);
  if (view[0] === "setup") bindSetup(context);
  const selected = currentRun(state.dashboardSnapshot, state.selectedRunId);
  renderTruthStrip(state.selectedAnalysis, selected);
  renderTransport(state);
  document.querySelector("#contextSummary").textContent = selected ? `${selected.program_id || "program UNKNOWN"} / ${selected.research_run_id} / ${selected.state || "UNKNOWN"}` : "No research run selected — projection idle";
  const health = state.dashboardSnapshot?.operator?.database?.health || state.dashboardSnapshot?.database?.state || "UNKNOWN";
  const healthChip = document.querySelector("#healthChip");
  healthChip.textContent = String(health).toUpperCase();
  healthChip.className = `status-chip status-${/healthy|available|pass/.test(String(health).toLowerCase()) ? "healthy" : "unknown"}`;
  const selector = document.querySelector("#runSelector");
  const runs = state.dashboardSnapshot?.database?.runs || [];
  selector.innerHTML = `<option value="">No run selected</option>${runs.map((run) => `<option value="${escapeHtml(run.research_run_id)}">${escapeHtml(run.research_run_id)} · ${escapeHtml(run.state || "UNKNOWN")}</option>`).join("")}`;
  selector.value = state.selectedRunId;
  if (state.analysisLoading) root.insertAdjacentHTML("afterbegin", `<div class="notice">Loading selected-run analysis…</div>`);
  if (state.analysisError) root.insertAdjacentHTML("afterbegin", `<div class="notice notice-danger">Analysis unavailable: ${escapeHtml(state.analysisError)}</div>`);
}

let dashboardTimer = null;
let analysisTimer = null;
let analysisKey = "";
let eventSource = null;
let transportPollTimer = null;
let transportRunId = "";

function renderTransport(state) {
  const chip = document.querySelector("#transportChip");
  const value = state.transportState || "OFFLINE";
  const classes = { LIVE: "active", RECONNECTING: "warning", POLLING: "info", OFFLINE: "unknown" };
  chip.textContent = value;
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
    if (event.lastEventId && event.lastEventId === store.getState().lastEventId) return;
    store.update({ lastEventId: event.lastEventId || "" });
    loadAnalysis(runId, { preserve: true });
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

async function loadAnalysis(runId, { preserve = false } = {}) {
  if (!runId) { analysisKey = ""; store.update({ selectedAnalysis: null, analysisError: "", analysisLoading: false }); return; }
  if (analysisKey === runId && store.getState().analysisLoading) return;
  analysisKey = runId;
  store.update({ ...(preserve ? {} : { selectedAnalysis: null }), analysisError: "", analysisLoading: true });
  try {
    const analysis = await getAnalysis(runId);
    if (store.getState().selectedRunId === runId) store.update({ selectedAnalysis: analysis, analysisLoading: false });
  } catch (error) {
    if (store.getState().selectedRunId === runId) store.update({ analysisError: error.message, analysisLoading: false });
  }
}

nav.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-view]");
  if (!button) return;
  store.update({ activeView: button.dataset.view });
});
document.querySelector("#runSelector").addEventListener("change", (event) => { analysisKey = ""; store.update({ selectedRunId: event.target.value, selectedAnalysis: null, analysisError: "" }); startLiveTransport(event.target.value); loadAnalysis(event.target.value); });
document.querySelector("#refreshButton").addEventListener("click", refreshDashboard);
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

store.subscribe(render);
render();
refreshDashboard();
dashboardTimer = setInterval(refreshDashboard, 5000);
analysisTimer = setInterval(() => { const id = store.getState().selectedRunId; if (id && store.getState().transportState !== "LIVE" && !store.getState().analysisLoading) loadAnalysis(id, { preserve: true }); }, 10000);
window.addEventListener("beforeunload", () => { clearInterval(dashboardTimer); clearInterval(analysisTimer); stopLiveTransport(); });
