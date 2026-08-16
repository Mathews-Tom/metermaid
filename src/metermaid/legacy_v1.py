"""Isolated import, storage, and reporting for legacy v0.2 CSV snapshots.

This module is the only place that reads a legacy per-session CSV file
written by the v0.2 ``Snapshot``/``csv_io`` layer. It never mutates or
deletes that file, and it never converts a snapshot into a
:class:`metermaid.domain.NormalizedEvent`: legacy history is preserved
as its own :class:`~metermaid.domain.LegacySnapshot` history, rendered
separately, and never combined with current event aggregates.

DM-003 (``.docs/decision-map/tickets/DM-003-legacy-compatibility.md``)
resolves the exact contract implemented here:

* A candidate file's header must equal :data:`metermaid.models.CSV_HEADERS`
  exactly, in order and spelling. Any other header is unsupported and the
  whole file is skipped without importing any of its rows.
* Only eight columns are ever read from a data row: ``timestamp``,
  ``provider``, ``model``, ``tokens_in``, ``tokens_out``, ``cache_read``,
  ``cache_write``, and ``cost_usd``. The raw path, session identifier,
  source provenance, deltas, sidechain metadata, and context/timing
  columns are never read.
* A row whose mapped numeric column cannot be parsed as its documented
  type is malformed. The whole file is rejected — no partial import —
  because DM-003 forbids manufacturing a replacement value.
* The idempotency key combines an opaque source-file fingerprint with a
  canonical fingerprint of the mapped row values, both derived through
  the machine-local secret. Neither the raw file path, nor any raw
  session/source value, is ever persisted.

Every candidate file is opened read-only. Nothing here ever writes to,
truncates, or removes a legacy CSV file.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .domain import LegacySnapshot
from .models import CSV_HEADERS, SESSIONS_DIR
from .state import opaque_identifier
from .store import EventStore

_MAPPED_INT_FIELDS: tuple[str, ...] = (
    "tokens_in",
    "tokens_out",
    "cache_read",
    "cache_write",
)
"""The DM-003 allow-listed cumulative counter columns, parsed as ``int``."""


class MalformedLegacyRowError(ValueError):
    """Raised when a mapped legacy column is not the documented shape."""


@dataclass(frozen=True, slots=True)
class _MappedCells:
    """Raw text of the eight DM-003 allow-listed columns for one row.

    Kept as the exact source cell text — never reformatted — so the row
    fingerprint derived from it is trivially stable across re-imports of
    byte-identical source bytes.
    """

    timestamp: str
    provider: str
    model: str
    tokens_in: str
    tokens_out: str
    cache_read: str
    cache_write: str
    cost_usd: str


def _mapped_cells(row: dict[str, str]) -> _MappedCells:
    return _MappedCells(
        timestamp=row["timestamp"],
        provider=row["provider"],
        model=row["model"],
        tokens_in=row["tokens_in"],
        tokens_out=row["tokens_out"],
        cache_read=row["cache_read"],
        cache_write=row["cache_write"],
        cost_usd=row["cost_usd"],
    )


def _validate_numeric_cells(cells: _MappedCells) -> None:
    for field_name in _MAPPED_INT_FIELDS:
        value = getattr(cells, field_name)
        try:
            int(value)
        except ValueError as exc:
            raise MalformedLegacyRowError(field_name) from exc
    try:
        float(cells.cost_usd)
    except ValueError as exc:
        raise MalformedLegacyRowError("cost_usd") from exc


def _framed_identifier(secret: bytes, namespace: str, *parts: str) -> str:
    """Derive a deterministic opaque identifier via unambiguous framing.

    Unlike :func:`metermaid.state.opaque_identifier`/``event_identifier``,
    this tolerates an empty ``part`` — a legacy CSV cell that is present
    but blank must still contribute a distinct, deterministic byte range
    instead of raising, so idempotency never depends on every mapped
    column being non-empty.
    """
    message = namespace.encode("utf-8") + b"\x00"
    for part in parts:
        encoded = part.encode("utf-8")
        message += len(encoded).to_bytes(4, "big") + encoded
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _source_fingerprint(secret: bytes, path: Path) -> str:
    """Opaque per-file identity derived from the resolved path, not stored raw."""
    return opaque_identifier(secret, "legacy-source", str(path.resolve()))


def _row_fingerprint(secret: bytes, cells: _MappedCells) -> str:
    """Opaque fingerprint of the exact mapped-column text for one row."""
    return _framed_identifier(
        secret,
        "legacy-row",
        cells.timestamp,
        cells.provider,
        cells.model,
        cells.tokens_in,
        cells.tokens_out,
        cells.cache_read,
        cells.cache_write,
        cells.cost_usd,
    )


def _build_snapshot(
    secret: bytes,
    source_fingerprint: str,
    cells: _MappedCells,
    imported_at: datetime,
) -> LegacySnapshot:
    row_fingerprint = _row_fingerprint(secret, cells)
    legacy_id = _framed_identifier(
        secret, "legacy-id", source_fingerprint, row_fingerprint
    )
    return LegacySnapshot(
        legacy_id=legacy_id,
        source_fingerprint=source_fingerprint,
        row_fingerprint=row_fingerprint,
        imported_at=imported_at,
        timestamp=cells.timestamp,
        provider=cells.provider,
        model=cells.model,
        tokens_in=int(cells.tokens_in),
        tokens_out=int(cells.tokens_out),
        cache_read=int(cells.cache_read),
        cache_write=int(cells.cache_write),
        cost_usd=float(cells.cost_usd),
    )


def _read_legacy_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    """Read one candidate file's header and raw data rows, read-only.

    The file is opened once and never written to, truncated, or removed;
    source bytes stay untouched no matter what this returns.
    """
    with path.open(newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return [], []
        return header, [row for row in reader]


def _row_to_snapshot(
    secret: bytes, source_fingerprint: str, raw_row: list[str], imported_at: datetime
) -> LegacySnapshot:
    """Map, validate, and fingerprint one raw CSV row.

    Raises :class:`MalformedLegacyRowError` (a ``ValueError``) if the row
    is the wrong width for ``CSV_HEADERS`` or a mapped numeric column
    cannot be parsed as its documented type.
    """
    row = dict(zip(CSV_HEADERS, raw_row, strict=True))
    cells = _mapped_cells(row)
    _validate_numeric_cells(cells)
    return _build_snapshot(secret, source_fingerprint, cells, imported_at)


@dataclass(frozen=True, slots=True)
class LegacyImportSummary:
    """Aggregate, structural import result — never a raw path or cell value.

    Every field here is a count, matching the project-wide convention
    (see :mod:`metermaid.doctor`) that diagnostics are counts or compact
    structural labels, never a filesystem path or source value.
    """

    files_scanned: int
    files_imported: int
    files_unsupported_header: int
    files_malformed: int
    rows_inserted: int
    rows_duplicate: int


def import_legacy_snapshots(
    store: EventStore, secret: bytes, legacy_dir: Path = SESSIONS_DIR
) -> LegacyImportSummary:
    """Explicitly import every supported v0.2 CSV in ``legacy_dir``.

    This is never called automatically by ``ingest`` or ``watch``; a
    caller must invoke it explicitly (the ``import-legacy`` CLI command).
    Each candidate file is imported atomically and independently: a file
    with an unsupported header or a malformed mapped value contributes
    zero rows (no partial import of that file), but does not stop other
    files in the directory from being imported. Re-running against the
    same file inserts no new rows the second time.
    """
    files_scanned = 0
    files_imported = 0
    files_unsupported_header = 0
    files_malformed = 0
    rows_inserted = 0
    rows_duplicate = 0

    if not legacy_dir.exists():
        return LegacyImportSummary(
            files_scanned=0,
            files_imported=0,
            files_unsupported_header=0,
            files_malformed=0,
            rows_inserted=0,
            rows_duplicate=0,
        )

    imported_at = datetime.now(UTC)
    for path in sorted(legacy_dir.glob("*.csv")):
        files_scanned += 1
        try:
            header, raw_rows = _read_legacy_csv(path)
        except OSError:
            continue

        if header != CSV_HEADERS:
            files_unsupported_header += 1
            continue

        source_fingerprint = _source_fingerprint(secret, path)
        try:
            snapshots = tuple(
                _row_to_snapshot(secret, source_fingerprint, raw_row, imported_at)
                for raw_row in raw_rows
            )
        except ValueError:
            files_malformed += 1
            continue

        commit = store.commit_legacy_import(snapshots)
        files_imported += 1
        rows_inserted += commit.inserted_rows
        rows_duplicate += len(snapshots) - commit.inserted_rows

    return LegacyImportSummary(
        files_scanned=files_scanned,
        files_imported=files_imported,
        files_unsupported_header=files_unsupported_header,
        files_malformed=files_malformed,
        rows_inserted=rows_inserted,
        rows_duplicate=rows_duplicate,
    )


# --- isolated legacy reporting -----------------------------------------


@dataclass(frozen=True, slots=True)
class LegacyTotals:
    """Summed mapped counters over imported legacy rows.

    Unlike :class:`metermaid.report_v1.TokenTotals`, every mapped legacy
    field is always present on a validated row, so these are plain sums —
    never ``None`` — and this type can never be assigned where an
    :class:`~metermaid.report_v1.ObservedReport` total is expected.
    """

    tokens_in: int
    tokens_out: int
    cache_read: int
    cache_write: int
    cost_usd: float


@dataclass(frozen=True, slots=True)
class LegacyProviderAggregate:
    """One legacy aggregate row keyed by the imported ``provider`` label."""

    provider: str
    row_count: int
    totals: LegacyTotals


@dataclass(frozen=True, slots=True)
class LegacyHistoryReport:
    """Whole-history legacy totals plus a by-provider breakdown.

    A distinct type from :class:`metermaid.report_v1.ObservedReport`: it
    is built only from :class:`~metermaid.domain.LegacySnapshot` rows and
    has no field a caller could confuse for an event-derived rate.
    """

    row_count: int
    totals: LegacyTotals
    by_provider: tuple[LegacyProviderAggregate, ...]


def _legacy_totals(rows: Sequence[LegacySnapshot]) -> LegacyTotals:
    return LegacyTotals(
        tokens_in=sum(row.tokens_in for row in rows),
        tokens_out=sum(row.tokens_out for row in rows),
        cache_read=sum(row.cache_read for row in rows),
        cache_write=sum(row.cache_write for row in rows),
        cost_usd=sum(row.cost_usd for row in rows),
    )


def build_legacy_report(
    snapshots: Sequence[LegacySnapshot],
) -> LegacyHistoryReport:
    """Build one isolated legacy-history report from imported rows."""
    by_provider: dict[str, list[LegacySnapshot]] = {}
    for row in snapshots:
        by_provider.setdefault(row.provider, []).append(row)

    return LegacyHistoryReport(
        row_count=len(snapshots),
        totals=_legacy_totals(snapshots),
        by_provider=tuple(
            LegacyProviderAggregate(
                provider=provider,
                row_count=len(rows),
                totals=_legacy_totals(rows),
            )
            for provider, rows in sorted(by_provider.items())
        ),
    )
