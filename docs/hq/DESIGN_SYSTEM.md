# Research OS HQ design system

## 1. Context and goals

Research OS HQ is a dark, high-information-density operational research command center. It exposes persisted provenance, authorization context, hypothesis testing, evidence lineage, coverage debt, and audit state. It is not a marketing site, generic SaaS admin panel, AI chat, Grafana clone, or fake attack graph.

The frontend is a modular vanilla JavaScript surface under `src/research_os/hq/static/`. `dashboard.py` serves known package assets and remains the HTTP/API boundary. The HQ projection is read-only with respect to research semantics; PostgreSQL remains the source of truth.

## 2. Semantic design tokens

- `--bg` is the near-black graphite/navy page background.
- `--surface-0` through `--surface-3` are progressively elevated cool-charcoal surfaces.
- `--text`, `--muted`, and `--subtle` are the off-white, steel/slate, and low-emphasis text tiers.
- `--accent` is restrained chartreuse for selection and primary action.
- `--cyan` means active or informational execution.
- `--green` means healthy, admitted, or verified-positive.
- `--amber` means warning, held, or human-required.
- `--red` means denied, failed, or dangerous.

The UI MUST NOT use color alone to communicate state. Purple/blue gradients, neon decoration, fake metrics, and giant glowing cards are prohibited.

## 3. Typography

The sans stack is `Geist, Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`; no external font request is made. Normal prose MUST remain sans-serif. Monospace is reserved for opaque IDs, hashes, correlation IDs, endpoints, attempt IDs, evidence IDs, and technical provenance. Metadata is 11–13px, normal UI is 14px, headings are 15–28px, and major live values are 24px or smaller.

## 4. Surfaces, borders, and radius

Panels use solid elevated surfaces and one-pixel `--line` borders. Glass/backdrop blur is limited to the operational context bar and inspector overlay. Radius is 6px for controls, 8px for panels, and 12px for larger surfaces. Full pills are reserved for genuine status chips.

## 5. Navigation

The primary rail contains twelve independent destinations. Each destination has its own view module and is selected without a page reload. `aria-current="page"` identifies the selected destination. The rail compacts into a horizontally scrollable navigation strip below 768px.

## 6. Operational state semantics

State labels MUST remain explicit: `DENY`, `BLOCKED`, `HUMAN_REQUIRED`, and `UNKNOWN_OUTCOME` are operational data. `UNKNOWN_OUTCOME` means automatic retry is disabled until explicitly reconciled. Approval is not execution authorization. WorkerResult is not Observation; Observation is not Evidence; Evidence is not Candidate; Candidate is not Finding.

## 7. Component anatomy and states

Reusable buttons, navigation items, run controls, filters, explorer rows, record links, inspector controls, refresh controls, and setup controls MUST define default, hover, focus-visible, active/selected, disabled, loading, and error behavior where applicable. Interactive records use semantic `<button>` elements. Empty projections say `NO DATA`, `UNKNOWN`, or `UNAVAILABLE`; they never contain invented records.

## 8. Inspector behavior

Selecting a persisted record opens the shared inspector. It presents semantic type/title, important fields, IDs/provenance, relationships when present, and a collapsible structured sanitized-data section. It MUST not expose raw bytes or secrets. Escape and the explicit close button close it, and focus returns to the triggering control.

## 9. Responsive rules

The primary target is a 1440px+ workstation, with functional layouts at 1280px, 1024px, 768px, and narrower widths. The rail may compact, workspace columns may stack, tables MUST use bounded horizontal overflow, and the inspector becomes near/full width on narrow screens.

## 10. Accessibility acceptance criteria

The surface targets WCAG 2.2 AA. It MUST provide keyboard navigation, visible `:focus-visible`, semantic buttons and labels, selected navigation state, loading/error text, non-color state communication, sufficient contrast, bounded table overflow, reduced-motion support, and a usable tab order. The inspector MUST be keyboard usable.

## 11. Content and tone rules

Copy is concise, operational, provenance-first, and explicit about uncertainty. Use `UNKNOWN`, `UNAVAILABLE`, and `NO DATA` when the projection cannot prove a value. Do not use marketing claims, fake confidence, fake cost, fake latency, fake quality, emoji, or hacker-movie decoration.

## 12. Anti-patterns

Do not add a second scheduler, client-side authorization, custom login, password database, fake graph, fabricated relationship, browser override of target/scope/budget, external asset, CDN, remote font, React/Vue/Svelte/Tailwind/Bootstrap, or a giant raw JSON dump as the primary UI.

## 13. QA checklist

- Confirm all twelve navigation destinations render independently.
- Confirm selected-run analysis is fetched only for the selected run and with bounded polling.
- Confirm run controls send only a run ID and preserve server-side configuration authority.
- Confirm setup remains available and bounded.
- Confirm no secrets, raw bytes, or fabricated records are shown.
- Confirm static path containment, no-store headers, CSP, and local-only binding.
- Confirm keyboard focus, Escape inspector close, reduced motion, and responsive overflow.
- Run the focused HQ/dashboard tests and `git diff --check` before handoff.
