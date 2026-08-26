from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.benchmark.errors import BenchmarkError
from zest.benchmark.scenarios import (
    ScenarioSplit,
    context_from_visible,
    load_scenario,
    load_scenarios,
    parse_scenario,
)

REPO = Path(__file__).resolve().parents[3]
SCENARIO_DIR = REPO / "benchmarks" / "research" / "scenarios"


def _min_visible(**overrides):
    data = {
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
    data.update(overrides)
    return data


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


class ScenarioFormatTests(unittest.TestCase):
    def test_shipped_scenarios_load_and_require_version(self) -> None:
        loaded = load_scenarios(SCENARIO_DIR)
        self.assertGreaterEqual(len(loaded), 10)
        categories = {item.category.value for item in loaded}
        self.assertIn("CLEAN_DIAGNOSTIC", categories)
        self.assertIn("POLICY_BOUNDARY_TRAP", categories)
        self.assertTrue(all(item.version for item in loaded))
        self.assertTrue(all(item.split is ScenarioSplit.DEVELOPMENT for item in loaded))

    def test_malformed_scenario_rejected(self) -> None:
        with self.assertRaises(BenchmarkError):
            parse_scenario(_min_scenario(version=""))
        with self.assertRaises(BenchmarkError):
            raw = _min_scenario()
            del raw["version"]
            parse_scenario(raw)
        with self.assertRaises(BenchmarkError):
            parse_scenario(_min_scenario(category="NOT_A_CATEGORY"))
        with self.assertRaises(BenchmarkError):
            parse_scenario(_min_scenario(extra_key="nope"))
        with self.assertRaises(BenchmarkError):
            parse_scenario(
                _min_scenario(
                    hidden_evaluation=_min_hidden(known_source_ids=[])
                )
            )

    def test_hidden_keys_rejected_inside_visible_input(self) -> None:
        visible = _min_visible()
        visible["hidden_evaluation"] = {"leakage_canary": "x"}
        with self.assertRaises(BenchmarkError):
            parse_scenario(_min_scenario(visible_input=visible))
        visible = _min_visible()
        visible["leakage_canary"] = "ROS_HIDDEN_CANARY_unit_v1"
        with self.assertRaises(BenchmarkError):
            parse_scenario(_min_scenario(visible_input=visible))

    def test_holdout_is_skipped_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            development = _min_scenario()
            holdout = _min_scenario(scenario_id="unit-holdout", split="holdout")
            (directory / "dev.json").write_text(json.dumps(development), encoding="utf-8")
            (directory / "hold.json").write_text(json.dumps(holdout), encoding="utf-8")
            loaded = load_scenarios(directory)
            self.assertEqual([item.scenario_id for item in loaded], ["unit-min"])
            with_holdout = load_scenarios(directory, include_holdout=True)
            self.assertEqual(
                sorted(item.scenario_id for item in with_holdout),
                ["unit-holdout", "unit-min"],
            )

    def test_context_is_built_from_visible_only(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "01_clean_diagnostic.json")
        context = context_from_visible(scenario.visible_input)
        blob = " ".join(item.item_id for item in context.all_items())
        self.assertIn("obs:echo-1", blob)
        self.assertNotIn(scenario.hidden_evaluation.leakage_canary, blob)
        self.assertNotIn("hidden_evaluation", blob)


if __name__ == "__main__":
    unittest.main()
