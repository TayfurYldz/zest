import { controlsForRun, displayValue, escapeHtml, section, statusChip } from "../components.js";
import { unavailable } from "./common.js";

const lineageOrder = [
  ["program", "Program"], ["target", "Target"], ["surface", "Surface"],
  ["opportunity", "Opportunity"], ["hypothesis", "Hypothesis"],
  ["experiment", "Experiment"], ["capability_action", "Capability / Action"],
  ["execution_attempt", "Attempt"], ["worker_result", "WorkerResult"],
  ["observation", "Observation"], ["evidence", "Evidence"], ["assessment", "Assessment"],
];

const motorOrder = [
  "core", "orchestrator_supervisor", "model_runtime", "http_worker",
  "browser_worker", "surface_engine", "oast", "evidence_pipeline",
  "memory_learning", "observer",
];

function inspectButton(context, key, record, label) {
  const safeKey = context.registerRecord(key, record);
  return `<button class="record-link mono" type="button" data-inspector-key="${escapeHtml(safeKey)}" aria-label="Inspect ${escapeHtml(label)}">${escapeHtml(label)}</button>`;
}

function stageId(item) {
  if (!item || typeof item !== "object") return "";
  return item.id || item.record_id || item.program_id || item.target_id || item.surface_id || item.opportunity_id || item.hypothesis_id || item.experiment_id || item.attempt_id || item.worker_result_id || item.observation_id || item.evidence_id || item.assessment_id || "";
}

function lineageMarkup(analysis, context) {
  const stages = analysis.current_research_lineage?.stages || {};
  const nodes = lineageOrder.map(([key, label]) => {
    const stage = stages[key] || {};
    const items = Array.isArray(stage.items) ? stage.items : [];
    const id = stageId(items[0]);
    const body = id ? inspectButton(context, `lineage-${key}`, items[0], id) : `<strong class="unknown-state">UNKNOWN</strong>`;
    return `<article class="lineage-node ${stage.status === "UNKNOWN" ? "is-unknown" : ""}" role="listitem"><div class="lineage-node-head"><span>${escapeHtml(label)}</span>${statusChip(stage.status || "UNKNOWN")}</div><div class="lineage-node-body">${body}<small>${escapeHtml(String(stage.count ?? 0))} persisted</small></div></article>`;
  }).join('<span class="lineage-arrow" aria-hidden="true">→</span>');
  return `<div class="lineage-track" role="list" aria-label="Current research lineage">${nodes}</div>`;
}

