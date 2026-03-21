"""Provider comparison — side-by-side Claude vs Codex summary."""

from __future__ import annotations

from collections import defaultdict

from rich.console import Console
from rich.table import Table

console = Console()


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
        if not cv or not xv:
            return "—"
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

    for name, d in [("Claude", c), ("Codex", x)]:
        if not d or not d.get("tok_out"):
            continue
        cph = d["cost"] / (d["wall_sec"] / 3600) if d["wall_sec"] > 0 else 0
        cpk = d["cost"] / d["tok_out"] * 1000 if d["tok_out"] > 0 else 0
        eff = d["tok_out"] / d["tok_in"] if d["tok_in"] > 0 else 0
        console.print(f"  {name}: [green]${cph:.2f}/hr[/green] | "
                      f"${cpk:.4f}/kTok_out | out/in {eff:.3f}")
