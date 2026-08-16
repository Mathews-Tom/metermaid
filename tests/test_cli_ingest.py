"""Fixture-backed smoke tests for the M3 `ingest`/`watch`/`status`/`doctor` CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from metermaid.cli import _positive_interval, _watch_loop, main
from metermaid.state import load_or_create_secret, resolve_state_paths
from metermaid.store import EventStore

_CODEX_RECORD = (
    b'{"type":"event_msg","timestamp":"2026-08-16T00:00:00Z",'
    b'"payload":{"type":"token_count","info":{"total_token_usage":'
    b'{"input_tokens":100,"output_tokens":20}}}}\n'
)


def _seed_codex_source(home: Path) -> None:
    """Place one fixture-backed Codex record where discovery expects it."""
    sessions_dir = home / ".codex" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "rollout-1.jsonl").write_bytes(_CODEX_RECORD)


def _use_fixture_home(monkeypatch: MonkeyPatch, home: Path) -> None:
    """Confine every documented root to `home`, with no `$CODEX_HOME` override."""
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr("metermaid.discover._platform_home_roots", lambda: (home,))


def test_ingest_command_persists_events_from_the_discovered_fixture_source(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _seed_codex_source(home)
    _use_fixture_home(monkeypatch, home)
    data_dir = tmp_path / "state"
    monkeypatch.setattr(
        "sys.argv", ["metermaid", "ingest", "--data-dir", str(data_dir)]
    )

    main()

    paths = resolve_state_paths(data_dir)
    store = EventStore(paths.database)
    store.initialize()
    assert len(store.events()) == 1
    assert store.events()[0].agent == "codex"


def test_ingest_command_output_is_an_aggregate_count_with_no_raw_path(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _seed_codex_source(home)
    _use_fixture_home(monkeypatch, home)
    data_dir = tmp_path / "state"
    monkeypatch.setattr(
        "sys.argv", ["metermaid", "ingest", "--data-dir", str(data_dir)]
    )

    main()

    out = capsys.readouterr().out
    assert "1 events inserted" in out
    assert str(home) not in out
    assert str(data_dir) not in out


def test_status_command_reports_aggregate_store_and_discovery_counts(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _seed_codex_source(home)
    _use_fixture_home(monkeypatch, home)
    data_dir = tmp_path / "state"
    monkeypatch.setattr(
        "sys.argv", ["metermaid", "ingest", "--data-dir", str(data_dir)]
    )
    main()
    capsys.readouterr()

    monkeypatch.setattr(
        "sys.argv", ["metermaid", "status", "--data-dir", str(data_dir)]
    )
    main()

    out = capsys.readouterr().out
    assert "1 events" in out
    assert "1 sessions" in out
    assert "codex" in out
    assert str(home) not in out


def test_doctor_command_reports_discovery_and_parsed_counts_by_adapter(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _seed_codex_source(home)
    _use_fixture_home(monkeypatch, home)
    data_dir = tmp_path / "state"
    monkeypatch.setattr(
        "sys.argv", ["metermaid", "ingest", "--data-dir", str(data_dir)]
    )
    main()
    capsys.readouterr()

    monkeypatch.setattr(
        "sys.argv", ["metermaid", "doctor", "--data-dir", str(data_dir)]
    )
    main()

    out = capsys.readouterr().out
    assert "codex" in out
    assert "codex.token_count" in out
    assert "parsed" in out
    assert str(home) not in out
    assert str(data_dir) not in out


def test_doctor_command_never_exercises_the_network(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _use_fixture_home(monkeypatch, home)
    data_dir = tmp_path / "state"
    monkeypatch.setattr(
        "sys.argv", ["metermaid", "doctor", "--data-dir", str(data_dir)]
    )

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("doctor must never open a socket")

    monkeypatch.setattr("socket.socket", _forbidden)

    main()


def test_watch_command_polls_ingest_once_per_wakeup_then_stops_on_interrupt(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _seed_codex_source(home)
    _use_fixture_home(monkeypatch, home)
    data_dir = tmp_path / "state"
    monkeypatch.setattr(
        "sys.argv",
        [
            "metermaid",
            "watch",
            "--data-dir",
            str(data_dir),
            "--interval",
            "5",
        ],
    )

    calls: list[float] = []

    def _fake_sleep(seconds: float) -> None:
        calls.append(seconds)
        raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", _fake_sleep)

    main()

    assert calls == [5]
    out = capsys.readouterr().out
    assert "Stopped" in out
    paths = resolve_state_paths(data_dir)
    store = EventStore(paths.database)
    store.initialize()
    assert len(store.events()) == 1


def test_watch_loop_polls_ingest_once_per_cycle_until_interrupted(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _seed_codex_source(home)
    _use_fixture_home(monkeypatch, home)
    paths = resolve_state_paths(tmp_path / "state")
    secret = load_or_create_secret(paths)
    store = EventStore(paths.database)
    store.initialize()

    calls = {"count": 0}

    def _sleep(_seconds: float) -> None:
        calls["count"] += 1
        if calls["count"] >= 2:
            raise KeyboardInterrupt()

    _watch_loop(store, secret, 1, sleep=_sleep)

    assert calls["count"] == 2
    assert len(store.events()) == 1


@pytest.mark.parametrize("bad_value", ["0", "-1", "-10"])
def test_watch_interval_must_be_strictly_positive(bad_value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _positive_interval(bad_value)


def test_watch_interval_accepts_a_positive_value() -> None:
    assert _positive_interval("5") == 5


def test_watch_command_rejects_a_non_positive_interval_via_argparse(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "metermaid",
            "watch",
            "--data-dir",
            str(tmp_path / "state"),
            "--interval",
            "0",
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 2


def test_ingest_command_honors_an_explicit_top_level_data_dir(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """`metermaid --data-dir X ingest` (flag before the subcommand) must
    resolve the v1 store to `X`, not silently fall back to the bare
    default because the `ingest` subparser's own `--data-dir` wasn't
    given."""
    home = tmp_path / "home"
    home.mkdir()
    _seed_codex_source(home)
    _use_fixture_home(monkeypatch, home)
    data_dir = tmp_path / "top-level-override"
    monkeypatch.setattr(
        "sys.argv", ["metermaid", "--data-dir", str(data_dir), "ingest"]
    )

    main()

    paths = resolve_state_paths(data_dir)
    store = EventStore(paths.database)
    store.initialize()
    assert len(store.events()) == 1


def test_subcommand_level_data_dir_overrides_an_explicit_top_level_one(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _seed_codex_source(home)
    _use_fixture_home(monkeypatch, home)
    top_level_dir = tmp_path / "top-level"
    sub_level_dir = tmp_path / "sub-level"
    monkeypatch.setattr(
        "sys.argv",
        [
            "metermaid",
            "--data-dir",
            str(top_level_dir),
            "ingest",
            "--data-dir",
            str(sub_level_dir),
        ],
    )

    main()

    sub_paths = resolve_state_paths(sub_level_dir)
    sub_store = EventStore(sub_paths.database)
    sub_store.initialize()
    assert len(sub_store.events()) == 1
    assert not top_level_dir.exists()


def test_stop_command_never_signals_a_pid_only_clears_a_stale_pid_file(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stale_pid_file = tmp_path / "metermaid.pid"
    stale_pid_file.write_text("999999999")
    monkeypatch.setattr("metermaid.cli.PID_FILE", stale_pid_file)
    monkeypatch.setattr("sys.argv", ["metermaid", "stop"])

    def _forbidden_kill(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("stop must never signal a PID")

    monkeypatch.setattr("os.kill", _forbidden_kill)

    main()

    assert not stale_pid_file.exists()
    out = capsys.readouterr().out
    assert "foreground" in out


def test_stop_command_prints_foreground_guidance_with_no_pid_file(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    absent_pid_file = tmp_path / "no-such-metermaid.pid"
    monkeypatch.setattr("metermaid.cli.PID_FILE", absent_pid_file)
    monkeypatch.setattr("sys.argv", ["metermaid", "stop"])

    main()

    out = capsys.readouterr().out
    assert "foreground" in out
    assert "Removed" not in out
