"""Submission package planning for approved Findings. Does not submit reports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from zest.research.types import ResearchInputError

REPORT_PACKAGE_VERSION = "report.package.v1"

FORBIDDEN_REPORT_PACKAGE_KEYS = frozenset(
    {
        "token",
        "password",
        "api_key",
        "apiKey",
        "raw_secret",
        "credential",
        "secret_value",
        "secretValue",
        "session_token",
        "cookie",
        "authorization",
        "payload",
        "body",
        "exploit",
    }
)


@dataclass(frozen=True)
class FindingReportInput:
    finding_id: str
    finding_proposal_id: str
    candidate_id: str
    research_run_id: str
    approval_id: str
    human_review_id: str
    title: str
    claim: str
    classification: str
    evidence_ids: tuple[str, ...]
    verification_ids: tuple[str, ...]
    impact_chain_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "finding_id",
            "finding_proposal_id",
            "candidate_id",
            "research_run_id",
            "approval_id",
            "human_review_id",
            "title",
            "claim",
            "classification",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self, "evidence_ids", _require_ids(self.evidence_ids, "evidence_ids")
        )
        object.__setattr__(
            self,
            "verification_ids",
            _require_ids(self.verification_ids, "verification_ids"),
        )
        if not isinstance(self.impact_chain_ids, tuple):
            raise ResearchInputError("impact_chain_ids must be a tuple")
        object.__setattr__(
            self,
            "impact_chain_ids",
            tuple(_require_text(item, f"impact_chain_ids[{index}]") for index, item in enumerate(self.impact_chain_ids)),
        )


@dataclass(frozen=True)
class ExternalDuplicateSignal:
    source: str
    signal_type: str
    reference: str
    relation: str = "POTENTIAL_MATCH"
    signal_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _require_text(self.source, "source"))
        object.__setattr__(
            self, "signal_type", _require_text(self.signal_type, "signal_type")
        )
        object.__setattr__(self, "reference", _require_text(self.reference, "reference"))
        object.__setattr__(self, "relation", _require_text(self.relation, "relation"))
        if self.signal_fingerprint is not None and len(self.signal_fingerprint) != 64:
            raise ResearchInputError("signal_fingerprint must be a SHA-256 hex digest")

    def to_dict(self) -> dict[str, str]:
        payload = {
            "source": self.source,
            "signal_type": self.signal_type,
            "reference": self.reference,
            "relation": self.relation,
        }
        if self.signal_fingerprint is not None:
            payload["signal_fingerprint"] = self.signal_fingerprint
        _reject_forbidden(payload, "external_duplicate_signal")
        return payload


@dataclass(frozen=True)
class FindingReportPackage:
    package_id: str
    package_version: str
    finding_id: str
    research_run_id: str
    internal_duplicate_fingerprint: str
    external_duplicate_signals: tuple[ExternalDuplicateSignal, ...]
    sections: Mapping[str, Any]
    package_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "package_id", _require_text(self.package_id, "package_id"))
        object.__setattr__(
            self,
            "package_version",
            _require_text(self.package_version, "package_version"),
        )
        object.__setattr__(self, "finding_id", _require_text(self.finding_id, "finding_id"))
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        if not isinstance(self.internal_duplicate_fingerprint, str) or len(self.internal_duplicate_fingerprint) != 64:
            raise ResearchInputError("internal_duplicate_fingerprint must be a SHA-256 hex digest")
        if not isinstance(self.external_duplicate_signals, tuple):
            raise ResearchInputError("external_duplicate_signals must be a tuple")
        sections = _reject_forbidden(self.sections, "sections")
        object.__setattr__(self, "sections", sections)
        if not isinstance(self.package_hash, str) or len(self.package_hash) != 64:
            raise ResearchInputError("package_hash must be a SHA-256 hex digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "package_version": self.package_version,
            "finding_id": self.finding_id,
            "research_run_id": self.research_run_id,
            "internal_duplicate_fingerprint": self.internal_duplicate_fingerprint,
            "external_duplicate_signals": [
                signal.to_dict() for signal in self.external_duplicate_signals
            ],
            "sections": dict(self.sections),
            "package_hash": self.package_hash,
            "not_auto_submitted": True,
        }


def build_finding_report_package(
    report_input: FindingReportInput,
    *,
    package_id: str,
    external_duplicate_signals: tuple[ExternalDuplicateSignal, ...] = (),
) -> FindingReportPackage:
    """Build a deterministic submission package from an approved Finding."""

    if not isinstance(report_input, FindingReportInput):
        raise ResearchInputError("report_input must be a FindingReportInput")
    package_id = _require_text(package_id, "package_id")
    if not isinstance(external_duplicate_signals, tuple):
        raise ResearchInputError("external_duplicate_signals must be a tuple")
    signals = tuple(
        signal
        if isinstance(signal, ExternalDuplicateSignal)
        else ExternalDuplicateSignal(
            **_reject_forbidden(dict(signal), "external_duplicate_signal")
        )
        for signal in external_duplicate_signals
    )
    duplicate_fingerprint = internal_duplicate_fingerprint(report_input)
    sections = {
        "summary": {
            "title": report_input.title,
            "classification": report_input.classification,
            "claim": report_input.claim,
        },
        "proof": {
            "evidence_ids": list(report_input.evidence_ids),
            "verification_ids": list(report_input.verification_ids),
            "impact_chain_ids": list(report_input.impact_chain_ids),
            "approval_id": report_input.approval_id,
            "human_review_id": report_input.human_review_id,
        },
        "reproduction": {
            "source": "verification_records",
            "verification_ids": list(report_input.verification_ids),
            "fresh_session_required": True,
        },
        "duplicate_check": {
            "internal_duplicate_fingerprint": duplicate_fingerprint,
            "external_signal_count": len(signals),
            "external_signals_are_truth": False,
        },
        "safety": {
            "not_auto_submitted": True,
            "redaction_required_before_platform_submission": True,
            "raw_payloads_included": False,
        },
    }
    payload = {
        "package_id": package_id,
        "package_version": REPORT_PACKAGE_VERSION,
        "finding_id": report_input.finding_id,
        "research_run_id": report_input.research_run_id,
        "internal_duplicate_fingerprint": duplicate_fingerprint,
        "external_duplicate_signals": [signal.to_dict() for signal in signals],
        "sections": sections,
    }
    return FindingReportPackage(
        package_id=package_id,
        package_version=REPORT_PACKAGE_VERSION,
        finding_id=report_input.finding_id,
        research_run_id=report_input.research_run_id,
        internal_duplicate_fingerprint=duplicate_fingerprint,
        external_duplicate_signals=signals,
        sections=sections,
        package_hash=_sha256_json(payload),
    )


def internal_duplicate_fingerprint(report_input: FindingReportInput) -> str:
    payload = {
        "title": _normalize(report_input.title),
        "claim": _normalize(report_input.claim),
        "classification": report_input.classification,
    }
    return _sha256_json(payload)


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_ids(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value:
        raise ResearchInputError(f"{field_name} must be a non-empty tuple")
    return tuple(_require_text(item, f"{field_name}[{index}]") for index, item in enumerate(value))


def _reject_forbidden(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ResearchInputError(f"{field_name} must be a mapping")
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


def _normalize(value: str) -> str:
    return " ".join(value.lower().split())


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
