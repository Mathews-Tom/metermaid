"""Contracts for the M4 PR4 CLI cutover: the exact supported command set.

`metermaid --help` must list exactly the seven v1 commands (`ingest`,
`watch`, `status`, `doctor`, `report`, `export`, `import-legacy`). Every
command from the deprecated v0.2 CSV/daemon surface (`stop`, `migrate`,
`hook`, `backfill`, `consolidate`, `mcp`, `heatmap`) and the temporary
M4 PR3 name `export-aggregate` must be rejected by argparse as an
unknown subcommand — never silently accepted, aliased, or routed
anywhere. `export` must remain the restricted, allowlisted-field
aggregate export (no `--format` multiplexer), and none of the
supported commands may ever delete or mutate a file they didn't
create.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from metermaid.cli import main

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "m4"

SUPPORTED_COMMANDS = (
    "ingest",
    "watch",
    "status",
    "doctor",
    "report",
    "export",
    "import-legacy",
)

REMOVED_COMMANDS = (
    "stop",
    "migrate",
    "hook",
    "backfill",
    "consolidate",
    "mcp",
    "heatmap",
    "export-aggregate",
)


def _run(monkeypatch: MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr("sys.argv", ["metermaid", *argv])
    main()


def test_help_lists_exactly_the_seven_supported_commands(
    monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["metermaid", "--help"])

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    match = re.search(r"\{([a-z0-9,\-]+)\}", out)
    assert match is not None, out
    assert tuple(match.group(1).split(",")) == SUPPORTED_COMMANDS


@pytest.mark.parametrize("name", REMOVED_COMMANDS)
def test_deprecated_and_temporary_command_names_are_rejected(
    monkeypatch: MonkeyPatch, name: str
) -> None:
    monkeypatch.setattr("sys.argv", ["metermaid", name])

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 2


def test_export_command_has_no_format_flag(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """`export` is the restricted aggregate export only — the old raw
    CSV/JSON/Markdown/HTML/OTLP multiplexer's `--format` flag must be
    gone, not merely defaulted."""
    monkeypatch.setattr(
        "sys.argv",
        ["metermaid", "export", "--format", "csv", "--out", str(tmp_path / "o")],
    )

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 2


def test_import_legacy_command_still_exists_and_is_explicit(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Legacy history import stays a distinct, explicit command — never
    folded into `ingest` or run implicitly by any other command."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    data_dir = tmp_path / "state"

    _run(
        monkeypatch,
        "import-legacy",
        "--data-dir",
        str(data_dir),
        str(legacy_dir),
    )

    out = capsys.readouterr().out
    assert "Legacy import" in out


def test_supported_commands_never_delete_or_mutate_an_unrelated_user_file(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Running every supported command end to end must never touch a
    file it did not itself create under the v1 state root — including
    an existing legacy v0.2 CSV that `import-legacy` reads from."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr("metermaid.discover._platform_home_roots", lambda: (home,))

    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    legacy_csv = legacy_dir / "legacy-supported.csv"
    legacy_csv.write_bytes((FIXTURE_ROOT / "legacy-supported.csv").read_bytes())
    legacy_before = legacy_csv.read_bytes()

    untouched = tmp_path / "do-not-touch.csv"
    untouched.write_text("some,unrelated,user,file\n1,2,3,4\n")
    before = untouched.read_bytes()
    before_mtime = untouched.stat().st_mtime_ns

    data_dir = tmp_path / "state"
    export_out = tmp_path / "export.json"

    for argv in (
        ("ingest", "--data-dir", str(data_dir)),
        ("status", "--data-dir", str(data_dir)),
        ("doctor", "--data-dir", str(data_dir)),
        ("report", "--data-dir", str(data_dir)),
        ("export", "--data-dir", str(data_dir), "--out", str(export_out)),
        ("import-legacy", "--data-dir", str(data_dir), str(legacy_dir)),
    ):
        _run(monkeypatch, *argv)
        capsys.readouterr()

    assert untouched.read_bytes() == before
    assert untouched.stat().st_mtime_ns == before_mtime
    assert legacy_csv.read_bytes() == legacy_before
    assert {p.name for p in legacy_dir.iterdir()} == {"legacy-supported.csv"}
