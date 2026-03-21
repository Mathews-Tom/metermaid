"""Backfill — one-shot import of historical sessions into metermaid."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn

from .csv_io import append_snapshot, session_csv_path
from .models import SESSIONS_DIR, Snapshot
from .parsers import parse_claude_transcript, parse_codex_session
from .pricing import stamp_cost
from .platform import discover_sessions


@dataclass
class BackfillResult:
    found: int = 0
    imported: int = 0
    already_tracked: int = 0
    no_data: int = 0


def _mtime_iso(path: Path) -> str:
    mt = path.stat().st_mtime
    return datetime.fromtimestamp(mt).isoformat(timespec="seconds")


def backfill(
    sessions_dir: Path = SESSIONS_DIR,
    since_hours: int = 0,
    dry_run: bool = False,
    force: bool = False,
) -> BackfillResult:
    """Scan all sessions and import those not yet tracked."""
    claude_files, codex_files = discover_sessions(max_age_hours=since_hours)
    all_files: list[tuple[Path, Callable[[Path], Snapshot | None]]] = [
        (p, parse_claude_transcript) for p in claude_files
    ] + [
        (p, parse_codex_session) for p in codex_files
    ]

    r = BackfillResult(found=len(all_files))

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("[dim]{task.fields[status]}"),
        transient=True,
    ) as progress:
        task = progress.add_task(
            "Backfilling" if not dry_run else "Scanning (dry run)",
            total=r.found, status="",
        )
        for path, parser in all_files:
            reason = _backfill_one(path, parser, sessions_dir, dry_run, force)
            if reason == "imported":
                r.imported += 1
                progress.update(task, advance=1, status=f"{r.imported} imported")
            elif reason == "exists":
                r.already_tracked += 1
                progress.update(task, advance=1)
            else:
                r.no_data += 1
                progress.update(task, advance=1)

    return r


def _backfill_one(
    path: Path,
    parser: Callable[[Path], Snapshot | None],
    sessions_dir: Path,
    dry_run: bool,
    force: bool = False,
) -> str:
    """Parse and import a single session. Returns reason string."""
    snap = parser(path)
    if snap is None:
        return "no_data"

    csv_path = session_csv_path(snap, sessions_dir)
    if csv_path.exists() and not force:
        return "exists"

    snap = stamp_cost(replace(snap, timestamp=_mtime_iso(path), source="backfill"))
    if not dry_run:
        if force and csv_path.exists():
            csv_path.unlink()
        append_snapshot(snap, sessions_dir)
    return "imported"
