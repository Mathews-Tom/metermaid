"""Nudges — heuristic-based actionable tips after reports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from rich.console import Console

console = Console()


def _int(row: dict[str, str], key: str) -> int:
    return int(row.get(key, 0) or 0)


def _float(row: dict[str, str], key: str) -> float:
    return float(row.get(key, 0) or 0)


@dataclass
class Nudge:
    severity: str  # info | warn | alert
    message: str


def _latest_per_session(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    best: dict[str, dict[str, str]] = {}
    for r in rows:
        sid = r["session_id"]
        if sid not in best or r["timestamp"] > best[sid]["timestamp"]:
            best[sid] = r
    return list(best.values())


def _split_weeks(
    all_rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Split into this-week and last-week latest-per-session rows."""
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
    return _latest_per_session(this_rows), _latest_per_session(last_rows)


def _cache_pct(rows: list[dict[str, str]]) -> float:
    cr = sum(_int(r, "cache_read") for r in rows)
    ti = sum(_int(r, "tokens_in") for r in rows)
    total = ti + cr
    return (cr / total * 100) if total > 0 else 0.0


def _cache_drop(this: list[dict[str, str]], last: list[dict[str, str]]) -> Nudge | None:
    if not last:
        return None
    tw = _cache_pct(this)
    lw = _cache_pct(last)
    drop = lw - tw
    if drop > 10:
        return Nudge(
            "warn", f"Cache hit rate dropped {drop:.0f}pp ({lw:.0f}% -> {tw:.0f}%)"
        )
    return None


def _cost_spike(this: list[dict[str, str]], last: list[dict[str, str]]) -> Nudge | None:
    if not last:
        return None
    tw_cost = sum(_float(r, "cost_usd") + _float(r, "sc_cost_usd") for r in this)
    lw_cost = sum(_float(r, "cost_usd") + _float(r, "sc_cost_usd") for r in last)
    if lw_cost > 0 and tw_cost > lw_cost * 1.2:
        pct = (tw_cost - lw_cost) / lw_cost * 100
        return Nudge(
            "warn",
            f"Cost up {pct:.0f}% week-over-week (${lw_cost:.2f} -> ${tw_cost:.2f})",
        )
    return None


def _context_pressure(latest: list[dict[str, str]]) -> Nudge | None:
    high = [r for r in latest if _float(r, "ctx_pct") > 85]
    if high:
        return Nudge("info", f"{len(high)} session(s) above 85% context utilization")
    return None


def analyze(rows: list[dict[str, str]], all_rows: list[dict[str, str]]) -> list[Nudge]:
    """Run heuristic checks, return triggered nudges."""
    this_week, last_week = _split_weeks(all_rows)
    nudges: list[Nudge] = []
    checks: list[Callable[[], Nudge | None]] = [
        lambda: _cache_drop(this_week, last_week),
        lambda: _cost_spike(this_week, last_week),
        lambda: _context_pressure(_latest_per_session(rows)),
    ]
    for check in checks:
        n = check()
        if n:
            nudges.append(n)
    return nudges


def render_nudges(nudges: list[Nudge]) -> None:
    """Print nudges with severity-colored prefix."""
    if not nudges:
        return
    for n in nudges:
        icon = {"alert": "[red]!![/red]", "warn": "[yellow]![/yellow]"}.get(
            n.severity, "[dim]i[/dim]"
        )
        console.print(f"  {icon} {n.message}")
