async function request(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.detail || payload.error || `HTTP ${response.status}`);
  }
  return payload.result === undefined ? payload : payload.result;
}

export function getDashboard() { return request("/api/dashboard"); }
export function getAnalysis(runId) { return request(`/api/runs/${encodeURIComponent(runId)}/analysis`); }
export function postRunAction(runId, action) {
  const body = action === "start" || action === "resume" ? "{}" : undefined;
  return request(`/api/runs/${encodeURIComponent(runId)}/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
}
export function bootstrapProgram(payload) {
  return request("/api/programs/bootstrap", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
