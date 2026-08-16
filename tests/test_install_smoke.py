"""Clean-install fixture smoke test for M5 PR1's install-readiness gate.

Builds the real `metermaid-cli` wheel with `uv build`, installs it into a
brand-new `uv venv`, and drives the installed `metermaid` console script
against fixtures from a working directory outside the checkout with an
isolated `HOME` and `--data-dir`. This proves the seven documented
commands work end to end for a genuinely fresh install — never a
source-tree `import metermaid` — and that installed state never leaks
into (or reads from) the developer's real `~/.metermaid` or session
directories.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("uv") is None,
    reason="uv is required to build the wheel and create the install venv",
)

ROOT = Path(__file__).parent.parent
LEGACY_FIXTURE = ROOT / "tests" / "fixtures" / "m4" / "legacy-supported.csv"

_CLAUDE_RECORD = (
    b'{"type":"assistant","timestamp":"2026-08-16T00:00:00Z","sessionId":"s1",'
    b'"message":{"model":"claude-opus-4","usage":{"input_tokens":100,'
    b'"output_tokens":50,"cache_read_input_tokens":10,'
    b'"cache_creation_input_tokens":5}}}\n'
)

SEVEN_COMMANDS = (
    "ingest",
    "watch",
    "status",
    "doctor",
    "report",
    "export",
    "import-legacy",
)


@pytest.fixture(scope="module")
def installed_metermaid(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the wheel, install it into a fresh venv, return its console script."""
    work = tmp_path_factory.mktemp("install-smoke")
    dist_dir = work / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = sorted(dist_dir.glob("metermaid_cli-*.whl"))
    assert len(wheels) == 1, f"expected exactly one built wheel, found {wheels}"

    venv_dir = work / "venv"
    subprocess.run(
        ["uv", "venv", str(venv_dir)], check=True, capture_output=True, text=True
    )
    bin_dir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(wheels[0])],
        check=True,
        capture_output=True,
        text=True,
    )

    console_script = bin_dir / ("metermaid.exe" if os.name == "nt" else "metermaid")
    assert console_script.exists(), "the metermaid console script was not installed"

    imported_from = subprocess.run(
        [
            str(python),
            "-c",
            "import metermaid, pathlib; "
            "print(pathlib.Path(metermaid.__file__).resolve())",
        ],
        cwd=str(work),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert Path(imported_from).is_relative_to(venv_dir), (
        f"installed `metermaid` resolved outside the venv: {imported_from}"
    )
    assert not Path(imported_from).is_relative_to(ROOT), (
        f"installed `metermaid` resolved into the source checkout: {imported_from}"
    )

    return console_script


@pytest.fixture
def isolated_environment(tmp_path: Path) -> Iterator[dict[str, str]]:
    """An isolated HOME (seeded with one fixture record) plus PATH only.

    The real developer environment (`~/.metermaid`, `~/.claude`, `CODEX_HOME`,
    `PYTHONPATH`, etc.) must never be visible to the installed console script.
    """
    home = tmp_path / "home"
    (home / ".claude" / "projects" / "proj1").mkdir(parents=True)
    (home / ".claude" / "projects" / "proj1" / "session1.jsonl").write_bytes(
        _CLAUDE_RECORD
    )
    yield {"HOME": str(home), "PATH": os.environ.get("PATH", "")}


def _run(
    metermaid: Path,
    env: dict[str, str],
    cwd: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(metermaid), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_help_lists_exactly_the_seven_supported_commands(
    installed_metermaid: Path, isolated_environment: dict[str, str], tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    result = _run(installed_metermaid, isolated_environment, outside, "--help")

    assert result.returncode == 0
    for command in SEVEN_COMMANDS:
        assert command in result.stdout


def test_seven_commands_run_end_to_end_under_an_isolated_data_dir(
    installed_metermaid: Path, isolated_environment: dict[str, str], tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    data_dir = tmp_path / "data"
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    shutil.copyfile(LEGACY_FIXTURE, legacy_dir / LEGACY_FIXTURE.name)
    export_out = tmp_path / "export.json"

    ingest = _run(
        installed_metermaid,
        isolated_environment,
        outside,
        "--data-dir",
        str(data_dir),
        "ingest",
    )
    assert ingest.returncode == 0
    assert "Ingest:" in ingest.stdout
    assert (data_dir / "metermaid.sqlite3").exists()
    assert (data_dir / "metermaid.secret").exists()

    status = _run(
        installed_metermaid,
        isolated_environment,
        outside,
        "--data-dir",
        str(data_dir),
        "status",
    )
    assert status.returncode == 0
    assert "Store:" in status.stdout

    doctor = _run(
        installed_metermaid,
        isolated_environment,
        outside,
        "--data-dir",
        str(data_dir),
        "doctor",
    )
    assert doctor.returncode == 0
    assert "Source discovery" in doctor.stdout

    report = _run(
        installed_metermaid,
        isolated_environment,
        outside,
        "--data-dir",
        str(data_dir),
        "report",
    )
    assert report.returncode == 0
    assert "Observed:" in report.stdout

    export = _run(
        installed_metermaid,
        isolated_environment,
        outside,
        "--data-dir",
        str(data_dir),
        "export",
        "--out",
        str(export_out),
    )
    assert export.returncode == 0
    assert export_out.exists()
    assert '"schema_version": 1' in export_out.read_text()

    import_legacy = _run(
        installed_metermaid,
        isolated_environment,
        outside,
        "--data-dir",
        str(data_dir),
        "import-legacy",
        str(legacy_dir),
    )
    assert import_legacy.returncode == 0
    assert "Legacy import:" in import_legacy.stdout
    assert (
        legacy_dir / LEGACY_FIXTURE.name
    ).read_bytes() == LEGACY_FIXTURE.read_bytes()

    # No real-home or fixture path leaked into any command's stdout.
    for result in (ingest, status, doctor, report, export, import_legacy):
        assert str(isolated_environment["HOME"]) not in result.stdout
        assert str(data_dir) not in result.stdout


def test_watch_command_polls_then_exits_cleanly_on_interrupt(
    installed_metermaid: Path, isolated_environment: dict[str, str], tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    data_dir = tmp_path / "data"

    process = subprocess.Popen(
        [
            str(installed_metermaid),
            "--data-dir",
            str(data_dir),
            "watch",
            "--interval",
            "5",
        ],
        cwd=str(outside),
        env=isolated_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        saw_ingest = False
        while time.monotonic() < deadline:
            line = process.stdout.readline() if process.stdout is not None else ""
            if not line:
                break
            if "Ingest:" in line:
                saw_ingest = True
                break
        assert saw_ingest, "watch never printed an ingest summary before timeout"

        process.send_signal(signal.SIGINT)
        returncode = process.wait(timeout=10)
        remainder = process.stdout.read() if process.stdout is not None else ""
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    assert returncode == 0
    assert "Stopped" in remainder


def test_only_the_isolated_state_root_is_written_never_the_real_metermaid_home(
    installed_metermaid: Path, isolated_environment: dict[str, str], tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    data_dir = tmp_path / "data"
    real_home_state = Path(isolated_environment["HOME"]) / ".metermaid"

    result = _run(
        installed_metermaid,
        isolated_environment,
        outside,
        "--data-dir",
        str(data_dir),
        "ingest",
    )

    assert result.returncode == 0
    assert not real_home_state.exists()
    assert {p.name for p in data_dir.iterdir()} == {
        "metermaid.sqlite3",
        "metermaid.secret",
    }
