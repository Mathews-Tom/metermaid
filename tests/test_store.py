"""Store contracts for Metermaid v1 SQLite persistence."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from metermaid.domain import FileWatermark, NormalizedEvent, ParseOutcome
from metermaid.store import SCHEMA_VERSION, EventStore, open_store


def _event(event_id: str = "a" * 64) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=event_id,
        agent="codex",
        source_session_id="b" * 64,
        project_key="c" * 64,
        occurred_at=datetime(2026, 8, 16, tzinfo=UTC),
        record_kind="usage",
        provenance="codex.message",
    )


def _watermark() -> FileWatermark:
    return FileWatermark(
        source_locator="d" * 64,
        file_identity="e" * 64,
        observed_size=100,
        modified_ns=50,
        complete_offset=80,
    )


def test_initialize_creates_wal_schema(tmp_path: Path) -> None:
    database = tmp_path / "metermaid.sqlite3"
    store = EventStore(database)

    store.initialize()

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "events",
        "file_watermarks",
        "ingest_diagnostics",
        "ingest_diagnostic_records",
        "legacy_snapshots",
    } <= tables


def test_initialize_rolls_back_interrupted_v1_migration(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    database = tmp_path / "metermaid.sqlite3"
    store = EventStore(database)
    apply_v1_schema = EventStore._apply_v1_schema

    def interrupted_migration(connection: sqlite3.Connection) -> None:
        apply_v1_schema(connection)
        raise RuntimeError("interrupted migration")

    with monkeypatch.context() as patch:
        patch.setattr(
            EventStore, "_apply_v1_schema", staticmethod(interrupted_migration)
        )
        with pytest.raises(RuntimeError, match="interrupted migration"):
            store.initialize()

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (0,)
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'events'"
            ).fetchone()
            is None
        )

    store.initialize()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)


def test_atomic_migration_skips_a_step_another_initializer_applied(
    tmp_path: Path,
) -> None:
    database = tmp_path / "metermaid.sqlite3"
    store = EventStore(database)
    store.initialize()
    outcome = ParseOutcome(agent="codex", discriminator="response", kind="parsed")
    store.commit_ingest([], [outcome], None)

    with sqlite3.connect(database) as connection:
        assert (
            EventStore._apply_atomic_migration(
                connection, target_version=5, apply=EventStore._apply_v5_schema
            )
            == 5
        )

    assert store.diagnostics() == [outcome]


def test_commit_is_idempotent_and_preserves_missing_values(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "metermaid.sqlite3")
    store.initialize()
    outcome = ParseOutcome(agent="codex", discriminator="response", kind="parsed")

    assert store.commit_ingest([_event()], [outcome], _watermark()).inserted_events == 1
    assert store.commit_ingest([_event()], [], None).inserted_events == 0

    assert store.events() == [_event()]
    assert store.events()[0].tokens_in is None
    assert store.watermark("d" * 64) == _watermark()
    assert store.diagnostics() == [outcome]


def test_identified_diagnostic_is_idempotent_without_source_text(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "metermaid.sqlite3")
    store.initialize()
    outcome = ParseOutcome(
        agent="codex",
        discriminator="response",
        kind="parsed",
        diagnostic_id="f" * 64,
    )

    assert store.commit_ingest([], [outcome], None).inserted_diagnostics == 1
    assert store.commit_ingest([], [outcome], None).inserted_diagnostics == 0
    assert store.diagnostics() == [
        ParseOutcome(agent="codex", discriminator="response", kind="parsed")
    ]


def test_rejected_transaction_leaves_no_partial_state(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "metermaid.sqlite3")
    store.initialize()
    outcome = ParseOutcome(agent="codex", discriminator="response", kind="parsed")

    with pytest.raises(ValueError, match="duplicate event identifiers"):
        store.commit_ingest([_event(), _event()], [outcome], _watermark())

    assert store.events() == []
    assert store.diagnostics() == []
    assert store.watermark("d" * 64) is None


def _create_v4_database(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE file_watermarks (
                source_locator TEXT PRIMARY KEY,
                file_identity TEXT NOT NULL,
                observed_size INTEGER NOT NULL,
                modified_ns INTEGER NOT NULL,
                complete_offset INTEGER NOT NULL,
                adapter_revision INTEGER NOT NULL DEFAULT 1,
                replay_cutoff INTEGER
            );
            INSERT INTO file_watermarks VALUES (
                'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
                'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
                100, 50, 80, 2, 80
            );
            CREATE TABLE ingest_diagnostics (
                agent TEXT NOT NULL,
                discriminator TEXT NOT NULL,
                kind TEXT NOT NULL,
                count INTEGER NOT NULL CHECK (count > 0),
                PRIMARY KEY (agent, discriminator, kind)
            );
            INSERT INTO ingest_diagnostics VALUES ('claude-code', 'assistant', 'malformed', 9);
            PRAGMA user_version = 4;
            """
        )


