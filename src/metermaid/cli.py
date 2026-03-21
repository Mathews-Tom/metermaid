"""CLI entry point — argparse, subcommands, dispatches to other modules."""

from __future__ import annotations

import argparse
import csv
import os
import signal
import sys
import time
from pathlib import Path

from rich.console import Console

from .backfill import backfill
from .compare import provider_comparison
from .consolidate import aggregate, write_aggregate_csv
from .csv_io import read_all_snapshots
from .hook import handle_claude_hook
from .models import CSV_HEADERS, METERMAID_HOME, DEFAULT_INTERVAL, PID_FILE, SESSIONS_DIR
from .platform import discover_sessions, is_wsl, pid_alive
from .report import filter_rows, report, session_table
from .watcher import SessionWatcher

console = Console()


def _cmd_watch(args: argparse.Namespace) -> None:
    SessionWatcher(args.data_dir, args.interval).run(daemon=args.daemon)


def _cmd_stop(args: argparse.Namespace) -> None:
    if not PID_FILE.exists():
        console.print("[yellow]No watcher running[/yellow]"); return
    pid = int(PID_FILE.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        console.print(f"[green]Stopped[/green] (PID {pid})")
    except (OSError, ProcessLookupError):
        console.print(f"[yellow]PID {pid} stale[/yellow]")
        PID_FILE.unlink(missing_ok=True)


def _cmd_status(args: argparse.Namespace) -> None:
    import platform as _platform
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        if pid_alive(pid):
            console.print(f"Watcher: [green]running[/green] (PID {pid})")
        else:
            console.print("Watcher: [yellow]stopped (stale PID)[/yellow]")
    else:
        console.print("Watcher: [dim]not running[/dim]")

    from rich.table import Table
    claude, codex = discover_sessions(max_age_hours=1)
    t = Table(title="Active sessions (last 1h)", box=None)
    t.add_column("Session", style="cyan"); t.add_column("Provider")
    t.add_column("Age"); t.add_column("Project", style="dim")
    for s in claude[:10]:
        age = (time.time() - s.stat().st_mtime) / 60
        t.add_row(s.stem[:12], "claude", f"{age:.0f}m ago", s.parent.name[:25])
    for s in codex[:10]:
        age = (time.time() - s.stat().st_mtime) / 60
        t.add_row(s.stem[:12], "codex", f"{age:.0f}m ago", "")
    if not claude and not codex:
        t.add_row("[dim]none[/dim]", "", "", "")
    console.print(t)
    n = len(list(args.data_dir.glob("*.csv"))) if args.data_dir.exists() else 0
    console.print(f"\nPlatform: {_platform.system()}" + (" (WSL)" if is_wsl() else ""))
    console.print(f"Data dir: {args.data_dir} ({n} session files)")


def _cmd_hook(args: argparse.Namespace) -> None:
    raw = sys.stdin.read()
    if raw.strip():
        handle_claude_hook(raw, args.data_dir)


def _cmd_report(args: argparse.Namespace) -> None:
    all_rows = read_all_snapshots(args.data_dir)
    filtered = filter_rows(
        all_rows, window=args.window, session=args.session, provider=args.provider
    )
    parts = [x for x in [args.window, args.provider,
             f"session {args.session}" if args.session else None] if x]
    console.rule(f"metermaid ({' | '.join(parts) or 'all time'})")
    report(filtered, all_rows)
    session_table(filtered)


def _cmd_export(args: argparse.Namespace) -> None:
    rows = read_all_snapshots(args.data_dir)
    filtered = filter_rows(
        rows, window=args.window, session=args.session, provider=args.provider
    )
    out = Path(args.out)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        w.writeheader()
        w.writerows(filtered)
    console.print(f"Exported [bold]{len(filtered)}[/bold] rows -> {out}")


def _cmd_backfill(args: argparse.Namespace) -> None:
    r = backfill(
        sessions_dir=args.data_dir, since_hours=args.since or 0,
        dry_run=args.dry_run, force=args.force,
    )
    mode = " [dim](dry run)[/dim]" if args.dry_run else ""
    console.print(
        f"Backfill{mode}: [bold]{r.found}[/bold] found, "
        f"[green]{r.imported}[/green] imported, "
        f"[dim]{r.already_tracked}[/dim] already tracked, "
        f"[dim]{r.no_data}[/dim] no usage data"
    )


def _cmd_consolidate(args: argparse.Namespace) -> None:
    rows = read_all_snapshots(args.data_dir)
    if args.since:
        rows = [r for r in rows if r.get("timestamp", "") >= args.since]
    if not rows:
        console.print("[yellow]No data found.[/yellow]"); return
    agg = aggregate(rows, args.window)
    if args.summary:
        provider_comparison(agg)
        return
    out_dir = METERMAID_HOME / "data" / f"{args.window}ly"
    latest = max(r["window"] for r in agg)
    out_path = out_dir / f"{latest}.csv"
    write_aggregate_csv(agg, out_path)
    console.print(f"Wrote [bold]{len(agg)}[/bold] rows -> {out_path}")


def _cmd_migrate(args: argparse.Namespace) -> None:
    import shutil
    old = Path.home() / ".codetrack"
    new = METERMAID_HOME
    if not old.exists():
        console.print("[dim]No ~/.codetrack found — nothing to migrate.[/dim]")
        return
    if new.exists() and any(new.iterdir()):
        console.print("[yellow]~/.metermaid already exists. Skipping.[/yellow]")
        return
    new.mkdir(parents=True, exist_ok=True)
    copied = 0
    for sub in ("sessions", "state"):
        src = old / sub
        if src.exists():
            shutil.copytree(src, new / sub, dirs_exist_ok=True)
            copied += len(list(src.iterdir()))
    console.print(f"[green]Migrated[/green] {copied} files from ~/.codetrack -> ~/.metermaid")


def main() -> None:
    p = argparse.ArgumentParser(
        prog="metermaid",
        description="Session metrics watcher for Claude Code & Codex CLI",
    )
    p.add_argument("--data-dir", type=Path, default=SESSIONS_DIR)
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("watch")
    w.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    w.add_argument("--daemon", action="store_true")
    w.set_defaults(func=_cmd_watch)

    sub.add_parser("stop").set_defaults(func=_cmd_stop)
    sub.add_parser("status").set_defaults(func=_cmd_status)
    sub.add_parser("migrate").set_defaults(func=_cmd_migrate)

    h = sub.add_parser("hook")
    h.add_argument("provider", choices=["claude"])
    h.set_defaults(func=_cmd_hook)

    bf = sub.add_parser("backfill")
    bf.add_argument("--since", type=int, default=0, metavar="HOURS")
    bf.add_argument("--dry-run", action="store_true")
    bf.add_argument("--force", action="store_true")
    bf.set_defaults(func=_cmd_backfill)

    con = sub.add_parser("consolidate")
    con.add_argument("--window", choices=["day", "week", "month"], default="day")
    con.add_argument("--since", type=str, metavar="ISO_DATE")
    con.add_argument("--summary", action="store_true")
    con.set_defaults(func=_cmd_consolidate)

    for name, func in [("report", _cmd_report), ("export", _cmd_export)]:
        s = sub.add_parser(name)
        s.add_argument("--window"); s.add_argument("--session")
        s.add_argument("--provider", choices=["claude", "codex"])
        if name == "export": s.add_argument("--out", default="metermaid_export.csv")
        s.set_defaults(func=func)

    args = p.parse_args()
    args.func(args)
