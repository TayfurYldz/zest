"""Invalid Core evaluator input. Distinct from a policy ExecutionDecision of DENY."""


class CoreInputError(Exception):
    """Programmer/schema bug: fail closed without pretending this is a policy DENY."""


class InvalidBudgetError(CoreInputError):
    """Negative or otherwise unusable budget/usage values."""


class BudgetAllocationError(CoreInputError):
    """Experiment allocation exceeds the parent ResearchRun envelope."""
