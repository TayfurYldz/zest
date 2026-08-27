import { displayValue, escapeHtml, simplePairs } from "./components.js";

export function createInspector(element, onClose) {
  let trigger = null;
  const scrim = document.querySelector("#inspectorScrim");

  function close() {
    element.setAttribute("aria-hidden", "true");
    element.hidden = true;
    if (scrim) scrim.hidden = true;
    element.innerHTML = "";
    if (trigger) trigger.focus();
    trigger = null;
    onClose();
  }
  function open(title, record, source) {
    trigger = document.activeElement;
    const entries = Object.entries(record || {}).slice(0, 32);
    element.setAttribute("aria-hidden", "false");
    element.hidden = false;
    if (scrim) scrim.hidden = false;
    element.innerHTML = `<div class="inspector-head"><div><p class="eyebrow">${escapeHtml(source)}</p><h2>${escapeHtml(title)}</h2></div><button class="inspector-close" type="button" aria-label="Close inspector">×</button></div><div class="inspector-section"><h3>Important fields</h3>${simplePairs(Object.fromEntries(entries.slice(0, 10)))}</div><div class="inspector-section"><h3>Structured sanitized data</h3><details><summary>Show projection fields</summary><pre class="mono">${escapeHtml(JSON.stringify(record, null, 2))}</pre></details></div>`;
    element.querySelector(".inspector-close").addEventListener("click", close);
    element.querySelector(".inspector-close").focus();
  }
  element.addEventListener("keydown", (event) => {
    if (element.getAttribute("aria-hidden") !== "false") return;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...element.querySelectorAll("button, [href], input, select, textarea, summary, [tabindex]:not([tabindex='-1'])")];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  if (scrim) scrim.addEventListener("click", close);
  return { open, close };
}
