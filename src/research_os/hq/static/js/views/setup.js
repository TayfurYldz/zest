import { bootstrapProgram } from "../api.js";
import { escapeHtml, section } from "../components.js";
import { viewHeader } from "./common.js";

let pendingPayload = null;

function uniqueLines(value) {
  const seen = new Set();
  const result = [];
  for (const raw of String(value || "").split(/\r?\n/)) {
    const item = raw.trim();
    if (!item || item.startsWith("#") || seen.has(item)) continue;
    seen.add(item);
    result.push(item);
  }
  return result;
}

function numberField(id, name, label, value, min = 1) {
  return `<div class="field">
    <label for="${id}">${escapeHtml(label)}</label>
    <input id="${id}" name="${name}" type="number" min="${min}" step="1" value="${value}" required>
  </div>`;
}

function reviewLines(items) {
  if (!items.length) return `<span class="muted">None supplied</span>`;
  return `<pre class="review-scope">${escapeHtml(items.join("\n"))}</pre>`;
}

function normalizedPayload(form) {
  const payload = Object.fromEntries(new FormData(form).entries());

  const inScope = uniqueLines(payload.in_scope);
  const outOfScope = uniqueLines(payload.out_of_scope);
  const forbiddenActions = uniqueLines(payload.forbidden_actions);

  if (!inScope.length) throw new Error("at least one in-scope entry is required");

  const outSet = new Set(outOfScope);
  const conflicts = inScope.filter((entry) => outSet.has(entry));
  if (conflicts.length) {
    throw new Error(`scope entry appears in both lists: ${conflicts.join(", ")}`);
  }

  payload.in_scope = inScope.join("\n");
  payload.out_of_scope = outOfScope.join("\n");
  payload.forbidden_actions = forbiddenActions.join("\n");

  return { payload, inScope, outOfScope, forbiddenActions };
}

function reviewMarkup(payload, inScope, outOfScope, forbiddenActions) {
  const bounds = [
    ["Requests", payload.max_requests],
    ["Rate", `${payload.max_requests_per_window}/${payload.window_seconds}s`],
    ["Tool calls", payload.max_tool_calls],
    ["Runtime", `${payload.max_runtime_ms} ms`],
    ["Concurrency", payload.max_concurrency],
    ["Cycles", payload.max_cycles],
    ["Experiments", payload.max_experiments],
    ["Model calls", payload.max_model_calls],
    ["Worker invocations", payload.max_worker_invocations],
    ["Elapsed ceiling", `${payload.max_elapsed_ms} ms`],
    ["Selected opportunities", payload.max_selected_opportunities],
    ["Runtime fallback", payload.max_runtime_fallback],
    ["Side-effect ceiling", payload.side_effect_ceiling],
  ];

  return `
    <div class="kv-list">
      <div class="kv"><span class="kv-key">Program</span><span class="kv-value">${escapeHtml(payload.program_name)}</span></div>
      <div class="kv"><span class="kv-key">Target</span><span class="kv-value mono">${escapeHtml(payload.target_reference)}</span></div>
      <div class="kv"><span class="kv-key">Authorization</span><span class="kv-value mono">${escapeHtml(payload.authorization_reference)}</span></div>
    </div>

    <div class="setup-review-grid">
      <div>
        <h3>IN SCOPE · ${inScope.length}</h3>
        ${reviewLines(inScope)}
      </div>
      <div>
        <h3>OUT OF SCOPE · ${outOfScope.length}</h3>
        ${reviewLines(outOfScope)}
      </div>
    </div>

    <div class="setup-review-grid">
      <div>
        <h3>FORBIDDEN ACTIONS · ${forbiddenActions.length}</h3>
        ${reviewLines(forbiddenActions)}
      </div>
      <div>
        <h3>BOUNDS</h3>
        <div class="kv-list">
          ${bounds.map(([key, value]) => `<div class="kv"><span class="kv-key">${escapeHtml(key)}</span><span class="kv-value mono">${escapeHtml(value)}</span></div>`).join("")}
        </div>
      </div>
    </div>

    <div class="notice">
      Review is not execution authorization. Bootstrap persists the bounded configuration only.
      Active testing does not begin here.
    </div>`;
}

