import { getAnalysis, getDashboard, postRunAction } from "./api.js";
import { createStore } from "./state.js";
import { escapeHtml } from "./components.js";
import { createInspector } from "./inspector.js";
import { render as renderMission } from "./views/mission.js";
import { render as renderSurface } from "./views/surface.js";
import { render as renderHunter } from "./views/hunter.js";
import { render as renderHypotheses } from "./views/hypotheses.js";
import { render as renderExperiments } from "./views/experiments.js";
import { render as renderModels } from "./views/models.js";
import { render as renderBrowser } from "./views/browser.js";
import { render as renderOast } from "./views/oast.js";
import { render as renderEvidence } from "./views/evidence.js";
import { render as renderCoverage } from "./views/coverage.js";
import { render as renderAudit } from "./views/audit.js";
import { bind as bindSetup, render as renderSetup } from "./views/setup.js";

const views = [
  ["mission", "01", "Mission Control", "Current activity, blockers, and human action", renderMission],
  ["surface", "02", "Attack Surface", "Persisted surface intelligence", renderSurface],
  ["hunter", "03", "Hunter Engine", "Selection and frontier provenance", renderHunter],
  ["hypotheses", "04", "Hypothesis Lab", "Reasoning and research lineage", renderHypotheses],
  ["experiments", "05", "Experiment Control", "Plans, attempts, and observations", renderExperiments],
  ["models", "06", "Model Intelligence", "Persisted model metadata", renderModels],
  ["browser", "07", "Browser Operations", "Actual browser-capable attempts", renderBrowser],
  ["oast", "08", "OAST Operations", "Correlations and admissions", renderOast],
  ["evidence", "09", "Evidence Chain", "Evidence to human review", renderEvidence],
  ["coverage", "10", "Coverage / Closure", "Debt and unresolved frontier", renderCoverage],
  ["audit", "11", "Audit / Safety", "Authorization and reconciliation", renderAudit],
  ["setup", "12", "Program Setup", "Bounded configuration bootstrap", renderSetup],
];

const descriptions = new Map(views.map(([id, _index, title, description]) => [id, { title, description }]));
const store = createStore();
const records = new Map();
const nav = document.querySelector("#primaryNav");
const root = document.querySelector("#viewRoot");
const inspector = createInspector(document.querySelector("#inspector"), () => store.update({ inspector: null }));

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

async function refreshDashboard() {
  if (store.getState().dashboardLoading) return;
  store.update({ dashboardLoading: true });
  try {
    const snapshot = await getDashboard();
    const runs = snapshot.database?.runs || [];
    const selected = store.getState().selectedRunId;
    const next = runs.some((run) => run.research_run_id === selected) ? selected : (runs[0]?.research_run_id || "");
    store.update({ dashboardSnapshot: snapshot, dashboardLoading: false, dashboardError: "", selectedRunId: next });
    if (next && next !== selected) await loadAnalysis(next);
  } catch (error) {
    store.update({ dashboardLoading: false, dashboardError: error.message });
  }
}

async function loadAnalysis(runId) {
  if (!runId) { analysisKey = ""; store.update({ selectedAnalysis: null, analysisError: "", analysisLoading: false }); return; }
  if (analysisKey === runId && store.getState().analysisLoading) return;
  analysisKey = runId;
  store.update({ selectedAnalysis: null, analysisError: "", analysisLoading: true });
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
document.querySelector("#runSelector").addEventListener("change", (event) => { analysisKey = ""; store.update({ selectedRunId: event.target.value, selectedAnalysis: null, analysisError: "" }); loadAnalysis(event.target.value); });
document.querySelector("#refreshButton").addEventListener("click", refreshDashboard);
root.addEventListener("click", async (event) => {
  const recordButton = event.target.closest("button[data-inspector-key]");
  if (recordButton) {
    const record = records.get(recordButton.dataset.inspectorKey);
    if (record) inspector.open("Persisted record", record, "Sanitized HQ projection");
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
analysisTimer = setInterval(() => { const id = store.getState().selectedRunId; if (id && !store.getState().analysisLoading) loadAnalysis(id); }, 10000);
window.addEventListener("beforeunload", () => { clearInterval(dashboardTimer); clearInterval(analysisTimer); });
