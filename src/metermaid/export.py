"""Export — CSV, JSON, Markdown, and HTML output formats."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from .models import CSV_HEADERS


def _int(row: dict[str, str], key: str) -> int:
    return int(row.get(key, 0) or 0)


def _float(row: dict[str, str], key: str) -> float:
    return float(row.get(key, 0) or 0)


def export_csv(rows: list[dict[str, str]], out: Path) -> None:
    """Write rows as CSV with standard headers."""
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        w.writeheader()
        w.writerows(rows)


def export_json(rows: list[dict[str, str]], out: Path) -> None:
    """Write rows as JSON array with proper numeric types."""
    typed: list[dict[str, str | int | float]] = []
    int_fields = {
        "tokens_in",
        "tokens_out",
        "cache_read",
        "cache_write",
        "ctx_tokens",
        "ctx_max",
        "diff_add",
        "diff_del",
        "tok_in_delta",
        "tok_out_delta",
        "sc_tokens_in",
        "sc_tokens_out",
    }
    float_fields = {"cost_usd", "sc_cost_usd", "ctx_pct", "wall_sec", "api_sec"}
    for r in rows:
        row: dict[str, str | int | float] = {}
        for k, v in r.items():
            if k in int_fields:
                row[k] = _int(r, k)
            elif k in float_fields:
                row[k] = _float(r, k)
            else:
                row[k] = v
        typed.append(row)
    with open(out, "w") as f:
        json.dump(typed, f, indent=2)


def _summary(rows: list[dict[str, str]]) -> dict[str, str | int | float]:
    """Compute summary stats for markdown/HTML header."""
    best: dict[str, dict[str, str]] = {}
    for r in rows:
        sid = r["session_id"]
        if sid not in best or r["timestamp"] > best[sid]["timestamp"]:
            best[sid] = r
    latest = list(best.values())
    tok_in = sum(_int(r, "tokens_in") for r in latest)
    tok_out = sum(_int(r, "tokens_out") for r in latest)
    cost = sum(_float(r, "cost_usd") + _float(r, "sc_cost_usd") for r in latest)
    cr = sum(_int(r, "cache_read") for r in latest)
    total = tok_in + cr
    cache_pct = (cr / total * 100) if total > 0 else 0.0
    return {
        "sessions": len(latest),
        "tokens_in": tok_in,
        "tokens_out": tok_out,
        "cost": round(cost, 3),
        "cache_hit_pct": round(cache_pct, 1),
    }


def export_markdown(rows: list[dict[str, str]], out: Path) -> None:
    """Write summary + session table as Markdown."""
    s = _summary(rows)
    lines = [
        "## metermaid Report",
        "",
        f"- **Sessions:** {s['sessions']}",
        f"- **Tokens in:** {s['tokens_in']:,}",
        f"- **Tokens out:** {s['tokens_out']:,}",
        f"- **Cost:** ${s['cost']:.3f}",
        f"- **Cache hit:** {s['cache_hit_pct']:.1f}%",
        "",
        "| Date | Session | Provider | Model | Tok In | Tok Out | Cost |",
        "|------|---------|----------|-------|--------|---------|------|",
    ]
    best: dict[str, dict[str, str]] = {}
    for r in rows:
        sid = r["session_id"]
        if sid not in best or r["timestamp"] > best[sid]["timestamp"]:
            best[sid] = r
    for r in sorted(best.values(), key=lambda x: x["timestamp"]):
        ts = r["timestamp"][:19].replace("T", " ")
        c = _float(r, "cost_usd") + _float(r, "sc_cost_usd")
        lines.append(
            f"| {ts} | {r['session_id'][:12]} | {r['provider']} | "
            f"{r['model'][:22]} | {_int(r, 'tokens_in'):,} | "
            f"{_int(r, 'tokens_out'):,} | ${c:.3f} |"
        )
    out.write_text("\n".join(lines) + "\n")


def export_html(rows: list[dict[str, str]], out: Path) -> None:
    """Write standalone HTML report with inline CSS."""
    s = _summary(rows)
    best: dict[str, dict[str, str]] = {}
    for r in rows:
        sid = r["session_id"]
        if sid not in best or r["timestamp"] > best[sid]["timestamp"]:
            best[sid] = r
    sorted_rows = sorted(best.values(), key=lambda x: x["timestamp"])

    table_rows = ""
    for r in sorted_rows:
        ts = r["timestamp"][:19].replace("T", " ")
        c = _float(r, "cost_usd") + _float(r, "sc_cost_usd")
        table_rows += (
            f"<tr><td>{ts}</td><td>{r['session_id'][:12]}</td>"
            f"<td>{r['provider']}</td><td>{r['model'][:22]}</td>"
            f"<td>{_int(r, 'tokens_in'):,}</td><td>{_int(r, 'tokens_out'):,}</td>"
            f"<td>${c:.3f}</td></tr>\n"
        )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>metermaid Report</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }}
h1 {{ color: #1a1a2e; }}
.summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 1rem; margin: 1rem 0; }}
.stat {{ background: #f0f0f5; padding: 1rem; border-radius: 8px; }}
.stat .label {{ font-size: 0.85rem; color: #666; }}
.stat .value {{ font-size: 1.4rem; font-weight: bold; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 1.5rem; }}
th {{ background: #1a1a2e; color: white; padding: 0.6rem; text-align: left; }}
td {{ padding: 0.5rem 0.6rem; border-bottom: 1px solid #e0e0e0; }}
tr:hover {{ background: #f5f5fa; }}
</style></head><body>
<h1>metermaid Report</h1>
<p>Generated {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
<div class="summary">
<div class="stat"><div class="label">Sessions</div><div class="value">{s["sessions"]}</div></div>
<div class="stat"><div class="label">Tokens In</div><div class="value">{s["tokens_in"]:,}</div></div>
<div class="stat"><div class="label">Tokens Out</div><div class="value">{s["tokens_out"]:,}</div></div>
<div class="stat"><div class="label">Cost</div><div class="value">${s["cost"]:.3f}</div></div>
<div class="stat"><div class="label">Cache Hit</div><div class="value">{s["cache_hit_pct"]:.1f}%</div></div>
</div>
<table>
<tr><th>Date</th><th>Session</th><th>Provider</th><th>Model</th><th>Tok In</th><th>Tok Out</th><th>Cost</th></tr>
{table_rows}</table>
</body></html>"""
    out.write_text(html)


