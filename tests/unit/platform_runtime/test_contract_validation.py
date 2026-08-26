from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.platform.contract_validation import (
    ContractValidationError,
    ContractValidator,
)
from support.worker_requests import valid_worker_request


class ContractValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = ContractValidator()

    def test_valid_request_and_result(self) -> None:
        request = valid_worker_request()
        self.validator.validate_worker_request(request)
        result = {
            "contract_version": "v1",
            "correlation": request["correlation"],
            "worker_id": "local-python-diagnostic",
            "status": "SUCCEEDED",
            "started_at": "2026-08-16T20:00:00Z",
            "completed_at": "2026-08-16T20:00:01+00:00",
            "raw_result": {"echoed": "ping"},
        }
        self.validator.validate_worker_result(result)
        self.assertTrue(self.validator.correlation_matches(request, result))

    def test_malformed_request_unknown_field(self) -> None:
        request = valid_worker_request()
        request["unexpected"] = True
        with self.assertRaises(ContractValidationError):
            self.validator.validate_worker_request(request)

    def test_unsupported_version_rejected(self) -> None:
        request = valid_worker_request(contract_version="v2")
        with self.assertRaises(ContractValidationError):
            self.validator.validate_worker_request(request)

    def test_unknown_urn_retrieve_fails_closed(self) -> None:
        from referencing.exceptions import Unresolvable

        from zest.platform.contract_validation import _no_network_retrieve

        with self.assertRaises(Unresolvable):
            _no_network_retrieve("https://example.invalid/schema.json")

    def test_correlation_mismatch_detected(self) -> None:
        request = valid_worker_request()
        result = {
            "contract_version": "v1",
            "correlation": {
                **request["correlation"],
                "correlation_id": "other",
            },
            "worker_id": "w",
            "status": "SUCCEEDED",
        }
        self.validator.validate_worker_result(result)
        self.assertFalse(self.validator.correlation_matches(request, result))


if __name__ == "__main__":
    unittest.main()
