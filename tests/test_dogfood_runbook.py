"""Guards for the M5 owner dogfood runbook and its local ledger.

Covers the procedural content required by `.docs/DEVELOPMENT_PLAN.md`
("M5 — Active personal dogfood and issue-driven updates") and DM-004,
and the structural guarantee that the local ledger the runbook
prescribes can never be committed alongside it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
RUNBOOK = ROOT / "docs" / "dogfood-runbook.md"
LEDGER = ROOT / ".docs" / "m5-dogfood-ledger.md"


def _normalized(text: str) -> str:
    """Collapse markdown line-wrap whitespace for prose substring checks."""
    return " ".join(text.split())


REQUIRED_COMMANDS = (
    "metermaid watch",
    "metermaid status",
    "metermaid doctor",
    "metermaid report",
)

FULL_GATE = (
    "uv run ruff check . && uv run ruff format --check . "
    "&& uv run mypy src tests && uv run pytest"
)

FORBIDDEN_LEDGER_CONTENT = (
    "transcript",
    "prompt",
    "tool argument",
    "path",
    "session id",
    "event payload",
    "identifying aggregate",
)

# Absolute path prefixes that would leak a real machine's directory
# layout if they ever appeared in the tracked runbook.
LEAKY_PATH_PREFIXES = ("/Users/", "/home/", "C:\\Users\\")


def test_runbook_is_present_and_procedural() -> None:
    assert RUNBOOK.exists()
    text = RUNBOOK.read_text()
    assert text.startswith("# Owner dogfood runbook")


@pytest.mark.parametrize("command", REQUIRED_COMMANDS)
def test_runbook_documents_every_required_command(command: str) -> None:
    assert command in RUNBOOK.read_text()


def test_runbook_requires_foreground_continuous_watch() -> None:
    text = _normalized(RUNBOOK.read_text())
    assert "foreground" in text
    assert "never run backgrounded" in text


def test_runbook_defines_the_fourteen_day_cadence() -> None:
    text = _normalized(RUNBOOK.read_text())
    assert "14 consecutive calendar days" in text
    assert "14 calendar days" in text


def test_runbook_requires_triage_after_every_source_change() -> None:
    text = _normalized(RUNBOOK.read_text())
    assert "After each observed source change" in text
    assert "triage" in text.lower()


def test_runbook_requires_redacted_reproduction_regression_test_and_full_gate() -> None:
    text = RUNBOOK.read_text()
    assert "Redacted reproduction first" in text
    assert "Regression test" in text
    assert FULL_GATE in text


def test_runbook_defines_all_three_dispositions() -> None:
    text = RUNBOOK.read_text()
    for disposition in ("**STABLE**", "**ITERATE**", "**STOP**"):
        assert disposition in text


def test_runbook_never_claims_the_outcome_or_a_start_date() -> None:
    text = _normalized(RUNBOOK.read_text())
    assert "only after" in text
    assert "never on the day the period starts" in text
    assert "asserts nothing about whether a 14-day period" in text


def test_runbook_states_the_ledger_forbidden_content() -> None:
    text = _normalized(RUNBOOK.read_text())
    for forbidden in FORBIDDEN_LEDGER_CONTENT:
        assert forbidden in text


def test_runbook_names_the_ignored_ledger_path() -> None:
    text = RUNBOOK.read_text()
    assert ".docs/m5-dogfood-ledger.md" in text
    assert "never be `git add`ed, committed, or pushed" in text


def test_runbook_contains_no_leaky_absolute_paths() -> None:
    text = RUNBOOK.read_text()
    for prefix in LEAKY_PATH_PREFIXES:
        assert prefix not in text


def test_ledger_template_exists_locally_and_carries_no_dates_or_dispositions() -> None:
    """The local template is not itself a persisted run: no filled-in
    date or disposition should ever be committed alongside it."""
    if not LEDGER.exists():
        pytest.skip("local ledger template not present in this checkout")
    text = LEDGER.read_text()
    assert "<start-date>" in text
    assert "<STABLE | ITERATE | STOP>" in text


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
    if not LEDGER.exists():
        pytest.skip("local ledger template not present in this checkout")
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(LEDGER)],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    assert result.returncode != 0
