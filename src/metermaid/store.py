"""SQLite-backed persistence for Metermaid v1 normalized events and isolated legacy history."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .domain import FileWatermark, LegacySnapshot, NormalizedEvent, ParseOutcome
from .state import load_or_create_secret, resolve_state_paths

SCHEMA_VERSION = 5
"""v1 adds ``events``/``file_watermarks``/``ingest_diagnostics`` plus an
empty ``legacy_snapshots`` placeholder table; v2 gives that placeholder its
mapped-value columns for the M4 legacy importer; v3 records per-file adapter
semantic revisions; v4 persisted replay progress; v5 deduplicates diagnostics
by opaque record identity."""


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
    inserted_diagnostics: int


@dataclass(frozen=True, slots=True)
class LegacyCommitResult:
    """Isolated legacy-import commit result — never mixed with event counts."""

    inserted_rows: int


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
            current = version[0]
            if current > SCHEMA_VERSION:
                raise RuntimeError("Metermaid database uses a newer schema version")
            if current < 1:
                current = self._apply_atomic_migration(
                    connection, target_version=1, apply=self._apply_v1_schema
                )
            if current < 2:
                current = self._apply_atomic_migration(
                    connection, target_version=2, apply=self._apply_v2_schema
                )
            if current < 3:
                current = self._apply_atomic_migration(
                    connection, target_version=3, apply=self._apply_v3_schema
                )
            if current < 4:
                current = self._apply_atomic_migration(
                    connection, target_version=4, apply=self._apply_v4_schema
                )
            if current < 5:
                current = self._apply_atomic_migration(
                    connection, target_version=5, apply=self._apply_v5_schema
                )

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
                inserted_diagnostics = 0
                for outcome in outcome_rows:
                    if outcome.diagnostic_id is not None:
                        inserted = connection.execute(
                            """
                            INSERT OR IGNORE INTO ingest_diagnostic_records (
                                diagnostic_id, agent, discriminator, kind
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (
                                outcome.diagnostic_id,
                                outcome.agent,
                                outcome.discriminator,
                                outcome.kind,
                            ),
                        ).rowcount
                        if inserted == 0:
                            continue
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
                    inserted_diagnostics += outcome.count
                if watermark is not None:
                    connection.execute(
                        """
                        INSERT INTO file_watermarks (
                            source_locator, file_identity, observed_size, modified_ns,
                            complete_offset, adapter_revision, diagnostic_rebuild
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(source_locator) DO UPDATE SET
                            file_identity = excluded.file_identity,
                            observed_size = excluded.observed_size,
                            modified_ns = excluded.modified_ns,
                            complete_offset = excluded.complete_offset,
                            adapter_revision = excluded.adapter_revision,
                            diagnostic_rebuild = excluded.diagnostic_rebuild
                        """,
                        (
                            watermark.source_locator,
                            watermark.file_identity,
                            watermark.observed_size,
                            watermark.modified_ns,
                            watermark.complete_offset,
                            watermark.adapter_revision,
                            watermark.diagnostic_rebuild,
                        ),
                    )
        return CommitResult(
            inserted_events=inserted_events, inserted_diagnostics=inserted_diagnostics
        )

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
                          complete_offset, adapter_revision, diagnostic_rebuild
                   FROM file_watermarks WHERE source_locator = ?""",
                (source_locator,),
            ).fetchone()
        if row is None:
            return None
        return FileWatermark(
            source_locator=row[0],
            file_identity=row[1],
            observed_size=row[2],
            modified_ns=row[3],
            complete_offset=row[4],
            adapter_revision=row[5],
            diagnostic_rebuild=bool(row[6]),
        )

    def commit_legacy_import(
        self, snapshots: Iterable[LegacySnapshot]
    ) -> LegacyCommitResult:
        """Atomically store idempotent legacy rows, isolated from event ingest.

        Never touches ``events``, ``file_watermarks``, or
        ``ingest_diagnostics``: legacy history has its own table, its own
        idempotency key, and its own read path (:meth:`legacy_snapshots`).
        """
        rows = tuple(snapshots)
        with self._connection() as connection:
            with connection:
                inserted_rows = sum(
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO legacy_snapshots (
                            legacy_id, source_fingerprint, row_fingerprint,
                            imported_at, timestamp, provider, model,
                            tokens_in, tokens_out, cache_read, cache_write,
                            cost_usd
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        _legacy_values(row),
                    ).rowcount
                    for row in rows
                )
        return LegacyCommitResult(inserted_rows=inserted_rows)

    def legacy_snapshots(self) -> list[LegacySnapshot]:
        """Return imported legacy rows, isolated from normalized events."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT legacy_id, source_fingerprint, row_fingerprint,
                       imported_at, timestamp, provider, model, tokens_in,
                       tokens_out, cache_read, cache_write, cost_usd
                FROM legacy_snapshots ORDER BY imported_at, legacy_id
                """
            ).fetchall()
        return [_legacy_snapshot_from_row(row) for row in rows]

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
        for statement in (
            """
            CREATE TABLE IF NOT EXISTS events (
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
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS file_watermarks (
                source_locator TEXT PRIMARY KEY,
                file_identity TEXT NOT NULL,
                observed_size INTEGER NOT NULL,
                modified_ns INTEGER NOT NULL,
                complete_offset INTEGER NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS ingest_diagnostics (
                agent TEXT NOT NULL,
                discriminator TEXT NOT NULL,
                kind TEXT NOT NULL,
                count INTEGER NOT NULL CHECK (count > 0),
                PRIMARY KEY (agent, discriminator, kind)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS legacy_snapshots (
                legacy_id TEXT PRIMARY KEY,
                source_fingerprint TEXT NOT NULL,
                row_fingerprint TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                UNIQUE(source_fingerprint, row_fingerprint)
            )
            """,
        ):
            connection.execute(statement)

    @staticmethod
    def _apply_v2_schema(connection: sqlite3.Connection) -> None:
        """Give ``legacy_snapshots`` its mapped-value columns.

        No released version has ever written to ``legacy_snapshots`` — it
        was declared by the M2 event-store migration purely as forward
        schema surface for this importer. Recreating it here is therefore
        safe: there is no live data to preserve, and SQLite cannot add a
        ``NOT NULL`` column without a default via ``ALTER TABLE``.
        """
        connection.execute("DROP TABLE IF EXISTS legacy_snapshots")
        connection.execute(
            """
            CREATE TABLE legacy_snapshots (
                legacy_id TEXT PRIMARY KEY,
                source_fingerprint TEXT NOT NULL,
                row_fingerprint TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                tokens_in INTEGER NOT NULL,
                tokens_out INTEGER NOT NULL,
                cache_read INTEGER NOT NULL,
                cache_write INTEGER NOT NULL,
                cost_usd REAL NOT NULL,
                UNIQUE(source_fingerprint, row_fingerprint)
            )
            """
        )

    @staticmethod
    def _apply_atomic_migration(
        connection: sqlite3.Connection,
        *,
        target_version: int,
        apply: Callable[[sqlite3.Connection], None],
    ) -> int:
        """Apply one resumable schema step with its version marker."""
        connection.execute("BEGIN IMMEDIATE")
        try:
            version = connection.execute("PRAGMA user_version").fetchone()
            if version is None:
                raise RuntimeError("Metermaid v1 store could not read schema version")
            current = version[0]
            if not isinstance(current, int):
                raise RuntimeError(
                    "Metermaid v1 store returned an invalid schema version"
                )
            if current > SCHEMA_VERSION:
                raise RuntimeError("Metermaid database uses a newer schema version")
            if current >= target_version:
                connection.rollback()
                return current
            apply(connection)
            connection.execute(f"PRAGMA user_version = {target_version}")
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
            return target_version

    @staticmethod
    def _has_watermark_column(connection: sqlite3.Connection, name: str) -> bool:
        return any(
            row[1] == name
            for row in connection.execute("PRAGMA table_info(file_watermarks)")
        )

    @staticmethod
    def _apply_v3_schema(connection: sqlite3.Connection) -> None:
        """Track parser semantics so corrected mappings can replay safely."""
        if not EventStore._has_watermark_column(connection, "adapter_revision"):
            connection.execute(
                """
                ALTER TABLE file_watermarks
                ADD COLUMN adapter_revision INTEGER NOT NULL DEFAULT 1
                """
            )

    @staticmethod
    def _apply_v4_schema(connection: sqlite3.Connection) -> None:
        """Preserve the published replay-progress schema migration."""
        if not EventStore._has_watermark_column(connection, "replay_cutoff"):
            connection.execute(
                "ALTER TABLE file_watermarks ADD COLUMN replay_cutoff INTEGER"
            )

    @staticmethod
    def _apply_v5_schema(connection: sqlite3.Connection) -> None:
        """Rebuild diagnostic counters from local source records after upgrade."""
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ingest_diagnostic_records (
                diagnostic_id TEXT PRIMARY KEY,
                agent TEXT NOT NULL,
                discriminator TEXT NOT NULL,
                kind TEXT NOT NULL
            )
            """
        )
        if not EventStore._has_watermark_column(connection, "diagnostic_rebuild"):
            connection.execute(
                """
                ALTER TABLE file_watermarks
                ADD COLUMN diagnostic_rebuild INTEGER NOT NULL DEFAULT 0
                """
            )
        connection.execute("DELETE FROM ingest_diagnostic_records")
        connection.execute("DELETE FROM ingest_diagnostics")
        connection.execute("UPDATE file_watermarks SET diagnostic_rebuild = 1")


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


def _legacy_values(row: LegacySnapshot) -> tuple[object, ...]:
    return (
        row.legacy_id,
        row.source_fingerprint,
        row.row_fingerprint,
        row.imported_at.isoformat(),
        row.timestamp,
        row.provider,
        row.model,
        row.tokens_in,
        row.tokens_out,
        row.cache_read,
        row.cache_write,
        row.cost_usd,
    )


def _legacy_snapshot_from_row(row: sqlite3.Row) -> LegacySnapshot:
    return LegacySnapshot(
        legacy_id=row["legacy_id"],
        source_fingerprint=row["source_fingerprint"],
        row_fingerprint=row["row_fingerprint"],
        imported_at=datetime.fromisoformat(row["imported_at"]),
        timestamp=row["timestamp"],
        provider=row["provider"],
        model=row["model"],
        tokens_in=row["tokens_in"],
        tokens_out=row["tokens_out"],
        cache_read=row["cache_read"],
        cache_write=row["cache_write"],
        cost_usd=row["cost_usd"],
    )
