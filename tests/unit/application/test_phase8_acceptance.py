"""Phase 8 census/trace unit checks. Not lifecycle qualification."""

from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.phase8_acceptance_census import (
    PHASE8_ACCEPTANCE_CENSUS,
    PHASE8_ACCEPTANCE_CENSUS_COMPLETE,
)


class Phase8CensusTests(unittest.TestCase):
    def test_census_is_complete_and_names_start_path(self) -> None:
        self.assertTrue(PHASE8_ACCEPTANCE_CENSUS_COMPLETE)
        names = {item["name"] for item in PHASE8_ACCEPTANCE_CENSUS}
        for required in (
            "Operator API / dashboard START",
            "reconstruct_start_command",
            "Preflight",
            "ARC.start",
            "LocalRunSupervisorRegistry",
            "PostgreSQL harness",
        ):
            self.assertIn(required, names)
