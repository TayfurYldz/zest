import { controlsForRun, escapeHtml } from "../components.js";
import { collection, listCollection, unavailable, viewHeader } from "./common.js";
export function render(context) {
  const run = (context.snapshot?.database?.runs || []).find((item) => item.research_run_id === context.selectedRunId);
  const controls = `${viewHeader("Experiment Control", "Approval is not execution authorization. Operational failure is not falsification.")}<div id="runs" class="panel"><div class="panel-head"><h2>Selected run control</h2></div><div class="panel-body">${run ? `<div class="kv-list"><div class="kv"><span class="kv-key">run</span><span class="kv-value mono">${escapeHtml(run.research_run_id)}</span></div><div class="kv"><span class="kv-key">state</span><span class="kv-value">${escapeHtml(run.state || "UNKNOWN")}</span></div></div>` + controlsForRun(run) : `<div class="empty-state">NO RUN SELECTED</div>`}</div></div>`;
  if (!context.analysis) return `${controls}${unavailable()}`;
  const a = context.analysis;
  return `${controls}<div class="workspace-grid">${collection(a, ["execution", "experiments"], "Experiments", context, ["experiment_id", "state", "created_at"])}${collection(a, ["execution", "plans"], "Experiment plans", context, ["experiment_id", "capability", "target"])}${collection(a, ["execution", "attempts"], "Execution attempts", context, ["attempt_id", "state", "worker_capability"])}${collection(a, ["execution", "worker_results"], "Worker results", context, ["worker_result_id", "status", "created_at"])}${collection(a, ["execution", "observations"], "Observations", context, ["observation_id", "created_at", "worker_result_id"])}${listCollection(a, ["execution", "preflights"], "Preflight reports", context)}</div>`;
}
