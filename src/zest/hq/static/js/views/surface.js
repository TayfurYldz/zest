import { section } from "../components.js";
import { collection, listCollection, unavailable, viewHeader } from "./common.js";
export function render(context) {
  if (!context.analysis) return `${viewHeader("Attack Surface", "Persisted surface intelligence, change, and coverage debt.")}${unavailable()}`;
  const a = context.analysis;
  const panels = [collection(a, ["surface", "sensor_observations"], "Sensor observations", context, ["observation_id", "kind", "created_at"]), collection(a, ["surface", "facts"], "Surface facts", context, ["fact_id", "kind", "created_at"]), collection(a, ["surface", "inferences"], "Target inferences", context, ["inference_id", "kind", "created_at"]), listCollection(a, ["surface", "coverage_debt"], "Coverage debt", context)];
  return `${viewHeader("Attack Surface", "Structured exploration of persisted surface observations; no graph is fabricated.")}<div class="notice">Graph projection is shown only when persisted impact-chain nodes and edges exist. Current view uses the available snapshot collections.</div><div class="workspace-grid">${panels.join("")}</div>`;
}
