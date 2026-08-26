"""External duplicate-signal normalization. Advisory only, not a verdict."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from zest.research.report_package import (
    ExternalDuplicateSignal,
    FindingReportInput,
    FORBIDDEN_REPORT_PACKAGE_KEYS,
)
from zest.research.types import ResearchInputError


class DuplicateSignalRelation(Enum):
    POTENTIAL_MATCH = "POTENTIAL_MATCH"
    NO_MATCH = "NO_MATCH"


@dataclass(frozen=True)
class DisclosedReportSignal:
    source: str
    program: str
    reference: str
    title: str
    classification: str
    tags: tuple[str, ...] = ()
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for field_name in ("source", "program", "reference", "title", "classification"):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name),
            )
        if not isinstance(self.tags, tuple):
            raise ResearchInputError("tags must be a tuple")
        object.__setattr__(
            self,
            "tags",
            tuple(_require_text(item, f"tags[{index}]") for index, item in enumerate(self.tags)),
        )
        if self.metadata is not None:
            if not isinstance(self.metadata, Mapping):
                raise ResearchInputError("metadata must be a mapping")
            object.__setattr__(self, "metadata", dict(self.metadata))
        _reject_forbidden(self.to_signal_payload(), "disclosed_report_signal")

    def to_signal_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "program": self.program,
            "reference": self.reference,
            "title": self.title,
            "classification": self.classification,
            "tags": list(self.tags),
            "metadata": {} if self.metadata is None else dict(self.metadata),
        }


@dataclass(frozen=True)
class DuplicateSignalEvaluation:
    relation: DuplicateSignalRelation
    reason_codes: tuple[str, ...]
    signal_fingerprint: str
    external_signal: ExternalDuplicateSignal | None

    def __post_init__(self) -> None:
        if not isinstance(self.relation, DuplicateSignalRelation):
            raise ResearchInputError("relation must be a DuplicateSignalRelation")
        if not isinstance(self.reason_codes, tuple) or not self.reason_codes:
            raise ResearchInputError("reason_codes must be a non-empty tuple")
        if not isinstance(self.signal_fingerprint, str) or len(self.signal_fingerprint) != 64:
            raise ResearchInputError("signal_fingerprint must be a SHA-256 hex digest")


def evaluate_disclosed_report_duplicate_signal(
    finding: FindingReportInput,
    disclosed: DisclosedReportSignal,
) -> DuplicateSignalEvaluation:
    """Evaluate one external disclosure signal as advisory duplicate metadata."""

    if not isinstance(finding, FindingReportInput):
        raise ResearchInputError("finding must be a FindingReportInput")
    if not isinstance(disclosed, DisclosedReportSignal):
        raise ResearchInputError("disclosed must be a DisclosedReportSignal")
    signal_fingerprint = disclosed_signal_fingerprint(disclosed)
    if _normalize(finding.classification) == _normalize(disclosed.classification):
        return _potential(disclosed, signal_fingerprint, "CLASSIFICATION_MATCH")
    title_overlap = _token_overlap(finding.title, disclosed.title)
    claim_overlap = _token_overlap(finding.claim, " ".join((disclosed.title, *disclosed.tags)))
    if title_overlap >= 2 or claim_overlap >= 3:
        return _potential(disclosed, signal_fingerprint, "TEXT_OVERLAP")
    return DuplicateSignalEvaluation(
        relation=DuplicateSignalRelation.NO_MATCH,
        reason_codes=("NO_DISCLOSED_REPORT_OVERLAP",),
        signal_fingerprint=signal_fingerprint,
        external_signal=None,
    )


def disclosed_signal_fingerprint(disclosed: DisclosedReportSignal) -> str:
    payload = {
        "source": _normalize(disclosed.source),
        "program": _normalize(disclosed.program),
        "reference": disclosed.reference,
        "title": _normalize(disclosed.title),
        "classification": _normalize(disclosed.classification),
        "tags": sorted(_normalize(tag) for tag in disclosed.tags),
    }
    return _sha256_json(payload)


def _potential(
    disclosed: DisclosedReportSignal,
    signal_fingerprint: str,
    reason_code: str,
) -> DuplicateSignalEvaluation:
    return DuplicateSignalEvaluation(
        relation=DuplicateSignalRelation.POTENTIAL_MATCH,
        reason_codes=(reason_code, "EXTERNAL_SIGNAL_IS_ADVISORY"),
        signal_fingerprint=signal_fingerprint,
        external_signal=ExternalDuplicateSignal(
            source=disclosed.source,
            signal_type="disclosed_report",
            reference=disclosed.reference,
            relation=DuplicateSignalRelation.POTENTIAL_MATCH.value,
            signal_fingerprint=signal_fingerprint,
        ),
    )


def _token_overlap(left: str, right: str) -> int:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    return len(left_tokens.intersection(right_tokens))


def _tokens(value: str) -> set[str]:
    return {token for token in _normalize(value).split() if len(token) >= 4}


def _normalize(value: str) -> str:
    return " ".join(value.lower().replace("/", " ").replace("-", " ").split())


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _reject_forbidden(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    found = _forbidden_keys(payload)
    if found:
        raise ResearchInputError(f"{field_name} must not contain {sorted(found)}")
    return dict(payload)


def _forbidden_keys(payload: object) -> set[str]:
    found: set[str] = set()
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if str(key) in FORBIDDEN_REPORT_PACKAGE_KEYS:
                found.add(str(key))
            found.update(_forbidden_keys(value))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            found.update(_forbidden_keys(item))
    return found


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
