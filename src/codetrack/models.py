"""Snapshot dataclass — the single shared data model for codetrack."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path

CODETRACK_HOME: Path = Path.home() / ".codetrack"
SESSIONS_DIR: Path = CODETRACK_HOME / "sessions"
STATE_DIR: Path = CODETRACK_HOME / "state"
PID_FILE: Path = CODETRACK_HOME / "codetrack.pid"
DEFAULT_INTERVAL: int = 10


@dataclass
class Snapshot:
    timestamp: str
    provider: str        # claude | codex
    model: str           # primary model (main chain)
    session_id: str
    cost_usd: float      # main chain cost from costUSD or statusLine; 0 if absent
    ctx_pct: float
    ctx_tokens: int      # input-side context usage
    ctx_max: int
    wall_sec: float
    api_sec: float
    tokens_in: int       # main chain cumulative input
    tokens_out: int      # main chain cumulative output
    cache_read: int
    cache_write: int
    diff_add: int
    diff_del: int
    path: str
    source: str          # watcher | hook | backfill
    tok_in_delta: int    # tokens_in delta since previous snapshot
    tok_out_delta: int   # tokens_out delta since previous snapshot
    sc_tokens_in: int    # sidechain (subagent) cumulative input
    sc_tokens_out: int   # sidechain (subagent) cumulative output
    sc_cost_usd: float   # sidechain cost
    sc_models: str       # comma-separated subagent models


CSV_HEADERS: list[str] = [f.name for f in fields(Snapshot)]


def ts_delta(first: str, last: str) -> float:
    """Seconds between two ISO timestamps. Returns 0 on any failure."""
    if not first or not last:
        return 0.0
    try:
        t0 = datetime.fromisoformat(first.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(last.replace("Z", "+00:00"))
        return max(0, (t1 - t0).total_seconds())
    except (ValueError, TypeError):
        return 0.0
