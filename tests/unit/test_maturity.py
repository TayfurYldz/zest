"""Maturity flags. GATE 14/15/16/17/18/19/20 PASS is not SECURITY_RESEARCH_VALIDATED or PRODUCTION_READY."""

from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from research_os.application.operator_status import OperatorStatusSnapshot, render_operator_status
from research_os.maturity import (
    GATE_01_STATUS,
    GATE_04B_STATUS,
    GATE_09_STATUS,
    GATE_10_STATUS,
    GATE_12_STATUS,
    GATE_13_STATUS,
    GATE_14_STATUS,
    GATE_15_STATUS,
    GATE_16_STATUS,
    GATE_17_STATUS,
    GATE_18_STATUS,
    GATE_19_STATUS,
    GATE_20_STATUS,
    GATE_21_STATUS,
    GATE_22_STATUS,
    LIVE_MODEL_VALIDATED,
    PRODUCTION_READY,
    SECURITY_RESEARCH_VALIDATED,
    maturity_mapping,
)


class Gate14MaturityTests(unittest.TestCase):
    def test_gate14_and_gate15_pass_do_not_advance_research_or_production_flags(self) -> None:
        self.assertEqual(GATE_14_STATUS, "PASS")
        self.assertEqual(GATE_15_STATUS, "PASS")
        self.assertEqual(GATE_16_STATUS, "PASS")
        self.assertEqual(GATE_17_STATUS, "PASS")
        self.assertEqual(GATE_18_STATUS, "PASS")
        self.assertEqual(GATE_19_STATUS, "PASS")
        self.assertEqual(GATE_20_STATUS, "PASS")
        self.assertEqual(GATE_21_STATUS, "PASS")
        self.assertEqual(GATE_22_STATUS, "PASS")
        self.assertEqual(GATE_01_STATUS, "PASS")
        self.assertEqual(GATE_09_STATUS, "PASS")
        self.assertEqual(GATE_10_STATUS, "PASS")
        self.assertEqual(GATE_12_STATUS, "PASS")
        self.assertEqual(GATE_13_STATUS, "PASS")
        self.assertEqual(GATE_04B_STATUS, "PASS")
        self.assertTrue(LIVE_MODEL_VALIDATED)
        self.assertFalse(SECURITY_RESEARCH_VALIDATED)
        self.assertFalse(PRODUCTION_READY)
        mapping = maturity_mapping()
        self.assertEqual(mapping["GATE_01"], "PASS")
        self.assertEqual(mapping["GATE_14"], "PASS")
        self.assertEqual(mapping["GATE_15"], "PASS")
        self.assertEqual(mapping["GATE_16"], "PASS")
        self.assertEqual(mapping["GATE_17"], "PASS")
        self.assertEqual(mapping["GATE_18"], "PASS")
        self.assertEqual(mapping["GATE_19"], "PASS")
        self.assertEqual(mapping["GATE_20"], "PASS")
        self.assertEqual(mapping["GATE_21"], "PASS")
        self.assertEqual(mapping["GATE_22"], "PASS")
        self.assertEqual(mapping["GATE_09"], "PASS")
        self.assertEqual(mapping["GATE_10"], "PASS")
        self.assertEqual(mapping["GATE_04B"], "PASS")
        self.assertIs(mapping["LIVE_MODEL_VALIDATED"], True)
        self.assertIs(mapping["SECURITY_RESEARCH_VALIDATED"], False)
        self.assertIs(mapping["PRODUCTION_READY"], False)

    def test_operator_status_reports_gate14_and_gate15_pass_without_production_ready(self) -> None:
        text = render_operator_status(
            OperatorStatusSnapshot(
                postgresql="HEALTHY",
                worker={"local-python": "HEALTHY"},
                model_runtimes={"API": "UNAVAILABLE"},
                strix="UNAVAILABLE",
                auth="runtime-owned sessions only",
                orchestrator="READY",
                budget_ledger="append-only",
                reconciliation="available",
                observability="in-memory",
            )
        )
        self.assertIn("GATE 01:", text)
        self.assertIn("GATE 10:", text)
        self.assertIn("GATE 14:", text)
        self.assertIn("GATE 15:", text)
        self.assertIn("GATE 16:", text)
        self.assertIn("GATE 17:", text)
        self.assertIn("GATE 18:", text)
        self.assertIn("GATE 19:", text)
        self.assertIn("GATE 20:", text)
        self.assertIn("GATE 21:", text)
        self.assertIn("GATE 22:", text)
        self.assertIn(f"  {GATE_01_STATUS}", text)
        self.assertIn(f"  {GATE_10_STATUS}", text)
        self.assertIn(f"  {GATE_14_STATUS}", text)
        self.assertIn(f"  {GATE_15_STATUS}", text)
        self.assertIn(f"  {GATE_16_STATUS}", text)
        self.assertIn(f"  {GATE_17_STATUS}", text)
        self.assertIn(f"  {GATE_18_STATUS}", text)
        self.assertIn(f"  {GATE_19_STATUS}", text)
        self.assertIn(f"  {GATE_20_STATUS}", text)
        self.assertIn(f"  {GATE_21_STATUS}", text)
        self.assertIn(f"  {GATE_22_STATUS}", text)
        self.assertIn(f"SECURITY_RESEARCH_VALIDATED: {SECURITY_RESEARCH_VALIDATED}", text)
        self.assertIn(f"PRODUCTION_READY: {PRODUCTION_READY}", text)
        self.assertIn(f"LIVE_MODEL_VALIDATED: {LIVE_MODEL_VALIDATED}", text)
        self.assertEqual(GATE_14_STATUS, "PASS")
        self.assertEqual(GATE_15_STATUS, "PASS")
        self.assertEqual(GATE_16_STATUS, "PASS")
        self.assertEqual(GATE_17_STATUS, "PASS")
        self.assertEqual(GATE_18_STATUS, "PASS")
        self.assertEqual(GATE_19_STATUS, "PASS")
        self.assertEqual(GATE_20_STATUS, "PASS")
        self.assertEqual(GATE_21_STATUS, "PASS")
        self.assertEqual(GATE_22_STATUS, "PASS")
        self.assertEqual(GATE_01_STATUS, "PASS")
        self.assertEqual(GATE_09_STATUS, "PASS")
        self.assertEqual(GATE_10_STATUS, "PASS")
        self.assertFalse(SECURITY_RESEARCH_VALIDATED)
        self.assertFalse(PRODUCTION_READY)
        self.assertTrue(LIVE_MODEL_VALIDATED)
        self.assertEqual(GATE_04B_STATUS, "PASS")


if __name__ == "__main__":
    unittest.main()
