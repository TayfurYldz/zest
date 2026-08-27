import { escapeHtml, section, statusChip } from "../components.js";
import { collection, unavailable, viewHeader } from "./common.js";

function value(value) {
  return escapeHtml(value === null || value === undefined || value === "" ? "UNKNOWN" : value);
}

function intentMarkup(intent) {
  if (!intent || intent.status === "UNKNOWN") return `<div class="empty-state">NO PERSISTED RESEARCH INTENT — no rationale is inferred.</div>`;
  const hypothesis = intent.hypothesis || {};
  const selection = intent.selection || {};
  const experiment = intent.experiment || {};
  const assessment = intent.assessment || {};
  const refs = Array.isArray(intent.source_references) ? intent.source_references : [];
  return `<dl class="intent-list"><div><dt>Hypothesis</dt><dd>${value(hypothesis.claim)}</dd></div><div><dt>Hypothesis state</dt><dd>${statusChip(intent.hypothesis_state)}</dd></div><div><dt>Selection reason</dt><dd>${value((intent.selection_reason_codes || []).join(" · "))}</dd></div><div><dt>Source references</dt><dd>${value(refs.join(" · "))}</dd></div><div><dt>Selected experiment</dt><dd>${value(experiment.experiment_id)}</dd></div><div><dt>Objective</dt><dd>${value(intent.experiment_objective?.action || intent.experiment_objective?.evaluation_strategy)}</dd></div><div><dt>Expected observation</dt><dd>${value(intent.expected_observation)}</dd></div><div><dt>Disconfirming observation</dt><dd>${value(intent.disconfirming_observation)}</dd></div><div><dt>Assessment</dt><dd>${value(assessment.assessment_outcome)}</dd></div><div><dt>Verification</dt><dd>${value(intent.verification?.outcome)}</dd></div><div><dt>Next direction</dt><dd>${value(intent.next_direction)}</dd></div></dl>`;
}

function verificationMarkup(projection) {
  const stages = Array.isArray(projection?.stages) ? projection.stages : [];
  if (!stages.length) return `<div class="empty-state">NO AUTHORITATIVE VERIFICATION CHAIN</div>`;
  return `<ol class="verification-track">${stages.map((stage) => `<li><span>${escapeHtml(stage.stage || "UNKNOWN")}</span>${statusChip(stage.status || "UNKNOWN")}${stage.record_id ? `<small class="mono">${escapeHtml(stage.record_id)}</small>` : ""}</li>`).join("")}</ol><p class="muted verification-note">Final state is sourced from persisted Candidate/Finding records: CONFIRMED or FALSE_POSITIVE only when authoritative records support it. The Observer cannot finalize it (observer_can_finalize=false).</p>`;
}

export function render(context) {
  if (!context.analysis) return `${viewHeader("Research Intent", "Persisted hypothesis intent and verification boundaries.")}${unavailable()}`;
  const analysis = context.analysis;
  return `${viewHeader("Research Intent", "System-decided intent only; absent fields remain UNKNOWN.")}${section("Intent", intentMarkup(analysis.research_intent), "No model narrative is used to fill missing intent.")}${section("Verification / false-positive boundary", verificationMarkup(analysis.verification_chain), "SIGNAL → HYPOTHESIS → EXPERIMENT → OBSERVATION → EVIDENCE → CANDIDATE → VERIFICATION → final state.")}${collection(analysis, ["research", "hypotheses"], "Persisted hypotheses", context, ["hypothesis_id", "claim", "created_at"])}${collection(analysis, ["evidence_chain", "candidates"], "Candidate states", context, ["candidate_id", "state", "created_at"])}`;
}
