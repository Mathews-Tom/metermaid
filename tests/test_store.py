"""Store contracts for Metermaid v1 SQLite persistence."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

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
        "legacy_snapshots",
    } <= tables


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


def test_rejected_transaction_leaves_no_partial_state(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "metermaid.sqlite3")
    store.initialize()
    outcome = ParseOutcome(agent="codex", discriminator="response", kind="parsed")

    with pytest.raises(ValueError, match="duplicate event identifiers"):
        store.commit_ingest([_event(), _event()], [outcome], _watermark())

    assert store.events() == []
    assert store.diagnostics() == []
    assert store.watermark("d" * 64) is None


def test_open_store_creates_only_v1_state_files(tmp_path: Path) -> None:
    state_root = tmp_path / "metermaid-state"

    store = open_store(state_root)

    assert store.events() == []
    assert (state_root / "metermaid.sqlite3").is_file()
    assert (state_root / "metermaid.secret").is_file()
