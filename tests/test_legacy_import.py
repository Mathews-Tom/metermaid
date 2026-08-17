"""Contracts for `metermaid import-legacy`'s isolated v0.2 CSV import (M4 PR3).

Covers DM-003's supported-header/mapped-field contract, idempotent
re-import, whole-file rejection of an altered header or a malformed
mapped numeric value (no partial import), byte-identical source
preservation, opaque secret-scoped fingerprints, and strict isolation
from `report_v1`'s current-event aggregates.
"""

from __future__ import annotations

import csv
import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from metermaid.cli import main
from metermaid.domain import NormalizedEvent
from metermaid.legacy_v1 import (
    LegacyImportSummary,
    MalformedLegacyRowError,
    build_legacy_report,
    import_legacy_snapshots,
)
from metermaid.models import CSV_HEADERS
from metermaid.report_v1 import ObservedReport, build_report
from metermaid.state import (
    load_or_create_secret,
    opaque_identifier,
    resolve_state_paths,
)
from metermaid.store import SCHEMA_VERSION, EventStore

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "m4"
SUPPORTED_FIXTURE = "legacy-supported.csv"
MALFORMED_FIXTURE = "legacy-malformed-numeric.csv"
ALTERED_FIXTURE = "legacy-altered-header.csv"
ALL_FIXTURES = (SUPPORTED_FIXTURE, MALFORMED_FIXTURE, ALTERED_FIXTURE)


def _store(tmp_path: Path) -> tuple[EventStore, bytes, Path]:
    data_dir = tmp_path / "state"
    paths = resolve_state_paths(data_dir)
    secret = load_or_create_secret(paths)
    store = EventStore(paths.database)
    store.initialize()
    return store, secret, data_dir


def _seed_legacy_dir(tmp_path: Path, *names: str) -> Path:
    """Copy named committed fixtures into an isolated legacy directory."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    for name in names:
        (legacy_dir / name).write_bytes((FIXTURE_ROOT / name).read_bytes())
    return legacy_dir


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _supported_row_values() -> list[str]:
    with (FIXTURE_ROOT / SUPPORTED_FIXTURE).open(newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        return next(reader)


def _event(
    *,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cache_read: int | None = None,
    cache_write: int | None = None,
) -> NormalizedEvent:
    return NormalizedEvent(
        event_id="1" * 64,
        agent="codex",
        source_session_id="a" * 64,
        project_key="b" * 64,
        occurred_at=datetime(2026, 8, 16, tzinfo=UTC),
        record_kind="usage",
        provenance="codex.message",
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_read=cache_read,
        cache_write=cache_write,
    )


# --- mapping: only the DM-003 allow-listed fields are ever read -------------


def test_import_maps_only_the_allowlisted_fields_from_the_supported_fixture(
    tmp_path: Path,
) -> None:
    store, secret, _data_dir = _store(tmp_path)
    legacy_dir = _seed_legacy_dir(tmp_path, SUPPORTED_FIXTURE)

    summary = import_legacy_snapshots(store, secret, legacy_dir)

    assert summary == LegacyImportSummary(
        files_scanned=1,
        files_imported=1,
        files_unsupported_header=0,
        files_malformed=0,
        rows_inserted=1,
        rows_duplicate=0,
    )
    rows = store.legacy_snapshots()
    assert len(rows) == 1
    row = rows[0]
    assert row.timestamp == "2026-08-16T00:00:00Z"
    assert row.provider == "claude"
    assert row.model == "fixture-model"
    assert row.tokens_in == 100
    assert row.tokens_out == 20
    assert row.cache_read == 10
    assert row.cache_write == 5
    assert row.cost_usd == pytest.approx(0.032)
    assert row.imported_at.tzinfo is not None


def test_import_never_touches_the_normalized_event_table(tmp_path: Path) -> None:
    """Isolation: a legacy import can never create, or be mistaken for, an event."""
    store, secret, _data_dir = _store(tmp_path)
    legacy_dir = _seed_legacy_dir(tmp_path, SUPPORTED_FIXTURE)

    import_legacy_snapshots(store, secret, legacy_dir)

    assert store.events() == []
    assert store.diagnostics() == []


# --- idempotency --------------------------------------------------------


def test_import_is_idempotent_on_a_repeated_import(tmp_path: Path) -> None:
    store, secret, _data_dir = _store(tmp_path)
    legacy_dir = _seed_legacy_dir(tmp_path, SUPPORTED_FIXTURE)

    first = import_legacy_snapshots(store, secret, legacy_dir)
    second = import_legacy_snapshots(store, secret, legacy_dir)

    assert first.rows_inserted == 1
    assert first.rows_duplicate == 0
    assert second.rows_inserted == 0
    assert second.rows_duplicate == 1
    assert second.files_imported == 1
    rows = store.legacy_snapshots()
    assert len(rows) == 1


def test_commit_legacy_import_is_idempotent_at_the_store_layer(tmp_path: Path) -> None:
    """The store's own INSERT OR IGNORE dedups by the derived legacy_id."""
    store, secret, _data_dir = _store(tmp_path)
    legacy_dir = _seed_legacy_dir(tmp_path, SUPPORTED_FIXTURE)
    import_legacy_snapshots(store, secret, legacy_dir)
    snapshot = store.legacy_snapshots()[0]

    result = store.commit_legacy_import([snapshot])

    assert result.inserted_rows == 0
    assert len(store.legacy_snapshots()) == 1


