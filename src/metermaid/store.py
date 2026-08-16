"""SQLite-backed persistence for Metermaid v1 normalized events."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .domain import FileWatermark, NormalizedEvent, ParseOutcome
from .state import load_or_create_secret, resolve_state_paths

SCHEMA_VERSION = 1


def open_store(data_dir: Path | None = None) -> EventStore:
    """Initialize and return the v1 store under its explicit state root."""
    paths = resolve_state_paths(data_dir)
    load_or_create_secret(paths)
    store = EventStore(paths.database)
    store.initialize()
    return store


@dataclass(frozen=True, slots=True)
class CommitResult:
    inserted_events: int


class EventStore:
    """Own the local v1 schema and its atomic ingest transaction."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def initialize(self) -> None:
        """Create the v1 database and apply all known schema migrations."""
        self._database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._connection() as connection:
            journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            if journal_mode is None or journal_mode[0].lower() != "wal":
                raise RuntimeError("Metermaid v1 store could not enable WAL")
            version = connection.execute("PRAGMA user_version").fetchone()
            if version is None:
                raise RuntimeError("Metermaid v1 store could not read schema version")
            if version[0] > SCHEMA_VERSION:
                raise RuntimeError("Metermaid database uses a newer schema version")
            if version[0] < 1:
                self._apply_v1_schema(connection)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def commit_ingest(
        self,
        events: Iterable[NormalizedEvent],
        outcomes: Iterable[ParseOutcome],
        watermark: FileWatermark | None,
    ) -> CommitResult:
        """Atomically store idempotent events, diagnostics, and optional watermark."""
        event_rows = tuple(events)
        outcome_rows = tuple(outcomes)
        if len({event.event_id for event in event_rows}) != len(event_rows):
            raise ValueError(
                "An ingest transaction cannot contain duplicate event identifiers"
            )

        with self._connection() as connection:
            with connection:
                inserted_events = sum(
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO events (
                            event_id, schema_version, agent, source_session_id, project_key,
                            occurred_at, record_kind, role, model, tokens_in, tokens_out,
                            cache_read, cache_write, reasoning_tokens, provider_cost_usd,
                            safe_tool_category, provenance
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        _event_values(event),
                    ).rowcount
                    for event in event_rows
                )
                for outcome in outcome_rows:
                    connection.execute(
                        """
                        INSERT INTO ingest_diagnostics (agent, discriminator, kind, count)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(agent, discriminator, kind) DO UPDATE
                        SET count = count + excluded.count
                        """,
                        (
                            outcome.agent,
                            outcome.discriminator,
                            outcome.kind,
                            outcome.count,
                        ),
                    )
                if watermark is not None:
                    connection.execute(
                        """
                        INSERT INTO file_watermarks (
                            source_locator, file_identity, observed_size, modified_ns,
                            complete_offset
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(source_locator) DO UPDATE SET
                            file_identity = excluded.file_identity,
                            observed_size = excluded.observed_size,
                            modified_ns = excluded.modified_ns,
                            complete_offset = excluded.complete_offset
                        """,
                        (
                            watermark.source_locator,
                            watermark.file_identity,
                            watermark.observed_size,
                            watermark.modified_ns,
                            watermark.complete_offset,
                        ),
                    )
        return CommitResult(inserted_events=inserted_events)

    def events(self) -> list[NormalizedEvent]:
        """Return canonical events for later report aggregation."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT event_id, schema_version, agent, source_session_id, project_key,
                       occurred_at, record_kind, role, model, tokens_in, tokens_out,
                       cache_read, cache_write, reasoning_tokens, provider_cost_usd,
                       safe_tool_category, provenance
                FROM events ORDER BY occurred_at, event_id
                """
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def diagnostics(self) -> list[ParseOutcome]:
        """Return countable source outcomes without source records."""
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT agent, discriminator, kind, count
                   FROM ingest_diagnostics
                   ORDER BY agent, discriminator, kind"""
            ).fetchall()
        return [ParseOutcome(*row) for row in rows]

    def watermark(self, source_locator: str) -> FileWatermark | None:
        """Read one opaque source watermark."""
        with self._connection() as connection:
            row = connection.execute(
                """SELECT source_locator, file_identity, observed_size, modified_ns,
                          complete_offset
                   FROM file_watermarks WHERE source_locator = ?""",
                (source_locator,),
            ).fetchone()
        return FileWatermark(*row) if row is not None else None

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _apply_v1_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                agent TEXT NOT NULL,
                source_session_id TEXT NOT NULL,
                project_key TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                record_kind TEXT NOT NULL,
                role TEXT,
                model TEXT,
                tokens_in INTEGER,
                tokens_out INTEGER,
                cache_read INTEGER,
                cache_write INTEGER,
                reasoning_tokens INTEGER,
                provider_cost_usd REAL,
                safe_tool_category TEXT,
                provenance TEXT NOT NULL
            );
            CREATE TABLE file_watermarks (
                source_locator TEXT PRIMARY KEY,
                file_identity TEXT NOT NULL,
                observed_size INTEGER NOT NULL,
                modified_ns INTEGER NOT NULL,
                complete_offset INTEGER NOT NULL
            );
            CREATE TABLE ingest_diagnostics (
                agent TEXT NOT NULL,
                discriminator TEXT NOT NULL,
                kind TEXT NOT NULL,
                count INTEGER NOT NULL CHECK (count > 0),
                PRIMARY KEY (agent, discriminator, kind)
            );
            CREATE TABLE legacy_snapshots (
                legacy_id TEXT PRIMARY KEY,
                source_fingerprint TEXT NOT NULL,
                row_fingerprint TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                UNIQUE(source_fingerprint, row_fingerprint)
            );
            """
        )


def _event_values(event: NormalizedEvent) -> tuple[object, ...]:
    return (
        event.event_id,
        event.schema_version,
        event.agent,
        event.source_session_id,
        event.project_key,
        event.occurred_at.isoformat(),
        event.record_kind,
        event.role,
        event.model,
        event.tokens_in,
        event.tokens_out,
        event.cache_read,
        event.cache_write,
        event.reasoning_tokens,
        event.provider_cost_usd,
        event.safe_tool_category,
        event.provenance,
    )


def _event_from_row(row: sqlite3.Row) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=row["event_id"],
        schema_version=row["schema_version"],
        agent=row["agent"],
        source_session_id=row["source_session_id"],
        project_key=row["project_key"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        record_kind=row["record_kind"],
        role=row["role"],
        model=row["model"],
        tokens_in=row["tokens_in"],
        tokens_out=row["tokens_out"],
        cache_read=row["cache_read"],
        cache_write=row["cache_write"],
        reasoning_tokens=row["reasoning_tokens"],
        provider_cost_usd=row["provider_cost_usd"],
        safe_tool_category=row["safe_tool_category"],
        provenance=row["provenance"],
    )
