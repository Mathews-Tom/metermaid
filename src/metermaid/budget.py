"""Budget tracking — config loading, gauge, forecasting, alerts."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table

from .models import METERMAID_HOME

console = Console()
CONFIG_FILE: Path = METERMAID_HOME / "config.toml"


@dataclass
class BudgetConfig:
    monthly_usd: float = 0.0
    alert_thresholds: list[int] = field(default_factory=lambda: [50, 75, 90, 100])
    provider_limits: dict[str, float] = field(default_factory=dict)


def load_config(path: Path | None = None) -> BudgetConfig | None:
    """Load ~/.metermaid/config.toml. Returns None if absent."""
    import tomllib

    p = path or CONFIG_FILE
    if not p.exists():
        return None
    with open(p, "rb") as f:
        data = tomllib.load(f)
    budget = data.get("budget", {})
    if not budget:
        return None
    return BudgetConfig(
        monthly_usd=float(budget.get("monthly_usd", 0)),
        alert_thresholds=budget.get("alert_thresholds", [50, 75, 90, 100]),
        provider_limits={k: float(v) for k, v in budget.get("provider", {}).items()},
    )


def _float(row: dict[str, str], key: str) -> float:
    return float(row.get(key, 0) or 0)


def month_spend(rows: list[dict[str, str]]) -> tuple[float, dict[str, float]]:
    """Total and per-provider spend for current calendar month."""
    now = datetime.now()
    prefix = now.strftime("%Y-%m")
    by_provider: dict[str, float] = {}
    total = 0.0

    # Group by session, take latest per session for this month
    best: dict[str, dict[str, str]] = {}
    for r in rows:
        if not r.get("timestamp", "").startswith(prefix):
            continue
        sid = r["session_id"]
        if sid not in best or r["timestamp"] > best[sid]["timestamp"]:
            best[sid] = r

    for r in best.values():
        c = _float(r, "cost_usd") + _float(r, "sc_cost_usd")
        total += c
        p = r["provider"]
        by_provider[p] = by_provider.get(p, 0) + c

    return total, by_provider


def forecast_eom(spent: float, day_of_month: int, days_in_month: int) -> float:
    """Linear extrapolation of end-of-month cost."""
    if day_of_month <= 0:
        return spent
    return spent / day_of_month * days_in_month


def check_alerts(config: BudgetConfig, spent: float) -> list[str]:
    """Return triggered alert messages for thresholds crossed."""
    if config.monthly_usd <= 0:
        return []
    pct = spent / config.monthly_usd * 100
    alerts: list[str] = []
    for threshold in sorted(config.alert_thresholds):
        if pct >= threshold:
            alerts.append(f"Budget {threshold}% reached (${spent:.2f} / ${config.monthly_usd:.2f})")
    return alerts


def budget_report(config: BudgetConfig, rows: list[dict[str, str]]) -> None:
    """Render budget gauge and forecast."""
    spent, by_provider = month_spend(rows)
    now = datetime.now()
    dom = now.day
    dim = calendar.monthrange(now.year, now.month)[1]
    projected = forecast_eom(spent, dom, dim)

    t = Table(title="Budget", show_header=False, box=None, padding=(0, 2))
    t.add_column(style="bold"); t.add_column()
    t.add_row("Monthly limit", f"${config.monthly_usd:.2f}")
    t.add_row("Spent", f"${spent:.2f}")
    t.add_row("Projected EOM", f"${projected:.2f}")
    remaining = max(0, config.monthly_usd - spent)
    t.add_row("Remaining", f"${remaining:.2f}")
    console.print(t)

    # Progress bar
    pct = min(1.0, spent / config.monthly_usd) if config.monthly_usd > 0 else 0.0
    with Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("{task.percentage:.0f}%"),
        console=console, transient=True,
    ) as progress:
        task = progress.add_task("Budget", total=100)
        progress.update(task, completed=pct * 100)
    # Print inline since transient clears
    bar_filled = int(pct * 30)
    bar_empty = 30 - bar_filled
    color = "green" if pct < 0.75 else "yellow" if pct < 0.90 else "red"
    console.print(f"  [{color}]{'█' * bar_filled}{'░' * bar_empty}[/{color}] {pct*100:.0f}%")

    # Provider breakdown
    for prov, amt in sorted(by_provider.items()):
        limit = config.provider_limits.get(prov)
        if limit:
            console.print(f"  {prov}: ${amt:.2f} / ${limit:.2f}")
        else:
            console.print(f"  {prov}: ${amt:.2f}")

    # Alerts
    alerts = check_alerts(config, spent)
    for a in alerts:
        console.print(f"  [yellow]![/yellow] {a}")
