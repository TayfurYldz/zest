import { collection, listCollection, unavailable, viewHeader } from "./common.js";
export function render(context) {
  if (!context.analysis) return `${viewHeader("Hunter Engine", "Selection provenance and bounded research frontier.")}${unavailable()}`;
  const a = context.analysis;
  return `${viewHeader("Hunter Engine", "Selection is shown as process provenance, not as a security conclusion.")}<div class="workspace-grid">${collection(a, ["hunter", "opportunities"], "Opportunities", context, ["opportunity_id", "opportunity_type", "outcome"])}${collection(a, ["hunter", "selections"], "Selections", context, ["selection_id", "decision", "reason_code"])}${collection(a, ["hunter", "selection_candidates"], "Selection candidates", context, ["candidate_id", "state", "created_at"])}${listCollection(a, ["hunter", "v3_queue"], "V3 queue", context)}</div>`;
}
