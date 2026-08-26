"""Deterministic model pricing. Core-only; no network or vendor SDK imports."""

from __future__ import annotations

from zest.core.errors import CoreInputError


class UnknownModelPriceError(CoreInputError):
    """No price table entry for the requested model_id. Fail-closed."""


# Microdollars per 1M tokens. 1 USD = 1_000_000 microdollars.
# Placeholder values; operational config overrides belong outside Core.
MODEL_PRICE_TABLE: dict[str, tuple[int, int]] = {
    "gpt-4o-mini": (150000, 600000),
    "gpt-4o": (2500000, 10000000),
    "claude-3-5-sonnet": (3000000, 15000000),
    "local-fixture": (0, 0),
}


def estimate_cost(model_id: str | None, tokens_in: int | None, tokens_out: int | None) -> int:
    """Return estimated cost in microdollars for a model invocation.

    None model_id or unknown model_id raises UnknownModelPriceError.
    None token counts are treated as 0.
    """
    if model_id is None:
        raise UnknownModelPriceError("model_id is required to estimate cost")
    if model_id not in MODEL_PRICE_TABLE:
        raise UnknownModelPriceError(f"no price table entry for model_id={model_id}")
    input_rate, output_rate = MODEL_PRICE_TABLE[model_id]
    in_tokens = tokens_in if tokens_in is not None else 0
    out_tokens = tokens_out if tokens_out is not None else 0
    if in_tokens < 0 or out_tokens < 0:
        raise UnknownModelPriceError("token counts must be non-negative")
    # Cost = tokens * rate / 1_000_000.  Integer arithmetic in microdollars.
    return (in_tokens * input_rate + out_tokens * output_rate) // 1_000_000
