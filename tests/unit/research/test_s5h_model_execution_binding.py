from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.context import (
    ContextBudget,
    ObservationSource,
    ResearchContextBuilder,
)
from zest.research.cycle import (
    _generator_instructions,
    _model_execution_catalog,
)
from zest.research.planning import (
    plan_admitted_hypothesis,
)
from zest.research.proposals import (
    parse_hypothesis_challenge,
    parse_hypothesis_proposal,
)
from zest.research.types import ResearchInputError


def _context():
    return ResearchContextBuilder().build(
        research_run_id="run-test",
        research_question="test authorized local behavior",
        observations=(
            ObservationSource(
                observation_id="obs-http",
                observation_kind="HTTP_TRANSACTION",
                payload={
                    "authorized_origin": "http://127.0.0.1:12345",
                    "method": "GET",
                    "path": "/api/orders/202",
                    "status_code": 200,
                },
            ),
        ),
        budget=ContextBudget(),
    )


def _proposal(capability: str, refs=("obs-http",)):
    return parse_hypothesis_proposal(
        {
            "proposed_claim": (
                "The cited read-only endpoint behavior is reproducible "
                "under the same authorized target context."
            ),
            "rationale": "cited HTTP observation",
            "source_references": list(refs),
            "assumptions": [
                "side_effect_estimate:0",
            ],
            "unresolved_questions": [],
            "suggested_disconfirming_test": (
                "repeat the cited read-only request and observe "
                "whether the response behavior differs"
            ),
            "suggested_capability": capability,
            "expected_security_relevance": None,
            "novelty_basis": None,
        }
    )


def _challenge():
    return parse_hypothesis_challenge(
        {
            "alternative_explanations": [
                "the prior observation was transient"
            ],
            "missing_preconditions": [],
            "contradictory_source_references": [],
            "required_negative_controls": [],
            "reasons_not_to_test": [],
            "proposed_disconfirming_observation": (
                "the repeated read no longer produces the observed behavior"
            ),
            "ambiguity": None,
        }
    )


class S5hModelExecutionBindingTests(unittest.TestCase):
    def test_catalog_exposes_exact_read_only_http_transaction(self):
        catalog = _model_execution_catalog(_context())

        self.assertEqual(
            [item["capability_id"] for item in catalog],
            ["http.transaction"],
        )

        self.assertEqual(
            catalog[0]["action"],
            "read",
        )

        self.assertTrue(
            catalog[0]["not_authorization"]
        )

    def test_generator_instructions_forbid_capability_aliases(self):
        instructions = _generator_instructions(
            _context()
        )

        self.assertIn(
            "suggested_capability must exactly equal",
            instructions,
        )

        self.assertIn(
            "Do not invent aliases",
            instructions,
        )

        self.assertIn(
            "http.transaction",
            instructions,
        )

        self.assertIn(
            "not authorization",
            instructions.lower(),
        )

    def test_http_transaction_is_bound_from_cited_observation(self):
        plan = plan_admitted_hypothesis(
            "hyp-test",
            _proposal("http.transaction"),
            _challenge(),
            budget_id="budget-test",
            target_reference="http://127.0.0.1:12345/",
            context=_context(),
        )

        self.assertEqual(
            plan.required_capability,
            "http.transaction",
        )

        self.assertEqual(
            plan.action,
            "read",
        )

        self.assertEqual(
            plan.side_effect_level,
            0,
        )

        self.assertEqual(
            plan.arguments["authorized_origin"],
            "http://127.0.0.1:12345",
        )

        self.assertEqual(
            plan.arguments["method"],
            "GET",
        )

        self.assertEqual(
            plan.arguments["path"],
            "/api/orders/202",
        )

    def test_uncited_observation_cannot_be_used_for_binding(self):
        with self.assertRaisesRegex(
            ResearchInputError,
            "requires a cited read-only HTTP transaction observation",
        ):
            plan_admitted_hypothesis(
                "hyp-test",
                _proposal(
                    "http.transaction",
                    refs=("proc:research-question",),
                ),
                _challenge(),
                budget_id="budget-test",
                target_reference="http://127.0.0.1:12345/",
                context=_context(),
            )

    def test_unknown_model_alias_remains_fail_closed(self):
        with self.assertRaisesRegex(
            ResearchInputError,
            "unknown or unsupported capability",
        ):
            plan_admitted_hypothesis(
                "hyp-test",
                _proposal(
                    "authorization.object_level_read_differential"
                ),
                _challenge(),
                budget_id="budget-test",
                target_reference="http://127.0.0.1:12345/",
                context=_context(),
            )


if __name__ == "__main__":
    unittest.main()
