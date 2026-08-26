import { bootstrapProgram } from "../api.js";
import { escapeHtml, section } from "../components.js";
import { viewHeader } from "./common.js";

export function render(context) {
  const snapshot = context.snapshot || {};
  const programs = snapshot.database?.programs || [];
  const runs = snapshot.database?.runs || [];
  const rows = runs.length ? runs.map((run) => `<tr><td class="mono">${escapeHtml(run.research_run_id)}</td><td>${escapeHtml(run.program_id)}</td><td>${escapeHtml(run.state || "UNKNOWN")}</td></tr>`).join("") : `<tr><td colspan="3">NO DATA</td></tr>`;
  return `${viewHeader("Program Setup", "Create a bounded ready run without weakening scope, authorization, budgets, or side-effect ceilings.")}<div class="panel"><div class="panel-head"><h2>Bootstrap authorized program</h2></div><div class="panel-body"><form id="bootstrapForm" class="form-grid"><div class="field"><label for="programName">Program name</label><input id="programName" name="program_name" required autocomplete="off"></div><div class="field"><label for="programHandle">Handle</label><input id="programHandle" name="program_handle" autocomplete="off"></div><div class="field"><label for="targetReference">Target reference</label><input id="targetReference" name="target_reference" required autocomplete="off"></div><div class="field"><label for="authorizationReference">Authorization reference</label><input id="authorizationReference" name="authorization_reference" required autocomplete="off"></div><div class="field field-full"><label for="inScope">In-scope entries</label><textarea id="inScope" name="in_scope" required></textarea></div><div class="field field-full"><label for="researchQuestion">Research question</label><textarea id="researchQuestion" name="research_question"></textarea></div><div class="form-actions field-full"><button class="button button-primary" type="submit">Create ready run</button><span id="formStatus" class="control-status" role="status"></span></div></form><p class="setup-note">Bootstrap creates bounded configuration and a ready run. It does not start active testing.</p></div></div>${section("Existing programs", `<div class="table-scroll"><table><thead><tr><th>program</th><th>scope / identity</th><th>state</th></tr></thead><tbody>${programs.map((item) => `<tr><td>${escapeHtml(item.name || item.program_id)}</td><td class="mono">${escapeHtml(item.program_id)}</td><td>${escapeHtml(item.platform || "UNKNOWN")}</td></tr>`).join("") || `<tr><td colspan="3">NO DATA</td></tr>`}</tbody></table></div>`)}${section("Research runs", `<div class="table-scroll"><table><thead><tr><th>run</th><th>program</th><th>state</th></tr></thead><tbody>${rows}</tbody></table></div>`)}`;
}

export function bind(context) {
  const form = document.querySelector("#bootstrapForm");
  if (!form || form.dataset.bound) return;
  form.dataset.bound = "true";
  const status = form.querySelector("#formStatus");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = form.querySelector("button[type=submit]");
    if (submit.disabled) return;
    submit.disabled = true;
    status.textContent = "creating bounded configuration…";
    const payload = Object.fromEntries(new FormData(form).entries());
    try {
      const result = await bootstrapProgram(payload);
      status.textContent = `ready: ${result.research_run_id || "run created"}`;
      form.reset();
      await context.refreshDashboard();
    } catch (error) {
      status.textContent = `error: ${error.message}`;
    } finally {
      submit.disabled = false;
    }
  });
}
