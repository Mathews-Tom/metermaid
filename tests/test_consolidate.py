"""Tests for consolidation — time-windowed aggregation and derived metrics."""

from __future__ import annotations

from metermaid.consolidate import aggregate, window_key


def _row(
    timestamp: str = "2026-03-16T10:00:00",
    provider: str = "claude",
    model: str = "claude-opus-4-6",
    session_id: str = "sess1",
    tokens_in: int = 1000,
    tokens_out: int = 500,
    **overrides: str,
) -> dict[str, str]:
    defaults = {
        "timestamp": timestamp,
        "provider": provider,
        "model": model,
        "session_id": session_id,
        "tokens_in": str(tokens_in),
        "tokens_out": str(tokens_out),
        "tok_in_delta": "100",
        "tok_out_delta": "50",
        "cost_usd": "0.1",
        "wall_sec": "60.0",
        "api_sec": "10.0",
        "cache_read": "0",
        "cache_write": "0",
        "diff_add": "5",
        "diff_del": "2",
    }
    defaults.update(overrides)
    return defaults


def test_window_key_day() -> None:
    assert window_key("2026-03-16T10:00:00", "day") == "2026-03-16"


def test_window_key_week() -> None:
    key = window_key("2026-03-16T10:00:00", "week")
    assert key.startswith("2026-W")


def test_window_key_month() -> None:
    assert window_key("2026-03-16T10:00:00", "month") == "2026-03"


def test_aggregate_single_session() -> None:
    rows = [_row(tokens_in=500), _row(tokens_in=1000)]
    agg = aggregate(rows, "day")
    assert len(agg) == 1
    # Cumulative: uses max (1000), not sum
    assert int(agg[0]["tok_in"]) == 1000
    # Deltas: summed (100 + 100 = 200)
    assert int(agg[0]["tok_in_delta"]) == 200


def test_aggregate_multiple_sessions() -> None:
    rows = [
        _row(session_id="s1", tokens_in=1000),
        _row(session_id="s2", tokens_in=2000),
    ]
    agg = aggregate(rows, "day")
    assert len(agg) == 1
    # Sum of per-session max: 1000 + 2000 = 3000
    assert int(agg[0]["tok_in"]) == 3000
    assert int(agg[0]["sessions"]) == 2


def test_derived_metrics() -> None:
    rows = [_row(tokens_in=1000, tokens_out=500, cost_usd="1.0", wall_sec="3600")]
    agg = aggregate(rows, "day")
    assert float(agg[0]["cost_per_hour"]) == 1.0  # $1 / 1h
    assert float(agg[0]["tok_efficiency"]) == 0.5  # 500/1000
    assert float(agg[0]["cost_per_ktok_out"]) == 2.0  # $1/500 * 1000


def test_aggregate_empty() -> None:
    assert aggregate([], "day") == []


def test_multiple_providers_separate_rows() -> None:
    rows = [
        _row(provider="claude", session_id="c1"),
        _row(provider="codex", session_id="x1", model="gpt-5.4"),
    ]
    agg = aggregate(rows, "day")
    providers = {r["provider"] for r in agg}
    assert providers == {"claude", "codex"}
