"""n-day advisory matching lane. Hypothesis metadata, not a Finding."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from zest.research.types import ResearchInputError

NDAY_LANE_VERSION = "nday.version_match.v1"


@dataclass(frozen=True)
class ObservedTechVersion:
    technology: str
    version: str
    canonical_key: str
    scope_classification: str
    source_ref: str

    def __post_init__(self) -> None:
        for field_name in (
            "technology",
            "version",
            "canonical_key",
            "scope_classification",
            "source_ref",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True)
class NDayAdvisory:
    advisory_id: str
    cve_id: str
    technology: str
    affected_ranges: tuple[str, ...]
    reference: str

    def __post_init__(self) -> None:
        for field_name in ("advisory_id", "cve_id", "technology", "reference"):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name),
            )
        if not self.cve_id.startswith("CVE-"):
            raise ResearchInputError("cve_id must start with CVE-")
        if not isinstance(self.affected_ranges, tuple) or not self.affected_ranges:
            raise ResearchInputError("affected_ranges must be a non-empty tuple")
        object.__setattr__(
            self,
            "affected_ranges",
            tuple(_require_text(item, f"affected_ranges[{index}]") for index, item in enumerate(self.affected_ranges)),
        )


@dataclass(frozen=True)
class NDayAdvisoryMatch:
    lane_version: str
    observed: ObservedTechVersion
    advisory: NDayAdvisory
    match_fingerprint: str
    relation: str
    reason_codes: tuple[str, ...]
    not_a_finding: bool = True

    def __post_init__(self) -> None:
        if self.lane_version != NDAY_LANE_VERSION:
            raise ResearchInputError("lane_version mismatch")
        if not isinstance(self.observed, ObservedTechVersion):
            raise ResearchInputError("observed must be an ObservedTechVersion")
        if not isinstance(self.advisory, NDayAdvisory):
            raise ResearchInputError("advisory must be an NDayAdvisory")
        if not isinstance(self.match_fingerprint, str) or len(self.match_fingerprint) != 64:
            raise ResearchInputError("match_fingerprint must be a SHA-256 hex digest")
        if self.relation != "AFFECTED_VERSION_CANDIDATE":
            raise ResearchInputError("relation must be AFFECTED_VERSION_CANDIDATE")
        if not isinstance(self.reason_codes, tuple) or not self.reason_codes:
            raise ResearchInputError("reason_codes must be a non-empty tuple")
        if self.not_a_finding is not True:
            raise ResearchInputError("n-day matches are not Findings")


def match_nday_advisories(
    observed: ObservedTechVersion,
    advisories: tuple[NDayAdvisory, ...],
) -> tuple[NDayAdvisoryMatch, ...]:
    """Match observed in-scope tech versions to advisory ranges."""

    if not isinstance(observed, ObservedTechVersion):
        raise ResearchInputError("observed must be an ObservedTechVersion")
    if observed.scope_classification != "IN_SCOPE":
        return ()
    if not isinstance(advisories, tuple):
        raise ResearchInputError("advisories must be a tuple")
    observed_version = _parse_version(observed.version)
    matches: list[NDayAdvisoryMatch] = []
    for advisory in advisories:
        if not isinstance(advisory, NDayAdvisory):
            raise ResearchInputError("advisories must contain NDayAdvisory")
        if _normalize(advisory.technology) != _normalize(observed.technology):
            continue
        if any(_range_matches(observed_version, spec) for spec in advisory.affected_ranges):
            fingerprint = _match_fingerprint(observed, advisory)
            matches.append(
                NDayAdvisoryMatch(
                    lane_version=NDAY_LANE_VERSION,
                    observed=observed,
                    advisory=advisory,
                    match_fingerprint=fingerprint,
                    relation="AFFECTED_VERSION_CANDIDATE",
                    reason_codes=(
                        "TECHNOLOGY_VERSION_IN_ADVISORY_RANGE",
                        "N_DAY_MATCH_IS_NOT_A_FINDING",
                    ),
                )
            )
    return tuple(matches)


def _range_matches(version: tuple[int, ...], spec: str) -> bool:
    clauses = [_require_text(item, "range_clause") for item in spec.split(",")]
    for clause in clauses:
        if clause.startswith(">="):
            if version < _parse_version(clause[2:]):
                return False
        elif clause.startswith(">"):
            if version <= _parse_version(clause[1:]):
                return False
        elif clause.startswith("<="):
            if version > _parse_version(clause[2:]):
                return False
        elif clause.startswith("<"):
            if version >= _parse_version(clause[1:]):
                return False
        elif clause.startswith("="):
            if version != _parse_version(clause[1:]):
                return False
        else:
            raise ResearchInputError("range clause must start with >=, >, <=, <, or =")
    return True


def _parse_version(value: str) -> tuple[int, ...]:
    text = _require_text(value, "version")
    parts = text.split(".")
    parsed: list[int] = []
    for part in parts:
        if not part.isdigit():
            raise ResearchInputError("version must use numeric dot-separated parts")
        parsed.append(int(part))
    while len(parsed) < 3:
        parsed.append(0)
    return tuple(parsed)


def _match_fingerprint(observed: ObservedTechVersion, advisory: NDayAdvisory) -> str:
    payload = {
        "lane_version": NDAY_LANE_VERSION,
        "technology": _normalize(observed.technology),
        "version": observed.version,
        "canonical_key": observed.canonical_key,
        "advisory_id": advisory.advisory_id,
        "cve_id": advisory.cve_id,
    }
    return _sha256_json(payload)


def _normalize(value: str) -> str:
    return " ".join(value.lower().replace("_", " ").replace("-", " ").split())


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
