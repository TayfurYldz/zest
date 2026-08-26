export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

export function displayValue(value) {
  if (value === null || value === undefined || value === "") return "UNKNOWN";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function statusChip(value) {
  const text = displayValue(value).toUpperCase();
  const lower = text.toLowerCase();
  const kind = /fail|denied|error|cancel/.test(lower) ? "danger" : /wait|hold|human|warning|pending/.test(lower) ? "warning" : /ready|active|run|progress/.test(lower) ? "active" : /pass|healthy|admit|verified|complete/.test(lower) ? "healthy" : "unknown";
  return `<span class="status-chip status-${kind}">${escapeHtml(text)}</span>`;
}

export function section(title, body, note = "") {
  return `<section class="panel"><div class="panel-head"><div><h2>${escapeHtml(title)}</h2>${note ? `<p class="subtle">${escapeHtml(note)}</p>` : ""}</div></div><div class="panel-body">${body}</div></section>`;
}

export function metric(label, value) {
  return `<div class="metric"><span class="metric-label">${escapeHtml(label)}</span><strong class="metric-value">${escapeHtml(displayValue(value))}</strong></div>`;
}

export function bundleItems(analysis, path) {
  let value = analysis;
  for (const key of path) value = value && value[key];
  return value && Array.isArray(value.items) ? value.items : [];
}

export function table(title, records, context, columns = ["id", "state", "created_at"]) {
  if (!records.length) return section(title, `<div class="empty-state">NO DATA — this projection has no records for the selected run.</div>`);
  const heads = columns.map((column) => `<th scope="col">${escapeHtml(column.replaceAll("_", " "))}</th>`).join("");
  const rows = records.map((record, index) => {
    const key = context.registerRecord(`${title}-${index}`, record);
    const cells = columns.map((column) => `<td>${column === "state" || column === "status" ? statusChip(record[column]) : `<span class="${/id|hash|correlation|attempt/.test(column) ? "mono" : ""}">${escapeHtml(displayValue(record[column]))}</span>`}</td>`).join("");
    return `<tr><td><button class="record-link" type="button" data-inspector-key="${escapeHtml(key)}">inspect</button></td>${cells}</tr>`;
  }).join("");
  return section(title, `<div class="table-scroll"><table><thead><tr><th scope="col">record</th>${heads}</tr></thead><tbody>${rows}</tbody></table></div>`, `${records.length} persisted record${records.length === 1 ? "" : "s"}`);
}

export function recordList(title, records, context) {
  if (!records.length) return section(title, `<div class="empty-state">NO DATA</div>`);
  const rows = records.map((record, index) => {
    const key = context.registerRecord(`${title}-${index}`, record);
    const primary = record.id || record.record_id || record.opportunity_id || record.attempt_id || record.observation_id || record.proposal_id || record.correlation_id || record.event_type || `record-${index + 1}`;
    const secondary = record.state || record.status || record.kind || record.type || record.created_at || "persisted projection";
    return `<li class="record-summary"><button class="record-link" type="button" data-inspector-key="${escapeHtml(key)}"><strong>${escapeHtml(displayValue(primary))}</strong><span>${escapeHtml(displayValue(secondary))}</span></button></li>`;
  }).join("");
  return section(title, `<ul class="kv-list">${rows}</ul>`);
}

export function controlsForRun(run) {
  if (!run) return "";
  const state = String(run.state || "CREATED").toUpperCase();
  const id = escapeHtml(run.research_run_id);
  const button = (action, label) => `<button class="button button-quiet" type="button" data-run-action="${action}" data-run-id="${id}">${label}</button>`;
  if (["CREATED", "STARTABLE"].includes(state)) return `<div class="control-row">${button("preflight", "PREFLIGHT")}${button("start", "START")}</div>`;
  if (["READY", "RUNNING", "ACTIVE", "EXECUTING"].includes(state)) return `<div class="control-row">${button("pause", "PAUSE")}${button("cancel", "CANCEL")}</div>`;
  if (["PAUSED", "WAITING_HUMAN", "RECONCILIATION_REQUIRED"].includes(state)) return `<div class="control-row">${button("preflight", "PREFLIGHT")}${button("resume", "RESUME")}${button("cancel", "CANCEL")}</div>`;
  return `<span class="control-status">Terminal: ${escapeHtml(state)}</span>`;
}

export function simplePairs(values) {
  return `<div class="kv-list">${Object.entries(values).map(([key, value]) => `<div class="kv"><span class="kv-key">${escapeHtml(key.replaceAll("_", " "))}</span><span class="kv-value">${escapeHtml(displayValue(value))}</span></div>`).join("")}</div>`;
}
