"""WSL detection, home roots, and session directory discovery."""

from __future__ import annotations

import os
import platform as _platform
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

_DARWIN_BOOT_TIME = re.compile(
    r"\bsec = (?P<seconds>\d+), usec = (?P<microseconds>\d+)\b"
)


def host_booted_at() -> datetime | None:
    """Return the current Darwin boot instant, or no evidence elsewhere.

    A missing platform capability never becomes invented reboot evidence:
    callers retain strict continuity handling when this returns ``None``.
    Darwin uses the system executable independently of the caller's PATH.
    Lookup failures and malformed output remain explicit errors rather
    than silently discarding a broken host capability.
    """

    if _platform.system() != "Darwin":
        return None
    result = subprocess.run(
        ["/usr/sbin/sysctl", "-n", "kern.boottime"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    match = _DARWIN_BOOT_TIME.search(result.stdout)
    if match is None:
        raise RuntimeError("could not parse Darwin host boot time")
    seconds = int(match["seconds"])
    microseconds = int(match["microseconds"])
    return datetime.fromtimestamp(seconds + microseconds / 1_000_000, UTC)


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
