"""Reporting — report, session_table, filtering."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from rich.console import Console
from rich.table import Table

from .trends import cost_windows, trend_block, week_over_week

console = Console()


def _int(row: dict[str, str], key: str) -> int:
    return int(row.get(key, 0) or 0)


def _float(row: dict[str, str], key: str) -> float:
    return float(row.get(key, 0) or 0)


def window_delta(w: str) -> timedelta:
    m = re.match(r"(\d+)\s*(h|d)", w.lower())
    if not m:
        raise ValueError(f"Bad window: {w}")
    v = int(m.group(1))
    return timedelta(hours=v) if m.group(2) == "h" else timedelta(days=v)


def filter_rows(
    rows: list[dict[str, str]], *,
    window: str | None = None, session: str | None = None, provider: str | None = None,
) -> list[dict[str, str]]:
    out = rows
    if window:
        cutoff = datetime.now() - window_delta(window)
        out = [r for r in out if datetime.fromisoformat(r["timestamp"]) >= cutoff]
    if session:
        out = [r for r in out if r["session_id"] == session]
    if provider:
        out = [r for r in out if r["provider"] == provider]
    return out


def latest_per_session(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    best: dict[str, dict[str, str]] = {}
    for r in rows:
        sid = r["session_id"]
        if sid not in best or r["timestamp"] > best[sid]["timestamp"]:
            best[sid] = r
    return list(best.values())


def _cache_hit_pct(cache_read: int, tokens_in: int) -> float:
    total = tokens_in + cache_read
    return (cache_read / total * 100) if total > 0 else 0.0


def _color_cache(pct: float) -> str:
    if pct >= 70:
        return f"[green]{pct:.0f}%[/green]"
    if pct >= 40:
        return f"[yellow]{pct:.0f}%[/yellow]"
    return f"[red]{pct:.0f}%[/red]"


def report(rows: list[dict[str, str]], all_rows: list[dict[str, str]]) -> None:
    if not rows:
        console.print("[dim]No data.[/dim]")
        return

    latest = latest_per_session(rows)
    tok_in = sum(_int(r, "tokens_in") for r in latest)
    tok_out = sum(_int(r, "tokens_out") for r in latest)
    sc_in = sum(_int(r, "sc_tokens_in") for r in latest)
    sc_out = sum(_int(r, "sc_tokens_out") for r in latest)
    wall = sum(_float(r, "wall_sec") for r in latest)
    api = sum(_float(r, "api_sec") for r in latest)
    cost = sum(_float(r, "cost_usd") for r in latest)
    sc_cost = sum(_float(r, "sc_cost_usd") for r in latest)
    adds = sum(_int(r, "diff_add") for r in latest)
    dels = sum(_int(r, "diff_del") for r in latest)
    provs = sorted({r["provider"] for r in latest})

    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column(style="bold"); t.add_column()
    t.add_row("Sessions", str(len(latest)))
    t.add_row("Providers", ", ".join(provs))
    t.add_row("Tokens in", f"{tok_in:,}")
    t.add_row("Tokens out", f"{tok_out:,}")
    if sc_in or sc_out:
        t.add_row("Subagent in", f"[yellow]{sc_in:,}[/yellow]")
        t.add_row("Subagent out", f"[yellow]{sc_out:,}[/yellow]")
        t.add_row("Total tokens", f"{tok_in + tok_out + sc_in + sc_out:,}")
    total_cost = cost + sc_cost
    if total_cost > 0:
        cost_str = f"[green]${cost:.3f}[/green]"
        if sc_cost > 0:
            cost_str += f" + [yellow]${sc_cost:.3f} subagent[/yellow]"
        t.add_row("Cost (USD)", cost_str)
    t.add_row("Wall time", f"{wall:.0f}s ({wall/60:.1f}m)")
    if api > 0:
        t.add_row("API time", f"{api:.0f}s ({api/60:.1f}m)")

    # Cache hit rate
    total_cache = sum(_int(r, "cache_read") for r in latest)
    cache_pct = _cache_hit_pct(total_cache, tok_in)
    t.add_row("Cache hit", _color_cache(cache_pct))

    if adds or dels:
        t.add_row("Lines +/-", f"[green]+{adds}[/green] [red]-{dels}[/red]")

    # Cost per line
    total_lines = adds + dels
    if total_lines > 0 and total_cost > 0:
        t.add_row("Cost/line", f"${total_cost / total_lines:.4f}")

    # Sparkline trends
    trends = trend_block(all_rows)
    for label, spark in trends.items():
        if spark.strip():
            t.add_row(f"{label} 7d", spark)

    console.print(t)
    cost_windows(all_rows)
    week_over_week(all_rows)


def session_table(rows: list[dict[str, str]]) -> None:
    latest = latest_per_session(rows)
    if not latest:
        return

    has_sc = any(_int(r, "sc_tokens_in") > 0 or _int(r, "sc_tokens_out") > 0 for r in latest)

    t = Table(title="Sessions", highlight=True)
    t.add_column("Date/Time (UTC)", style="dim")
    t.add_column("Session", style="cyan")
    t.add_column("Provider")
    t.add_column("Model")
    t.add_column("Tok In", justify="right")
    t.add_column("Tok Out", justify="right")
    if has_sc:
        t.add_column("SC In", justify="right", style="yellow")
        t.add_column("SC Out", justify="right", style="yellow")
        t.add_column("SC Models", style="dim")
    t.add_column("Cost", justify="right")
    t.add_column("Cache%", justify="right")
    t.add_column("Ctx%", justify="right")
    t.add_column("Wall", justify="right")

    for r in sorted(latest, key=lambda x: x["timestamp"]):
        ts = r["timestamp"][:19].replace("T", " ")
        c = _float(r, "cost_usd") + _float(r, "sc_cost_usd")
        cost_s = f"[green]${c:.3f}[/green]" if c > 0 else "[dim]—[/dim]"
        ctx = _float(r, "ctx_pct")
        ctx_s = f"[red]{ctx:.0f}%[/red]" if ctx > 80 else f"{ctx:.0f}%"
        cr = _int(r, "cache_read")
        ti = _int(r, "tokens_in")
        cpct = _cache_hit_pct(cr, ti)
        row = [
            ts, r["session_id"][:12], r["provider"], r["model"][:22],
            f"{ti:,}", f"{_int(r, 'tokens_out'):,}",
        ]
        if has_sc:
            si, so = _int(r, "sc_tokens_in"), _int(r, "sc_tokens_out")
            row.extend([
                f"{si:,}" if si else "[dim]—[/dim]",
                f"{so:,}" if so else "[dim]—[/dim]",
                r.get("sc_models", "") or "[dim]—[/dim]",
            ])
        row.extend([cost_s, _color_cache(cpct), ctx_s, f"{_float(r, 'wall_sec'):.0f}s"])
        t.add_row(*row)
    console.print(t)
