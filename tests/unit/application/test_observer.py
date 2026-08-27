from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

import pathsetup  # noqa: F401

from zest.application.observer import (
    OBSERVER_SCHEMA,
    ObserverContext,
    ObserverProviderError,
    ObserverService,
    ObserverSettings,
    build_fallback_brief,
    build_observer_context,
    validate_observer_brief,
)
from zest.integrations.observer import NvidiaCompatibleObserverProvider


NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def _analysis() -> dict[str, object]:
    return {
        "research_run_id": "run-1",
        "truth": {
            "research_run_id": "run-1",
            "effective_operational_state": "RUNNING",
            "persisted_lifecycle_state": "RUNNING",
            "current_phase": "DISPATCHING",
            "runtime_liveness": "LIVE",
            "human_attention_required": False,
        },
        "semantic_activity_timeline": {
            "items": [{"activity_id": "activity-1", "summary": "not provider input"}],
        },
    }


class _Provider:
    provider_name = "fake"
    model_name = "fake-model"

    def __init__(self, result=None, error=None) -> None:
        self.calls: list[ObserverContext] = []
        self.result = result
        self.error = error

    def observe(self, context: ObserverContext):
        self.calls.append(context)
        if self.error:
            raise self.error
        return self.result or build_fallback_brief(context, provider="fake", model="fake-model")


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode()

    def read(self, _limit: int) -> bytes:
        return self.payload


class ObserverTests(unittest.TestCase):
    def test_context_and_provider_output_never_receive_secret_or_raw_payload(self) -> None:
        analysis = _analysis()
        analysis["secret"] = "do-not-send"
        analysis["raw_logs"] = "password=do-not-send"
        context = build_observer_context(analysis)
        self.assertNotIn("do-not-send", json.dumps(context.to_mapping()))
        provider = _Provider()
        result = ObserverService(provider, wall_clock=lambda: NOW).observe(analysis)
        self.assertNotIn("do-not-send", json.dumps(result))
        self.assertEqual(provider.calls[0].source_event_ids, ("activity-1",))

    def test_invalid_provider_output_falls_back_and_cannot_add_source_event(self) -> None:
        provider = _Provider(result={"schema": OBSERVER_SCHEMA, "status": "ACTIVE"})
        result = ObserverService(provider, wall_clock=lambda: NOW).observe(_analysis())
        self.assertEqual(result["mode"], "DETERMINISTIC")
        self.assertEqual(result["fallback_reason"], "INVALID_OUTPUT")
        self.assertEqual(result["brief"]["source_event_ids"], ["activity-1"])

    def test_timeout_and_unavailable_are_deterministic_fallbacks(self) -> None:
        for error, expected in ((TimeoutError(), "TIMEOUT"), (ObserverProviderError("down"), "UNAVAILABLE")):
            provider = _Provider(error=error)
            result = ObserverService(provider, wall_clock=lambda: NOW).observe(_analysis())
            self.assertEqual(result["fallback_reason"], expected)

    def test_delta_cooldown_and_local_budget_bound_provider_calls(self) -> None:
        ticks = iter((0.0, 1.0, 25.0, 26.0))
        provider = _Provider()
        settings = ObserverSettings(cooldown_seconds=20.0, budget_calls=1, budget_window_seconds=3600.0)
        service = ObserverService(provider, settings=settings, clock=lambda: next(ticks), wall_clock=lambda: NOW)
        first = service.observe(_analysis())
        cached = service.observe(_analysis())
        changed = _analysis()
        changed["truth"] = {**changed["truth"], "current_phase": "COMPLETE"}
        cooldown = service.observe(changed)
        budget = service.observe({**changed, "research_run_id": "run-2", "truth": {**changed["truth"], "research_run_id": "run-2"}})
        self.assertEqual(first["mode"], "PROVIDER")
        self.assertEqual(cached["mode"], "PROVIDER")
        self.assertEqual(cooldown["fallback_reason"], "COOLDOWN_OR_BUDGET")
        self.assertEqual(budget["fallback_reason"], "COOLDOWN_OR_BUDGET")
        self.assertEqual(len(provider.calls), 1)

    def test_brief_source_ids_must_be_context_bound(self) -> None:
        context = build_observer_context(_analysis())
        brief = build_fallback_brief(context, now=NOW)
        brief["source_event_ids"] = ["foreign-event"]
        with self.assertRaises(ValueError):
            validate_observer_brief(brief, context)

    def test_nvidia_compatible_adapter_is_provider_neutral_at_boundary(self) -> None:
        captured: dict[str, object] = {}

        def opener(request, timeout):
            captured["body"] = json.loads(request.data.decode())
            captured["authorization"] = request.headers["Authorization"]
            captured["timeout"] = timeout
            context = ObserverContext(
                "run-1", "RUNNING", "RUNNING", "DISPATCHING", "LIVE", False,
                ("Effective operational state is RUNNING.",), (), ("activity-1",),
            )
            response = build_fallback_brief(context, provider="nvidia-compatible", model="nvidia-model")
            return _Response({"choices": [{"message": {"content": json.dumps(response)}}]})

        provider = NvidiaCompatibleObserverProvider(
            api_key="secret-key",
            model="nvidia-model",
            base_url="http://observer.invalid/v1",
            opener=opener,
        )
        context = build_observer_context(_analysis())
        result = provider.observe(context)
        self.assertEqual(result["provider"], "nvidia-compatible")
        self.assertNotIn("secret-key", json.dumps(result))
        self.assertEqual(captured["authorization"], "Bearer secret-key")
        self.assertNotIn("raw_logs", json.dumps(captured["body"]))


if __name__ == "__main__":
    unittest.main()