export function render(context) {
  const snapshot = context.snapshot || {};
  const programs = snapshot.database?.programs || [];
  const runs = snapshot.database?.runs || [];

  const rows = runs.length
    ? runs.map((run) => `<tr>
        <td class="mono">${escapeHtml(run.research_run_id)}</td>
        <td>${escapeHtml(run.program_id)}</td>
        <td>${escapeHtml(run.state || "UNKNOWN")}</td>
      </tr>`).join("")
    : `<tr><td colspan="3">NO DATA</td></tr>`;

  return `${viewHeader(
    "Program Setup",
    "Create a bounded ready run without weakening scope, authorization, budgets, or side-effect ceilings."
  )}

  <div class="panel">
    <div class="panel-head">
      <div>
        <h2>Bootstrap authorized program</h2>
        <p class="muted">Normalize → review → persist. No active testing is started by this form.</p>
      </div>
    </div>

    <div class="panel-body">
      <form id="bootstrapForm" class="form-grid">
        <input type="hidden" name="platform" value="manual">

        <div class="field">
          <label for="programName">Program name</label>
          <input id="programName" name="program_name" required autocomplete="off">
        </div>

        <div class="field">
          <label for="programHandle">Handle</label>
          <input id="programHandle" name="program_handle" autocomplete="off">
        </div>

        <div class="field">
          <label for="targetReference">Target reference</label>
          <input id="targetReference" name="target_reference" required autocomplete="off">
        </div>

        <div class="field">
          <label for="authorizationReference">Authorization reference</label>
          <input id="authorizationReference" name="authorization_reference" required autocomplete="off">
        </div>

        <div class="field field-full">
          <label for="inScope">In-scope entries · one per line</label>
          <textarea id="inScope" name="in_scope" rows="8" required></textarea>
          <span class="field-hint">Duplicate lines are removed before review.</span>
        </div>

        <div class="field field-full">
          <label for="outOfScope">Out-of-scope entries · one per line</label>
          <textarea id="outOfScope" name="out_of_scope" rows="5"></textarea>
        </div>

        <div class="field field-full">
          <label for="researchQuestion">Research question</label>
          <textarea id="researchQuestion" name="research_question" rows="3">Which explicitly authorized surfaces warrant deeper security research and manual validation?</textarea>
        </div>

        <div class="field field-full">
          <label for="forbiddenActions">Forbidden actions · one per line</label>
          <textarea id="forbiddenActions" name="forbidden_actions" rows="4"></textarea>
        </div>

        <div class="field field-full">
          <label for="requiredUserAgent">Required User-Agent</label>
          <input id="requiredUserAgent" name="required_user_agent" autocomplete="off">
        </div>

        <details class="field-full setup-advanced" open>
          <summary>Field execution bounds</summary>

          <div class="form-grid setup-advanced-grid">
            ${numberField("maxRequests", "max_requests", "Maximum requests", 100)}
            ${numberField("maxToolCalls", "max_tool_calls", "Maximum tool calls", 50)}
            ${numberField("maxRuntimeMs", "max_runtime_ms", "Maximum runtime (ms)", 900000)}
            ${numberField("maxConcurrency", "max_concurrency", "Maximum concurrency", 1)}
            ${numberField("maxRequestsPerWindow", "max_requests_per_window", "Requests per window", 10)}
            ${numberField("windowSeconds", "window_seconds", "Window seconds", 60)}
            ${numberField("maxCycles", "max_cycles", "Maximum cycles", 5)}
            ${numberField("maxExperiments", "max_experiments", "Maximum experiments", 12)}
            ${numberField("maxModelCalls", "max_model_calls", "Maximum model calls", 20)}
            ${numberField("maxWorkerInvocations", "max_worker_invocations", "Maximum worker invocations", 30)}
            ${numberField("maxElapsedMs", "max_elapsed_ms", "Maximum elapsed (ms)", 900000)}
            ${numberField("maxSelectedOpportunities", "max_selected_opportunities", "Selected opportunities", 2)}
            ${numberField("maxRuntimeFallback", "max_runtime_fallback", "Runtime fallback count", 1)}
            ${numberField("maxResponseBytes", "max_response_bytes", "Maximum response bytes", 1048576)}
            ${numberField("timeoutMs", "timeout_ms", "Per-operation timeout (ms)", 10000)}

            <div class="field">
              <label for="dailyLlmBudget">Daily LLM budget · microdollars</label>
              <input id="dailyLlmBudget" name="daily_llm_budget_microdollars" type="number" min="0" step="1" value="0" required>
            </div>

            <div class="field">
              <label for="sideEffectCeiling">Side-effect ceiling</label>
              <select id="sideEffectCeiling" name="side_effect_ceiling" required>
                <option value="0" selected>0 · observation / read-only</option>
                <option value="1">1</option>
                <option value="2">2</option>
                <option value="3">3</option>
              </select>
              <span class="field-hint">Core remains authoritative; this value cannot bypass scope, approval, or Preflight.</span>
            </div>
          </div>
        </details>

        <div class="form-actions field-full">
          <button class="button button-primary" type="submit">Review bounded run</button>
          <span id="formStatus" class="control-status" role="status"></span>
        </div>

        <div id="bootstrapReview" class="field-full setup-review" hidden>
          <div class="panel">
            <div class="panel-head">
              <div>
                <h2>Human review</h2>
                <p class="muted">Confirm the exact scope and bounds before persistence.</p>
              </div>
            </div>
            <div class="panel-body">
              <div id="bootstrapReviewBody"></div>
              <div class="form-actions">
                <button id="confirmBootstrap" class="button button-primary" type="button">Confirm &amp; Create Ready Run</button>
                <button id="editBootstrap" class="button button-quiet" type="button">Edit configuration</button>
              </div>
            </div>
          </div>
        </div>
      </form>

      <p class="setup-note">
        Bootstrap creates scope, policy, authorization, rate-limit and budget records plus a STARTABLE run.
        It does not start active testing.
      </p>
    </div>
  </div>

  ${section(
    "Existing programs",
    `<div class="table-scroll"><table>
      <thead><tr><th>program</th><th>scope / identity</th><th>state</th></tr></thead>
      <tbody>${programs.map((item) => `<tr>
        <td>${escapeHtml(item.name || item.program_id)}</td>
        <td class="mono">${escapeHtml(item.program_id)}</td>
        <td>${escapeHtml(item.platform || "UNKNOWN")}</td>
      </tr>`).join("") || `<tr><td colspan="3">NO DATA</td></tr>`}</tbody>
    </table></div>`
  )}

  ${section(
    "Research runs",
    `<div class="table-scroll"><table>
      <thead><tr><th>run</th><th>program</th><th>state</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`
  )}`;
}

