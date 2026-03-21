"""StatusLine hook — handle_claude_hook, statusLine output."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .csv_io import append_snapshot
from .delta import compute_deltas
from .models import SESSIONS_DIR, Snapshot


def handle_claude_hook(raw: str, sessions_dir: Path = SESSIONS_DIR) -> None:
    """Parse Claude Code statusLine JSON. Adds real cost, api_sec, diffs."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    mi = data.get("model", {})
    cost = data.get("cost", {})
    ctx = data.get("context_window", {})
    cur = ctx.get("current_usage", {})

    total_in = ctx.get("total_input_tokens", 0) or 0
    total_out = ctx.get("total_output_tokens", 0) or 0
    ctx_size = ctx.get("context_window_size", 200000) or 200000
    used_pct = ctx.get("used_percentage", 0) or 0

    cur_in = cur.get("input_tokens", 0) or 0
    cr = cur.get("cache_read_input_tokens", 0) or 0
    cw = cur.get("cache_creation_input_tokens", 0) or 0

    snap = Snapshot(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        provider="claude",
        model=mi.get("id", mi.get("display_name", "unknown")),
        session_id=data.get("session_id", "")[:12],
        cost_usd=cost.get("total_cost_usd", 0.0) or 0.0,
        ctx_pct=used_pct,
        ctx_tokens=cur_in + cr + cw,
        ctx_max=ctx_size,
        wall_sec=(cost.get("total_duration_ms", 0) or 0) / 1000,
        api_sec=(cost.get("total_api_duration_ms", 0) or 0) / 1000,
        tokens_in=total_in,
        tokens_out=total_out,
        cache_read=cr,
        cache_write=cw,
        diff_add=cost.get("total_lines_added", 0) or 0,
        diff_del=cost.get("total_lines_removed", 0) or 0,
        path=data.get("cwd", ""),
        source="hook",
        tok_in_delta=0,
        tok_out_delta=0,
        sc_tokens_in=0,
        sc_tokens_out=0,
        sc_cost_usd=0.0,
        sc_models="",
    )
    snap = compute_deltas(snap)
    append_snapshot(snap, sessions_dir)

    wm = snap.wall_sec / 60
    am = snap.api_sec / 60
    ik = snap.tokens_in / 1000
    ok_ = snap.tokens_out / 1000
    print(
        f"{snap.model} | ${snap.cost_usd:.3f} | "
        f"ctx {snap.ctx_pct:.0f}% ({snap.ctx_tokens // 1000}k/{snap.ctx_max // 1000}k) | "
        f"{wm:.0f}m ({am:.0f}m api) | "
        f"{ik:.0f}k in {ok_:.0f}k out | "
        f"+{snap.diff_add} -{snap.diff_del}"
    )


