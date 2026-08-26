from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.benchmark.errors import BenchmarkError
from zest.benchmark.holdout import load_sealed_holdout
from zest.benchmark.scenarios import load_scenarios, parse_scenario


def _min_visible():
    return {
        "research_run_id": "run-x",
        "research_question": "What is observable?",
        "observations": [
            {
                "observation_id": "obs:1",
                "observation_kind": "diagnostic.echo.result",
                "payload": {"echoed": "x"},
            }
        ],
    }


def _min_hidden(**overrides):
    data = {
        "leakage_canary": "ROS_HIDDEN_CANARY_unit_v1",
        "known_source_ids": ["run:run-x", "proc:research-question", "obs:1"],
        "forbidden_fabricated_source_ids": ["src:fabricated-cve-db"],
        "expected_admission_outcomes": ["ADMITTED"],
    }
    data.update(overrides)
    return data


def _min_scenario(**overrides):
    data = {
        "scenario_id": "unit-min",
        "version": "1",
        "category": "CLEAN_DIAGNOSTIC",
        "split": "development",
        "visible_input": _min_visible(),
        "hidden_evaluation": _min_hidden(),
    }
    data.update(overrides)
    return data


class HoldoutIntegrityTests(unittest.TestCase):
    def test_missing_holdout_is_unavailable_not_fake_pass(self) -> None:
        missing = load_sealed_holdout(Path(tempfile.gettempdir()) / "no-such-holdout")
        self.assertFalse(missing.available)
        self.assertIn("unavailable", missing.reason)
        mapping = missing.to_mapping()
        self.assertTrue(mapping["sealed_contents_omitted"])
        self.assertNotIn("hidden_evaluation", mapping)

    def test_external_sealed_path_loads_and_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            sealed = _min_scenario(scenario_id="sealed-one", split="sealed_holdout")
            (directory / "one.json").write_text(json.dumps(sealed), encoding="utf-8")
            loaded = load_sealed_holdout(directory)
            self.assertTrue(loaded.available)
            self.assertEqual(loaded.manifest.scenario_count, 1)
            self.assertTrue(loaded.manifest.sealed)
            self.assertNotIn("ROS_HIDDEN_CANARY", json.dumps(loaded.to_mapping()))
            first = loaded.manifest.suite_fingerprint
            again = load_sealed_holdout(directory)
            self.assertEqual(first, again.manifest.suite_fingerprint)
            mutated = parse_scenario(sealed)
            raw = json.loads((directory / "one.json").read_text(encoding="utf-8"))
            raw["hidden_evaluation"]["expected_admission_outcomes"] = ["REJECTED_UNTESTABLE"]
            (directory / "one.json").write_text(json.dumps(raw), encoding="utf-8")
            changed = load_sealed_holdout(directory)
            self.assertNotEqual(first, changed.manifest.suite_fingerprint)

    def test_mixed_split_in_sealed_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "dev.json").write_text(
                json.dumps(_min_scenario()), encoding="utf-8"
            )
            (directory / "seal.json").write_text(
                json.dumps(_min_scenario(scenario_id="sealed-one", split="sealed_holdout")),
                encoding="utf-8",
            )
            with self.assertRaises(BenchmarkError):
                load_sealed_holdout(directory)

    def test_calibration_is_not_default_and_not_called_holdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "dev.json").write_text(json.dumps(_min_scenario()), encoding="utf-8")
            (directory / "cal.json").write_text(
                json.dumps(_min_scenario(scenario_id="cal-one", split="calibration")),
                encoding="utf-8",
            )
            default = load_scenarios(directory)
            self.assertEqual([item.scenario_id for item in default], ["unit-min"])
            with_cal = load_scenarios(directory, include_calibration=True)
            self.assertEqual(
                sorted(item.scenario_id for item in with_cal), ["cal-one", "unit-min"]
            )


if __name__ == "__main__":
    unittest.main()