export function bind(context) {
  const form = document.querySelector("#bootstrapForm");
  if (!form || form.dataset.bound) return;

  form.dataset.bound = "true";

  const submit = form.querySelector('button[type="submit"]');
  const status = form.querySelector("#formStatus");
  const review = form.querySelector("#bootstrapReview");
  const reviewBody = form.querySelector("#bootstrapReviewBody");
  const confirm = form.querySelector("#confirmBootstrap");
  const edit = form.querySelector("#editBootstrap");

  form.addEventListener("input", () => {
    if (!review.hidden) {
      pendingPayload = null;
      review.hidden = true;
      status.textContent = "configuration changed — review again";
    }
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (submit.disabled) return;

    submit.disabled = true;

    try {
      const normalized = normalizedPayload(form);
      pendingPayload = normalized.payload;
      reviewBody.innerHTML = reviewMarkup(
        normalized.payload,
        normalized.inScope,
        normalized.outOfScope,
        normalized.forbiddenActions
      );
      review.hidden = false;
      status.textContent = "review required before persistence";
      review.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      pendingPayload = null;
      review.hidden = true;
      status.textContent = `error: ${error.message}`;
    } finally {
      submit.disabled = false;
    }
  });

  edit.addEventListener("click", () => {
    pendingPayload = null;
    review.hidden = true;
    status.textContent = "edit configuration and review again";
    form.querySelector("#targetReference").focus();
  });

  confirm.addEventListener("click", async () => {
    if (!pendingPayload || confirm.disabled) return;

    confirm.disabled = true;
    submit.disabled = true;
    status.textContent = "creating bounded configuration…";

    try {
      const result = await bootstrapProgram(pendingPayload);
      const readyMessage = `ready: ${result.research_run_id || "run created"}`;

      // Persistence succeeded at this point. Do not allow a later projection
      // refresh failure to misreport the durable bootstrap as failed.
      pendingPayload = null;
      review.hidden = true;
      form.reset();
      status.textContent = readyMessage;

      try {
        await context.refreshDashboard();

        // refreshDashboard re-renders the active view. The original status
        // node may therefore be detached; restore the durable success state
        // on the freshly rendered Program Setup form.
        const refreshedStatus = document.querySelector("#formStatus");
        if (refreshedStatus) refreshedStatus.textContent = readyMessage;
      } catch (refreshError) {
        const currentStatus =
          document.querySelector("#formStatus") || status;
        currentStatus.textContent =
          `${readyMessage} · dashboard refresh error: ${refreshError.message}`;
      }
    } catch (error) {
      const currentStatus =
        document.querySelector("#formStatus") || status;
      currentStatus.textContent = `error: ${error.message}`;
    } finally {
      const currentConfirm = document.querySelector("#confirmBootstrap");
      const currentSubmit =
        document.querySelector('#bootstrapForm button[type="submit"]');

      if (currentConfirm) currentConfirm.disabled = false;
      if (currentSubmit) currentSubmit.disabled = false;
    }
  });
}
