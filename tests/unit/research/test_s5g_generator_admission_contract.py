from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.admission import (
    SIDE_EFFECT_ESTIMATE_PREFIX,
)
from zest.research.output_contracts import (
    GENERATOR_CONTRACT,
    GENERATOR_INSTRUCTION_VERSION,
)


class S5gGeneratorAdmissionContractTests(
    unittest.TestCase
):
    def test_generator_v4_exposes_required_side_effect_semantics(
        self,
    ) -> None:
        self.assertEqual(
            GENERATOR_INSTRUCTION_VERSION,
            "research.generator.v4",
        )

        instructions = (
            GENERATOR_CONTRACT.instructions()
        )

        self.assertIn(
            SIDE_EFFECT_ESTIMATE_PREFIX,
            instructions,
        )

        self.assertIn(
            "N is 0, 1, 2, or 3",
            instructions,
        )

        self.assertIn(
            "assumptions must be a non-null array",
            instructions,
        )

    def test_side_effect_estimate_does_not_grant_authority(
        self,
    ) -> None:
        instructions = (
            GENERATOR_CONTRACT
            .instructions()
            .lower()
        )

        self.assertIn(
            "advisory research metadata",
            instructions,
        )

        self.assertIn(
            "does not grant execution or "
            "authorization authority",
            instructions,
        )

        assumptions = next(
            field
            for field
            in GENERATOR_CONTRACT.fields
            if field.name == "assumptions"
        )

        self.assertIn(
            SIDE_EFFECT_ESTIMATE_PREFIX,
            assumptions.description,
        )


if __name__ == "__main__":
    unittest.main()
