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
