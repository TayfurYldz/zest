import { collection, listCollection, unavailable, viewHeader } from "./common.js";
export function render(context) {
  if (!context.analysis) return `${viewHeader("Hypothesis Lab", "Reasoning, admission, hypothesis, experiment, and assessment lineage.")}${unavailable()}`;
  const a = context.analysis;
  return `${viewHeader("Hypothesis Lab", "Model/research reasoning is untrusted provenance; it is never rendered as hypothesis truth.")}<div class="workspace-grid">${collection(a, ["research", "reasoning"], "Reasoning provenance", context, ["reasoning_id", "model_id", "created_at"])}${collection(a, ["research", "admissions"], "Research admissions", context, ["admission_id", "outcome", "created_at"])}${collection(a, ["research", "hypotheses"], "Hypotheses", context, ["hypothesis_id", "state", "created_at"])}${collection(a, ["research", "assessments"], "Assessments", context, ["assessment_id", "polarity", "created_at"])}${listCollection(a, ["research", "cycles"], "Research cycles", context)}</div>`;
}
