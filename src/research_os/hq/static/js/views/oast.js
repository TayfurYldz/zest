import { collection, unavailable, viewHeader } from "./common.js";
export function render(context) {
  if (!context.analysis) return `${viewHeader("OAST Operations", "Correlations, provider deliveries, admissions, and admitted observations.")}${unavailable()}`;
  const a = context.analysis;
  return `${viewHeader("OAST Operations", "External callbacks are untrusted until correlated and admitted through the existing application semantics.")}${collection(a, ["oast", "correlations"], "Correlations", context, ["correlation_id", "attempt_id", "expires_at"])}${collection(a, ["oast", "deliveries"], "Provider deliveries", context, ["delivery_id", "correlation_id", "received_at"])}${collection(a, ["oast", "admissions"], "Admissions", context, ["admission_id", "correlation_id", "outcome"])}`;
}