def export_otlp(rows: list[dict[str, str]], out: Path) -> None:
    """Write OTLP-compatible JSON for Prometheus/Grafana integration."""
    data_points: list[dict[str, object]] = []
    for r in rows:
        attrs = {
            "provider": r.get("provider", ""),
            "model": r.get("model", ""),
            "session_id": r.get("session_id", ""),
        }
        data_points.append(
            {
                "timeUnixNano": r.get("timestamp", ""),
                "attributes": [
                    {"key": k, "value": {"stringValue": v}} for k, v in attrs.items()
                ],
                "asDouble": _float(r, "cost_usd"),
                "metrics": {
                    "cost_usd": _float(r, "cost_usd"),
                    "tokens_in": _int(r, "tokens_in"),
                    "tokens_out": _int(r, "tokens_out"),
                    "cache_read": _int(r, "cache_read"),
                    "ctx_pct": _float(r, "ctx_pct"),
                    "wall_sec": _float(r, "wall_sec"),
                },
            }
        )
    otlp = {
        "resourceMetrics": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "metermaid"}},
                    ],
                },
                "scopeMetrics": [
                    {
                        "scope": {"name": "metermaid.export"},
                        "metrics": [
                            {
                                "name": "metermaid.session.snapshot",
                                "description": "Per-session usage snapshot",
                                "gauge": {"dataPoints": data_points},
                            }
                        ],
                    }
                ],
            }
        ],
    }
    with open(out, "w") as f:
        json.dump(otlp, f, indent=2)


def export_dispatch(rows: list[dict[str, str]], out: Path, fmt: str) -> None:
    """Dispatch to the correct exporter by format name."""
    exporters = {
        "csv": export_csv,
        "json": export_json,
        "markdown": export_markdown,
        "html": export_html,
        "otlp": export_otlp,
    }
    fn = exporters.get(fmt)
    if fn is None:
        raise ValueError(f"Unknown format: {fmt}")
    fn(rows, out)