# --- rejection: unsupported header, no partial import -----------------------


def test_import_rejects_the_altered_header_fixture_without_importing_any_row(
    tmp_path: Path,
) -> None:
    store, secret, _data_dir = _store(tmp_path)
    legacy_dir = _seed_legacy_dir(tmp_path, ALTERED_FIXTURE)

    summary = import_legacy_snapshots(store, secret, legacy_dir)

    assert summary.files_scanned == 1
    assert summary.files_unsupported_header == 1
    assert summary.files_imported == 0
    assert summary.rows_inserted == 0
    assert store.legacy_snapshots() == []


# --- rejection: malformed numeric, no partial import -------------------


def test_import_rejects_the_malformed_numeric_fixture_without_importing_any_row(
    tmp_path: Path,
) -> None:
    store, secret, _data_dir = _store(tmp_path)
    legacy_dir = _seed_legacy_dir(tmp_path, MALFORMED_FIXTURE)

    summary = import_legacy_snapshots(store, secret, legacy_dir)

    assert summary.files_scanned == 1
    assert summary.files_malformed == 1
    assert summary.files_imported == 0
    assert summary.rows_inserted == 0
    assert store.legacy_snapshots() == []


def test_import_never_partially_imports_a_file_with_one_good_and_one_bad_row(
    tmp_path: Path,
) -> None:
    """A malformed row anywhere in the file rejects the whole file — no
    partial import of the rows that would otherwise have been valid."""
    store, secret, _data_dir = _store(tmp_path)
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    good_row = _supported_row_values()
    bad_row = list(good_row)
    bad_row[CSV_HEADERS.index("tokens_in")] = "not-a-number"
    _write_csv(legacy_dir / "mixed.csv", CSV_HEADERS, [good_row, bad_row])

    summary = import_legacy_snapshots(store, secret, legacy_dir)

    assert summary.files_malformed == 1
    assert summary.files_imported == 0
    assert summary.rows_inserted == 0
    assert store.legacy_snapshots() == []


def test_malformed_legacy_row_error_is_a_value_error() -> None:
    assert issubclass(MalformedLegacyRowError, ValueError)


# --- source bytes are never mutated -------------------------------------


@pytest.mark.parametrize("fixture_name", ALL_FIXTURES)
def test_import_leaves_source_bytes_byte_identical(
    tmp_path: Path, fixture_name: str
) -> None:
    store, secret, _data_dir = _store(tmp_path)
    legacy_dir = _seed_legacy_dir(tmp_path, fixture_name)
    source_path = legacy_dir / fixture_name
    before = hashlib.sha256(source_path.read_bytes()).hexdigest()

    import_legacy_snapshots(store, secret, legacy_dir)

    after = hashlib.sha256(source_path.read_bytes()).hexdigest()
    assert before == after


# --- opaque, secret-scoped fingerprints ---------------------------------


