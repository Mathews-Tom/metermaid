"""WSL detection, home roots, and session directory discovery."""

from __future__ import annotations

import os
import platform as _platform
from pathlib import Path


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
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
