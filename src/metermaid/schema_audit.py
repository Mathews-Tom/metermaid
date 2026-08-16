"""Read JSONL schemas without persisting source record content."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import TextIO

Schema = dict[str, object] | str


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
        return {"type": "array", "items": list(item_schemas.values())}
    if isinstance(value, dict):
        fields: dict[str, Schema] = {}
        for field, item in value.items():
            if not isinstance(field, str):
                raise ValueError("JSON object contains a non-string field name")
            fields[field] = _schema_for(item)
        return {"type": "object", "fields": fields}
    raise TypeError(f"Unsupported JSON value type: {type(value).__name__}")


def _schema_key(schema: Schema) -> str:
    return json.dumps(schema, sort_keys=True)


def _non_empty_lines(source: Path) -> Iterable[tuple[int, str]]:
    with source.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if line.strip():
                yield line_number, line


def audit_json_lines(source: Path, output: TextIO) -> int:
    """Emit JSON schemas to ``output`` and return nonzero for invalid records.

    The source path and scalar values are never included in output. This helper
    reads the selected source directly and does not create or modify files.
    """
    record_count = 0
    failures = 0
    for line_number, line in _non_empty_lines(source):
        record_count += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
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
        print(
            json.dumps(
                {"record": record_count, "schema": _schema_for(record)}, sort_keys=True
            ),
            file=output,
        )

    if record_count == 0:
        print("ERROR source contains no JSON records", file=output)
        return 1
    return int(failures > 0)
