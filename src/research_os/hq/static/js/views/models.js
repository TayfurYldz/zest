import { collection, unavailable, viewHeader } from "./common.js";
export function render(context) {
  if (!context.analysis) return `${viewHeader("Model Intelligence", "Persisted model and reasoning metadata.")}${unavailable()}`;
  return `${viewHeader("Model Intelligence", "Only persisted model metadata and sanitized structured outputs are displayed.")}${collection(context.analysis, ["research", "reasoning"], "Reasoning records", context, ["reasoning_id", "model_id", "role", "created_at"])}<div class="notice">Latency, cost, token counts, confidence, and quality are UNKNOWN unless persisted by the source-of-truth projection.</div>`;
}
