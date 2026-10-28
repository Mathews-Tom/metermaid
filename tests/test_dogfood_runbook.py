"""Git privacy guards for the local dogfood evidence ledger."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEDGER = ROOT / ".docs" / "m5-dogfood-ledger.md"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_docs_directory_is_git_ignored() -> None:
    """Structural guard: the local ledger directory must be excluded by
    `.gitignore`, independent of whether the ledger file exists yet."""
    probe = ROOT / ".docs" / "m5-dogfood-ledger.md"
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(probe)],
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_ledger_is_not_tracked_by_git() -> None:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(LEDGER)],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    assert result.returncode != 0
