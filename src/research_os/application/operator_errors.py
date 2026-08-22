"""Typed operator-API failures. Transport errors are not research truth."""

from __future__ import annotations

from enum import Enum
from http import HTTPStatus

from research_os.application.errors import ApplicationError


class OperatorErrorCode(Enum):
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    INVALID_STATE = "INVALID_STATE"
    INVALID_INPUT = "INVALID_INPUT"
    LEASE_CONFLICT = "LEASE_CONFLICT"
    AUTHORIZATION_UNAVAILABLE = "AUTHORIZATION_UNAVAILABLE"
    MODEL_AUTH_REQUIRED = "MODEL_AUTH_REQUIRED"
    MODEL_RATE_LIMITED = "MODEL_RATE_LIMITED"
    WORKER_UNAVAILABLE = "WORKER_UNAVAILABLE"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    OSD_UNREACHABLE = "OSD_UNREACHABLE"


_STATUS = {
    OperatorErrorCode.PREFLIGHT_FAILED: HTTPStatus.CONFLICT,
    OperatorErrorCode.RUN_NOT_FOUND: HTTPStatus.NOT_FOUND,
    OperatorErrorCode.INVALID_STATE: HTTPStatus.CONFLICT,
    OperatorErrorCode.INVALID_INPUT: HTTPStatus.BAD_REQUEST,
    OperatorErrorCode.LEASE_CONFLICT: HTTPStatus.CONFLICT,
    OperatorErrorCode.AUTHORIZATION_UNAVAILABLE: HTTPStatus.CONFLICT,
    OperatorErrorCode.MODEL_AUTH_REQUIRED: HTTPStatus.SERVICE_UNAVAILABLE,
    OperatorErrorCode.MODEL_RATE_LIMITED: HTTPStatus.TOO_MANY_REQUESTS,
    OperatorErrorCode.WORKER_UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,
    OperatorErrorCode.DATABASE_UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,
    OperatorErrorCode.BUDGET_EXHAUSTED: HTTPStatus.CONFLICT,
    OperatorErrorCode.RECONCILIATION_REQUIRED: HTTPStatus.CONFLICT,
    OperatorErrorCode.OSD_UNREACHABLE: HTTPStatus.SERVICE_UNAVAILABLE,
}


class OperatorError(ApplicationError):
    """Structured operator failure. Not a vulnerability verdict."""

    def __init__(self, code: OperatorErrorCode, detail: str = "") -> None:
        if not isinstance(code, OperatorErrorCode):
            raise TypeError("code must be an OperatorErrorCode")
        self.code = code
        self.detail = detail.strip() or code.value
        super().__init__(self.detail)

    @property
    def http_status(self) -> HTTPStatus:
        return _STATUS[self.code]

    def to_payload(self) -> dict[str, object]:
        return {
            "ok": False,
            "error": self.code.value,
            "detail": self.detail,
            "not_research_truth": True,
        }
