"""Tests for cost estimation from pricing tables."""

from __future__ import annotations

from metermaid.models import Snapshot
from metermaid.pricing import estimate_cost, fill_cost, stamp_cost


def _snap(
    provider: str = "codex",
    model: str = "gpt-5.4",
    tokens_in: int = 1_000_000,
    tokens_out: int = 100_000,
    cache_read: int = 0,
    cost_usd: float = 0.0,
) -> Snapshot:
    return Snapshot(
        timestamp="2025-01-01T00:00:00",
        provider=provider,
        model=model,
        session_id="test",
        cost_usd=cost_usd,
        ctx_pct=0.0,
        ctx_tokens=0,
        ctx_max=0,
        wall_sec=0.0,
        api_sec=0.0,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_read=cache_read,
        cache_write=0,
        diff_add=0,
        diff_del=0,
        path="",
        source="watcher",
        tok_in_delta=0,
        tok_out_delta=0,
        sc_tokens_in=0,
        sc_tokens_out=0,
        sc_cost_usd=0.0,
        sc_models="",
    )


def test_known_claude_model() -> None:
    cost = estimate_cost(
        "claude-opus-4-6", "claude", tokens_in=1_000_000, tokens_out=100_000
    )
    # 1M * $15/MTok + 100k * $75/MTok = $15 + $7.50 = $22.50
    assert abs(cost - 22.5) < 0.01


def test_known_codex_model() -> None:
    cost = estimate_cost("gpt-5.4", "codex", tokens_in=1_000_000, tokens_out=100_000)
    # 1M * $2.50/MTok + 100k * $10/MTok = $2.50 + $1.00 = $3.50
    assert abs(cost - 3.5) < 0.01


def test_unknown_model_uses_default() -> None:
    cost = estimate_cost("future-model-99", "claude", tokens_in=1_000_000, tokens_out=0)
    assert abs(cost - 3.0) < 0.01


def test_cache_read_discounted_for_claude() -> None:
    # 100k full price + 900k at 10%
    cost = estimate_cost(
        "claude-sonnet-4-6",
        "claude",
        tokens_in=1_000_000,
        tokens_out=0,
        cache_read=900_000,
    )
    # 100k * $3/MTok + 900k * $0.3/MTok = $0.30 + $0.27 = $0.57
    assert abs(cost - 0.57) < 0.01


def test_cache_read_50pct_discount_for_codex() -> None:
    # 100k full price + 900k at 50%
    cost = estimate_cost(
        "gpt-5.4", "codex", tokens_in=1_000_000, tokens_out=0, cache_read=900_000
    )
    # 100k * $2.50/MTok + 900k * $1.25/MTok = $0.25 + $1.125 = $1.375
    assert abs(cost - 1.375) < 0.01


def test_fill_cost_preserves_real_cost() -> None:
    row = {
        "cost_usd": "1.234",
        "model": "claude-opus-4-6",
        "provider": "claude",
        "tokens_in": "999999",
        "tokens_out": "999999",
        "cache_read": "0",
    }
    assert fill_cost(row) == 1.234


def test_fill_cost_estimates_when_zero() -> None:
    row = {
        "cost_usd": "0",
        "model": "gpt-5.4",
        "provider": "codex",
        "tokens_in": "1000000",
        "tokens_out": "100000",
        "cache_read": "0",
    }
    assert fill_cost(row) > 0


def test_stamp_cost_preserves_existing() -> None:
    snap = _snap(provider="claude", cost_usd=5.0)
    assert stamp_cost(snap).cost_usd == 5.0


def test_stamp_cost_estimates_when_zero() -> None:
    snap = _snap(provider="codex", model="gpt-5.4", cost_usd=0.0)
    stamped = stamp_cost(snap)
    assert stamped.cost_usd > 0
