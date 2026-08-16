"""MCP server — JSON-RPC over stdin/stdout for programmatic stats access."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .csv_io import read_all_snapshots
from .models import SESSIONS_DIR
from .report import filter_rows, latest_per_session

JsonObject = dict[str, Any]


def _int(row: dict[str, str], key: str) -> int:
    return int(row.get(key, 0) or 0)


def _float(row: dict[str, str], key: str) -> float:
    return float(row.get(key, 0) or 0)


def _usage_summary(sessions_dir: Path) -> JsonObject:
    """Return summary stats as a dict."""
    rows = read_all_snapshots(sessions_dir)
    latest = latest_per_session(rows)
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
        "cost_usd": round(cost, 3),
        "cache_hit_pct": round(cache_pct, 1),
        "providers": sorted({r["provider"] for r in latest}),
    }


def _session_list(sessions_dir: Path, window: str | None = None) -> list[JsonObject]:
    """Return per-session details."""
    rows = read_all_snapshots(sessions_dir)
    filtered = filter_rows(rows, window=window)
    latest = latest_per_session(filtered)
    result = []
    for r in sorted(latest, key=lambda x: x["timestamp"]):
        result.append(
            {
                "timestamp": r["timestamp"],
                "session_id": r["session_id"],
                "provider": r["provider"],
                "model": r["model"],
                "tokens_in": _int(r, "tokens_in"),
                "tokens_out": _int(r, "tokens_out"),
                "cost_usd": round(_float(r, "cost_usd") + _float(r, "sc_cost_usd"), 3),
                "cache_read": _int(r, "cache_read"),
                "ctx_pct": _float(r, "ctx_pct"),
                "wall_sec": _float(r, "wall_sec"),
            }
        )
    return result


def _cost_windows(sessions_dir: Path) -> list[JsonObject]:
    """Return 5h/7d/30d cost windows."""
    from datetime import datetime, timedelta

    rows = read_all_snapshots(sessions_dir)
    now = datetime.now()
    windows = []
    for label, delta in [
        ("5h", timedelta(hours=5)),
        ("7d", timedelta(days=7)),
        ("30d", timedelta(days=30)),
    ]:
        cutoff = now - delta
        wr = [r for r in rows if datetime.fromisoformat(r["timestamp"]) >= cutoff]
        wl = latest_per_session(wr)
        total_cost = sum(_float(r, "cost_usd") + _float(r, "sc_cost_usd") for r in wl)
        total_tok = sum(_int(r, "tokens_in") + _int(r, "tokens_out") for r in wl)
        windows.append(
            {
                "window": label,
                "cost_usd": round(total_cost, 3),
                "tokens": total_tok,
                "sessions": len(wl),
            }
        )
    return windows


TOOLS: list[JsonObject] = [
    {
        "name": "get_usage_summary",
        "description": "Get aggregate usage stats: sessions, tokens, cost, cache hit rate.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_session_list",
        "description": "Get per-session usage details with optional time window filter.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "window": {
                    "type": "string",
                    "description": "Time window (e.g. '7d', '5h')",
                },
            },
        },
    },
    {
        "name": "get_cost_windows",
        "description": "Get cost totals for 5h, 7d, and 30d windows.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def handle_request(request: JsonObject, sessions_dir: Path) -> JsonObject:
    """Route JSON-RPC method to handler."""
    method = request.get("method", "")
    rid = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "metermaid", "version": "0.2.0"},
            },
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        data: Any

        if tool_name == "get_usage_summary":
            data = _usage_summary(sessions_dir)
        elif tool_name == "get_session_list":
            data = _session_list(sessions_dir, tool_args.get("window"))
        elif tool_name == "get_cost_windows":
            data = _cost_windows(sessions_dir)
        else:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "content": [{"type": "text", "text": json.dumps(data, indent=2)}]
            },
        }

    if method == "notifications/initialized":
        return {}  # No response for notifications

    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def serve(sessions_dir: Path = SESSIONS_DIR) -> None:
    """Read stdin line-by-line, parse JSON-RPC, respond on stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_request(request, sessions_dir)
        if response:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
