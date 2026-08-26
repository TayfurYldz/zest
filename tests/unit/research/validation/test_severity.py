from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.impact.types import ImpactKind
from zest.research.validation.severity import (
    InternalSeverity,
    ScopeState,
    SeverityInput,
    ValidationState,
    bound_severity,
    classify_severity,
)


class SeverityEngineTests(unittest.TestCase):
    def test_validation_must_pass_before_severity_is_scored(self) -> None:
        result = classify_severity(
            SeverityInput(
                validation_state=ValidationState.NOT_PASSED,
                scope_state=ScopeState.IN_SCOPE,
                impact_kinds=(ImpactKind.AUTH_BYPASS,),
            )
        )

        self.assertFalse(result.scored)
        self.assertIsNone(result.severity)
        self.assertEqual(result.reason_codes, ("SEVERITY_REJECTED_VALIDATION_NOT_PASSED",))

    def test_out_of_scope_signal_is_not_scored(self) -> None:
        result = classify_severity(
            SeverityInput(
                validation_state="PASSED",
                scope_state="NOT_IN_SCOPE",
                impact_kinds=(ImpactKind.DATA_READ,),
            )
        )

        self.assertFalse(result.scored)
        self.assertEqual(result.reason_codes, ("SEVERITY_REJECTED_NOT_IN_SCOPE",))

    def test_account_takeover_maps_to_internal_p0(self) -> None:
        result = classify_severity(
            SeverityInput(
                validation_state="PASSED",
                scope_state="IN_SCOPE",
                impact_kinds=(ImpactKind.ACCOUNT_TAKEOVER_PATH,),
            )
        )

        self.assertEqual(result.severity, InternalSeverity.P0)
        assert result.platform_mapping is not None
        self.assertEqual(result.platform_mapping.bugcrowd_priority, "P1")
        self.assertEqual(result.platform_mapping.hackerone_severity, "Critical")

    def test_bounded_data_read_maps_to_internal_p2(self) -> None:
        result = classify_severity(
            SeverityInput(
                validation_state="PASSED",
                scope_state="IN_SCOPE",
                impact_kinds=(ImpactKind.DATA_READ,),
            )
        )

        self.assertEqual(result.severity, InternalSeverity.P2)
        assert result.platform_mapping is not None
        self.assertEqual(result.platform_mapping.bugcrowd_priority, "P3")

    def test_bulk_sensitive_data_escalates_to_p0(self) -> None:
        result = classify_severity(
            SeverityInput(
                validation_state="PASSED",
                scope_state="IN_SCOPE",
                impact_kinds=(ImpactKind.DATA_READ,),
                data_sensitivity="BULK_SENSITIVE",
            )
        )

        self.assertEqual(result.severity, InternalSeverity.P0)

    def test_caller_bulk_sensitive_cannot_exceed_demonstrated_data_read(self) -> None:
        demonstrated = classify_severity(
            SeverityInput(
                validation_state="PASSED",
                scope_state="IN_SCOPE",
                impact_kinds=(ImpactKind.DATA_READ,),
            )
        )
        requested = classify_severity(
            SeverityInput(
                validation_state="PASSED",
                scope_state="IN_SCOPE",
                impact_kinds=(ImpactKind.DATA_READ,),
                data_sensitivity="BULK_SENSITIVE",
                affected_scope="ADMIN",
            )
        )
        bounded = bound_severity(demonstrated, requested)
        self.assertEqual(demonstrated.severity, InternalSeverity.P2)
        self.assertEqual(requested.severity, InternalSeverity.P0)
        self.assertEqual(bounded.severity, InternalSeverity.P2)
        self.assertIn("CALLER_SEVERITY_CLAMPED", bounded.reason_codes)


if __name__ == "__main__":
    unittest.main()
