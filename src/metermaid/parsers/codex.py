"""Parse Codex CLI session JSONL — token_count events + legacy fallback."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..models import Snapshot, ts_delta


def _int(val: object) -> int:
    """Coerce to int, treating None/missing as 0."""
    return int(val) if isinstance(val, (int, float)) else 0


def parse_codex_session(path: Path) -> Snapshot | None:
    """Parse Codex CLI session JSONL.

    Token data from three formats (checked in order):
      1. payload.info.total_token_usage (current Codex, 2026+)
      2. payload.{input_tokens, output_tokens} (mid-2025 Codex)
      3. message.usage (OpenAI API response format, older builds)

    Model from:
      - type="turn_context" → payload.model (current)
      - entry.turn_context.model (mid-2025)
      - type="codex.conversation_starts" → entry.model (legacy)
    """
    try:
        content = path.read_text(errors="replace")
    except OSError:
        return None

    # Best source: info.total_token_usage (cumulative, current format)
    latest_total: dict | None = None

    # Mid-2025: payload-level token fields (cumulative, need deltas)
    prev_in = 0
    prev_out = 0
    prev_cached = 0
    mid_in = 0
    mid_out = 0
    mid_cached = 0
    found_mid = False

    # Legacy: message.usage
    fb_in = 0
    fb_out = 0
    fb_cached = 0
    fb_model = ""

    model = ""
    first_ts = ""
    latest_ts = ""

    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts = entry.get("timestamp", "")
        if ts and not first_ts:
            first_ts = ts
        if ts:
            latest_ts = ts

        # Model: type="turn_context" with payload.model (current)
        if entry.get("type") == "turn_context":
            p = entry.get("payload", {})
            if isinstance(p, dict) and p.get("model"):
                model = p["model"]

        # Model: top-level turn_context dict (mid-2025)
        tc = entry.get("turn_context", {})
        if isinstance(tc, dict) and tc.get("model"):
            model = tc["model"]

        if isinstance(entry.get("payload"), dict):
            payload = entry["payload"]
            if payload.get("type") == "token_count":
                # Current format: payload.info.total_token_usage
                info = payload.get("info")
                if isinstance(info, dict):
                    total = info.get("total_token_usage")
                    if isinstance(total, dict) and _int(total.get("input_tokens")) > 0:
                        latest_total = total

                # Mid-2025 format: payload.input_tokens directly
                cur_in = _int(payload.get("input_tokens"))
                cur_out = _int(payload.get("output_tokens"))
                cur_cached = _int(payload.get("cached_input_tokens"))
                if cur_in > 0 or cur_out > 0:
                    found_mid = True
                    mid_in += max(0, cur_in - prev_in)
                    mid_out += max(0, cur_out - prev_out)
                    mid_cached += max(0, cur_cached - prev_cached)
                    prev_in, prev_out, prev_cached = cur_in, cur_out, cur_cached

        # Legacy model source
        if entry.get("type") == "codex.conversation_starts":
            fb_model = entry.get("model", fb_model)

        # Legacy: message.usage
        msg = entry.get("message", {})
        if isinstance(msg, dict):
            usage = msg.get("usage", {})
            if isinstance(usage, dict) and usage:
                fb_in += _int(usage.get("prompt_tokens")) + _int(usage.get("input_tokens"))
                fb_out += _int(usage.get("completion_tokens")) + _int(usage.get("output_tokens"))
                details = usage.get("prompt_tokens_details", {})
                if isinstance(details, dict):
                    fb_cached += _int(details.get("cached_tokens"))
            mm = msg.get("model", "")
            if mm:
                fb_model = mm

    # Pick best data source (newest format first)
    if latest_total is not None:
        tok_in = _int(latest_total.get("input_tokens"))
        tok_out = _int(latest_total.get("output_tokens"))
        cached = _int(latest_total.get("cached_input_tokens"))
        used_model = model or fb_model or "unknown"
    elif found_mid and (mid_in > 0 or mid_out > 0):
        tok_in, tok_out, cached = mid_in, mid_out, mid_cached
        used_model = model or fb_model or "unknown"
    elif fb_in > 0 or fb_out > 0:
        tok_in, tok_out, cached = fb_in, fb_out, fb_cached
        used_model = fb_model or "unknown"
    else:
        return None

    wall_sec = ts_delta(first_ts, latest_ts)
    # Codex filenames: rollout-YYYY-MM-DDThh-mm-ss-UUID
    # Extract UUID (last 36 chars of stem) for unique session ID
    stem = path.stem
    session_id = stem[-36:][:12] if len(stem) > 36 else stem[:12]

    return Snapshot(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        provider="codex",
        model=used_model,
        session_id=session_id,
        cost_usd=0.0,
        ctx_pct=0.0,
        ctx_tokens=0,
        ctx_max=0,
        wall_sec=round(wall_sec, 1),
        api_sec=0.0,
        tokens_in=tok_in,
        tokens_out=tok_out,
        cache_read=cached,
        cache_write=0,
        diff_add=0,
        diff_del=0,
        path="",
        source="watcher",
        tok_in_delta=0,
        tok_out_delta=0,
        sc_tokens_in=0,
        sc_tokens_out=0,
        sc_cost_usd=0.0,
        sc_models="",
    )
