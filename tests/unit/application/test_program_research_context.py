"""ProgramResearchContext + ProgramPolicyView tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

import pathsetup  # noqa: F401

from zest.application.program_research_context import (
    ProgramPolicyView,
    load_program_research_context,
)
from zest.data.records import ProgramPolicyRecord, RateLimitProfileRecord
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run


class ProgramResearchContextTests(unittest.TestCase):
    def test_policy_view_includes_rate_limit_profile(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        store.program_policies["prog-1"] = ProgramPolicyRecord(
            program_id="prog-1",
            loopback_fixture=False,
            max_response_bytes=4096,
            timeout_ms=2000,
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
            action_policy={},
        )
        store.rate_limit_profiles["rl-1"] = RateLimitProfileRecord(
            profile_id="rl-1",
            program_id="prog-1",
            max_requests_per_window=10,
            window_seconds=60,
            created_at=CREATED_AT,
        )
        factory = FakeUnitOfWorkFactory(store)

        with factory.open() as uow:
            context = load_program_research_context(uow, "prog-1")

        assert context is not None
        self.assertIsInstance(context.policy.rate_limit_profile, RateLimitProfileRecord)
        assert context.policy.rate_limit_profile is not None
        self.assertEqual(context.policy.rate_limit_profile.profile_id, "rl-1")
        self.assertEqual(context.policy.rate_limit_profile.max_requests_per_window, 10)

    def test_policy_view_without_rate_limit_profile(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        store.program_policies["prog-1"] = ProgramPolicyRecord(
            program_id="prog-1",
            loopback_fixture=False,
            max_response_bytes=4096,
            timeout_ms=2000,
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
            action_policy={},
        )
        factory = FakeUnitOfWorkFactory(store)

        with factory.open() as uow:
            context = load_program_research_context(uow, "prog-1")

        assert context is not None
        self.assertIsNone(context.policy.rate_limit_profile)

    def test_empty_policy_defaults_to_allow(self) -> None:
        policy = ProgramPolicyView(
            loopback_fixture=False,
            max_response_bytes=4096,
            timeout_ms=2000,
            action_policy={},
        )

        self.assertTrue(policy.allows_action("read"))
        self.assertFalse(policy.allows_action(""))
        self.assertFalse(policy.allows_action("   "))

    def test_dashboard_forbidden_actions_denies_exact_action(self) -> None:
        policy = ProgramPolicyView(
            loopback_fixture=False,
            max_response_bytes=4096,
            timeout_ms=2000,
            action_policy={
                "forbidden_actions": [" READ ", "login"],
                "dashboard_bootstrap": True,
            },
        )

        self.assertFalse(policy.allows_action("read"))
        self.assertFalse(policy.allows_action("LOGIN"))
        self.assertTrue(policy.allows_action("echo"))

    def test_forbidden_actions_does_not_interpret_natural_language(self) -> None:
        policy = ProgramPolicyView(
            loopback_fixture=False,
            max_response_bytes=4096,
            timeout_ms=2000,
            action_policy={
                "forbidden_actions": [
                    "do not perform read operations",
                ],
            },
        )

        self.assertTrue(policy.allows_action("read"))

    def test_direct_action_deny_remains_supported(self) -> None:
        policy = ProgramPolicyView(
            loopback_fixture=False,
            max_response_bytes=4096,
            timeout_ms=2000,
            action_policy={
                " READ ": {"decision": "DENY"},
            },
        )

        self.assertFalse(policy.allows_action("read"))

    def test_malformed_forbidden_actions_fails_closed(self) -> None:
        policy = ProgramPolicyView(
            loopback_fixture=False,
            max_response_bytes=4096,
            timeout_ms=2000,
            action_policy={
                "forbidden_actions": {
                    "read": True,
                },
            },
        )

        self.assertFalse(policy.allows_action("echo"))

    def test_default_policy_has_no_rate_limit_profile(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)

        with factory.open() as uow:
            context = load_program_research_context(uow, "prog-1")

        assert context is not None
        self.assertIsNone(context.policy.rate_limit_profile)


if __name__ == "__main__":
    unittest.main()
