"""Byte-offset and watermark contracts for the M3 incremental reader."""

from __future__ import annotations

import os
from pathlib import Path

from pytest import MonkeyPatch

import metermaid.ingest as ingest_module
from metermaid.ingest import read_increment
from metermaid.state import opaque_identifier

_SECRET = b"fixture-secret-for-watermark-reader"


def _locator(path: Path) -> str:
    return opaque_identifier(_SECRET, "source-locator", str(path))


def test_first_read_of_a_new_file_starts_at_zero(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"a":1}\n{"a":2}\n')

    result = read_increment(path, None, _locator(path), _SECRET)

    assert [line.payload for line in result.lines] == [b'{"a":1}', b'{"a":2}']
    assert [line.byte_start for line in result.lines] == [0, 8]
    assert result.watermark.complete_offset == len(b'{"a":1}\n{"a":2}\n')
    assert result.watermark.observed_size == result.watermark.complete_offset


def test_append_reads_only_new_complete_lines(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"a":1}\n')
    first = read_increment(path, None, _locator(path), _SECRET)
    assert [line.payload for line in first.lines] == [b'{"a":1}']

    with path.open("ab") as handle:
        handle.write(b'{"a":2}\n{"a":3}\n')
    second = read_increment(path, first.watermark, _locator(path), _SECRET)

    assert [line.payload for line in second.lines] == [b'{"a":2}', b'{"a":3}']
    assert second.watermark.complete_offset == path.stat().st_size
    assert second.watermark.file_identity == first.watermark.file_identity


def test_incomplete_final_line_is_deferred_without_advancing_offset(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"a":1}\n{"partial"')

    result = read_increment(path, None, _locator(path), _SECRET)

    assert [line.payload for line in result.lines] == [b'{"a":1}']
    assert result.watermark.complete_offset == len(b'{"a":1}\n')
    assert result.watermark.observed_size == path.stat().st_size
    assert result.watermark.complete_offset < result.watermark.observed_size


def test_completing_a_deferred_line_yields_exactly_one_new_record(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"a":1}\n{"partial"')
    partial = read_increment(path, None, _locator(path), _SECRET)
    assert [line.payload for line in partial.lines] == [b'{"a":1}']

    with path.open("ab") as handle:
        handle.write(b":true}\n")
    completed = read_increment(path, partial.watermark, _locator(path), _SECRET)

    assert [line.payload for line in completed.lines] == [b'{"partial":true}']
    assert completed.watermark.complete_offset == path.stat().st_size


def test_a_same_size_reread_adds_no_data(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"a":1}\n{"a":2}\n')
    first = read_increment(path, None, _locator(path), _SECRET)

    second = read_increment(path, first.watermark, _locator(path), _SECRET)

    assert second.lines == ()
    assert second.watermark == first.watermark


def test_a_duplicate_reread_of_the_same_bytes_stays_idempotent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"a":1}\n')
    first = read_increment(path, None, _locator(path), _SECRET)
    second = read_increment(path, first.watermark, _locator(path), _SECRET)
    third = read_increment(path, second.watermark, _locator(path), _SECRET)

    assert second.watermark == first.watermark == third.watermark
    assert second.lines == third.lines == ()


def test_adapter_revision_change_replays_complete_records_once(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"a":1}\n{"a":2}\n')
    first = read_increment(path, None, _locator(path), _SECRET, adapter_revision=1)

    replay = read_increment(
        path, first.watermark, _locator(path), _SECRET, adapter_revision=2
    )
    stable = read_increment(
        path, replay.watermark, _locator(path), _SECRET, adapter_revision=2
    )

    assert [line.payload for line in replay.lines] == [b'{"a":1}', b'{"a":2}']
    assert replay.watermark.file_identity == first.watermark.file_identity
    assert replay.watermark.adapter_revision == 2
    assert stable.lines == ()


def test_truncation_in_place_forces_a_safe_full_reread(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"a":1}\n{"a":2}\n{"a":3}\n')
    first = read_increment(path, None, _locator(path), _SECRET)
    assert len(first.lines) == 3
    original_inode = path.stat().st_ino

    with path.open("wb") as handle:
        handle.write(b'{"b":1}\n')

    assert path.stat().st_ino == original_inode
    second = read_increment(path, first.watermark, _locator(path), _SECRET)

    assert [line.payload for line in second.lines] == [b'{"b":1}']
    assert [line.byte_start for line in second.lines] == [0]
    assert second.watermark.complete_offset == len(b'{"b":1}\n')
    assert second.watermark.observed_size == path.stat().st_size
    assert second.watermark.file_identity != first.watermark.file_identity


def test_rotation_to_a_new_inode_forces_a_new_identity_and_safe_reread(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"a":1}\n{"a":2}\n')
    first = read_increment(path, None, _locator(path), _SECRET)
    original_inode = path.stat().st_ino

    replacement = tmp_path / "session.jsonl.new"
    replacement.write_bytes(b'{"c":1}\n')
    os.replace(replacement, path)

    assert path.stat().st_ino != original_inode
    second = read_increment(path, first.watermark, _locator(path), _SECRET)

    assert second.watermark.file_identity != first.watermark.file_identity
    assert [line.payload for line in second.lines] == [b'{"c":1}']
    assert [line.byte_start for line in second.lines] == [0]
    assert second.watermark.complete_offset == len(b'{"c":1}\n')


def test_watermark_source_locator_matches_the_supplied_opaque_value(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"a":1}\n')
    locator = _locator(path)

    result = read_increment(path, None, locator, _SECRET)

    assert result.watermark.source_locator == locator


def test_same_size_truncation_with_different_content_is_still_detected(
    tmp_path: Path,
) -> None:
    """A resume must be validated against content, not size alone: a
    truncate-and-rewrite that happens to land back on the exact same
    byte count as before must still be treated as a new generation."""
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"a":11111}\n')
    first = read_increment(path, None, _locator(path), _SECRET)
    original_inode = path.stat().st_ino

    with path.open("wb") as handle:
        handle.write(b'{"b":22222}\n')

    assert path.stat().st_ino == original_inode
    assert path.stat().st_size == len(b'{"a":11111}\n')
    second = read_increment(path, first.watermark, _locator(path), _SECRET)

    assert second.watermark.file_identity != first.watermark.file_identity
    assert [line.payload for line in second.lines] == [b'{"b":22222}']
    assert [line.byte_start for line in second.lines] == [0]


