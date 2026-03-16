"""Tests for session discovery across Claude dirs (new + legacy)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from codetrack.platform import discover_sessions


def _create_claude_session(base: Path, project: str, session: str) -> Path:
    d = base / project
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{session}.jsonl"
    f.write_text(json.dumps({"timestamp": "2025-01-01T00:00:00Z"}) + "\n")
    return f


def test_new_config_dir_discovery(tmp_path: Path) -> None:
    """Discovers sessions under .config/claude/projects/."""
    home = tmp_path / "home"
    config_projects = home / ".config" / "claude" / "projects"
    _create_claude_session(config_projects, "projhash1", "sess-aaa")
    _create_claude_session(config_projects, "projhash2", "sess-bbb")

    with patch("codetrack.platform.home_roots", return_value=[home]):
        claude, codex = discover_sessions(max_age_hours=24)

    assert len(claude) == 2
    stems = {f.stem for f in claude}
    assert "sess-aaa" in stems
    assert "sess-bbb" in stems
    assert len(codex) == 0


def test_legacy_dir_discovery(tmp_path: Path) -> None:
    """Discovers sessions under .claude/projects/."""
    home = tmp_path / "home"
    legacy_projects = home / ".claude" / "projects"
    _create_claude_session(legacy_projects, "projhash1", "sess-legacy")

    with patch("codetrack.platform.home_roots", return_value=[home]):
        claude, codex = discover_sessions(max_age_hours=24)

    assert len(claude) == 1
    assert claude[0].stem == "sess-legacy"


def test_both_dirs_deduplicated(tmp_path: Path) -> None:
    """Sessions from both new and legacy dirs are returned."""
    home = tmp_path / "home"
    _create_claude_session(home / ".config" / "claude" / "projects", "p1", "sess-new")
    _create_claude_session(home / ".claude" / "projects", "p2", "sess-old")

    with patch("codetrack.platform.home_roots", return_value=[home]):
        claude, _ = discover_sessions(max_age_hours=24)

    assert len(claude) == 2


def test_old_sessions_excluded(tmp_path: Path) -> None:
    """Sessions older than max_age_hours are excluded."""
    home = tmp_path / "home"
    config_projects = home / ".config" / "claude" / "projects"
    f = _create_claude_session(config_projects, "p1", "old-session")
    # Set mtime to 48 hours ago
    old_time = time.time() - 48 * 3600
    import os
    os.utime(f, (old_time, old_time))

    with patch("codetrack.platform.home_roots", return_value=[home]):
        claude, _ = discover_sessions(max_age_hours=24)

    assert len(claude) == 0


def test_codex_session_discovery(tmp_path: Path) -> None:
    """Discovers Codex sessions."""
    home = tmp_path / "home"
    codex_dir = home / ".codex" / "sessions"
    codex_dir.mkdir(parents=True)
    (codex_dir / "session1.jsonl").write_text("{}\n")

    with patch("codetrack.platform.home_roots", return_value=[home]):
        _, codex = discover_sessions(max_age_hours=24)

    assert len(codex) == 1
