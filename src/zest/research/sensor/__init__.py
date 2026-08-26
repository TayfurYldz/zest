"""Sensor/Acquisition Plane: passive/semi-passive external census.

Sensors produce SensorObservation records. They never write domain truth.
Observations are UNTRUSTED_EXTERNAL until admitted through the Research
admission chain.
"""

from zest.research.sensor.archive import WaybackArchiveSensor
from zest.research.sensor.cert import CertificateMetaSensor
from zest.research.sensor.ctlog import CTLogSensor
from zest.research.sensor.dns import DNSSensor
from zest.research.sensor.techfp import TechnologyFingerprintSensor
from zest.research.sensor.types import (
    FixtureLoader,
    ScopeCensusView,
    SensorCollectionResult,
    SensorError,
    SensorObservation,
    SensorPort,
    SensorTimeoutError,
)

__all__ = [
    "CertificateMetaSensor",
    "CTLogSensor",
    "DNSSensor",
    "FixtureLoader",
    "ScopeCensusView",
    "SensorCollectionResult",
    "SensorError",
    "SensorObservation",
    "SensorPort",
    "SensorTimeoutError",
    "TechnologyFingerprintSensor",
    "WaybackArchiveSensor",
]
