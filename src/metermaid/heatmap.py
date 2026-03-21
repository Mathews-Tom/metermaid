"""Contributions heatmap — GitHub-style terminal calendar."""

from __future__ import annotations

from datetime import datetime, timedelta

from rich.console import Console
from rich.text import Text

console = Console()


def _int(row: dict[str, str], key: str) -> int:
    return int(row.get(key, 0) or 0)


def _float(row: dict[str, str], key: str) -> float:
    return float(row.get(key, 0) or 0)


def daily_activity(
    rows: list[dict[str, str]], days: int = 365, metric: str = "cost",
) -> dict[str, float]:
    """Map date string -> activity score."""
    best: dict[tuple[str, str], dict[str, str]] = {}
    for r in rows:
        ts = r.get("timestamp", "")
        if not ts:
            continue
        date = ts[:10]
        sid = r["session_id"]
        key = (date, sid)
        if key not in best or ts > best[key]["timestamp"]:
            best[key] = r

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    activity: dict[str, float] = {}
    for (date, _sid), r in best.items():
        if date < cutoff:
            continue
        if metric == "cost":
            val = _float(r, "cost_usd") + _float(r, "sc_cost_usd")
        elif metric == "tokens":
            val = float(_int(r, "tokens_in") + _int(r, "tokens_out"))
        else:
            val = 1.0  # sessions: count
        activity[date] = activity.get(date, 0) + val
    return activity


_BLOCKS = " ░▒▓█"
_COLORS = ["dim", "green", "green", "bold green", "bold green"]


def _intensity(value: float, max_val: float) -> int:
    """Map value to 0-4 intensity level."""
    if value <= 0 or max_val <= 0:
        return 0
    ratio = value / max_val
    if ratio < 0.25:
        return 1
    if ratio < 0.50:
        return 2
    if ratio < 0.75:
        return 3
    return 4


def render_heatmap(
    activity: dict[str, float], metric: str = "cost", days: int = 365,
) -> None:
    """Print GitHub-style contributions calendar."""
    today = datetime.now().date()
    max_val = max(activity.values()) if activity else 1.0
    total = sum(activity.values())
    active_days = sum(1 for v in activity.values() if v > 0)

    # Build weeks (columns) x weekdays (rows)
    # Start from the earliest Monday within the range
    start = today - timedelta(days=days)
    # Align to Monday
    start = start - timedelta(days=start.weekday())
    weeks: list[list[tuple[str, float]]] = []
    current = start
    week: list[tuple[str, float]] = []
    while current <= today:
        date_str = current.isoformat()
        week.append((date_str, activity.get(date_str, 0)))
        if len(week) == 7:
            weeks.append(week)
            week = []
        current += timedelta(days=1)
    if week:
        weeks.append(week)

    # Month labels
    month_labels = Text()
    month_labels.append("     ")  # weekday label space
    prev_month = ""
    for w in weeks:
        first_date = w[0][0]
        month = first_date[5:7]
        if month != prev_month:
            month_labels.append(datetime.strptime(first_date, "%Y-%m-%d").strftime("%b")[:3])
            prev_month = month
        else:
            month_labels.append(" ")
    console.print(month_labels)

    # Weekday rows
    day_names = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    for dow in range(7):
        row = Text()
        row.append(f" {day_names[dow]}  ", style="dim")
        for w in weeks:
            if dow < len(w):
                _date, val = w[dow]
                level = _intensity(val, max_val)
                row.append(_BLOCKS[level], style=_COLORS[level])
            else:
                row.append(" ")
        console.print(row)

    # Summary
    unit = {"cost": "$", "tokens": "", "sessions": ""}.get(metric, "")
    if metric == "cost":
        total_str = f"${total:.2f}"
    elif metric == "tokens":
        total_str = f"{total:,.0f}"
    else:
        total_str = f"{total:.0f}"
    console.print(f"\n  {active_days} active days | {total_str} total {metric}")

    # Legend
    legend = Text("  Less ")
    for i in range(5):
        legend.append(_BLOCKS[i], style=_COLORS[i])
    legend.append(" More")
    console.print(legend)
