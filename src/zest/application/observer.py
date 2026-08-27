"""Read-only, provider-neutral narration for the Zest operator surface.

The Observer consumes only the bounded HQ analysis projection. It cannot write
state, authorize work, dispatch a Worker, or turn a narrative into evidence.
Provider output is untrusted and must pass the bounded brief validator before it
is exposed to the dashboard.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol


OBSERVER_SCHEMA = "zest.observer.brief.v1"
OBSERVER_STATUSES = frozenset({"ACTIVE", "WAITING", "FAULT", "IDLE", "UNKNOWN"})
OBSERVER_ATTENTION = frozenset({"NONE", "REVIEW", "REQUIRED"})
MAX_HEADLINE = 180
MAX_NARRATIVE = 500
MAX_FACTS = 8
MAX_UNKNOWNS = 8
MAX_FACT_LENGTH = 240
MAX_SOURCE_IDS = 50
MAX_ID_LENGTH = 160
MAX_CONTEXT_BYTES = 16_000
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_COOLDOWN_SECONDS = 20.0
DEFAULT_MAX_BACKOFF_SECONDS = 300.0
DEFAULT_BUDGET_CALLS = 12
DEFAULT_BUDGET_WINDOW_SECONDS = 3600.0


class ObserverProviderError(RuntimeError):
    """Provider failure; never a research or verification outcome."""


class ObserverTimeoutError(ObserverProviderError):
    pass


class ObserverRateLimitError(ObserverProviderError):
    pass


class ObserverProviderUnavailableError(ObserverProviderError):
    pass


class ObserverProvider(Protocol):
    provider_name: str
    model_name: str

    def observe(self, context: "ObserverContext") -> Mapping[str, Any]: ...


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split())
    return value[:limit]


def _id(value: object) -> str | None:
    value = _text(value, MAX_ID_LENGTH)
    return value or None


def _now_iso(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.isoformat()


@dataclass(frozen=True)
class ObserverContext:
    """Bounded authoritative facts available to a provider."""

    research_run_id: str
    effective_state: str
    persisted_state: str
    current_phase: str
    runtime_liveness: str
    human_attention_required: bool
    confirmed_facts: tuple[str, ...]
    unknowns: tuple[str, ...]
    source_event_ids: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "research_run_id": self.research_run_id,
            "effective_state": self.effective_state,
            "persisted_state": self.persisted_state,
            "current_phase": self.current_phase,
            "runtime_liveness": self.runtime_liveness,
            "human_attention_required": self.human_attention_required,
            "confirmed_facts": list(self.confirmed_facts),
            "unknowns": list(self.unknowns),
            "source_event_ids": list(self.source_event_ids),
        }


def build_observer_context(analysis: Mapping[str, Any]) -> ObserverContext:
    """Select a small, non-sensitive view from the Stage2 read model."""

    truth = analysis.get("truth") if isinstance(analysis.get("truth"), Mapping) else {}
    run_id = _id(truth.get("research_run_id") or analysis.get("research_run_id")) or "UNKNOWN"
    effective = _text(truth.get("effective_operational_state"), 80) or "UNKNOWN"
    persisted = _text(truth.get("persisted_lifecycle_state"), 80) or "UNKNOWN"
    phase = _text(truth.get("current_phase"), 120) or "UNKNOWN"
    liveness = _text(truth.get("runtime_liveness"), 80) or "UNKNOWN"
    attention = bool(truth.get("human_attention_required"))
    facts = [
        f"Persisted lifecycle state is {persisted}.",
        f"Effective operational state is {effective}.",
        f"Runtime liveness is {liveness}.",
        f"Current phase is {phase}.",
    ]
    unknowns: list[str] = []
    for label, value in (("runtime liveness", liveness), ("current phase", phase)):
        if value == "UNKNOWN":
            unknowns.append(f"{label.capitalize()} is UNKNOWN in the authoritative projection.")
    timeline = analysis.get("semantic_activity_timeline")
    items = timeline.get("items", []) if isinstance(timeline, Mapping) else []
    source_ids: list[str] = []
    if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
        for item in items[:MAX_SOURCE_IDS]:
            if not isinstance(item, Mapping):
                continue
            event_id = _id(item.get("activity_id"))
            if event_id and event_id not in source_ids:
                source_ids.append(event_id)
    if not source_ids:
        unknowns.append("No persisted semantic activity is available as an event reference.")
    return ObserverContext(
        research_run_id=run_id,
        effective_state=effective,
        persisted_state=persisted,
        current_phase=phase,
        runtime_liveness=liveness,
        human_attention_required=attention,
        confirmed_facts=tuple(_text(item, MAX_FACT_LENGTH) for item in facts[:MAX_FACTS]),
        unknowns=tuple(_text(item, MAX_FACT_LENGTH) for item in unknowns[:MAX_UNKNOWNS]),
        source_event_ids=tuple(source_ids),
    )


def _observer_status(context: ObserverContext) -> str:
    if context.effective_state in {"RUNTIME_FAULT", "FAILED_OPERATIONAL", "FAILED"}:
        return "FAULT"
    if context.human_attention_required or context.effective_state in {
        "WAITING_HUMAN", "BLOCKED", "RECONCILIATION_REQUIRED"
    }:
        return "WAITING"
    if context.effective_state in {"RUNNING", "READY", "ACTIVE", "EXECUTING"}:
        return "ACTIVE"
    if context.effective_state in {"IDLE", "CREATED", "COMPLETED", "CANCELLED"}:
        return "IDLE"
    return "UNKNOWN"


def build_fallback_brief(
    context: ObserverContext,
    *,
    provider: str = "none",
    model: str | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create deterministic narration from persisted facts only."""

    status = _observer_status(context)
    if status == "FAULT":
        headline = "Operational fault requires review"
        what = "The authoritative projection contains a failed or runtime-fault state."
        why = "The Observer cannot determine recovery or retry authority."
    elif status == "WAITING":
        headline = "Run is waiting for an authoritative decision"
        what = "The run is blocked or marked for human attention in persisted state."
        why = "Only the system and an authorized operator can decide the next action."
    elif status == "ACTIVE":
        headline = "Run is active"
        what = "The authoritative projection reports an active lifecycle or execution state."
        why = "Persisted activity is available as operational context, not as a conclusion."
    elif status == "IDLE":
        headline = "No active execution is reported"
        what = "The authoritative projection reports no active execution state."
        why = "No further direction is inferred when persisted state is idle or terminal."
    else:
        headline = "Operational state is unknown"
        what = "The bounded authoritative projection does not establish an operational state."
        why = "Unknown state must remain visible until authoritative data is available."
    return {
        "schema": OBSERVER_SCHEMA,
        "status": status,
        "headline": headline,
        "what": what,
        "why": why,
        "confirmed_facts": list(context.confirmed_facts),
        "unknowns": list(context.unknowns),
        "operator_attention": "REQUIRED" if context.human_attention_required else "NONE",
        "source_event_ids": list(context.source_event_ids),
        "generated_at": _now_iso(now),
        "provider": provider,
        "model": model,
    }


