"""Parse Claude Code JSONL transcripts — main chain + sidechain tracking."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..models import Snapshot, ts_delta


def parse_claude_transcript(path: Path) -> Snapshot | None:
    """Parse Claude Code JSONL transcript.

    Main chain: non-sidechain, non-error, non-synthetic entries.
    Sidechain: isSidechain=true entries (subagent calls via Agent tool).
    Filters out: isApiErrorMessage, <synthetic> model (context compression).
    """
    try:
        content = path.read_text(errors="replace")
    except OSError:
        return None

    # Main chain
    cum_in = 0
    cum_out = 0
    cum_cache_r = 0
    cum_cache_w = 0
    cum_cost = 0.0
    latest_usage: dict[str, Any] | None = None
    model = "unknown"
    first_ts = ""
    latest_ts = ""

    # Sidechain (subagents)
    sc_in = 0
    sc_out = 0
    sc_cache_r = 0
    sc_cache_w = 0
    sc_cost = 0.0
    sc_model_set: set[str] = set()

    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if entry.get("isApiErrorMessage"):
            continue

        ts = entry.get("timestamp", "")
        if ts and not first_ts:
            first_ts = ts

        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue

        m = msg.get("model", "")
        if m == "<synthetic>":
            continue

        usage = msg.get("usage")
        if not usage or not isinstance(usage, dict):
            if m and not entry.get("isSidechain"):
                model = m
            continue

        entry_cost = entry.get("costUSD")
        cost_val = entry_cost if isinstance(entry_cost, (int, float)) else 0.0

        u_in = usage.get("input_tokens", 0)
        u_out = usage.get("output_tokens", 0)
        u_cr = usage.get("cache_read_input_tokens", 0)
        u_cw = usage.get("cache_creation_input_tokens", 0)

        if entry.get("isSidechain"):
            sc_in += u_in + u_cr + u_cw
            sc_out += u_out
            sc_cache_r += u_cr
            sc_cache_w += u_cw
            sc_cost += cost_val
            if m:
                sc_model_set.add(m)
        else:
            latest_usage = usage
            latest_ts = ts
            cum_in += u_in + u_cr + u_cw
            cum_out += u_out
            cum_cache_r += u_cr
            cum_cache_w += u_cw
            cum_cost += cost_val
            if m:
                model = m

    if latest_usage is None:
        return None

    cur_in = latest_usage.get("input_tokens", 0)
    cur_cache_r = latest_usage.get("cache_read_input_tokens", 0)
    cur_cache_w = latest_usage.get("cache_creation_input_tokens", 0)
    ctx_tokens = cur_in + cur_cache_r + cur_cache_w
    ctx_max = 200000
    ctx_pct = min(100.0, (ctx_tokens / ctx_max) * 100) if ctx_max else 0.0

    wall_sec = ts_delta(first_ts, latest_ts)

    return Snapshot(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        provider="claude",
        model=model,
        session_id=path.stem[:12],
        cost_usd=round(cum_cost, 6),
        ctx_pct=round(ctx_pct, 1),
        ctx_tokens=ctx_tokens,
        ctx_max=ctx_max,
        wall_sec=round(wall_sec, 1),
        api_sec=0.0,
        tokens_in=cum_in,
        tokens_out=cum_out,
        cache_read=cum_cache_r,
        cache_write=cum_cache_w,
        diff_add=0,
        diff_del=0,
        path=str(path.parent.name),
        source="watcher",
        tok_in_delta=0,
        tok_out_delta=0,
        sc_tokens_in=sc_in,
        sc_tokens_out=sc_out,
        sc_cost_usd=round(sc_cost, 6),
        sc_models=",".join(sorted(sc_model_set)),
    )