function observerMarkup(analysis, context) {
  // The Observer slot · deterministic remains the no-provider fallback; No LLM inference
  // is needed to render the authoritative cockpit.
  const observer = analysis.observer || {};
  const brief = observer.brief || {};
  const facts = Array.isArray(brief.confirmed_facts) ? brief.confirmed_facts : [];
  const unknowns = Array.isArray(brief.unknowns) ? brief.unknowns : [];
  const sourceIds = Array.isArray(brief.source_event_ids) ? brief.source_event_ids : [];
  const timeline = analysis.semantic_activity_timeline?.items || [];
  const sourceButtons = sourceIds.map((id, index) => {
    const item = timeline.find((candidate) => candidate.activity_id === id) || { activity_id: id, source_event_id: id };
    return inspectButton(context, `observer-source-${index}`, item, id);
  }).join(" ");
  const list = (items, empty) => items.length ? `<ul class="observer-list">${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : `<p class="muted">${empty}</p>`;
  return `<section class="observer-slot" aria-label="AI Observer"><div class="observer-brief"><div class="observer-heading"><div><span class="section-label">AI Observer</span><span class="observer-non-authoritative">Non-authoritative narrator</span></div><div class="observer-state">${statusChip(brief.status || "UNKNOWN")} ${statusChip(brief.operator_attention || "NONE")}</div></div><h2>${escapeHtml(brief.headline || "Observer unavailable")}</h2><p><strong>What:</strong> ${escapeHtml(brief.what || "UNKNOWN")}</p><p><strong>Why:</strong> ${escapeHtml(brief.why || "UNKNOWN")}</p><div class="observer-columns"><div><span class="section-label">Confirmed facts</span>${list(facts, "No confirmed facts in bounded context.")}</div><div><span class="section-label">Unknowns</span>${list(unknowns, "No additional unknowns recorded.")}</div></div>${sourceButtons ? `<div class="observer-sources"><span class="section-label">Source events</span><div>${sourceButtons}</div></div>` : ""}</div><div class="observer-meta"><span>${escapeHtml(observer.mode || "DETERMINISTIC")}</span><span>${escapeHtml(observer.provider || "none")}</span><span>${escapeHtml(observer.model || "no model")}</span>${observer.fallback_reason ? `<span>fallback: ${escapeHtml(observer.fallback_reason)}</span>` : ""}</div></section>`;
}

function motorMarkup(analysis) {
  const motors = analysis.motors || {};
  const rows = motorOrder.map((key) => {
    const motor = motors[key] || {};
    return `<div class="motor-row" role="listitem"><strong>${escapeHtml(motor.name || key.replaceAll("_", " "))}</strong><span>${statusChip(motor.availability || "UNKNOWN")}</span><span>${statusChip(motor.activity || "UNKNOWN")}</span><span>${statusChip(motor.last_outcome || "UNKNOWN")}</span><small>${escapeHtml(motor.reason || `${motor.evidence_count ?? 0} persisted evidence`)}</small></div>`;
  }).join("");
  return `<div class="motor-list" role="list"><div class="motor-row motor-heading" aria-hidden="true"><strong>Motor</strong><span>Availability</span><span>Activity</span><span>Last outcome</span><small>Context</small></div>${rows}</div>`;
}

function pipelineMarkup(analysis, context) {
  const pipeline = Array.isArray(analysis.active_pipeline) ? analysis.active_pipeline : [];
  if (!pipeline.length) return `<div class="empty-state">NO ACTIVE PIPELINE — authoritative stages are UNKNOWN.</div>`;
  return `<div class="pipeline" role="list">${pipeline.map((step, index) => {
    const id = step.record_id;
    const label = step.stage || step.kind || `stage-${index + 1}`;
    const value = id ? inspectButton(context, `pipeline-${index}`, step, id) : `<strong class="unknown-state">UNKNOWN</strong>`;
    return `<article class="pipeline-step" role="listitem"><div class="pipeline-step-head"><span>${escapeHtml(label)}</span>${statusChip(step.status || "UNKNOWN")}</div>${value}<small>next: ${escapeHtml(step.next_state || "UNKNOWN")}</small></article>`;
  }).join("")}</div>`;
}

function timelineMarkup(analysis, context) {
  const timeline = analysis.semantic_activity_timeline || {};
  const items = Array.isArray(timeline.items) ? timeline.items : [];
  if (!items.length) return `<div class="empty-state">NO SEMANTIC ACTIVITY — raw logs are not substituted.</div>`;
  return `<ol class="activity-list">${items.map((item, index) => {
    const title = item.summary || item.event_type || item.kind || "Persisted activity";
    const source = [item.plane, item.event_type || item.kind, item.source_type].filter(Boolean).join(" · ");
    return `<li class="activity-item"><time class="mono">${escapeHtml(item.timestamp || "UNKNOWN")}</time><div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(source || "UNKNOWN")}</span></div>${inspectButton(context, `activity-${index}`, item, "inspect")}</li>`;
  }).join("")}</ol><p class="timeline-footnote">Showing ${escapeHtml(String(timeline.shown ?? items.length))} of ${escapeHtml(String(timeline.count ?? items.length))} semantic activities${timeline.truncated ? " · truncated by read-model bound" : ""}.</p>`;
}

function countersMarkup(analysis) {
  const counters = analysis.counters || {};
  const fields = [
    ["Reserved requests", "request_budget_reserved"], ["Request attempts", "network_request_attempted"],
    ["Target contact", "target_contact_confirmed"], ["Responses received", "response_received"],
    ["Worker invocations", "worker_invocations"], ["Model calls", "model_calls"],
    ["Tokens in / out", "model_tokens_in"], ["Experiments", "experiments"],
    ["Cycles", "cycles"], ["Observations", "observations"], ["Evidence admitted", "evidence_admitted"],
  ];
  return `<dl class="counter-list">${fields.map(([label, key]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(displayValue(counters[key]))}${key === "model_tokens_in" ? ` / ${escapeHtml(displayValue(counters.model_tokens_out))}` : ""}</dd></div>`).join("")}</dl><p class="muted counter-note">UNKNOWN means the read model has no authoritative value; it is not zero.</p>`;
}

function faultMarkup(analysis, context) {
  const failure = analysis.failure_inspector;
  if (!failure) return `<div class="empty-state">NO UNRESOLVED FATAL FAULT</div>`;
  const fault = failure.fault || {};
  const drawerRecord = {
    ...fault,
    boundary: failure.boundary,
    core_decision: failure.core_decision,
    dispatch_committed: failure.dispatch_committed,
    invocation_started: failure.invocation_started,
    worker_result_present: failure.worker_result_present,
    target_contact_status: failure.target_contact_status,
    response_present: failure.response_present,
    observation_present: failure.observation_present,
    evidence_present: failure.evidence_present,
    assessment_present: failure.assessment_present,
    persisted_state: failure.persisted_state,
    effective_state: failure.effective_state,
    reason_codes: failure.reason_codes,
    retry_classification: failure.retry_classification,
    resolved: failure.resolved,
    unresolved: failure.unresolved,
  };
  const key = context.registerRecord("failure-inspector", drawerRecord);
  return `<div class="fault-readout"><div><span class="section-label">${escapeHtml(fault.fault_code || "OPERATIONAL FAULT")}</span><strong>${escapeHtml(fault.component || "UNKNOWN COMPONENT")} · ${escapeHtml(fault.phase || "UNKNOWN PHASE")}</strong><p>${escapeHtml(fault.diagnostic_summary || "Sanitized diagnostic summary unavailable.")}</p></div><div>${statusChip(failure.effective_state || "UNKNOWN")}<button class="button button-quiet" type="button" data-inspector-key="${escapeHtml(key)}" data-inspector-title="Failure inspector">Inspect fault</button></div></div><div class="fault-boundary">WorkerResult ${failure.worker_result_present ? "present" : "absent"} · Observation ${failure.observation_present ? "present" : "absent"} · Evidence ${failure.evidence_present ? "present" : "absent"}</div>`;
}

function warningsMarkup(analysis) {
  const warnings = Array.isArray(analysis.state_consistency_warnings) ? analysis.state_consistency_warnings : [];
  if (!warnings.length) return "";
  return `<section class="consistency-warnings" aria-label="State consistency warnings"><div class="consistency-heading"><span class="section-label">Consistency</span><span class="muted">Authoritative state comparison</span></div>${warnings.map((warning) => `<div class="warning-row"><span class="status-chip status-warning">WARNING</span><div><strong>${escapeHtml(warning.summary || warning.code || "State warning")}</strong><p>${escapeHtml(warning.detail || "No further detail is available.")}</p></div></div>`).join("")}</section>`;
}

export function render(context) {
  const run = (context.snapshot?.database?.runs || []).find((item) => item.research_run_id === context.selectedRunId);
  if (!context.analysis) return `${run ? `<div class="cockpit-actions">${controlsForRun(run)}</div>` : ""}${unavailable(run ? "Selected-run analysis is unavailable; operational truth remains UNKNOWN." : "Select a persisted run to open the operational cockpit.")}`;
  const analysis = context.analysis;
  return `<div class="cockpit-actions">${controlsForRun(run)}<span class="projection-label">Projection only · PostgreSQL source of truth</span></div>${observerMarkup(analysis, context)}${warningsMarkup(analysis)}<div class="cockpit-grid"><div class="cockpit-primary">${section("Current research lineage", lineageMarkup(analysis, context), "Persisted relationships only; absence is UNKNOWN.")}${section("Active pipeline", pipelineMarkup(analysis, context), "Decision and execution stages remain distinct.")}${section("Semantic activity", timelineMarkup(analysis, context), "Human-readable events, not raw logs.")}</div><aside class="cockpit-secondary"><section class="panel"><div class="panel-head"><div><h2>Motor status</h2><p class="muted">Availability, activity, and outcome are independent.</p></div></div><div class="panel-body">${motorMarkup(analysis)}</div></section>${section("Operational counters", countersMarkup(analysis))}${section("Failure inspector", faultMarkup(analysis, context))}</aside></div>`;
}
