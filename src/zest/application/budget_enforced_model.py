"""Budget-enforced ModelPort decorator.

Reserves MODEL_CALL on the append-only ledger BEFORE the external invocation.
Does not hold a PostgreSQL transaction open across the model call.

A reserved attempt that crashes before the network still consumes one allowance.
Replay of the same invocation identity does not double-charge.
"""

from __future__ import annotations

from datetime import date, timezone

from zest.application.budget_consumption import (
    BudgetConsumptionRejected,
    RecordBudgetConsumption,
    RecordBudgetConsumptionCommand,
)
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.program_daily_budget import (
    CheckProgramDailyBudget,
    program_daily_budget_id,
)
from zest.research.model_port import ModelCallRequest, ModelCallResult, ModelPort, ModelRole


def model_invocation_request_id(
    *,
    cycle_id: str,
    role: ModelRole,
    attempt_no: int,
    invocation_namespace: str | None = None,
) -> str:
    base = f"cycle:{cycle_id}:{role.value.lower()}:{attempt_no}"
    if invocation_namespace is None:
        return base
    return f"{base}:runtime:{invocation_namespace}"


class BudgetEnforcedModelPort:
    """Application decorator. Research ModelPort remains provider-neutral."""

    def __init__(
        self,
        inner: ModelPort,
        uow_factory: UnitOfWorkFactory,
        *,
        budget_id: str,
        research_run_id: str,
        cycle_id: str,
        program_id: str | None = None,
        invocation_namespace: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._inner = inner
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._consume = RecordBudgetConsumption(uow_factory, clock=self._clock)
        self._check_program_budget = CheckProgramDailyBudget(uow_factory, clock=self._clock)
        self._budget_id = budget_id
        self._research_run_id = research_run_id
        self._cycle_id = cycle_id
        self._program_id = program_id
        if invocation_namespace is not None and (
            not isinstance(invocation_namespace, str)
            or not invocation_namespace.strip()
        ):
            raise ValueError(
                "invocation_namespace must be a non-empty string when set"
            )
        self._invocation_namespace = (
            invocation_namespace.strip()
            if invocation_namespace is not None
            else None
        )
        self._attempts = {ModelRole.GENERATOR: 0, ModelRole.FALSIFIER: 0}
        self.reserved_invocations: list[str] = []

    def complete(self, request: ModelCallRequest) -> ModelCallResult:
        self._attempts[request.role] = self._attempts.get(request.role, 0) + 1
        invocation_id = model_invocation_request_id(
            cycle_id=self._cycle_id,
            role=request.role,
            attempt_no=self._attempts[request.role],
            invocation_namespace=self._invocation_namespace,
        )
        if self._program_id is not None:
            program_check = self._check_program_budget.execute(self._program_id)
            if not program_check.allowed_to_continue:
                raise BudgetConsumptionRejected(
                    f"program daily LLM budget denied: {program_check.reason_code.value}"
                )
        self._consume.execute(
            RecordBudgetConsumptionCommand(
                budget_id=self._budget_id,
                research_run_id=self._research_run_id,
                resource_type="MODEL_CALL",
                amount=1,
                unit="count",
                provenance="budget_enforced_model_port.complete",
                request_id=invocation_id,
            )
        )
        self.reserved_invocations.append(invocation_id)
        result = self._inner.complete(request)
        self._record_tokens(invocation_id, result)
        return result

    def _record_tokens(self, invocation_id: str, result: ModelCallResult) -> None:
        if self._program_id is None or result.model_id is None:
            return
        # Only record tokens when a program policy exists (legacy runs without a
        # configured policy keep the pre-SD-G4 behavior).
        with self._uow_factory.open() as uow:
            policy = uow.program_policies.get(self._program_id)
            has_policy = policy is not None
            uow.rollback()
        if not has_policy:
            return
        daily_budget_id = program_daily_budget_id(
            self._program_id, self._clock.now().date().isoformat()
        )
        metadata = {"model_id": result.model_id}
        if result.prompt_tokens is not None:
            self._consume.execute(
                RecordBudgetConsumptionCommand(
                    budget_id=daily_budget_id,
                    research_run_id=None,
                    resource_type="MODEL_TOKENS_IN",
                    amount=result.prompt_tokens,
                    unit="count",
                    provenance="budget_enforced_model_port.tokens",
                    request_id=invocation_id,
                    resource_metadata=metadata,
                )
            )
        if result.completion_tokens is not None:
            self._consume.execute(
                RecordBudgetConsumptionCommand(
                    budget_id=daily_budget_id,
                    research_run_id=None,
                    resource_type="MODEL_TOKENS_OUT",
                    amount=result.completion_tokens,
                    unit="count",
                    provenance="budget_enforced_model_port.tokens",
                    request_id=invocation_id,
                    resource_metadata=metadata,
                )
            )
