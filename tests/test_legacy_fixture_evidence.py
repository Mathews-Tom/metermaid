"""Fixture contracts that close DM-003's legacy-CSV schema evidence gate.

DM-003 (``.docs/decision-map/tickets/DM-003-legacy-compatibility.md``)
resolves that a future M4 legacy importer may accept only the exact v0.2
header sequence produced by the committed ``CSV_HEADERS`` contract in
``src/metermaid/models.py``, must map a narrow allow-list of columns
(timestamp, provider, model, cumulative input/output/cache counters, and
cost) into ``imported_legacy`` history, and must never import the remaining
columns: raw path, session identifier, source provenance, deltas, sidechain
metadata, or context/timing fields. This module proves the three approved
synthetic fixture candidates in ``tests/fixtures/m4`` are structural
evidence for that policy before any importer code exists. It asserts only
structural facts (header shape, cardinality, cell identity) and privacy
facts (redaction/synthetic-value policy) — never importer behavior.
"""

from __future__ import annotations

import csv
from dataclasses import fields
from pathlib import Path

import pytest

from metermaid.models import CSV_HEADERS, SESSIONS_DIR, Snapshot
from tests.fixture_helpers import REDACTION_MARKER

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "m4"
SUPPORTED_FIXTURE = "legacy-supported.csv"
MALFORMED_FIXTURE = "legacy-malformed-numeric.csv"
ALTERED_FIXTURE = "legacy-altered-header.csv"
ALL_FIXTURES = (SUPPORTED_FIXTURE, MALFORMED_FIXTURE, ALTERED_FIXTURE)

# DM-003: "The importer maps only timestamp, provider, model, cumulative
# input/output/cache counters when present, and the existing cost value
# into explicitly marked imported_legacy history." Every other Snapshot
# column is a raw field DM-003 prohibits the importer from ever reading.
_MAPPED_FIELDS = frozenset(
    {
        "timestamp",
        "provider",
        "model",
        "tokens_in",
        "tokens_out",
        "cache_read",
        "cache_write",
        "cost_usd",
    }
)
_ALL_SNAPSHOT_FIELDS = frozenset(CSV_HEADERS)
_PROHIBITED_FIELDS = _ALL_SNAPSHOT_FIELDS - _MAPPED_FIELDS

# Fields whose declared Snapshot type is `str`, derived from the dataclass
# itself (not hardcoded) so a future field-type change is caught here too.
_STRING_FIELDS = frozenset(f.name for f in fields(Snapshot) if f.type == "str")
_PROHIBITED_STRING_FIELDS = _PROHIBITED_FIELDS & _STRING_FIELDS

# The only literal values a prohibited string column may hold in a reviewed
# fixture: the shared redaction marker, or a fixed synthetic placeholder.
_ALLOWED_SYNTHETIC_VALUES = frozenset(
    {REDACTION_MARKER, "fixture", "fixture-session", ""}
)


