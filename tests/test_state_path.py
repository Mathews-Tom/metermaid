"""State-root and local-secret contracts for Metermaid v1."""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

from pytest import MonkeyPatch

from metermaid.state import load_or_create_secret, resolve_state_paths


def test_bare_default_state_path_uses_home_metermaid(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    paths = resolve_state_paths()

    assert paths.database == tmp_path / ".metermaid" / "metermaid.sqlite3"
    assert paths.secret.parent == tmp_path / ".metermaid"


def test_local_secret_is_stable_and_owner_readable_only(tmp_path: Path) -> None:
    paths = resolve_state_paths(tmp_path / "isolated-state")

    first = load_or_create_secret(paths)

    assert first == load_or_create_secret(paths)
    assert len(first) == 32
    assert stat.S_IMODE(paths.secret.stat().st_mode) == 0o600


def test_module_help_opens_no_socket() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import runpy, socket, sys; "
                "socket.socket = lambda *args, **kwargs: (_ for _ in ()).throw("
                "AssertionError('socket attempted')); "
                "sys.argv = ['metermaid', '--help']; runpy.run_module("
                "'metermaid', run_name='__main__')"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
