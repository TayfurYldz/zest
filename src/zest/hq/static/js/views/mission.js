import { controlsForRun, metric, section, simplePairs, statusChip } from "../components.js";
import { unavailable, viewHeader } from "./common.js";

export function render(context) {
  const snapshot = context.snapshot || {};
  const db = snapshot.database || {};
  const run = (db.runs || []).find((item) => item.research_run_id === context.selectedRunId);
  const analysis = context.analysis;
  const summary = analysis ? analysis.engine_summary || {} : db.summary || {};
  const pulse = analysis ? { state: run?.state, phase: run?.current_phase, authorization: analysis.authority?.postgresql_source_of_truth ? "POSTGRESQL SOURCE OF TRUTH" : "UNKNOWN", projection: "PROJECTION ONLY" } : { state: run?.state, phase: run?.current_phase, authorization: "UNKNOWN", projection: "SELECT A RUN" };
  const metrics = ["hypotheses", "experiments", "execution_attempts", "observations", "evidence", "findings"].map((key) => metric(key.replaceAll("_", " "), summary[key]));
  return `${viewHeader("Mission Control", "What is happening, why it is blocked, and what requires human validation.", controlsForRun(run))}<div class="metric-grid">${metrics.join("")}</div><div class="hero-readout"><div class="panel"><div class="panel-head"><h2>Operational pulse</h2>${statusChip(pulse.state)}</div><div class="panel-body">${simplePairs(pulse)}</div></div><div class="panel"><div class="panel-head"><h2>Run context</h2></div><div class="panel-body">${run ? simplePairs({ run: run.research_run_id, program: run.program_id, phase: run.current_phase, updated: run.updated_at }) : `<div class="empty-state">NO RUN SELECTED</div>`}</div></div></div>${analysis ? section("Authority boundary", `<div class="notice">The displayed analysis is a bounded sanitized projection. It does not authorize execution, create state, dispatch a Worker, or call a Model.</div>`) : unavailable()}`;
}