def validate_observer_brief(
    payload: Mapping[str, Any],
    context: ObserverContext,
) -> dict[str, Any]:
    """Strictly validate untrusted provider output against the context."""

    required = {
        "schema", "status", "headline", "what", "why", "confirmed_facts",
        "unknowns", "operator_attention", "source_event_ids", "generated_at",
        "provider", "model",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise ValueError("ObserverBrief must contain exactly the public schema fields")
    if payload["schema"] != OBSERVER_SCHEMA or payload["status"] not in OBSERVER_STATUSES:
        raise ValueError("invalid ObserverBrief schema or status")
    if payload["status"] != _observer_status(context):
        raise ValueError("ObserverBrief status is not supported by authoritative state")
    if payload["operator_attention"] not in OBSERVER_ATTENTION:
        raise ValueError("invalid operator attention state")
    expected_attention = "REQUIRED" if context.human_attention_required else "NONE"
    if payload["operator_attention"] != expected_attention:
        raise ValueError("ObserverBrief attention is not supported by authoritative state")
    for name, limit in (("headline", MAX_HEADLINE), ("what", MAX_NARRATIVE), ("why", MAX_NARRATIVE), ("generated_at", 80), ("provider", 120)):
        if not isinstance(payload[name], str) or len(payload[name]) > limit:
            raise ValueError(f"invalid bounded ObserverBrief field: {name}")
    if payload["model"] is not None and (not isinstance(payload["model"], str) or len(payload["model"]) > 120):
        raise ValueError("invalid ObserverBrief model")
    known_ids = set(context.source_event_ids)
    for name in ("confirmed_facts", "unknowns"):
        values = payload[name]
        if not isinstance(values, list) or len(values) > MAX_FACTS or any(
            not isinstance(value, str) or not value.strip() or len(value) > MAX_FACT_LENGTH
            for value in values
        ):
            raise ValueError(f"invalid ObserverBrief {name}")
        allowed = set(context.confirmed_facts if name == "confirmed_facts" else context.unknowns)
        if any(value not in allowed for value in values):
            raise ValueError(f"ObserverBrief {name} contains unsupported facts")
    source_ids = payload["source_event_ids"]
    if not isinstance(source_ids, list) or len(source_ids) > MAX_SOURCE_IDS or any(
        not isinstance(value, str) or len(value) > MAX_ID_LENGTH or value not in known_ids
        for value in source_ids
    ):
        raise ValueError("ObserverBrief source_event_ids must reference context")
    return dict(payload)


@dataclass(frozen=True)
class ObserverSettings:
    enabled: bool = False
    provider: str = "none"
    model: str = ""
    nvidia_api_key: str = ""
    base_url: str = "https://integrate.api.nvidia.com/v1"
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
    max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS
    budget_calls: int = DEFAULT_BUDGET_CALLS
    budget_window_seconds: float = DEFAULT_BUDGET_WINDOW_SECONDS

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ObserverSettings":
        source = os.environ if env is None else env
        enabled = source.get("ZEST_OBSERVER_ENABLED", "0").strip().lower() in {"1", "true", "yes"}
        return cls(
            enabled=enabled,
            provider=source.get("ZEST_OBSERVER_PROVIDER", "none").strip().lower() or "none",
            model=_text(source.get("ZEST_OBSERVER_MODEL", ""), 120),
            nvidia_api_key=source.get("ZEST_NVIDIA_API_KEY", ""),
            base_url=source.get("ZEST_OBSERVER_BASE_URL", cls.base_url).strip() or cls.base_url,
            timeout_seconds=max(0.1, float(source.get("ZEST_OBSERVER_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))),
            cooldown_seconds=max(0.0, float(source.get("ZEST_OBSERVER_COOLDOWN_SECONDS", DEFAULT_COOLDOWN_SECONDS))),
            max_backoff_seconds=max(0.0, float(source.get("ZEST_OBSERVER_MAX_BACKOFF_SECONDS", DEFAULT_MAX_BACKOFF_SECONDS))),
            budget_calls=max(1, int(source.get("ZEST_OBSERVER_BUDGET_CALLS", DEFAULT_BUDGET_CALLS))),
            budget_window_seconds=max(1.0, float(source.get("ZEST_OBSERVER_BUDGET_WINDOW_SECONDS", DEFAULT_BUDGET_WINDOW_SECONDS))),
        )


class ObserverService:
    """In-memory bounded observer cache; no persistence and no execution authority."""

    def __init__(
        self,
        provider: ObserverProvider | None = None,
        *,
        settings: ObserverSettings | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._settings = settings or ObserverSettings()
        self._provider = provider
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = threading.Lock()
        self._last_digest: str | None = None
        self._last_brief: dict[str, Any] | None = None
        self._last_call_at = float("-inf")
        self._next_allowed_at = float("-inf")
        self._failure_count = 0
        self._call_times: list[float] = []
        self._last_fallback_reason: str | None = None

    def _digest(self, context: ObserverContext) -> str:
        encoded = json.dumps(context.to_mapping(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def observe(self, analysis: Mapping[str, Any]) -> dict[str, Any]:
        context = build_observer_context(analysis)
        digest = self._digest(context)
        now = self._clock()
        with self._lock:
            if self._last_digest == digest and self._last_brief is not None:
                return {"mode": "PROVIDER" if self._provider else "DETERMINISTIC", "enabled": bool(self._provider), "provider": self._last_brief.get("provider", "none"), "model": self._last_brief.get("model"), "fallback_reason": self._last_fallback_reason, "brief": dict(self._last_brief)}
            if self._provider is None:
                return self._fallback(context, digest, "DISABLED")
            self._prune_budget(now)
            if now < self._next_allowed_at or now - self._last_call_at < self._settings.cooldown_seconds or len(self._call_times) >= self._settings.budget_calls:
                return self._fallback(context, digest, "COOLDOWN_OR_BUDGET", cache=False)
            self._call_times.append(now)
            self._last_call_at = now
            try:
                candidate = dict(self._provider.observe(context))
                candidate.setdefault("schema", OBSERVER_SCHEMA)
                candidate.setdefault("generated_at", _now_iso(self._wall_clock()))
                candidate.setdefault("provider", self._provider.provider_name)
                candidate.setdefault("model", self._provider.model_name)
                brief = validate_observer_brief(candidate, context)
            except ObserverRateLimitError:
                return self._failed(context, digest, "RATE_LIMITED", now)
            except (ObserverTimeoutError, TimeoutError):
                return self._failed(context, digest, "TIMEOUT", now)
            except ObserverProviderError:
                return self._failed(context, digest, "UNAVAILABLE", now)
            except (ValueError, TypeError, KeyError):
                return self._failed(context, digest, "INVALID_OUTPUT", now)
            if brief["provider"] != self._provider.provider_name or brief["model"] != self._provider.model_name:
                return self._failed(context, digest, "INVALID_OUTPUT", now)
            self._failure_count = 0
            self._next_allowed_at = now + self._settings.cooldown_seconds
            self._last_digest = digest
            self._last_brief = brief
            self._last_fallback_reason = None
            return {"mode": "PROVIDER", "enabled": True, "provider": brief["provider"], "model": brief["model"], "fallback_reason": None, "brief": brief}

    def _prune_budget(self, now: float) -> None:
        self._call_times = [item for item in self._call_times if now - item < self._settings.budget_window_seconds]

    def _fallback(self, context: ObserverContext, digest: str, reason: str, *, cache: bool = True) -> dict[str, Any]:
        brief = build_fallback_brief(context, reason=reason, now=self._wall_clock())
        if cache:
            self._last_digest = digest
            self._last_brief = brief
            self._last_fallback_reason = reason
        return {"mode": "DETERMINISTIC", "enabled": False, "provider": "none", "model": None, "fallback_reason": reason, "brief": brief}

    def _failed(self, context: ObserverContext, digest: str, reason: str, now: float) -> dict[str, Any]:
        self._failure_count += 1
        delay = min(self._settings.max_backoff_seconds, max(self._settings.cooldown_seconds, 2 ** self._failure_count))
        self._next_allowed_at = now + delay
        return self._fallback(context, digest, reason, cache=False)


def build_observer_service(env: Mapping[str, str] | None = None) -> ObserverService:
    settings = ObserverSettings.from_env(env)
    return ObserverService(settings=settings)
