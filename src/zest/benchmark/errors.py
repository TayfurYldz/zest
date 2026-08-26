"""Benchmark harness errors. Not Core DENY and not a Finding."""


class BenchmarkError(ValueError):
    """Invalid scenario, leakage, or harness input."""
