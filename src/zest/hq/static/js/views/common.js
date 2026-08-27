import { bundleItems, recordList, section, table } from "../components.js";

export function viewHeader(title, description, actions = "") {
  return `<div class="view-header"><div><h2>${title}</h2><p class="muted">${description}</p></div><div class="view-actions">${actions}</div></div>`;
}

export function collection(analysis, path, title, context, columns) {
  return table(title, bundleItems(analysis, path), context, columns);
}

export function listCollection(analysis, path, title, context) {
  return recordList(title, bundleItems(analysis, path), context);
}

export function unavailable(message = "PostgreSQL destekli analizi yüklemek için bir çalışma seçin.") {
  return section("Analiz kullanılamıyor", `<div class="notice">${message}</div>`);
}
