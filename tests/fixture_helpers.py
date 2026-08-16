"""Helpers for creating fixtures that contain no source-record values."""

from __future__ import annotations

from collections.abc import Iterable

REDACTION_MARKER = "<redacted>"


def redacted_record(field_names: Iterable[str]) -> dict[str, str]:
    """Create a review-ready record with no source values."""
    fields = tuple(field_names)
    if not fields:
        raise ValueError("A redacted fixture requires at least one field name")
    if any(not field for field in fields):
        raise ValueError("Fixture field names must be non-empty")
    return {field: REDACTION_MARKER for field in fields}
