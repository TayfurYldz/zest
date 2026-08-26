from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.nday import (
    NDAY_LANE_VERSION,
    NDayAdvisory,
    ObservedTechVersion,
    match_nday_advisories,
)
from zest.research.types import ResearchInputError


def _observed(*, scope: str = "IN_SCOPE", version: str = "1.4.2") -> ObservedTechVersion:
    return ObservedTechVersion(
        technology="Example Server",
        version=version,
        canonical_key="tech:example-server:https://example.test",
        scope_classification=scope,
        source_ref="tech-fingerprint-1",
    )


def _advisory(*, technology: str = "example-server") -> NDayAdvisory:
    return NDayAdvisory(
        advisory_id="adv-1",
        cve_id="CVE-2026-0001",
        technology=technology,
        affected_ranges=(">=1.0.0,<1.5.0",),
        reference="https://advisory.test/CVE-2026-0001",
    )


class SDG14NDayTests(unittest.TestCase):
    def test_in_scope_matching_version_creates_not_finding_candidate(self) -> None:
        matches = match_nday_advisories(_observed(), (_advisory(),))

        self.assertEqual(len(matches), 1)
        match = matches[0]
        self.assertEqual(match.lane_version, NDAY_LANE_VERSION)
        self.assertEqual(match.relation, "AFFECTED_VERSION_CANDIDATE")
        self.assertTrue(match.not_a_finding)
        self.assertEqual(len(match.match_fingerprint), 64)
        self.assertIn("N_DAY_MATCH_IS_NOT_A_FINDING", match.reason_codes)

    def test_out_of_scope_observation_never_matches(self) -> None:
        matches = match_nday_advisories(_observed(scope="OUT_OF_SCOPE"), (_advisory(),))

        self.assertEqual(matches, ())

    def test_different_technology_does_not_match(self) -> None:
        matches = match_nday_advisories(_observed(), (_advisory(technology="other"),))

        self.assertEqual(matches, ())

    def test_unsupported_version_format_fails_closed(self) -> None:
        with self.assertRaises(ResearchInputError):
            match_nday_advisories(_observed(version="1.4.2-beta"), (_advisory(),))

    def test_invalid_range_clause_fails_closed(self) -> None:
        with self.assertRaises(ResearchInputError):
            match_nday_advisories(
                _observed(),
                (
                    NDayAdvisory(
                        advisory_id="adv-1",
                        cve_id="CVE-2026-0001",
                        technology="example-server",
                        affected_ranges=("1.0.0-1.5.0",),
                        reference="https://advisory.test/CVE-2026-0001",
                    ),
                ),
            )


if __name__ == "__main__":
    unittest.main()
