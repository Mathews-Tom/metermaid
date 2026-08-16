"""Snapshot dataclass — the single shared data model for metermaid."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

METERMAID_HOME: Path = Path.home() / ".metermaid"
SESSIONS_DIR: Path = METERMAID_HOME / "sessions"
DEFAULT_INTERVAL: int = 10


@dataclass
class Snapshot:
    timestamp: str
    provider: str  # claude | codex
    model: str  # primary model (main chain)
    session_id: str
    cost_usd: float  # main chain cost from costUSD or statusLine; 0 if absent
    ctx_pct: float
    ctx_tokens: int  # input-side context usage
    ctx_max: int
    wall_sec: float
    api_sec: float
    tokens_in: int  # main chain cumulative input
    tokens_out: int  # main chain cumulative output
    cache_read: int
    cache_write: int
    diff_add: int
    diff_del: int
    path: str
    source: str  # watcher | hook | backfill
    tok_in_delta: int  # tokens_in delta since previous snapshot
    tok_out_delta: int  # tokens_out delta since previous snapshot
    sc_tokens_in: int  # sidechain (subagent) cumulative input
    sc_tokens_out: int  # sidechain (subagent) cumulative output
    sc_cost_usd: float  # sidechain cost
    sc_models: str  # comma-separated subagent models


CSV_HEADERS: list[str] = [f.name for f in fields(Snapshot)]
