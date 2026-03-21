"""WSL detection, home roots, and session directory discovery."""

from __future__ import annotations

import os
import platform as _platform
import time
from pathlib import Path


_IS_WINDOWS = _platform.system() == "Windows"


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def pid_alive(pid: int) -> bool:
    """Check if a PID is running. Cross-platform."""
    try:
        if _IS_WINDOWS:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x100000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _wsl_win_home() -> Path | None:
    mnt = Path("/mnt/c/Users")
    if not mnt.exists():
        return None
    skip = {"Public", "Default", "Default User", "All Users"}
    for d in mnt.iterdir():
        if d.is_dir() and d.name not in skip:
            return d
    return None


def _win_home() -> Path | None:
    if _platform.system() != "Windows":
        return None
    p = os.environ.get("USERPROFILE", "")
    return Path(p) if p else None


def home_roots() -> list[Path]:
    """All home directories to scan (native + WSL Windows-side)."""
    roots = [Path.home()]
    if is_wsl():
        wh = _wsl_win_home()
        if wh:
            roots.append(wh)
    wh = _win_home()
    if wh and wh not in roots:
        roots.append(wh)
    return roots


def claude_project_dirs() -> list[Path]:
    dirs: list[Path] = []
    for home in home_roots():
        for base in [
            home / ".config" / "claude" / "projects",  # v1.0.30+
            home / ".claude" / "projects",              # legacy
        ]:
            if base.exists() and base not in dirs:
                dirs.append(base)
    return dirs


def codex_session_dirs() -> list[Path]:
    dirs: list[Path] = []
    codex_home = os.environ.get("CODEX_HOME", "")
    if codex_home:
        p = Path(codex_home) / "sessions"
        if p.exists():
            dirs.append(p)
    for home in home_roots():
        p = home / ".codex" / "sessions"
        if p.exists() and p not in dirs:
            dirs.append(p)
    return dirs


def discover_sessions(max_age_hours: int = 24) -> tuple[list[Path], list[Path]]:
    """Returns (claude_files, codex_files) modified within max_age_hours.

    Pass max_age_hours=0 to discover all sessions regardless of age.
    """
    cutoff = time.time() - max_age_hours * 3600 if max_age_hours > 0 else 0.0
    claude: list[Path] = []
    codex: list[Path] = []

    for proj_dir in claude_project_dirs():
        try:
            for project in proj_dir.iterdir():
                if not project.is_dir():
                    continue
                for f in project.iterdir():
                    if f.suffix == ".jsonl":
                        try:
                            if f.stat().st_mtime > cutoff:
                                claude.append(f)
                        except OSError:
                            pass
        except OSError:
            pass

    for sess_dir in codex_session_dirs():
        try:
            for f in sess_dir.rglob("*.jsonl"):
                try:
                    if f.stat().st_mtime > cutoff:
                        codex.append(f)
                except OSError:
                    pass
        except OSError:
            pass

    return claude, codex
