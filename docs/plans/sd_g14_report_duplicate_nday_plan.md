# SD-G14 Plan — Report, Duplicate Economics, and n-day Lane

**Status:** PASS
**Previous gate:** SD-G13 PASS (`2bb0c23`)

## Purpose

SD-G14 turns approved Findings into reviewable report packages, adds duplicate
economics, and introduces the n-day lane without treating public signals as
vulnerability truth. This gate is an operations/review muscle, not a submission
bot.

## Non-Negotiables

- No package exists without an approved Finding.
- Report packages do not auto-submit to any platform.
- Internal duplicate fingerprints are deterministic and source from approved
  Finding content.
- External duplicate signals are advisory only; they do not prove or disprove a
  Finding.
- Packages must not include raw payload/body content or secrets.
- n-day mapping is a lane, not the main brain and not a finding by itself.

## P1 — Approved Finding Report Package

Files:

- `src/zest/research/report_package.py`
- `src/zest/application/package_finding_report.py`
- `tests/unit/research/test_sd_g14_report_package.py`
- `tests/unit/application/test_sd_g14_package_finding_report.py`

Behavior:

- builds deterministic `report.package.v1` packages from approved Findings;
- includes summary, proof anchors, reproduction anchors, duplicate-check
  metadata, and safety metadata;
- computes a normalized internal duplicate fingerprint from title, claim, and
  classification;
- accepts external duplicate signals as advisory metadata only;
- rejects secret/raw request keys in package inputs;
- writes an audit event when a package is built;
- does not create Findings, Evidence, Candidates, HumanReview, Approval, or
  platform submissions.

Evidence:

- Focused checks (2026-08-20): `7 passed`.
- Affected checks (2026-08-20): `53 passed`.
- Full suite (2026-08-20): `1514 passed, 9 skipped, 53 subtests passed`.

## P2 — External Duplicate Signal Normalization

Files:

- `src/zest/research/report_duplicate.py`
- `src/zest/research/report_package.py`
- `tests/unit/research/test_sd_g14_duplicate_signals.py`

Behavior:

- normalizes disclosed-report or program-page signals into advisory duplicate
  metadata;
- creates SHA-256 signal fingerprints for external duplicate signals;
- emits `POTENTIAL_MATCH` only as advisory metadata, never as a duplicate
  verdict;
- emits `NO_MATCH` for unrelated disclosed reports without changing Finding
  truth;
- rejects provider metadata containing secret/raw request keys;
- preserves external signal fingerprints inside report packages.

Evidence:

- Focused checks (2026-08-20): `11 passed`.
- Affected checks (2026-08-20): `57 passed`.
- Full suite (2026-08-20): `1518 passed, 9 skipped, 53 subtests passed`.

## P3 — n-day Version-to-CVE Lane

Files:

- `src/zest/research/nday.py`
- `tests/unit/research/test_sd_g14_nday.py`

Behavior:

- matches in-scope observed technology versions to provider-supplied advisory
  records;
- supports bounded numeric version range clauses such as `>=1.0.0,<1.5.0`;
- produces `AFFECTED_VERSION_CANDIDATE` metadata only, not Findings;
- returns no matches for out-of-scope observations;
- fails closed on unsupported version or range formats;
- keeps version-to-CVE mapping as a lane, not the main Research brain.

Evidence:

- Focused checks (2026-08-20): `16 passed`.
- Affected checks (2026-08-20): `74 passed, 10 subtests passed`.

## P4 — PostgreSQL Package Vertical Slice

Files:

- `tests/integration/test_sd_g14_report_package.py`

Behavior:

- proves `PackageFindingReport` can load an approved Finding from PostgreSQL;
- builds a `report.package.v1` package with proof/reproduction anchors;
- persists a `REPORT_PACKAGE_BUILT` audit event;
- does not create submissions, Findings, Candidates, Evidence, Approvals, or
  HumanReview records.

Evidence:

- Focused checks (2026-08-20): `17 passed`.
- Affected checks (2026-08-20): `63 passed`.
- Full suite (2026-08-20): `1524 passed, 9 skipped, 53 subtests passed`.
