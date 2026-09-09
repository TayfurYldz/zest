"""Phase 8 START-path census. Not a second scheduler and not a lab-only runtime."""

from __future__ import annotations

PHASE8_ACCEPTANCE_CENSUS_COMPLETE = True

PHASE8_ACCEPTANCE_CENSUS = (
    {
        "name": "Operator API / dashboard START",
        "module": "zest.interface.operator_api",
        "production": "POST run start → ZestdRuntime.start_run",
        "test": "ZestdRuntime.start_run after program/run seed",
        "coverage": "integration/test_operator_staging.py, Phase 8 harness",
        "bypass_risk": "calling ARC.start without reconstruct/preflight",
        "missing_link": "none for Phase 8; harness uses start_run",
    },
    {
        "name": "reconstruct_start_command",
        "module": "zest.application.reconstruct_run_command",
        "production": "SoR-only command; client cannot widen bounds",
        "test": "invoked inside ZestdRuntime.start_run",
        "coverage": "field regressions + Phase 8",
        "bypass_risk": "hand-built StartAutonomousResearchCommand",
        "missing_link": "none",
    },
    {
        "name": "Preflight",
        "module": "zest.application.preflight / zestd._run_preflight",
        "production": "schema/worker/model/lease before start",
        "test": "healthy probes matching production Preflight",
        "coverage": "test_zestd + Phase 8",
        "bypass_risk": "skipping PreflightStatus.READY_TO_START",
        "missing_link": "none",
    },
    {
        "name": "ARC.start",
        "module": "zest.application.autonomous_research_controller",
        "production": "durable orchestration insert",
        "test": "ZestdRuntime._unfenced_controller.start",
        "coverage": "field + Phase 8",
        "bypass_risk": "direct orchestration row insert",
        "missing_link": "none",
    },
    {
        "name": "LocalRunSupervisorRegistry",
        "module": "zest.application.local_run_supervisor",
        "production": "lease attach + cadence thread",
        "test": "start_run attaches registry; tests tick the same supervisor.tick",
        "coverage": "Phase 5 field + Phase 8",
        "bypass_risk": "unowned LocalRunSupervisor without lease",
        "missing_link": "Phase 8 uses leased registry supervisor",
    },
    {
        "name": "PostgreSQL harness",
        "module": "tests.integration.harness",
        "production": "PostgresUnitOfWork",
        "test": "ZEST_TEST_DATABASE_URL + alembic upgrade + truncate_spine",
        "coverage": "field regressions",
        "bypass_risk": "FakeUnitOfWork as lifecycle proof",
        "missing_link": "Phase 8 is PostgreSQL-only",
    },
    {
        "name": "Lab target / worker",
        "module": "tests/e2e/lab + worker_runtime executors",
        "production": "native Worker capabilities",
        "test": "loopback HTTP lab + IMPLEMENTATION_EXECUTORS",
        "coverage": "Phase 8 authz/protocol/OAST labs",
        "bypass_risk": "scripted WorkerResult success rows",
        "missing_link": "none; crash injection raises from invoke",
    },
    {
        "name": "Identities / sessions",
        "module": "research_identity_catalog + session_contexts",
        "production": "configured catalog; no secrets in SoR",
        "test": "persist_research_identities + ACTIVE session fixture",
        "coverage": "Phase 6.3 + Phase 8",
        "bypass_risk": "anonymous substitution",
        "missing_link": "catalog is operator configuration, not Evidence",
    },
    {
        "name": "Promotion / human review",
        "module": "promotion_pipeline / start_human_review",
        "production": "ARC._continue_promotion after assessment",
        "test": "driven by real assessment; review is operator use-case",
        "coverage": "Phase 6.8 + Phase 8",
        "bypass_risk": "direct Finding insert / auto-approve",
        "missing_link": "none",
    },
)