def test_a_large_backlog_is_read_incrementally_across_multiple_calls(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(ingest_module, "_MAX_READ_BYTES", 16)
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"a":1}\n{"a":2}\n{"a":3}\n{"a":4}\n')

    first = read_increment(path, None, _locator(path), _SECRET)
    assert first.watermark.complete_offset < path.stat().st_size
    assert len(first.lines) >= 1

    second = read_increment(path, first.watermark, _locator(path), _SECRET)
    assert second.watermark.complete_offset > first.watermark.complete_offset

    offset = second.watermark
    while offset.complete_offset < path.stat().st_size:
        result = read_increment(path, offset, _locator(path), _SECRET)
        assert result.watermark.complete_offset >= offset.complete_offset
        offset = result.watermark

    assert offset.complete_offset == path.stat().st_size


def test_partial_incomplete_content_change_is_not_masked_by_a_constant_fallback(
    tmp_path: Path,
) -> None:
    """Two different partial (no-complete-line-yet) generations at the
    same path and inode must not derive the identical identity merely
    because neither has a complete first line yet — a constant fallback
    fingerprint would collapse them onto the same value."""
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"partial-generation-one"')
    first = read_increment(path, None, _locator(path), _SECRET)
    assert first.lines == ()
    original_inode = path.stat().st_ino

    with path.open("wb") as handle:
        handle.write(b'{"totally-different-partial-generation-two"')

    assert path.stat().st_ino == original_inode
    second = read_increment(path, first.watermark, _locator(path), _SECRET)

    assert second.lines == ()
    assert second.watermark.file_identity != first.watermark.file_identity


def test_metadata_comes_from_the_open_handle_not_a_separate_path_stat(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Guards the TOCTOU fix: if metadata were derived from a separate
    ``path.stat()`` call taken before opening the file, a path replaced
    between that stat and the subsequent open could describe the wrong
    file. Patching ``Path.stat`` to raise proves ``read_increment``
    never calls it and instead derives everything from the open handle
    via ``os.fstat``."""
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"a":1}\n')

    def _stat_must_not_be_called(self: Path, *args: object, **kwargs: object) -> object:
        raise AssertionError("read_increment must not call Path.stat()")

    monkeypatch.setattr(Path, "stat", _stat_must_not_be_called)

    result = read_increment(path, None, _locator(path), _SECRET)

    assert [line.payload for line in result.lines] == [b'{"a":1}']
    assert result.watermark.observed_size == len(b'{"a":1}\n')