def test_source_fingerprint_matches_the_opaque_identifier_derivation(
    tmp_path: Path,
) -> None:
    store, secret, _data_dir = _store(tmp_path)
    legacy_dir = _seed_legacy_dir(tmp_path, SUPPORTED_FIXTURE)
    source_path = legacy_dir / SUPPORTED_FIXTURE

    import_legacy_snapshots(store, secret, legacy_dir)

    row = store.legacy_snapshots()[0]
    expected = opaque_identifier(secret, "legacy-source", str(source_path.resolve()))
    assert row.source_fingerprint == expected


def test_fingerprints_are_opaque_hex_and_never_contain_source_text(
    tmp_path: Path,
) -> None:
    store, secret, _data_dir = _store(tmp_path)
    legacy_dir = _seed_legacy_dir(tmp_path, SUPPORTED_FIXTURE)

    import_legacy_snapshots(store, secret, legacy_dir)

    row = store.legacy_snapshots()[0]
    for value in (row.legacy_id, row.source_fingerprint, row.row_fingerprint):
        assert len(value) == 64
        assert all(c in "0123456789abcdef" for c in value)
    assert str(legacy_dir) not in row.legacy_id
    assert "claude" not in row.source_fingerprint


def test_fingerprints_are_scoped_to_the_machine_local_secret(tmp_path: Path) -> None:
    legacy_dir = _seed_legacy_dir(tmp_path, SUPPORTED_FIXTURE)

    store_a, secret_a, _dir_a = _store(tmp_path / "a")
    store_b, secret_b, _dir_b = _store(tmp_path / "b")
    assert secret_a != secret_b

    import_legacy_snapshots(store_a, secret_a, legacy_dir)
    import_legacy_snapshots(store_b, secret_b, legacy_dir)

    row_a = store_a.legacy_snapshots()[0]
    row_b = store_b.legacy_snapshots()[0]
    assert row_a.source_fingerprint != row_b.source_fingerprint
    assert row_a.row_fingerprint != row_b.row_fingerprint
    assert row_a.legacy_id != row_b.legacy_id


# --- no automatic import: an absent/empty legacy directory is a no-op --------


def test_import_over_a_missing_directory_returns_an_empty_summary(
    tmp_path: Path,
) -> None:
    store, secret, _data_dir = _store(tmp_path)

    summary = import_legacy_snapshots(store, secret, tmp_path / "does-not-exist")

    assert summary == LegacyImportSummary(0, 0, 0, 0, 0, 0)
    assert store.legacy_snapshots() == []


# --- isolation from report_v1's current-event aggregates ---------------


def test_legacy_report_never_combines_with_observed_event_totals(
    tmp_path: Path,
) -> None:
    store, secret, _data_dir = _store(tmp_path)
    legacy_dir = _seed_legacy_dir(tmp_path, SUPPORTED_FIXTURE)
    import_legacy_snapshots(store, secret, legacy_dir)
    store.commit_ingest(
        [_event(tokens_in=7, tokens_out=3, cache_read=1, cache_write=1)], [], None
    )

    legacy_report = build_legacy_report(store.legacy_snapshots())
    observed_report = build_report(store.events())

    assert legacy_report.row_count == 1
    assert legacy_report.totals.tokens_in == 100  # from the legacy fixture only
    assert legacy_report.totals.tokens_out == 20
    assert isinstance(observed_report, ObservedReport)
    assert observed_report.event_count == 1
    assert observed_report.tokens.tokens_in == 7  # from the event only
    assert observed_report.tokens.tokens_out == 3
    # Neither report's numbers is the sum of the other's — proving the two
    # data sources were never merged before or during aggregation.
    assert legacy_report.totals.tokens_in != observed_report.tokens.tokens_in


def test_legacy_history_report_groups_by_provider_without_touching_events(
    tmp_path: Path,
) -> None:
    store, secret, _data_dir = _store(tmp_path)
    legacy_dir = _seed_legacy_dir(tmp_path, SUPPORTED_FIXTURE)
    import_legacy_snapshots(store, secret, legacy_dir)

    report = build_legacy_report(store.legacy_snapshots())

    assert report.row_count == 1
    assert len(report.by_provider) == 1
    assert report.by_provider[0].provider == "claude"
    assert report.by_provider[0].row_count == 1
    assert report.by_provider[0].totals.cost_usd == pytest.approx(0.032)


