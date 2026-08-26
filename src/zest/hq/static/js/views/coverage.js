import { collection, listCollection, unavailable, viewHeader } from "./common.js";
export function render(context) {
  if (!context.analysis) return `${viewHeader("Coverage / Closure", "Explored surface, unresolved frontier, and coverage debt.")}${unavailable()}`;
  const a = context.analysis;
  return `${viewHeader("Coverage / Closure", "No percentage is invented without a persisted denominator.")}${collection(a, ["surface", "coverage_debt"], "Coverage debt snapshots", context, ["snapshot_id", "total_debt", "created_at"])}<div class="workspace-grid">${listCollection(a, ["surface", "frontier_items"], "Frontier items", context)}${listCollection(a, ["surface", "change_events"], "Surface change events", context)}${listCollection(a, ["hunter", "opportunities"], "Remaining opportunities", context)}</div>`;
}
