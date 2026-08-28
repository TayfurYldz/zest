"""Durable control obligations that must not be mistaken for completion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


TERMINAL_EXPERIMENT_STATES = frozenset(
    {
        "EXECUTION_SUCCEEDED",
        "EXECUTION_FAILED",
        "BLOCKED",
        "CANCELLED",
        "BUDGET_EXHAUSTED",
    }
)
OPEN_ATTEMPT_STATES = frozenset({"AUTHORIZED", "DISPATCHING"})


@dataclass(frozen=True)
class ControlObligation:
    code: str
    subject_id: str | None
    detail: str


def unresolved_control_obligations(
    *,
    attempts: Sequence[Any],
    experiments: Sequence[Any],
    worker_results: Sequence[Any],
    active_experiment_ids: frozenset[str] | None = None,
) -> tuple[ControlObligation, ...]:
    """Return durable control work that prevents natural run completion."""

    obligations: list[ControlObligation] = []
    for attempt in attempts:
        state = getattr(attempt, "state", None)
        if state == "UNKNOWN_OUTCOME":
            obligations.append(
                ControlObligation(
                    "UNKNOWN_OUTCOME",
                    getattr(attempt, "attempt_id", None),
                    "external execution outcome is unresolved",
                )
            )
        elif state in OPEN_ATTEMPT_STATES:
            obligations.append(
                ControlObligation(
                    "PENDING_RECONCILIATION",
                    getattr(attempt, "attempt_id", None),
                    f"execution attempt remains {state}",
                )
            )

    experiment_by_id = {
        getattr(experiment, "experiment_id", None): experiment
        for experiment in experiments
    }
    for result in worker_results:
        if getattr(result, "status", None) != "REAUTHORIZATION_REQUIRED":
            continue
        experiment_id = getattr(result, "experiment_id", None)
        experiment = experiment_by_id.get(experiment_id)
        if (
            experiment is None
            or getattr(experiment, "execution_state", None)
            not in TERMINAL_EXPERIMENT_STATES
        ):
            obligations.append(
                ControlObligation(
                    "REAUTHORIZATION_REQUIRED",
                    getattr(result, "worker_result_id", None),
                    "Core scope re-evaluation is required before any follow-up dispatch",
                )
            )

    for experiment in experiments:
        experiment_id = getattr(experiment, "experiment_id", None)
        if active_experiment_ids is not None and experiment_id not in active_experiment_ids:
            continue
        state = getattr(experiment, "execution_state", None)
        if state not in TERMINAL_EXPERIMENT_STATES:
            obligations.append(
                ControlObligation(
                    "OPEN_EXPERIMENT_CONTROL",
                    experiment_id,
                    f"experiment remains {state}",
                )
            )
    return tuple(obligations)
