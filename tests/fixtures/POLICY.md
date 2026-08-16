# Fixture policy

Every fixture must be manually reviewed for redaction before it enters version control. Fixtures may describe source schemas and adapter behavior, but must never contain a real transcript, prompt, response, tool argument, tool result, project path, session path, or raw record.

Use `uv run python scripts/audit_source_schema.py <source-jsonl>` only to inspect a user-selected local source. The command prints field names and value shapes to standard output for human review. It never prints source values or the source path, and it does not write a fixture, archive, or copy of the source.

Create committed redacted fixture records with `tests.fixture_helpers.redacted_record`. It accepts field names only and replaces every value with `<redacted>`. A reviewer remains responsible for confirming that field names and any intentionally synthetic values reveal no private information.

Reject malformed JSON and non-object records during audit. Do not infer a replacement record shape.
