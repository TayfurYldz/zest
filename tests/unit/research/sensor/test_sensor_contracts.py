"""SD-G2 sensor contract tests: every sensor, every fixture class."""

from __future__ import annotations

import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.core.enums import ReasonCode, ScopeClassification
from zest.research.sensor import (
    CTLogSensor,
    CertificateMetaSensor,
    DNSSensor,
    SensorPort,
    TechnologyFingerprintSensor,
    WaybackArchiveSensor,
)
from zest.research.sensor.fixture_loader import FileFixtureLoader
from zest.research.sensor.types import ScopeCensusView


FIXTURE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "sensor"

IN_SCOPE = ScopeCensusView(
    classification=ScopeClassification.IN_SCOPE,
    reason_code=ReasonCode.ALLOWED,
)
UNKNOWN = ScopeCensusView(
    classification=ScopeClassification.UNKNOWN,
    reason_code=ReasonCode.SCOPE_UNKNOWN_CLASSIFICATION,
)
OUT_OF_SCOPE = ScopeCensusView(
    classification=ScopeClassification.OUT_OF_SCOPE,
    reason_code=ReasonCode.SCOPE_DENIED,
)


class SensorContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._observation_counter = 0

    def _next_observation_id(self) -> str:
        self._observation_counter += 1
        return f"obs:test:{self._observation_counter}"

    def _sensor(self, cls, target: str):
        return cls(FileFixtureLoader(FIXTURE_DIR)), target

    def _assert_observation(self, sensor, target, scope_view):
        observation_id = self._next_observation_id()
        result = sensor.collect(
            observation_id,
            target,
            scope_view,
            research_run_id="run-1",
        )
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.observations[0].observation_id, observation_id)
        self.assertEqual(result.observations[0].sensor_id, sensor.sensor_id)
        self.assertEqual(result.observations[0].target_reference, target)
        self.assertEqual(result.observations[0].research_run_id, "run-1")
        self.assertEqual(result.budget_units_consumed, 1)
        return result.observations[0]

    def _assert_empty(self, sensor, target, scope_view):
        result = sensor.collect(
            self._next_observation_id(),
            target,
            scope_view,
            research_run_id="run-1",
        )
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(len(result.errors), 0)

    def _assert_malformed(self, sensor, target, scope_view):
        result = sensor.collect(
            self._next_observation_id(),
            target,
            scope_view,
            research_run_id="run-1",
        )
        self.assertEqual(len(result.observations), 0)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].reason_code, ReasonCode.SENSOR_FAILED)

    def _assert_timeout(self, sensor, target, scope_view):
        result = sensor.collect(
            self._next_observation_id(),
            target,
            scope_view,
            research_run_id="run-1",
        )
        self.assertEqual(len(result.observations), 0)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].reason_code, ReasonCode.SENSOR_TIMEOUT)

    def test_dns_normal(self) -> None:
        sensor, target = self._sensor(DNSSensor, "https://example.com")
        obs = self._assert_observation(sensor, target, IN_SCOPE)
        self.assertIn("record_types", obs.payload)
        self.assertIn("A", obs.payload["record_types"])

    def test_dns_empty(self) -> None:
        sensor, target = self._sensor(DNSSensor, "https://example-empty.com")
        self._assert_empty(sensor, target, IN_SCOPE)

    def test_dns_malformed(self) -> None:
        sensor, target = self._sensor(DNSSensor, "https://example-malformed.com")
        self._assert_malformed(sensor, target, IN_SCOPE)

    def test_dns_timeout(self) -> None:
        sensor, target = self._sensor(DNSSensor, "https://example-timeout.com")
        self._assert_timeout(sensor, target, IN_SCOPE)

    def test_ctlog_normal(self) -> None:
        sensor, target = self._sensor(CTLogSensor, "https://example.com")
        obs = self._assert_observation(sensor, target, IN_SCOPE)
        self.assertIn("entries", obs.payload)

    def test_ctlog_empty(self) -> None:
        sensor, target = self._sensor(CTLogSensor, "https://example-empty.com")
        self._assert_empty(sensor, target, IN_SCOPE)

    def test_ctlog_malformed(self) -> None:
        sensor, target = self._sensor(CTLogSensor, "https://example-malformed.com")
        self._assert_malformed(sensor, target, IN_SCOPE)

    def test_ctlog_timeout(self) -> None:
        sensor, target = self._sensor(CTLogSensor, "https://example-timeout.com")
        self._assert_timeout(sensor, target, IN_SCOPE)

    def test_archive_normal(self) -> None:
        sensor, target = self._sensor(WaybackArchiveSensor, "https://example.com")
        obs = self._assert_observation(sensor, target, IN_SCOPE)
        self.assertIn("urls", obs.payload)

    def test_archive_empty(self) -> None:
        sensor, target = self._sensor(WaybackArchiveSensor, "https://example-empty.com")
        self._assert_empty(sensor, target, IN_SCOPE)

    def test_archive_malformed(self) -> None:
        sensor, target = self._sensor(WaybackArchiveSensor, "https://example-malformed.com")
        self._assert_malformed(sensor, target, IN_SCOPE)

    def test_archive_timeout(self) -> None:
        sensor, target = self._sensor(WaybackArchiveSensor, "https://example-timeout.com")
        self._assert_timeout(sensor, target, IN_SCOPE)

    def test_cert_normal(self) -> None:
        sensor, target = self._sensor(CertificateMetaSensor, "https://example.com")
        obs = self._assert_observation(sensor, target, IN_SCOPE)
        self.assertIn("issuer", obs.payload)

    def test_cert_empty(self) -> None:
        sensor, target = self._sensor(CertificateMetaSensor, "https://example-empty.com")
        self._assert_empty(sensor, target, IN_SCOPE)

    def test_cert_malformed(self) -> None:
        sensor, target = self._sensor(CertificateMetaSensor, "https://example-malformed.com")
        self._assert_malformed(sensor, target, IN_SCOPE)

    def test_cert_timeout(self) -> None:
        sensor, target = self._sensor(CertificateMetaSensor, "https://example-timeout.com")
        self._assert_timeout(sensor, target, IN_SCOPE)

    def test_techfp_normal(self) -> None:
        sensor, target = self._sensor(TechnologyFingerprintSensor, "https://example.com")
        obs = self._assert_observation(sensor, target, IN_SCOPE)
        self.assertIn("technologies", obs.payload)

    def test_techfp_empty(self) -> None:
        sensor, target = self._sensor(TechnologyFingerprintSensor, "https://example-empty.com")
        self._assert_empty(sensor, target, IN_SCOPE)

    def test_techfp_malformed(self) -> None:
        sensor, target = self._sensor(TechnologyFingerprintSensor, "https://example-malformed.com")
        self._assert_malformed(sensor, target, IN_SCOPE)

    def test_techfp_timeout(self) -> None:
        sensor, target = self._sensor(TechnologyFingerprintSensor, "https://example-timeout.com")
        self._assert_timeout(sensor, target, IN_SCOPE)

    def test_out_of_scope_denies_all_sensors(self) -> None:
        for cls in (
            DNSSensor,
            CTLogSensor,
            WaybackArchiveSensor,
            CertificateMetaSensor,
            TechnologyFingerprintSensor,
        ):
            with self.subTest(sensor=cls.sensor_id):
                sensor, target = self._sensor(cls, "https://example.com")
                result = sensor.collect(
                    self._next_observation_id(),
                    target,
                    OUT_OF_SCOPE,
                    research_run_id="run-1",
                )
                self.assertEqual(len(result.observations), 0)
                self.assertEqual(len(result.errors), 1)
                self.assertEqual(result.errors[0].reason_code, ReasonCode.CENSUS_DENIED)

    def test_unknown_allows_census(self) -> None:
        sensor, target = self._sensor(DNSSensor, "https://example.com")
        result = sensor.collect(
            self._next_observation_id(),
            target,
            UNKNOWN,
            research_run_id="run-1",
        )
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(len(result.errors), 0)

    def test_protocol_conformance(self) -> None:
        for cls in (
            DNSSensor,
            CTLogSensor,
            WaybackArchiveSensor,
            CertificateMetaSensor,
            TechnologyFingerprintSensor,
        ):
            with self.subTest(sensor=cls.sensor_id):
                self.assertTrue(isinstance(cls(None), SensorPort))


if __name__ == "__main__":
    unittest.main()
