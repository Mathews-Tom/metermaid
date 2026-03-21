"""Tests for backfill — historical session import."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

from metermaid.backfill import backfill
from metermaid.csv_io import read_all_snapshots


def _make_claude_session(base: Path, project: str, session: str,
                          tokens_in: int = 100, age_hours: float = 0) -> Path:
    proj_dir = base / project
    proj_dir.mkdir(parents=True, exist_ok=True)
    f = proj_dir / f"{session}.jsonl"
    entry = {
        "timestamp": "2025-06-01T10:00:00Z",
        "message": {
            "usage": {"input_tokens": tokens_in, "output_tokens": 50},
            "model": "claude-sonnet-4-6",
        },
    }
    f.write_text(json.dumps(entry) + "\n")
    if age_hours > 0:
        old_time = time.time() - age_hours * 3600
        os.utime(f, (old_time, old_time))
    return f


def test_backfill_imports_sessions(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_projects = home / ".config" / "claude" / "projects"
    _make_claude_session(claude_projects, "proj1", "sess-aaa")
    _make_claude_session(claude_projects, "proj2", "sess-bbb")
    sessions_dir = tmp_path / "sessions"

    with patch("metermaid.platform.home_roots", return_value=[home]):
        r = backfill(sessions_dir=sessions_dir, since_hours=0)

    assert r.found == 2
    assert r.imported == 2
    assert r.already_tracked == 0
    assert r.no_data == 0
    rows = read_all_snapshots(sessions_dir)
    assert len(rows) == 2
    assert all(row["source"] == "backfill" for row in rows)


def test_backfill_skips_existing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_projects = home / ".config" / "claude" / "projects"
    _make_claude_session(claude_projects, "proj1", "sess-aaa")
    sessions_dir = tmp_path / "sessions"

    with patch("metermaid.platform.home_roots", return_value=[home]):
        backfill(sessions_dir=sessions_dir, since_hours=0)
        r = backfill(sessions_dir=sessions_dir, since_hours=0)

    assert r.found == 1
    assert r.imported == 0
    assert r.already_tracked == 1


def test_backfill_uses_file_mtime(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_projects = home / ".config" / "claude" / "projects"
    _make_claude_session(claude_projects, "proj1", "sess-old", age_hours=48)
    sessions_dir = tmp_path / "sessions"

    with patch("metermaid.platform.home_roots", return_value=[home]):
        backfill(sessions_dir=sessions_dir, since_hours=0)

    rows = read_all_snapshots(sessions_dir)
    assert len(rows) == 1
    from datetime import datetime, timedelta
    ts = datetime.fromisoformat(rows[0]["timestamp"])
    assert ts < datetime.now() - timedelta(hours=47)


def test_backfill_dry_run(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_projects = home / ".config" / "claude" / "projects"
    _make_claude_session(claude_projects, "proj1", "sess-aaa")
    sessions_dir = tmp_path / "sessions"

    with patch("metermaid.platform.home_roots", return_value=[home]):
        r = backfill(sessions_dir=sessions_dir, since_hours=0, dry_run=True)

    assert r.found == 1
    assert r.imported == 1
    assert not sessions_dir.exists() or len(list(sessions_dir.glob("*.csv"))) == 0


def test_backfill_since_filter(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_projects = home / ".config" / "claude" / "projects"
    _make_claude_session(claude_projects, "proj1", "sess-recent")
    _make_claude_session(claude_projects, "proj2", "sess-old", age_hours=72)
    sessions_dir = tmp_path / "sessions"

    with patch("metermaid.platform.home_roots", return_value=[home]):
        r = backfill(sessions_dir=sessions_dir, since_hours=24)

    assert r.found == 1
    assert r.imported == 1


def test_backfill_no_data_counted(tmp_path: Path) -> None:
    """Sessions with no usage data are counted as no_data, not skipped."""
    home = tmp_path / "home"
    proj_dir = home / ".config" / "claude" / "projects" / "proj1"
    proj_dir.mkdir(parents=True)
    # JSONL with no usage entries
    (proj_dir / "empty-sess.jsonl").write_text(
        json.dumps({"timestamp": "2025-01-01T00:00:00Z", "type": "human"}) + "\n"
    )
    sessions_dir = tmp_path / "sessions"

    with patch("metermaid.platform.home_roots", return_value=[home]):
        r = backfill(sessions_dir=sessions_dir, since_hours=0)

    assert r.found == 1
    assert r.imported == 0
    assert r.no_data == 1
    assert r.already_tracked == 0
