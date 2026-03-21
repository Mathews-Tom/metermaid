"""CSV I/O — one file per session, no shared read-modify-write.

Layout: ~/.metermaid/sessions/{provider}_{session_id}.csv
Each session file is append-only, written by exactly one process.
Reports scan all session files → merge rows → filter/aggregate.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .models import CSV_HEADERS, SESSIONS_DIR, Snapshot


def session_csv_path(snap: Snapshot, sessions_dir: Path = SESSIONS_DIR) -> Path:
    """Per-session CSV path: {sessions_dir}/{provider}_{session_id}.csv"""
    return sessions_dir / f"{snap.provider}_{snap.session_id}.csv"


def _ensure_csv(path: Path) -> None:
    """Create CSV with headers if it doesn't exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(CSV_HEADERS)


def append_snapshot(snap: Snapshot, sessions_dir: Path = SESSIONS_DIR) -> Path:
    """Append snapshot to its per-session CSV. Returns the file path."""
    path = session_csv_path(snap, sessions_dir)
    _ensure_csv(path)
    with open(path, "a", newline="") as f:
        csv.writer(f).writerow([getattr(snap, h) for h in CSV_HEADERS])
    return path


def read_session(path: Path) -> list[dict[str, str]]:
    """Read all snapshot rows from a single session CSV."""
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def read_all_snapshots(sessions_dir: Path = SESSIONS_DIR) -> list[dict[str, str]]:
    """Scan all session CSVs in the directory and merge rows."""
    if not sessions_dir.exists():
        return []
    rows: list[dict[str, str]] = []
    for f in sorted(sessions_dir.glob("*.csv")):
        rows.extend(read_session(f))
    return rows
