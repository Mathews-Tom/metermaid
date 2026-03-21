"""SessionWatcher — poll loop, mtime + hash dedup, cross-platform daemon."""

from __future__ import annotations

import hashlib
import os
import platform as _platform
import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.live import Live
from rich.table import Table

from .csv_io import append_snapshot
from .delta import compute_deltas
from .models import METERMAID_HOME, PID_FILE, SESSIONS_DIR, Snapshot
from .pricing import stamp_cost
from .parsers import parse_claude_transcript, parse_codex_session
from .platform import claude_project_dirs, codex_session_dirs, discover_sessions, is_wsl

_IS_WINDOWS = _platform.system() == "Windows"
console = Console()


def snap_hash(snap: Snapshot) -> str:
    key = f"{snap.provider}:{snap.session_id}:{snap.tokens_in}:{snap.tokens_out}:{snap.ctx_tokens}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


_MAX_ROWS = 10
_STALE_MINUTES = 5


def _live_table(snapshots: dict[str, Snapshot], last_change: dict[str, datetime],
                polls: int) -> Table:
    now = datetime.now()
    # Sort all sessions by last change time (most recent first)
    ranked = []
    for s in snapshots.values():
        changed = last_change.get(s.session_id, now)
        mins = (now - changed).total_seconds() / 60
        ranked.append((s, changed, mins))
    ranked.sort(key=lambda x: x[1], reverse=True)

    n_active = sum(1 for _, _, m in ranked if m < _STALE_MINUTES)
    total = len(ranked)
    t = Table(title=f"metermaid watcher — {n_active} active, {total} tracked (poll #{polls})")
    t.add_column("Updated"); t.add_column("Session", style="cyan")
    t.add_column("Provider"); t.add_column("Model")
    t.add_column("Tok In", justify="right"); t.add_column("Tok Out", justify="right")
    t.add_column("Cost", justify="right")

    for s, changed, mins in ranked[:_MAX_ROWS]:
        ts = changed.strftime("%H:%M:%S")
        cost = f"${s.cost_usd:.3f}" if s.cost_usd > 0 else "—"
        if mins < _STALE_MINUTES:
            t.add_row(f"[bold]{ts}[/bold]", s.session_id, s.provider, s.model[:22],
                      f"{s.tokens_in:,}", f"{s.tokens_out:,}", f"[green]{cost}[/green]")
        else:
            age = f"{mins:.0f}m ago"
            t.add_row(f"[dim]{ts} ({age})[/dim]", f"[dim]{s.session_id}[/dim]",
                      f"[dim]{s.provider}[/dim]", f"[dim]{s.model[:22]}[/dim]",
                      f"[dim]{s.tokens_in:,}[/dim]", f"[dim]{s.tokens_out:,}[/dim]",
                      f"[dim]{cost}[/dim]")
    if total > _MAX_ROWS:
        t.add_row(f"[dim]... +{total - _MAX_ROWS} more[/dim]", "", "", "", "", "", "")
    if not snapshots:
        t.add_row("[dim]—[/dim]", "[dim]waiting...[/dim]", "", "", "", "", "")
    return t


class SessionWatcher:
    def __init__(self, sessions_dir: Path = SESSIONS_DIR, interval: int = 10) -> None:
        self.sessions_dir = sessions_dir
        self.interval = interval
        self._hashes: dict[str, str] = {}
        self._mtimes: dict[str, float] = {}
        self._snaps: dict[str, Snapshot] = {}
        self._last_change: dict[str, datetime] = {}
        self._running = False
        self._stop = threading.Event()

    def run(self, daemon: bool = False) -> None:
        if daemon:
            self._daemonize(); return
        self._run_loop()

    def _run_loop(self) -> None:
        self._running = True
        signal.signal(signal.SIGTERM, self._signal)
        signal.signal(signal.SIGINT, self._signal)
        self._write_pid()
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        c_dirs, x_dirs = claude_project_dirs(), codex_session_dirs()
        console.print(f"[bold]metermaid[/bold] watching (interval={self.interval}s)")
        console.print(f"  Claude: {c_dirs or ['(none)']}")
        console.print(f"  Codex:  {x_dirs or ['(none)']}")
        if is_wsl():
            console.print("  [yellow]WSL — scanning Windows-side paths[/yellow]")
        try:
            if sys.stdout.isatty():
                self._loop_live()
            else:
                self._loop_plain()
        finally:
            PID_FILE.unlink(missing_ok=True)

    def _sleep(self) -> None:
        """Sleep for interval, but wake immediately on stop signal."""
        self._stop.wait(timeout=self.interval)

    def _loop_live(self) -> None:
        n = 0
        lc = self._last_change
        with Live(_live_table(self._snaps, lc, 0), console=console, refresh_per_second=1) as live:
            while self._running:
                n += 1; self._poll()
                live.update(_live_table(self._snaps, lc, n))
                self._sleep()

    def _loop_plain(self) -> None:
        while self._running:
            recorded = self._poll()
            if recorded:
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"[{ts}] Recorded {recorded} snapshot(s)", flush=True)
            self._sleep()

    def _poll(self) -> int:
        claude_files, codex_files = discover_sessions()
        recorded = 0
        for files, parser in [(claude_files, parse_claude_transcript),
                               (codex_files, parse_codex_session)]:
            for f in files:
                s = self._check(f, parser)
                if s:
                    s = stamp_cost(compute_deltas(s))
                    append_snapshot(s, self.sessions_dir)
                    self._snaps[s.session_id] = s
                    self._last_change[s.session_id] = datetime.now()
                    recorded += 1
        return recorded

    def _check(self, path: Path, parser: Callable[[Path], Snapshot | None]) -> Snapshot | None:
        key = str(path)
        try:
            mt = path.stat().st_mtime
        except OSError:
            return None
        if key in self._mtimes and mt <= self._mtimes[key]:
            return None
        self._mtimes[key] = mt
        snap = parser(path)
        if snap is None:
            return None
        h = snap_hash(snap)
        if self._hashes.get(snap.session_id) == h:
            return None
        self._hashes[snap.session_id] = h
        return snap

    def _daemonize(self) -> None:
        log_path = METERMAID_HOME / "metermaid.log"
        METERMAID_HOME.mkdir(parents=True, exist_ok=True)
        if _IS_WINDOWS:
            self._daemon_win(log_path)
        else:
            self._daemon_unix(log_path)

    def _daemon_unix(self, log_path: Path) -> None:
        pid = os.fork()
        if pid > 0:
            console.print(f"Watcher started (PID {pid})"); sys.exit(0)
        os.setsid()
        devnull = os.open(os.devnull, os.O_RDWR); os.dup2(devnull, 0)
        log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(log_fd, 1); os.dup2(log_fd, 2)
        self._run_loop(); sys.exit(0)

    def _daemon_win(self, log_path: Path) -> None:
        cmd = [sys.executable, "-m", "metermaid", "watch",
               "--data-dir", str(self.sessions_dir), "--interval", str(self.interval)]
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        with open(log_path, "a") as lf:
            proc = subprocess.Popen(cmd, stdout=lf, stderr=lf,
                                    stdin=subprocess.DEVNULL, creationflags=flags)
        console.print(f"Watcher started (PID {proc.pid})")

    def _signal(self, *_: object) -> None:
        self._running = False
        self._stop.set()

    def _write_pid(self) -> None:
        METERMAID_HOME.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()))
