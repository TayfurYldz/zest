"""Availability is not a benchmark failure and not a research-quality judgment."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UnavailableReason(Enum):
    AVAILABLE = "AVAILABLE"
    MISSING_SDK = "MISSING_SDK"
    MISSING_CREDENTIAL = "MISSING_CREDENTIAL"
    MISSING_MODEL_ID = "MISSING_MODEL_ID"
    UNKNOWN_ADAPTER = "UNKNOWN_ADAPTER"


@dataclass(frozen=True)
class AdapterAvailability:
    adapter_id: str
    available: bool
    reason: UnavailableReason
    detail: str

    def to_mapping(self) -> dict[str, str | bool]:
        return {
            "adapter_id": self.adapter_id,
            "available": self.available,
            "reason": self.reason.value,
            "detail": self.detail,
            "unavailable_is_not_benchmark_failure": True,
        }
