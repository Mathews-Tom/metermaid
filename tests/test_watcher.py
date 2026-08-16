"""Tests for SessionWatcher — mtime dedup, content hash dedup, incremental."""

from __future__ import annotations

import json
import time
from pathlib import Path

from metermaid.csv_io import read_all_snapshots
from metermaid.models import Snapshot
from metermaid.parsers.claude import parse_claude_transcript
from metermaid.watcher import SessionWatcher, snap_hash


def _make_claude_jsonl(path: Path, tokens_in: int = 100, tokens_out: int = 50) -> Path:
    entries = [
        {
            "timestamp": "2025-01-01T00:00:00Z",
            "message": {
                "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
                "model": "claude-sonnet-4-6",
            },
        },
    ]
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return path


def test_mtime_dedup(tmp_path: Path) -> None:
    """Same mtime should not re-parse the file."""
    watcher = SessionWatcher(sessions_dir=tmp_path / "sessions")
    session = _make_claude_jsonl(tmp_path / "session-aaa.jsonl")

    snap1 = watcher._check(session, parse_claude_transcript)
    assert snap1 is not None

    # Same mtime -> skip
    snap2 = watcher._check(session, parse_claude_transcript)
    assert snap2 is None


def test_content_hash_dedup(tmp_path: Path) -> None:
    """Different mtime but same content hash -> skip."""
    watcher = SessionWatcher(sessions_dir=tmp_path / "sessions")
    session = _make_claude_jsonl(tmp_path / "session-bbb.jsonl")

    snap1 = watcher._check(session, parse_claude_transcript)
    assert snap1 is not None

    # Touch file to update mtime, same content
    time.sleep(0.05)
    session.write_text(session.read_text())
    snap2 = watcher._check(session, parse_claude_transcript)
    assert snap2 is None


def test_incremental_update(tmp_path: Path) -> None:
    """Changed content with new mtime should produce a new snapshot."""
    watcher = SessionWatcher(sessions_dir=tmp_path / "sessions")
    session = _make_claude_jsonl(tmp_path / "session-ccc.jsonl", tokens_in=100)

    snap1 = watcher._check(session, parse_claude_transcript)
    assert snap1 is not None
    assert snap1.tokens_in == 100

    # Update with more tokens
    time.sleep(0.05)
    _make_claude_jsonl(session, tokens_in=500, tokens_out=200)

    snap2 = watcher._check(session, parse_claude_transcript)
    assert snap2 is not None
    assert snap2.tokens_in == 500


def test_snap_hash_deterministic() -> None:
    snap = Snapshot(
        timestamp="2025-01-01T00:00:00",
        provider="claude",
        model="test",
        session_id="abc",
        cost_usd=0.0,
        ctx_pct=0.0,
        ctx_tokens=1000,
        ctx_max=200000,
        wall_sec=0.0,
        api_sec=0.0,
        tokens_in=100,
        tokens_out=50,
        cache_read=0,
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
    h1 = snap_hash(snap)
    h2 = snap_hash(snap)
    assert h1 == h2
    assert len(h1) == 12


def test_per_session_file_isolation(tmp_path: Path) -> None:
    """Each session writes to its own CSV — no shared file."""
    from metermaid.csv_io import append_snapshot

    sessions_dir = tmp_path / "sessions"
    snap_a = Snapshot(
        timestamp="2025-01-01T00:00:00",
        provider="claude",
        model="test",
        session_id="aaa",
        cost_usd=0.1,
        ctx_pct=0.0,
        ctx_tokens=0,
        ctx_max=0,
        wall_sec=0.0,
        api_sec=0.0,
        tokens_in=100,
        tokens_out=50,
        cache_read=0,
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
    snap_b = Snapshot(
        timestamp="2025-01-01T00:01:00",
        provider="codex",
        model="test",
        session_id="bbb",
        cost_usd=0.0,
        ctx_pct=0.0,
        ctx_tokens=0,
        ctx_max=0,
        wall_sec=0.0,
        api_sec=0.0,
        tokens_in=200,
        tokens_out=80,
        cache_read=0,
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

    append_snapshot(snap_a, sessions_dir)
    append_snapshot(snap_b, sessions_dir)

    # Two separate files
    assert (sessions_dir / "claude_aaa.csv").exists()
    assert (sessions_dir / "codex_bbb.csv").exists()

    # read_all_snapshots merges both
    all_rows = read_all_snapshots(sessions_dir)
    assert len(all_rows) == 2
    providers = {r["provider"] for r in all_rows}
    assert providers == {"claude", "codex"}


def test_multiple_rows_per_session(tmp_path: Path) -> None:
    """Multiple snapshots for the same session accumulate in one file."""
    from metermaid.csv_io import append_snapshot

    sessions_dir = tmp_path / "sessions"
    for i in range(3):
        snap = Snapshot(
            timestamp=f"2025-01-01T00:0{i}:00",
            provider="claude",
            model="test",
            session_id="xxx",
            cost_usd=0.0,
            ctx_pct=0.0,
            ctx_tokens=0,
            ctx_max=0,
            wall_sec=0.0,
            api_sec=0.0,
            tokens_in=100 * (i + 1),
            tokens_out=50,
            cache_read=0,
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
        append_snapshot(snap, sessions_dir)

    rows = read_all_snapshots(sessions_dir)
    assert len(rows) == 3
    assert all(r["session_id"] == "xxx" for r in rows)
    # Only one file created
    csv_files = list(sessions_dir.glob("*.csv"))
    assert len(csv_files) == 1
    assert csv_files[0].name == "claude_xxx.csv"
