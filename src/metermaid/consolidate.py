"""Consolidation — time-windowed aggregates with derived metrics.

Scans per-session CSVs (read-only), groups by day/week/month,
computes per-session max of cumulative fields, sums deltas,
derives cost_per_hour, tok_efficiency, cost_per_ktok_out.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .pricing import fill_cost

console = Console()

AGG_FIELDS: list[str] = [
    "window", "provider", "model", "sessions",
    "tok_in", "tok_out", "tok_in_delta", "tok_out_delta",
    "cost_usd", "wall_sec", "api_sec",
    "diffs_add", "diffs_del",
    "cost_per_hour", "tok_efficiency", "cost_per_ktok_out",
]


def _int(row: dict[str, str], key: str) -> int:
    return int(row.get(key, 0) or 0)


def _float(row: dict[str, str], key: str) -> float:
    return float(row.get(key, 0) or 0)


def window_key(ts_str: str, window: str) -> str:
    """Map ISO timestamp to a bucket key."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return "unknown"
    if window == "week":
        return f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
    if window == "month":
        return dt.strftime("%Y-%m")
    return dt.strftime("%Y-%m-%d")


def aggregate(
    rows: list[dict[str, str]], window: str,
) -> list[dict[str, str]]:
    """Group rows by (bucket, provider, model). Cumulative fields use per-session max."""
    session_max: dict[tuple, dict] = {}
    session_deltas: dict[tuple, dict] = defaultdict(
        lambda: {"tok_in_delta": 0, "tok_out_delta": 0, "diffs_add": 0, "diffs_del": 0}
    )

    for r in rows:
        bucket = window_key(r.get("timestamp", ""), window)
        key = (bucket, r["provider"], r.get("model", ""), r["session_id"])
        tok_in = _int(r, "tokens_in")
        cost = fill_cost(r)

        if key not in session_max or tok_in > session_max[key]["tok_in"]:
            session_max[key] = {
                "tok_in": tok_in, "tok_out": _int(r, "tokens_out"),
                "cost": cost, "wall_sec": _float(r, "wall_sec"),
                "api_sec": _float(r, "api_sec"),
            }

        dk = session_deltas[key]
        dk["tok_in_delta"] += _int(r, "tok_in_delta")
        dk["tok_out_delta"] += _int(r, "tok_out_delta")
        dk["diffs_add"] += _int(r, "diffs_add")
        dk["diffs_del"] += _int(r, "diffs_del")

    agg: dict[tuple, dict] = defaultdict(lambda: {
        "sessions": 0, "tok_in": 0, "tok_out": 0,
        "tok_in_delta": 0, "tok_out_delta": 0,
        "cost": 0.0, "wall_sec": 0.0, "api_sec": 0.0,
        "diffs_add": 0, "diffs_del": 0,
    })

    for (bucket, provider, model, sid), sm in session_max.items():
        ak = (bucket, provider, model)
        a = agg[ak]
        a["sessions"] += 1
        a["tok_in"] += sm["tok_in"]
        a["tok_out"] += sm["tok_out"]
        a["cost"] += sm["cost"]
        a["wall_sec"] += sm["wall_sec"]
        a["api_sec"] += sm["api_sec"]
        dk = session_deltas[(bucket, provider, model, sid)]
        for f in ("tok_in_delta", "tok_out_delta", "diffs_add", "diffs_del"):
            a[f] += dk[f]

    output: list[dict[str, str]] = []
    for (bucket, provider, model), a in sorted(agg.items()):
        cph = a["cost"] / (a["wall_sec"] / 3600) if a["wall_sec"] > 0 else 0
        eff = a["tok_out"] / a["tok_in"] if a["tok_in"] > 0 else 0
        cpk = a["cost"] / a["tok_out"] * 1000 if a["tok_out"] > 0 else 0
        output.append({
            "window": bucket, "provider": provider, "model": model,
            "sessions": str(a["sessions"]),
            "tok_in": str(a["tok_in"]), "tok_out": str(a["tok_out"]),
            "tok_in_delta": str(a["tok_in_delta"]), "tok_out_delta": str(a["tok_out_delta"]),
            "cost_usd": f"{a['cost']:.6f}",
            "wall_sec": f"{a['wall_sec']:.1f}", "api_sec": f"{a['api_sec']:.1f}",
            "diffs_add": str(a["diffs_add"]), "diffs_del": str(a["diffs_del"]),
            "cost_per_hour": f"{cph:.4f}",
            "tok_efficiency": f"{eff:.4f}",
            "cost_per_ktok_out": f"{cpk:.6f}",
        })
    return output


def write_aggregate_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=AGG_FIELDS)
        w.writeheader()
        w.writerows(rows)


def provider_comparison(rows: list[dict[str, str]]) -> None:
    """Side-by-side Claude vs Codex summary with rich table."""
    by_p: dict[str, dict] = defaultdict(lambda: {
        "sessions": 0, "tok_in": 0, "tok_out": 0,
        "cost": 0.0, "wall_sec": 0.0, "api_sec": 0.0,
        "diffs": 0,
    })
    for r in rows:
        p = by_p[r["provider"]]
        p["sessions"] += int(r["sessions"])
        p["tok_in"] += int(r["tok_in"])
        p["tok_out"] += int(r["tok_out"])
        p["cost"] += float(r["cost_usd"])
        p["wall_sec"] += float(r["wall_sec"])
        p["diffs"] += int(r["diffs_add"]) + int(r["diffs_del"])

    t = Table(title="Provider Comparison", highlight=True)
    t.add_column("Metric", style="bold")
    t.add_column("Claude", justify="right")
    t.add_column("Codex", justify="right")
    t.add_column("Ratio", justify="right", style="dim")

    c, x = by_p.get("claude", {}), by_p.get("codex", {})

    def _ratio(cv: float, xv: float) -> str:
        if not cv or not xv: return "—"
        return f"{cv / xv:.1f}x"

    def _row(label: str, cv: object, xv: object, fmt: str = "", ratio: bool = True) -> None:
        cs = fmt.format(cv) if cv else "—"
        xs = fmt.format(xv) if xv else "—"
        rs = _ratio(float(cv or 0), float(xv or 0)) if ratio else "—"
        t.add_row(label, cs, xs, rs)

    _row("Sessions", c.get("sessions"), x.get("sessions"), "{}")
    _row("Tokens In", c.get("tok_in"), x.get("tok_in"), "{:,}")
    _row("Tokens Out", c.get("tok_out"), x.get("tok_out"), "{:,}")
    _row("Est. Cost", c.get("cost"), x.get("cost"), "${:.2f}")
    ws_c, ws_x = c.get("wall_sec", 0), x.get("wall_sec", 0)
    t.add_row("Wall Time",
              f"{ws_c / 3600:.1f}h" if ws_c else "—",
              f"{ws_x / 3600:.1f}h" if ws_x else "—",
              _ratio(ws_c, ws_x))

    console.print(t)

    # Efficiency row
    for name, d in [("Claude", c), ("Codex", x)]:
        if not d or not d.get("tok_out"): continue
        cph = d["cost"] / (d["wall_sec"] / 3600) if d["wall_sec"] > 0 else 0
        cpk = d["cost"] / d["tok_out"] * 1000 if d["tok_out"] > 0 else 0
        eff = d["tok_out"] / d["tok_in"] if d["tok_in"] > 0 else 0
        console.print(f"  {name}: [green]${cph:.2f}/hr[/green] | "
                       f"${cpk:.4f}/kTok_out | out/in {eff:.3f}")