def test_initialize_upgrades_v2_watermarks_with_baseline_adapter_revision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "metermaid.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE file_watermarks (
                source_locator TEXT PRIMARY KEY,
                file_identity TEXT NOT NULL,
                observed_size INTEGER NOT NULL,
                modified_ns INTEGER NOT NULL,
                complete_offset INTEGER NOT NULL
            );
            INSERT INTO file_watermarks VALUES (
                'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
                'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
                100, 50, 80
            );
            CREATE TABLE ingest_diagnostics (
                agent TEXT NOT NULL,
                discriminator TEXT NOT NULL,
                kind TEXT NOT NULL,
                count INTEGER NOT NULL CHECK (count > 0),
                PRIMARY KEY (agent, discriminator, kind)
            );
            PRAGMA user_version = 2;
            """
        )

    store = EventStore(database)
    store.initialize()

    assert store.watermark("d" * 64) == replace(_watermark(), diagnostic_rebuild=True)


def test_initialize_upgrades_v4_replay_progress_to_diagnostic_identities(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    database = tmp_path / "metermaid.sqlite3"
    _create_v4_database(database)

    store = EventStore(database)

    apply_v5_schema = EventStore._apply_v5_schema

    def interrupted_migration(connection: sqlite3.Connection) -> None:
        apply_v5_schema(connection)
        raise RuntimeError("interrupted migration")

    with monkeypatch.context() as patch:
        patch.setattr(
            EventStore, "_apply_v5_schema", staticmethod(interrupted_migration)
        )
        with pytest.raises(RuntimeError, match="interrupted migration"):
            store.initialize()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (4,)
        assert connection.execute(
            "SELECT agent, discriminator, kind, count FROM ingest_diagnostics"
        ).fetchall() == [("claude-code", "assistant", "malformed", 9)]
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'ingest_diagnostic_records'"
            ).fetchone()
            is None
        )
        assert "diagnostic_rebuild" not in {
            row[1] for row in connection.execute("PRAGMA table_info(file_watermarks)")
        }

    store.initialize()

    assert store.watermark("d" * 64) == replace(
        _watermark(), adapter_revision=2, diagnostic_rebuild=True
    )
    assert store.diagnostics() == []
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'ingest_diagnostic_records'"
        ).fetchone() == ("ingest_diagnostic_records",)


def test_initialize_recovers_partially_applied_v5_diagnostic_rebuild(
    tmp_path: Path,
) -> None:
    database = tmp_path / "metermaid.sqlite3"
    _create_v4_database(database)
    diagnostic_id = "a" * 64
    with sqlite3.connect(database) as connection:
        connection.executescript(
            f"""
            CREATE TABLE ingest_diagnostic_records (
                diagnostic_id TEXT PRIMARY KEY,
                agent TEXT NOT NULL,
                discriminator TEXT NOT NULL,
                kind TEXT NOT NULL
            );
            INSERT INTO ingest_diagnostic_records VALUES (
                '{diagnostic_id}', 'claude-code', 'assistant', 'malformed'
            );
            ALTER TABLE file_watermarks
            ADD COLUMN diagnostic_rebuild INTEGER NOT NULL DEFAULT 0;
            """
        )

    store = EventStore(database)
    store.initialize()
    result = store.commit_ingest(
        [],
        [
            ParseOutcome(
                agent="claude-code",
                discriminator="assistant",
                kind="malformed",
                diagnostic_id=diagnostic_id,
            )
        ],
        None,
    )

    assert result.inserted_diagnostics == 1
    assert store.diagnostics() == [
        ParseOutcome(
            agent="claude-code",
            discriminator="assistant",
            kind="malformed",
        )
    ]


def test_open_store_creates_only_v1_state_files(tmp_path: Path) -> None:
    state_root = tmp_path / "metermaid-state"

    store = open_store(state_root)

    assert store.events() == []
    assert (state_root / "metermaid.sqlite3").is_file()
    assert (state_root / "metermaid.secret").is_file()
