"""Phase 8 START-path harness. Uses ZestdRuntime.start_run, not ARC.start bypass."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

from zest.application.identity import new_opaque_id
from zest.application.orchestration_lease import LeaseConfig
from zest.application.phase8_acceptance_trace import build_acceptance_trace
from zest.application.preflight import (
    ModelReadinessInput,
    SchemaHealthInput,
    WorkerReadinessInput,
)
from zest.application.research_identity_catalog import (
    ResearchIdentityCatalog,
    persist_research_identities,
)
from zest.application.zestd import ZestdRuntime
from zest.core.enums import ScopeRuleEffect
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    AuthorizationSourceRecord,
    DiscoveryFactRecord,
    DiscoveryFactSourceRecord,
    IssuedBudgetRecord,
    ProgramPolicyRecord,
    ProgramRecord,
    ResearchRunRecord,
    ScopeRuleV2Record,
    SessionContextRecord,
)
from zest.platform.health import ComponentHealth, HealthCheck
from zest.platform.local_process_worker import (
    LocalProcessWorkerAdapter,
    LocalProcessWorkerConfig,
    PACKAGED_WORKER_MODULE,
)
from zest.research.identity_session import HttpFormLoginProfile, Identity, local_dev_credential
from zest.research.model_runtime import api_runtime_identity
from zest.research.routing import CandidateLocality, RuntimeCandidate
from zest.worker_runtime.python.implementation import IMPLEMENTATION_EXECUTORS
from support.fake_model import ScriptedModelPort
from support.recording_worker import RecordingWorkerPort

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
CADENCE_SECONDS = 3600.0

PROFILE = HttpFormLoginProfile(
    profile_id="profile-form",
    path="/login",
    username_field="username",
    password_secret_name="login_password",
    session_cookie_name="sid",
)


class CrashOnInvokeWorker:
    """Raises after dispatch commit when invoke() is entered. Not a fake WorkerResult."""

    def __init__(self, inner, *, crash_on: int = 1) -> None:
        self._inner = inner
        self._crash_on = crash_on
        self.invocations = 0

    def invoke(self, request, *, timeout_ms=None):
        self.invocations += 1
        if self.invocations >= self._crash_on:
            raise RuntimeError("phase8 injected crash after dispatch before durable result")
        return self._inner.invoke(request, timeout_ms=timeout_ms)


def _healthy_worker() -> WorkerReadinessInput:
    return WorkerReadinessInput(
        health=HealthCheck("worker", ComponentHealth.HEALTHY, "ok"),
        available_capabilities=frozenset(IMPLEMENTATION_EXECUTORS) | frozenset({"browser.page"}),
        browser_containment=HealthCheck("browser-containment", ComponentHealth.HEALTHY, "ready"),
    )


def _healthy_model() -> ModelReadinessInput:
    return ModelReadinessInput(
        candidate=RuntimeCandidate(
            identity=api_runtime_identity(adapter_id="fake", runtime_id="fake"),
            available=True,
            authenticated=True,
            structured_output_compatible=True,
            locality=CandidateLocality.LOCAL,
        ),
        health=HealthCheck("model", ComponentHealth.HEALTHY, "ok"),
    )


def production_worker(inner=None):
    adapter = inner or LocalProcessWorkerAdapter(
        LocalProcessWorkerConfig(module=PACKAGED_WORKER_MODULE, default_timeout_ms=15_000)
    )
    return RecordingWorkerPort(inner=adapter)


def identities_for(origin: str) -> ResearchIdentityCatalog:
    return ResearchIdentityCatalog(
        identities=(
            Identity(
                identity_id="id-alice",
                actor_reference="alice",
                target_reference=origin,
                credential_reference=local_dev_credential("ALICE_PASSWORD"),
                authentication_profile_reference=PROFILE.profile_id,
            ),
            Identity(
                identity_id="id-bob",
                actor_reference="bob",
                target_reference=origin,
                credential_reference=local_dev_credential("BOB_PASSWORD"),
                authentication_profile_reference=PROFILE.profile_id,
            ),
        ),
        profiles=(PROFILE,),
    )


def seed_program(
    uow: PostgresUnitOfWork,
    *,
    origin: str,
    program_id: str,
    run_id: str,
    side_effect_ceiling: int,
    max_selected: int,
    max_cycles: int,
    identities: bool,
    sessions: bool,
    research_question: str = "Phase 8 controlled lifecycle",
) -> None:
    parsed = urlsplit(origin if origin.endswith("/") else origin + "/")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    now = NOW
    uow.programs.insert(ProgramRecord(program_id=program_id, created_at=now, name="phase8"))
    uow.authorization_sources.insert(
        AuthorizationSourceRecord(
            authorization_source_id=f"as-{program_id}",
            program_id=program_id,
            state="ACTIVE",
            provenance_reference="written-phase8-lab-auth",
            created_at=now,
        )
    )
    uow.research_runs.insert(
        ResearchRunRecord(
            research_run_id=run_id,
            program_id=program_id,
            authorization_source_id=f"as-{program_id}",
            initiated_by_actor_id="operator-phase8",
            initiated_by_actor_type="HUMAN_OPERATOR",
            started_at=now,
        )
    )
    uow.issued_budgets.insert(
        IssuedBudgetRecord(
            budget_id=f"budget-{run_id}",
            research_run_id=run_id,
            max_requests=80,
            max_tool_calls=80,
            max_runtime_ms=120_000,
            max_concurrency=1,
            issued_at=now,
        )
    )
    uow.program_policies.insert(
        ProgramPolicyRecord(
            program_id=program_id,
            loopback_fixture=True,
            max_response_bytes=65536,
            timeout_ms=8000,
            created_at=now,
            updated_at=now,
            daily_llm_budget_microdollars=10_000_000,
            action_policy={
                "run": {
                    "target_reference": origin if origin.endswith("/") else origin + "/",
                    "research_question": research_question,
                },
                "orchestration": {
                    "max_cycles": max_cycles,
                    "max_experiments": max_cycles,
                    "max_model_calls": max(40, max_cycles),
                    "max_worker_invocations": max_cycles,
                    "max_elapsed_ms": 120_000,
                    "max_selected_opportunities": max_selected,
                    "max_runtime_fallback": 0,
                    "side_effect_ceiling": side_effect_ceiling,
                    "allow_repeated_control_experiments": False,
                },
            },
        )
    )
    uow.scope_rules_v2.insert(
        ScopeRuleV2Record(
            rule_id=f"rule-allow-{program_id}",
            program_id=program_id,
            effect=ScopeRuleEffect.ALLOW.value,
            scheme=parsed.scheme or "http",
            host=host,
            port=port,
            source_reference="scope-src-phase8",
            created_at=now,
        )
    )
    if identities:
        persist_research_identities(
            uow,
            research_run_id=run_id,
            catalog=identities_for(origin.rstrip("/")),
            now=now,
            actor_id="operator-phase8",
        )
    if sessions:
        for session_id, identity_id, actor in (
            ("session-alice", "id-alice", "alice"),
            ("session-bob", "id-bob", "bob"),
        ):
            uow.session_contexts.insert(
                SessionContextRecord(
                    session_context_id=session_id + "-" + run_id[:8],
                    research_run_id=run_id,
                    identity_id=identity_id,
                    actor_reference=actor,
                    origin=origin.rstrip("/"),
                    authentication_profile_reference=PROFILE.profile_id,
                    authentication_method="HTTP_FORM_LOGIN",
                    secret_scheme="SESSION_MATERIAL",
                    secret_name=f"session:{session_id}",
                    state="ACTIVE",
                    created_at=now,
                    updated_at=now,
                    established_at=now,
                    session_cookie_name=PROFILE.session_cookie_name,
                )
            )


def attach_facts(
    uow: PostgresUnitOfWork,
    *,
    run_id: str,
    origin: str,
    observation_id: str | None,
    worker_result_id: str | None,
    kinds: tuple[str, ...],
) -> None:
    origin = origin.rstrip("/")
    now = datetime.now(timezone.utc)
    specs = []
    if "authz" in kinds:
        specs.append(
            (
                "HTTP_OPERATION",
                f"GET {origin}/vulnerable/accounts/alice",
                "/vulnerable/accounts/alice",
                {
                    "scope_classification": "IN_SCOPE",
                    "authorized_origin": origin,
                    "actor": "alice",
                    "own_object": "alice",
                    "cross_object": "bob",
                    "mode": "vulnerable",
                },
            )
        )
        specs.append(
            (
                "HTTP_OPERATION",
                f"GET {origin}/secure/accounts/bob",
                "/secure/accounts/bob",
                {
                    "scope_classification": "IN_SCOPE",
                    "authorized_origin": origin,
                    "actor": "alice",
                    "own_object": "alice",
                    "cross_object": "bob",
                    "mode": "secure_only",
                },
            )
        )
    if "authz_negative" in kinds:
        specs.append(
            (
                "HTTP_OPERATION",
                f"GET {origin}/secure/accounts/alice",
                "/secure/accounts/alice",
                {
                    "scope_classification": "IN_SCOPE",
                    "authorized_origin": origin,
                    "actor": "alice",
                    "own_object": "alice",
                    "cross_object": "bob",
                    "mode": "secure_only",
                },
            )
        )
    if "protocol" in kinds:
        specs.append(
            (
                "HTTP_OPERATION",
                f"GET {origin}/proxy",
                "/proxy",
                {
                    "scope_classification": "IN_SCOPE",
                    "protocol_surface_signals": ["reverse_proxy", "cdn"],
                },
            )
        )
    if "oast" in kinds:
        specs.append(
            (
                "HTTP_OPERATION",
                f"GET {origin}/oast-sink",
                "/oast-sink",
                {
                    "scope_classification": "IN_SCOPE",
                    "query_params": ["url"],
                    "oast_listener_origin": origin,
                    "authorized_origin": origin,
                    "path": "/oast-sink",
                    "method": "GET",
                },
            )
        )
    if "missing_identity" in kinds:
        specs.append(
            (
                "HTTP_OPERATION",
                f"GET {origin}/vulnerable/accounts/carol",
                "/vulnerable/accounts/carol",
                {
                    "scope_classification": "IN_SCOPE",
                    "authorized_origin": origin,
                    "actor": "carol",
                    "own_object": "carol",
                    "cross_object": "dave",
                    "mode": "vulnerable",
                },
            )
        )
    for kind, canonical, path, attrs in specs:
        fact_id = new_opaque_id()
        uow.discovery_facts.insert(
            DiscoveryFactRecord(
                fact_id=fact_id,
                research_run_id=run_id,
                fact_kind=kind,
                canonical_key=canonical,
                epistemic_status="OBSERVED",
                identity_id="id-alice" if "carol" not in path else "ANONYMOUS",
                target_reference=origin + "/",
                created_at=now,
                normalized_origin=origin,
                normalized_path=path,
                http_method="GET",
                attributes=attrs,
            )
        )
        uow.discovery_fact_sources.insert(
            DiscoveryFactSourceRecord(
                source_row_id=new_opaque_id(),
                research_run_id=run_id,
                fact_id=fact_id,
                created_at=now,
                observation_id=observation_id,
                worker_result_id=worker_result_id,
            )
        )


def build_runtime(factory, worker, model, *, process_id: str | None = None) -> ZestdRuntime:
    return ZestdRuntime(
        factory,
        worker,
        model,
        lease_config=LeaseConfig(heartbeat_interval_seconds=30, lease_ttl_seconds=120),
        cadence_seconds=CADENCE_SECONDS,
        probe_schema=lambda: SchemaHealthInput(True, "ok"),
        probe_worker=_healthy_worker,
        probe_model=_healthy_model,
        host_identity="zestd-phase8",
        process_id=process_id or str(os.getpid()) + "-" + uuid.uuid4().hex[:8],
        environment_name="phase8-lab",
    )


def wait_first_tick(runtime: ZestdRuntime, run_id: str, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        supervisor = runtime._registry.supervisor(run_id) if runtime._registry else None
        if supervisor is not None and supervisor.last_result is not None:
            return
        time.sleep(0.05)
    raise AssertionError("supervisor did not complete the first START tick")


def tick_until(
    runtime: ZestdRuntime,
    factory,
    run_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    max_ticks: int = 40,
) -> dict[str, Any]:
    supervisor = runtime._registry.supervisor(run_id)
    if supervisor is None:
        raise AssertionError("leased supervisor missing")
    last: dict[str, Any] = {}
    for _ in range(max_ticks):
        with factory.open() as uow:
            last = build_acceptance_trace(uow, run_id)
            uow.rollback()
        if predicate(last):
            return last
        supervisor.tick()
    return last


def snapshot_counts(factory, run_id: str) -> dict[str, int]:
    with factory.open() as uow:
        attempts = uow.execution_attempts.list_for_research_run(run_id)
        proposals = uow.finding_proposals.list_for_research_run(run_id)
        payload = {
            "attempts": len(attempts),
            "worker_results": len(uow.worker_results.list_for_research_run(run_id))
            if hasattr(uow.worker_results, "list_for_research_run")
            else 0,
            "observations": len(uow.observations.list_for_research_run(run_id)),
            "evidence": len(uow.evidence.list_for_research_run(run_id)),
            "candidates": len(uow.candidates.list_for_research_run(run_id)),
            "verifications": len(uow.verifications.list_for_research_run(run_id)),
            "proposals": len(proposals),
        }
        uow.rollback()
    return payload


def oast_delivery(correlation_id: str, event_id: str, *, received_at=None) -> Any:
    from zest.research.oast.types import OastCallbackDelivery

    payload = {"callback_channel": "http"}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return OastCallbackDelivery(
        delivery_id=new_opaque_id(),
        correlation_id=correlation_id,
        provider_adapter_id="loopback",
        provider_event_id=event_id,
        received_at=received_at or datetime.now(timezone.utc),
        normalized_payload=payload,
        normalized_digest=digest,
    )


def future_clock(delta: timedelta):
    class _Clock:
        def now(self):
            return datetime.now(timezone.utc) + delta

    return _Clock()
