"""Read JSONL schemas without persisting source record content."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import TextIO

Schema = dict[str, object] | str

MAX_SAFE_FIELD_NAME_LENGTH = 64


def _schema_for(value: object) -> Schema:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        item_schemas: dict[str, Schema] = {}
        for item in value:
            item_schema = _schema_for(item)
            item_schemas[_schema_key(item_schema)] = item_schema
        return {
            "type": "array",
            "items": [item_schemas[key] for key in sorted(item_schemas)],
        }
    if isinstance(value, dict):
        return _schema_for_object(value)
    raise TypeError(f"Unsupported JSON value type: {type(value).__name__}")


def _schema_for_object(value: dict[object, object]) -> Schema:
    fields: dict[str, Schema] = {}
    redacted_key_count = 0
    for field, item in sorted(value.items(), key=lambda pair: str(pair[0])):
        if (
            isinstance(field, str)
            and field.isascii()
            and field.isidentifier()
            and len(field) <= MAX_SAFE_FIELD_NAME_LENGTH
        ):
            field_name = field
        else:
            redacted_key_count += 1
            field_name = f"<redacted-field-{redacted_key_count}>"
        fields[field_name] = _schema_for(item)
    return {"type": "object", "fields": fields}


def _schema_key(schema: Schema) -> str:
    return json.dumps(schema, sort_keys=True)


def _audit_stream(stream: Iterable[str], output: TextIO) -> int:
    record_count = 0
    failures = 0
    for line_number, line in enumerate(stream, start=1):
        if not line.strip():
            continue
        record_count += 1
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            print(
                f"ERROR record {record_count} at line {line_number}: malformed JSON",
                file=output,
            )
            failures += 1
            continue
        if not isinstance(record, dict):
            print(
                f"ERROR record {record_count} at line {line_number}: expected JSON object",
                file=output,
            )
            failures += 1
            continue
        try:
            schema = _schema_for(record)
        except RecursionError:
            print(
                f"ERROR record {record_count} at line {line_number}: nesting exceeds audit limit",
                file=output,
            )
            failures += 1
            continue
        print(
            json.dumps({"record": record_count, "schema": schema}, sort_keys=True),
            file=output,
        )

    if record_count == 0:
        print("ERROR source contains no JSON records", file=output)
        return 1
    return int(failures > 0)


def audit_json_lines(source: Path, output: TextIO) -> int:
    """Emit JSON schemas to ``output`` and return nonzero for invalid records.

    The source path and scalar values are never included in output. This helper
    reads the selected source directly and does not create or modify files.
    """
    try:
        with source.open(encoding="utf-8", errors="replace") as stream:
            return _audit_stream(stream, output)
    except OSError:
        print("ERROR source is unreadable", file=output)
        return 1
