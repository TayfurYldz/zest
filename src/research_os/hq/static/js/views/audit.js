import { collection, unavailable, viewHeader } from "./common.js";
export function render(context) {
  if (!context.analysis) return `${viewHeader("Audit / Safety", "Run state, authorization, leases, preflight, reconciliation, and human-required state.")}${unavailable()}`;
  return `${viewHeader("Audit / Safety", "Safety is operational data, not decorative warning copy.")}${collection(context.analysis, ["audit", "events"], "Audit events", context, ["event_type", "subject_id", "occurred_at", "correlation_id"])}<div class="notice">DENY, BLOCKED, HUMAN_REQUIRED, and UNKNOWN_OUTCOME remain explicit states. UNKNOWN_OUTCOME is not automatically retried.</div>`;
}
