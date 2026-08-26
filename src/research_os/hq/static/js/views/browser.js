import { collection, unavailable, viewHeader } from "./common.js";
export function render(context) {
  if (!context.analysis) return `${viewHeader("Browser Operations", "Actual browser-capable attempts and their persisted outcomes.")}${unavailable()}`;
  return `${viewHeader("Browser Operations", "No screenshot, DOM trace, or browser result is fabricated.")}${collection(context.analysis, ["browser", "attempts"], "Browser attempts", context, ["attempt_id", "state", "worker_capability"])}<div class="notice">WorkerResult is untrusted execution output; it is not an Observation or Evidence.</div>`;
}
