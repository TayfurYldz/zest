from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.core import CoreInputError, evaluate_execution
from fixtures import base_request


class InputErrorTests(unittest.TestCase):
    def test_invalid_side_effect_level_is_input_error_not_policy_deny(self) -> None:
        with self.assertRaises(CoreInputError):
            evaluate_execution(base_request(side_effect_level=9))

    def test_empty_subject_is_input_error_not_policy_deny(self) -> None:
        with self.assertRaises(CoreInputError):
            evaluate_execution(base_request(requested_subject=""))


if __name__ == "__main__":
    unittest.main()
