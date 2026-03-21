"""Tests for reporting — filtering, latest-per-session dedup."""

from __future__ import annotations

import io
from datetime import datetime, timedelta

from rich.console import Console

from metermaid.report import filter_rows, latest_per_session, report


def _make_row(
    session_id: str = "sess1",
    provider: str = "claude",
    timestamp: str | None = None,
    tokens_in: int = 1000,
    tokens_out: int = 500,
    cost_usd: float = 0.1,
    **kwargs: str,
) -> dict[str, str]:
    ts = timestamp or datetime.now().isoformat(timespec="seconds")
    defaults = {
        "timestamp": ts, "provider": provider, "model": "test-model",
        "session_id": session_id, "cost_usd": str(cost_usd),
        "ctx_pct": "50.0", "ctx_tokens": "100000", "ctx_max": "200000",
        "wall_sec": "60.0", "api_sec": "30.0",
        "tokens_in": str(tokens_in), "tokens_out": str(tokens_out),
        "cache_read": "0", "cache_write": "0",
        "diff_add": "0", "diff_del": "0", "path": "/test", "source": "watcher",
        "tok_in_delta": "0", "tok_out_delta": "0",
        "sc_tokens_in": "0", "sc_tokens_out": "0", "sc_cost_usd": "0", "sc_models": "",
    }
    defaults.update(kwargs)
    return defaults


def _capture_report(rows: list[dict[str, str]], all_rows: list[dict[str, str]]) -> str:
    """Capture rich console output from report() as plain text."""
    import metermaid.report as mod
    buf = io.StringIO()
    old_console = mod.console
    mod.console = Console(file=buf, force_terminal=False, width=120)
    try:
        report(rows, all_rows)
    finally:
        mod.console = old_console
    return buf.getvalue()


def test_filter_by_provider() -> None:
    rows = [_make_row(provider="claude"), _make_row(provider="codex")]
    assert len(filter_rows(rows, provider="claude")) == 1
    assert len(filter_rows(rows, provider="codex")) == 1


def test_filter_by_session() -> None:
    rows = [_make_row(session_id="aaa"), _make_row(session_id="bbb")]
    result = filter_rows(rows, session="aaa")
    assert len(result) == 1
    assert result[0]["session_id"] == "aaa"


def test_filter_by_window() -> None:
    old_ts = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
    new_ts = datetime.now().isoformat(timespec="seconds")
    rows = [_make_row(timestamp=old_ts), _make_row(timestamp=new_ts)]
    result = filter_rows(rows, window="7d")
    assert len(result) == 1
    assert result[0]["timestamp"] == new_ts


def test_latest_per_session_dedup() -> None:
    old_ts = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    new_ts = datetime.now().isoformat(timespec="seconds")
    rows = [
        _make_row(session_id="s1", timestamp=old_ts, tokens_in=100),
        _make_row(session_id="s1", timestamp=new_ts, tokens_in=500),
        _make_row(session_id="s2", timestamp=new_ts, tokens_in=200),
    ]
    latest = latest_per_session(rows)
    assert len(latest) == 2
    s1 = [r for r in latest if r["session_id"] == "s1"][0]
    assert s1["tokens_in"] == "500"


def test_report_no_data() -> None:
    output = _capture_report([], [])
    assert "No data" in output


def test_report_with_data() -> None:
    rows = [_make_row(session_id="s1", cost_usd=0.5, source="watcher")]
    output = _capture_report(rows, rows)
    assert "Sessions" in output
    assert "$0.500" in output


def test_combined_filters() -> None:
    old_ts = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
    rows = [
        _make_row(session_id="s1", provider="claude", timestamp=old_ts),
        _make_row(session_id="s2", provider="claude"),
        _make_row(session_id="s3", provider="codex"),
    ]
    result = filter_rows(rows, window="7d", provider="claude")
    assert len(result) == 1
    assert result[0]["session_id"] == "s2"