def _read_csv(name: str) -> tuple[list[str], list[dict[str, str]]]:
    with (FIXTURE_ROOT / name).open(newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        rows = [dict(zip(header, row, strict=True)) for row in reader]
    return header, rows


def test_mapped_and_prohibited_fields_partition_every_snapshot_column() -> None:
    """The DM-003 allow-list derived above must cover every column exactly once."""
    assert _MAPPED_FIELDS <= _ALL_SNAPSHOT_FIELDS
    assert _MAPPED_FIELDS | _PROHIBITED_FIELDS == _ALL_SNAPSHOT_FIELDS
    assert _MAPPED_FIELDS.isdisjoint(_PROHIBITED_FIELDS)


def test_fixture_root_is_the_committed_synthetic_tree_not_a_live_install() -> None:
    """Prove these evidence bytes come from the reviewed fixture tree, never a real install."""
    assert FIXTURE_ROOT.parent == Path(__file__).parent / "fixtures"
    resolved_root = FIXTURE_ROOT.resolve()
    assert resolved_root != SESSIONS_DIR.resolve()
    assert not str(resolved_root).startswith(str(SESSIONS_DIR.resolve()))
    for name in ALL_FIXTURES:
        path = FIXTURE_ROOT / name
        assert path.is_file()
        assert path.resolve().parent == resolved_root


@pytest.mark.parametrize("fixture_name", ALL_FIXTURES)
def test_every_fixture_header_and_row_share_csv_headers_cardinality(
    fixture_name: str,
) -> None:
    """Every candidate must carry exactly one header row and one data row of matching width."""
    header, rows = _read_csv(fixture_name)
    assert len(header) == len(CSV_HEADERS)
    assert len(rows) == 1
    assert len(rows[0]) == len(header)
    assert len(header) == len(set(header)), "header must not repeat a column name"


def test_supported_fixture_header_matches_csv_headers_source_exactly() -> None:
    header, _ = _read_csv(SUPPORTED_FIXTURE)
    assert header == CSV_HEADERS


def test_malformed_numeric_fixture_header_matches_supported_layout() -> None:
    """The malformed fixture must exercise a value defect, not a header defect."""
    header, _ = _read_csv(MALFORMED_FIXTURE)
    assert header == CSV_HEADERS


def test_altered_header_fixture_differs_from_csv_headers_source() -> None:
    header, _ = _read_csv(ALTERED_FIXTURE)
    # DM-003: "Header order and spelling must match exactly. A file that
    # differs in any header is unsupported ... and is not partially imported."
    assert header != CSV_HEADERS
    assert len(header) == len(CSV_HEADERS)
    # Prove the difference is a genuine single-column substitution (same
    # shape, one renamed column), not a shifted, missing, or extra field.
    changed = [
        (expected, actual)
        for expected, actual in zip(CSV_HEADERS, header, strict=True)
        if expected != actual
    ]
    assert changed == [("sc_models", "unexpected_column")]
    assert set(CSV_HEADERS) - set(header) == {"sc_models"}
    assert set(header) - set(CSV_HEADERS) == {"unexpected_column"}


def test_altered_header_fixture_row_values_are_otherwise_identical_to_supported() -> (
    None
):
    """The altered-header fixture isolates a header defect, not also a value defect."""
    supported_header, supported_rows = _read_csv(SUPPORTED_FIXTURE)
    altered_header, altered_rows = _read_csv(ALTERED_FIXTURE)
    supported_row = supported_rows[0]
    altered_row = altered_rows[0]
    for expected_name, actual_name in zip(
        supported_header[:-1], altered_header[:-1], strict=True
    ):
        assert expected_name == actual_name
        assert supported_row[expected_name] == altered_row[actual_name]


def test_malformed_numeric_fixture_breaks_exactly_one_allowlisted_counter() -> None:
    _, supported_rows = _read_csv(SUPPORTED_FIXTURE)
    _, malformed_rows = _read_csv(MALFORMED_FIXTURE)
    supported_row = supported_rows[0]
    malformed_row = malformed_rows[0]

    differing_fields = [
        name for name in CSV_HEADERS if supported_row[name] != malformed_row[name]
    ]
    assert differing_fields == ["tokens_in"]
    (broken_field,) = differing_fields

    # The intentionally broken field must be one DM-003 actually allow-lists
    # for import; otherwise this fixture would not exercise rejection of a
    # value the importer needs to trust.
    assert broken_field in _MAPPED_FIELDS

    # Identify the cell as non-numeric without coercing it into a number:
    # a successful `int()`/`float()` call would silently manufacture a value
    # DM-003 forbids ("do not infer a replacement record shape").
    broken_value = malformed_row[broken_field]
    assert not broken_value.strip("-").isdigit()
    with pytest.raises(ValueError):
        int(broken_value)
    with pytest.raises(ValueError):
        float(broken_value)


@pytest.mark.parametrize("fixture_name", ALL_FIXTURES)
def test_prohibited_string_fields_hold_only_synthetic_or_redacted_values(
    fixture_name: str,
) -> None:
    header, rows = _read_csv(fixture_name)
    row = rows[0]
    present_prohibited_string_fields = _PROHIBITED_STRING_FIELDS & set(header)
    assert present_prohibited_string_fields, (
        "fixture must still carry at least one prohibited string column"
    )
    for field_name in present_prohibited_string_fields:
        assert row[field_name] in _ALLOWED_SYNTHETIC_VALUES, (
            f"{fixture_name}:{field_name} must be redacted or a known synthetic placeholder, "
            f"got {row[field_name]!r}"
        )


@pytest.mark.parametrize("fixture_name", ALL_FIXTURES)
def test_path_field_is_the_review_redaction_marker(fixture_name: str) -> None:
    _, rows = _read_csv(fixture_name)
    assert rows[0]["path"] == REDACTION_MARKER
