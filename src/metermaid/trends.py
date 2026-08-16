"""Trends — sparklines, cost windows, week-over-week comparison."""

from __future__ import annotations

from datetime import datetime, timedelta

from rich.console import Console
from rich.table import Table

console = Console()

_BARS = " ▁▂▃▄▅▆▇█"


def sparkline(values: list[float], width: int = 7) -> str:
    """Map values to Unicode block chars. Returns string of length min(width, len(values))."""
    if not values:
        return ""
    vals = values[-width:]
    lo, hi = min(vals), max(vals)
    spread = hi - lo if hi > lo else 1.0
    return "".join(
        _BARS[min(8, max(1, int((v - lo) / spread * 8)))] if v > 0 else _BARS[0]
        for v in vals
    )


def _int(row: dict[str, str], key: str) -> int:
    return int(row.get(key, 0) or 0)


def _float(row: dict[str, str], key: str) -> float:
    return float(row.get(key, 0) or 0)


def _latest_per_session_day(
    rows: list[dict[str, str]],
    date: str,
) -> list[dict[str, str]]:
    """Latest snapshot per session for a given date string (YYYY-MM-DD)."""
    best: dict[str, dict[str, str]] = {}
    for r in rows:
        if not r.get("timestamp", "").startswith(date):
            continue
        sid = r["session_id"]
        if sid not in best or r["timestamp"] > best[sid]["timestamp"]:
            best[sid] = r
    return list(best.values())


def daily_series(rows: list[dict[str, str]], field: str, days: int = 7) -> list[float]:
    """Daily totals for a field over the last N days."""
    today = datetime.now().date()
    result: list[float] = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        day_rows = _latest_per_session_day(rows, d)
        if field == "cache_hit_pct":
            cr = sum(_int(r, "cache_read") for r in day_rows)
            ti = sum(_int(r, "tokens_in") for r in day_rows)
            total = ti + cr
            result.append((cr / total * 100) if total > 0 else 0.0)
        else:
            result.append(sum(_float(r, field) for r in day_rows))
    return result


def trend_block(all_rows: list[dict[str, str]], days: int = 7) -> dict[str, str]:
    """Return {metric_label: sparkline_string} for key metrics."""
    return {
        "Tokens": sparkline(daily_series(all_rows, "tokens_out", days), days),
        "Cost": sparkline(daily_series(all_rows, "cost_usd", days), days),
        "Cache%": sparkline(daily_series(all_rows, "cache_hit_pct", days), days),
    }


def cost_windows(all_rows: list[dict[str, str]]) -> None:
    """Show 5h / 7d / 30d cost windows."""
    from .report import latest_per_session

    now = datetime.now()
    t = Table(title="Cost Windows", box=None, padding=(0, 2))
    t.add_column("Window", style="bold")
    t.add_column("Cost / Tokens", justify="right")
    t.add_column("Sessions", justify="right", style="dim")

    for label, delta in [
        ("5h", timedelta(hours=5)),
        ("7d", timedelta(days=7)),
        ("30d", timedelta(days=30)),
    ]:
        cutoff = now - delta
        wr = [r for r in all_rows if datetime.fromisoformat(r["timestamp"]) >= cutoff]
        wl = latest_per_session(wr)
        by_p: dict[str, float] = {}
        by_p_tok: dict[str, int] = {}
        for r in wl:
            p = r["provider"]
            c = _float(r, "cost_usd") + _float(r, "sc_cost_usd")
            by_p[p] = by_p.get(p, 0) + c
            tok = (
                _int(r, "tokens_in")
                + _int(r, "tokens_out")
                + _int(r, "sc_tokens_in")
                + _int(r, "sc_tokens_out")
            )
            by_p_tok[p] = by_p_tok.get(p, 0) + tok
        total_c = sum(by_p.values())
        total_t = sum(by_p_tok.values())
        parts = " + ".join(f"{p}=${v:.2f}" for p, v in sorted(by_p.items()) if v > 0)
        val = (
            f"[green]${total_c:.3f}[/green] ({parts})"
            if total_c > 0
            else f"{total_t:,} tokens"
        )
        t.add_row(label, val, str(len(wl)))
    console.print(t)


def week_over_week(all_rows: list[dict[str, str]]) -> None:
    """Show this week vs last week comparison."""
    from .report import latest_per_session

    now = datetime.now()
    this_cutoff = now - timedelta(days=7)
    last_cutoff = now - timedelta(days=14)

    this_rows = [
        r for r in all_rows if datetime.fromisoformat(r["timestamp"]) >= this_cutoff
    ]
    last_rows = [
        r
        for r in all_rows
        if last_cutoff <= datetime.fromisoformat(r["timestamp"]) < this_cutoff
    ]

    this_l = latest_per_session(this_rows)
    last_l = latest_per_session(last_rows)

    if not this_l and not last_l:
        return

    def _sum_field(rows: list[dict[str, str]], field: str) -> float:
        return sum(_float(r, field) for r in rows)

    def _cache_pct(rows: list[dict[str, str]]) -> float:
        cr = sum(_int(r, "cache_read") for r in rows)
        ti = sum(_int(r, "tokens_in") for r in rows)
        total = ti + cr
        return (cr / total * 100) if total > 0 else 0.0

    def _delta(cur: float, prev: float) -> str:
        if prev == 0:
            return "[dim]—[/dim]"
        pct = (cur - prev) / prev * 100
        arrow = "[green]\u2193[/green]" if pct < 0 else "[red]\u2191[/red]"
        return f"{arrow} {abs(pct):.0f}%"

    def _delta_good_up(cur: float, prev: float) -> str:
        if prev == 0:
            return "[dim]—[/dim]"
        pct = (cur - prev) / prev * 100
        arrow = "[green]\u2191[/green]" if pct > 0 else "[red]\u2193[/red]"
        return f"{arrow} {abs(pct):.0f}%"

    t = Table(title="Week over Week", box=None, padding=(0, 2))
    t.add_column("Metric", style="bold")
    t.add_column("This Week", justify="right")
    t.add_column("Last Week", justify="right", style="dim")
    t.add_column("Change", justify="right")

    tw_tok = _sum_field(this_l, "tokens_out")
    lw_tok = _sum_field(last_l, "tokens_out")
    t.add_row("Tokens out", f"{tw_tok:,.0f}", f"{lw_tok:,.0f}", _delta(tw_tok, lw_tok))

    tw_cost = _sum_field(this_l, "cost_usd") + _sum_field(this_l, "sc_cost_usd")
    lw_cost = _sum_field(last_l, "cost_usd") + _sum_field(last_l, "sc_cost_usd")
    t.add_row("Cost", f"${tw_cost:.2f}", f"${lw_cost:.2f}", _delta(tw_cost, lw_cost))

    tw_cache = _cache_pct(this_l)
    lw_cache = _cache_pct(last_l)
    cache_delta = _delta_good_up(tw_cache, lw_cache)
    t.add_row("Cache hit", f"{tw_cache:.0f}%", f"{lw_cache:.0f}%", cache_delta)

    t.add_row(
        "Sessions",
        str(len(this_l)),
        str(len(last_l)),
        _delta(float(len(this_l)), float(len(last_l))),
    )

    console.print(t)
