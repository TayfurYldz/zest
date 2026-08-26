"""Operator-facing readiness rendering. No secrets. Not research truth."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from zest.maturity import (
    ARCHITECTURE_VALIDATED,
    DIAGNOSTIC_E2E_VALIDATED,
    GATE_01_STATUS,
    GATE_04B_STATUS,
    GATE_10_STATUS,
    GATE_14_STATUS,
    GATE_15_STATUS,
    GATE_16_STATUS,
    GATE_17_STATUS,
    GATE_18_STATUS,
    GATE_19_STATUS,
    GATE_20_STATUS,
    GATE_21_STATUS,
    GATE_22_STATUS,
    LIVE_MODEL_VALIDATED,
    PRODUCTION_READY,
    SECURITY_RESEARCH_VALIDATED,
)


@dataclass(frozen=True)
class OperatorStatusSnapshot:
    postgresql: str
    worker: Mapping[str, str]
    model_runtimes: Mapping[str, str]
    strix: str
    auth: str
    orchestrator: str
    budget_ledger: str
    reconciliation: str
    observability: str
    gate_01: str = GATE_01_STATUS
    gate_04b: str = GATE_04B_STATUS
    gate_10: str = GATE_10_STATUS
    gate_14: str = GATE_14_STATUS
    gate_15: str = GATE_15_STATUS
    gate_16: str = GATE_16_STATUS
    gate_17: str = GATE_17_STATUS
    gate_18: str = GATE_18_STATUS
    gate_19: str = GATE_19_STATUS
    gate_20: str = GATE_20_STATUS
    gate_21: str = GATE_21_STATUS
    gate_22: str = GATE_22_STATUS
    test_postgresql: str = "not configured"
    application_dsn: str = "unset"
    test_dsn: str = "unset"

    def __post_init__(self) -> None:
        for name in (
            "postgresql",
            "test_postgresql",
            "application_dsn",
            "test_dsn",
            "strix",
            "auth",
            "orchestrator",
            "budget_ledger",
            "reconciliation",
            "observability",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        lowered = " ".join(
            [
                self.postgresql,
                self.test_postgresql,
                self.application_dsn,
                self.test_dsn,
                self.strix,
                self.auth,
                *self.worker.values(),
                *self.model_runtimes.values(),
            ]
        ).lower()
        for marker in ("sk-", "token=", "password", "api_key", "secret_value"):
            if marker in lowered:
                raise ValueError("operator status must not contain secret material")


def render_operator_status(snapshot: OperatorStatusSnapshot) -> str:
    worker_lines = "\n".join(
        f"  {name}: {health}" for name, health in snapshot.worker.items()
    )
    runtime_lines = "\n".join(
        f"  {name}: {health}" for name, health in snapshot.model_runtimes.items()
    )
    return "\n".join(
        [
            "ARCHITECTURE:",
            "  Decisions 001-050 accepted with GATE 01-13 diagnostic architecture",
            "POSTGRESQL:",
            f"  {snapshot.postgresql}",
            f"  dsn: {snapshot.application_dsn}",
            "TEST_POSTGRESQL:",
            f"  {snapshot.test_postgresql}",
            f"  dsn: {snapshot.test_dsn}",
            "WORKER:",
            worker_lines or "  none",
            "MODEL RUNTIMES:",
            runtime_lines or "  none",
            "STRIX:",
            f"  {snapshot.strix}",
            "AUTH:",
            f"  {snapshot.auth}",
            "ORCHESTRATOR:",
            f"  {snapshot.orchestrator}",
            "BUDGET LEDGER:",
            f"  {snapshot.budget_ledger}",
            "RECONCILIATION:",
            f"  {snapshot.reconciliation}",
            "OBSERVABILITY:",
            f"  {snapshot.observability}",
            "GATE 01:",
            f"  {snapshot.gate_01}",
            "GATE 04B:",
            f"  {snapshot.gate_04b}",
            "GATE 10:",
            f"  {snapshot.gate_10}",
            "GATE 14:",
            f"  {snapshot.gate_14}",
            "GATE 15:",
            f"  {snapshot.gate_15}",
            "GATE 16:",
            f"  {snapshot.gate_16}",
            "GATE 17:",
            f"  {snapshot.gate_17}",
            "GATE 18:",
            f"  {snapshot.gate_18}",
            "GATE 19:",
            f"  {snapshot.gate_19}",
            "GATE 20:",
            f"  {snapshot.gate_20}",
            "GATE 21:",
            f"  {snapshot.gate_21}",
            "GATE 22:",
            f"  {snapshot.gate_22}",
            "MATURITY:",
            f"  ARCHITECTURE_VALIDATED: {ARCHITECTURE_VALIDATED}",
            f"  DIAGNOSTIC_E2E_VALIDATED: {DIAGNOSTIC_E2E_VALIDATED}",
            f"  LIVE_MODEL_VALIDATED: {LIVE_MODEL_VALIDATED}",
            f"  SECURITY_RESEARCH_VALIDATED: {SECURITY_RESEARCH_VALIDATED}",
            f"  PRODUCTION_READY: {PRODUCTION_READY}",
        ]
    )
