from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.compiler_registry import (
    COMPILER_GENERIC_PLANNER,
    CompilerOutcome,
    FAMILY_OBJECT_AUTHORIZATION,
)
from zest.research.exploratory import (
    ExploratorySignal,
    ExploratorySignalKind,
    draft_registry_external_hypothesis,
    exploratory_draft_from_audit,
)
from zest.research.exploratory_compile import compile_exploratory_hypothesis
from zest.research.types import ResearchInputError
from zest.tools.capabilities import DIAGNOSTIC_ECHO_CAPABILITY


def _signal(**overrides) -> ExploratorySignal:
    values = dict(
        signal_id="sig-1",
        research_run_id="run-1",
        kind=ExploratorySignalKind.LAB_ZERO_DAY_STYLE_ANOMALY,
        description="A lab-only zero-day-style behavior changed the response shape.",
        source_refs=("change-1",),
        target_node_kind="ACTION",
        attributes={"lab_fixture": "zero_day_style"},
    )
    values.update(overrides)
    return ExploratorySignal(**values)


def _draft(*, name: str = "Unmapped Response Shape Coupling"):
    return draft_registry_external_hypothesis(
        draft_id="draft-1",
        research_run_id="run-1",
        proposed_family_name=name,
        proposed_family_rationale="Registry-external lab anomaly needs a run-scoped test.",
        signals=(_signal(),),
        registry=(),
    )


class ExploratoryCompileTests(unittest.TestCase):
    def test_generic_planner_compiles_diagnostic_echo_without_family_binding(self) -> None:
        result = compile_exploratory_hypothesis(
            _draft(),
            hypothesis_id="hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
        )
        self.assertTrue(result.compiled)
        self.assertEqual(result.compiler_id, COMPILER_GENERIC_PLANNER)
        assert result.plan is not None
        self.assertEqual(result.plan.required_capability, DIAGNOSTIC_ECHO_CAPABILITY)
        self.assertEqual(result.plan.side_effect_level, 0)
        self.assertIsNone(result.family_name)

    def test_known_family_name_does_not_capture_exploratory_compile(self) -> None:
        result = compile_exploratory_hypothesis(
            _draft(name=FAMILY_OBJECT_AUTHORIZATION),
            hypothesis_id="hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
        )
        self.assertTrue(result.compiled)
        self.assertEqual(result.compiler_id, COMPILER_GENERIC_PLANNER)
        assert result.plan is not None
        self.assertEqual(result.plan.required_capability, DIAGNOSTIC_ECHO_CAPABILITY)
        self.assertEqual(result.plan.side_effect_level, 0)

    def test_tampered_registry_write_flag_fails_closed(self) -> None:
        draft = _draft()
        payload = draft.to_audit_payload(hypothesis_id="hyp-1")
        payload["may_write_hunter_registry"] = True
        with self.assertRaisesRegex(
            ResearchInputError, "cannot authorize hunter registry write"
        ):
            exploratory_draft_from_audit(
                draft_id=draft.draft_id,
                research_run_id="run-1",
                payload=payload,
            )


if __name__ == "__main__":
    unittest.main()
