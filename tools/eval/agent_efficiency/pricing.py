"""API price calculator.

Rates are the OpenAI standard text tier published at
https://developers.openai.com/api/docs/pricing and the gpt-4.1-mini model page,
read on 13 October 2026. Batch and other tiers are not applied.

Cost is computed only from provider-reported input and output token counts.
Cached tokens are priced at the cached rate only when the provider reports
them as a subset of input tokens. A missing cached count is not stored as
zero and does not receive the cache discount. Reasoning tokens are recorded
by the caller and are not added on top of output tokens, because this price
table bills output tokens and does not list a separate reasoning rate.
"""

from __future__ import annotations

from typing import Any

from agent_efficiency.telemetry import Usage

PRICING_AS_OF = "2026-10-13"
PRICING_SOURCE = "https://developers.openai.com/api/docs/pricing"

# USD per 1,000,000 tokens. Standard tier only.
PRICE_TABLE: dict[str, dict[str, Any]] = {
    "openai:gpt-4.1": {
        "input_per_million_usd": 2.00,
        "cached_input_per_million_usd": 0.50,
        "output_per_million_usd": 8.00,
    },
    "openai:gpt-4.1-mini": {
        "input_per_million_usd": 0.40,
        "cached_input_per_million_usd": 0.10,
        "output_per_million_usd": 1.60,
    },
    "openai:gpt-4.1-nano": {
        "input_per_million_usd": 0.10,
        "cached_input_per_million_usd": 0.025,
        "output_per_million_usd": 0.40,
    },
    "openai:gpt-4o-mini": {
        "input_per_million_usd": 0.15,
        "cached_input_per_million_usd": 0.075,
        "output_per_million_usd": 0.60,
    },
}


def price_usage(usage: Usage | None, model: str) -> dict[str, Any]:
    """Return a cost record. ``usd`` is None when the usage cannot be priced."""
    base = {
        "model": model,
        "source": PRICING_SOURCE,
        "as_of": PRICING_AS_OF,
        "tier": "standard",
        "cached_discount_applied": False,
        "usd": None,
    }
    if usage is None or usage.counting_method != "provider":
        base["reason"] = "usage_not_provider_reported"
        return base
    if usage.input_tokens is None or usage.output_tokens is None:
        base["reason"] = "input_or_output_tokens_missing"
        return base
    rates = PRICE_TABLE.get(model)
    if rates is None:
        base["reason"] = "no_price_for_model"
        return base
    cached = usage.cached_input_tokens
    if cached is None:
        uncached = usage.input_tokens
        cached_billed = 0
        discount = False
    else:
        if cached < 0 or cached > usage.input_tokens:
            base["reason"] = "cached_tokens_inconsistent_with_input"
            return base
        uncached = usage.input_tokens - cached
        cached_billed = cached
        discount = cached > 0
    usd = (
        uncached * rates["input_per_million_usd"]
        + cached_billed * rates["cached_input_per_million_usd"]
        + usage.output_tokens * rates["output_per_million_usd"]
    ) / 1_000_000
    base["usd"] = usd
    base["cached_discount_applied"] = discount
    base["reason"] = None
    return base
