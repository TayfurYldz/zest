"""SD-G4 deterministic pricing tests."""

from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.core.pricing import (
    MODEL_PRICE_TABLE,
    UnknownModelPriceError,
    estimate_cost,
)


class PricingTests(unittest.TestCase):
    def test_known_model_cost_in_microdollars(self) -> None:
        # local-fixture is priced at 0/0.
        self.assertEqual(estimate_cost("local-fixture", 1000, 500), 0)

    def test_none_tokens_are_zero(self) -> None:
        self.assertEqual(estimate_cost("local-fixture", None, None), 0)
        self.assertEqual(estimate_cost("local-fixture", 100, None), 0)
        self.assertEqual(estimate_cost("local-fixture", None, 100), 0)

    def test_unknown_model_is_fail_closed(self) -> None:
        with self.assertRaises(UnknownModelPriceError):
            estimate_cost("unknown-model", 10, 10)

    def test_missing_model_id_is_fail_closed(self) -> None:
        with self.assertRaises(UnknownModelPriceError):
            estimate_cost(None, 10, 10)

    def test_negative_tokens_rejected(self) -> None:
        with self.assertRaises(UnknownModelPriceError):
            estimate_cost("local-fixture", -1, 0)

    def test_gpt_4o_mini_known_rate(self) -> None:
        input_rate, output_rate = MODEL_PRICE_TABLE["gpt-4o-mini"]
        # 1M in + 1M out should equal sum of rates in microdollars.
        self.assertEqual(estimate_cost("gpt-4o-mini", 1_000_000, 1_000_000), input_rate + output_rate)


if __name__ == "__main__":
    unittest.main()
