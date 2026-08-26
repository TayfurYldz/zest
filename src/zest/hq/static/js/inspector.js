import { displayValue, escapeHtml, simplePairs } from "./components.js";

export function createInspector(element, onClose) {
  let trigger = null;
  function close() {
    element.setAttribute("aria-hidden", "true");
    element.innerHTML = "";
    if (trigger) trigger.focus();
    trigger = null;
    onClose();
  }
  function open(title, record, source) {
    trigger = document.activeElement;
    const entries = Object.entries(record || {}).slice(0, 32);
    element.setAttribute("aria-hidden", "false");
    element.innerHTML = `<div class="inspector-head"><div><p class="eyebrow">${escapeHtml(source)}</p><h2>${escapeHtml(title)}</h2></div><button class="inspector-close" type="button" aria-label="Close inspector">×</button></div><div class="inspector-section"><h3>Important fields</h3>${simplePairs(Object.fromEntries(entries.slice(0, 10)))}</div><div class="inspector-section"><h3>Structured sanitized data</h3><details><summary>Show projection fields</summary><pre class="mono">${escapeHtml(JSON.stringify(record, null, 2))}</pre></details></div>`;
    element.querySelector(".inspector-close").addEventListener("click", close);
    element.querySelector(".inspector-close").focus();
  }
  element.addEventListener("keydown", (event) => { if (event.key === "Escape" && element.getAttribute("aria-hidden") === "false") close(); });
  return { open, close };
}
