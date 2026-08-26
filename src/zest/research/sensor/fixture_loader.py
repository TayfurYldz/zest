"""File-based fixture loader for sensor tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from zest.research.sensor.types import FixtureLoader


class FileFixtureLoader:
    """Load sensor fixtures from a directory tree.

    Fixture file naming: ``<sensor_id>/<target_reference>.json``.
    Targets containing ``://`` are normalized to a safe filename.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def load(self, sensor_id: str, target_reference: str) -> Mapping[str, Any]:
        safe = target_reference.replace("://", "_").replace("/", "_")
        candidate = self._base_dir / sensor_id / f"{safe}.json"
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
        return {"payload": {}, "source_metadata": {"matched": False}}


# Register as a FixtureLoader implementation at runtime.
FixtureLoader.register(FileFixtureLoader)
