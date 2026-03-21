"""Cost estimation from pricing tables.

Three modes (matching ccusage):
  auto: use costUSD when present, estimate otherwise (default)
  calculate: always estimate from tokens
  display: only show provider-reported costs

Cache pricing:
  Claude: cache read at 10% of input rate (Anthropic pricing)
  Codex/OpenAI: cached input at 50% discount (OpenAI prompt caching)
"""

from __future__ import annotations

from dataclasses import replace

from .models import Snapshot

# (input $/MTok, output $/MTok)
CLAUDE_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-6": (15.0, 75.0),
    "claude-opus-4-5": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
}

CODEX_PRICING: dict[str, tuple[float, float]] = {
    "gpt-5.4": (2.50, 10.0),
    "gpt-5.3-codex-spark": (1.0, 4.0),
    "gpt-5": (2.50, 10.0),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "o3": (10.0, 40.0),
    "o4-mini": (1.10, 4.40),
}

_DEFAULTS: dict[str, tuple[float, float]] = {
    "claude": (3.0, 15.0),
    "codex": (2.50, 10.0),
}


def estimate_cost(model: str, provider: str, tokens_in: int, tokens_out: int,
                  cache_read: int = 0) -> float:
    """Estimate cost from token counts and model pricing.

    Claude: cache read at 10% of input rate.
    Codex/OpenAI: cached input at 50% discount.
    """
    table = CLAUDE_PRICING if provider == "claude" else CODEX_PRICING
    price_in, price_out = table.get(model, _DEFAULTS.get(provider, (3.0, 15.0)))

    non_cached_in = max(0, tokens_in - cache_read)
    if provider == "claude":
        cache_cost = (cache_read / 1e6) * (price_in * 0.1)
    else:
        cache_cost = (cache_read / 1e6) * (price_in * 0.5)
    input_cost = (non_cached_in / 1e6) * price_in + cache_cost
    output_cost = (tokens_out / 1e6) * price_out
    return round(input_cost + output_cost, 6)


def fill_cost(row: dict[str, str]) -> float:
    """For a CSV row: return cost_usd if nonzero, otherwise estimate (auto mode)."""
    cost = float(row.get("cost_usd", 0) or 0)
    if cost > 0:
        return cost
    return estimate_cost(
        model=row.get("model", ""),
        provider=row.get("provider", "claude"),
        tokens_in=int(row.get("tokens_in", 0) or 0),
        tokens_out=int(row.get("tokens_out", 0) or 0),
        cache_read=int(row.get("cache_read", 0) or 0),
    )


def stamp_cost(snap: Snapshot) -> Snapshot:
    """Stamp a Snapshot with estimated cost if cost_usd is zero (capture-time)."""
    if snap.cost_usd > 0:
        return snap
    est = estimate_cost(
        model=snap.model, provider=snap.provider,
        tokens_in=snap.tokens_in, tokens_out=snap.tokens_out,
        cache_read=snap.cache_read,
    )
    return replace(snap, cost_usd=est)