# --- store schema: legacy mappings survive later schema upgrades ------------


def test_store_schema_keeps_legacy_snapshots_mapped_through_current_version(
    tmp_path: Path,
) -> None:
    database = tmp_path / "metermaid.sqlite3"
    store = EventStore(database)

    store.initialize()

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(legacy_snapshots)")
        }
    assert columns == {
        "legacy_id",
        "source_fingerprint",
        "row_fingerprint",
        "imported_at",
        "timestamp",
        "provider",
        "model",
        "tokens_in",
        "tokens_out",
        "cache_read",
        "cache_write",
        "cost_usd",
    }


def test_a_store_already_at_v1_upgrades_cleanly_to_current_version(
    tmp_path: Path,
) -> None:
    """A pre-M4 install whose ``legacy_snapshots`` was only the empty
    placeholder table must still migrate cleanly the next time it opens."""
    database = tmp_path / "metermaid.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode = WAL")
    EventStore._apply_v1_schema(connection)
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()

    store = EventStore(database)
    store.initialize()

    with sqlite3.connect(database) as verify:
        assert verify.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        columns = {
            row[1] for row in verify.execute("PRAGMA table_info(legacy_snapshots)")
        }
    assert "timestamp" in columns
    assert "cost_usd" in columns


# --- CLI: `metermaid import-legacy` -------------------------------------


def test_import_legacy_command_persists_rows_and_prints_no_raw_path(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "state"
    legacy_dir = _seed_legacy_dir(tmp_path, SUPPORTED_FIXTURE)
    monkeypatch.setattr(
        "sys.argv",
        [
            "metermaid",
            "import-legacy",
            "--data-dir",
            str(data_dir),
            str(legacy_dir),
        ],
    )

    main()

    out = capsys.readouterr().out
    assert "Legacy import" in out
    assert str(legacy_dir) not in out
    assert str(data_dir) not in out

    paths = resolve_state_paths(data_dir)
    store = EventStore(paths.database)
    store.initialize()
    assert len(store.legacy_snapshots()) == 1


def test_import_legacy_command_is_idempotent_across_invocations(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "state"
    legacy_dir = _seed_legacy_dir(tmp_path, SUPPORTED_FIXTURE)
    monkeypatch.setattr(
        "sys.argv",
        ["metermaid", "import-legacy", "--data-dir", str(data_dir), str(legacy_dir)],
    )

    main()
    main()

    paths = resolve_state_paths(data_dir)
    store = EventStore(paths.database)
    store.initialize()
    assert len(store.legacy_snapshots()) == 1


def test_import_legacy_command_never_runs_automatically_from_ingest(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """`ingest` never touches legacy CSVs; only an explicit `import-legacy` does."""
    home = tmp_path / "home"
    data_dir = tmp_path / "state"
    (home / "sessions").mkdir(parents=True)
    with (home / "sessions" / f"{SUPPORTED_FIXTURE}").open("wb") as handle:
        handle.write((FIXTURE_ROOT / SUPPORTED_FIXTURE).read_bytes())
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr("metermaid.discover._platform_home_roots", lambda: (home,))
    monkeypatch.setattr(
        "sys.argv", ["metermaid", "ingest", "--data-dir", str(data_dir)]
    )

    main()

    paths = resolve_state_paths(data_dir)
    store = EventStore(paths.database)
    store.initialize()
    assert store.legacy_snapshots() == []


# --- CLI: `metermaid report` renders a separate legacy section -------------


def test_report_command_renders_legacy_history_separately_from_observed_events(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store, secret, data_dir = _store(tmp_path)
    legacy_dir = _seed_legacy_dir(tmp_path, SUPPORTED_FIXTURE)
    import_legacy_snapshots(store, secret, legacy_dir)
    monkeypatch.setattr(
        "sys.argv", ["metermaid", "report", "--data-dir", str(data_dir)]
    )

    main()

    out = capsys.readouterr().out
    assert "Observed: 0" in out
    assert "Legacy history" in out
    assert "Legacy totals: in=100" in out
    assert str(legacy_dir) not in out
    assert str(data_dir) not in out
