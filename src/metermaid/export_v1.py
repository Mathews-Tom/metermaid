"""Restricted aggregate export — one fixed, allowlisted JSON schema.

The exported document is a pure aggregate built from the same
per-agent breakdown ``report_v1.build_report`` computes over the
current normalized events — never a raw event, a raw session, a raw
snapshot row, or free text. There is exactly one row schema
(:data:`ROW_FIELDS`) and no format selector: callers get this JSON
document or nothing.

By construction the emitted rows carry only an ``agent`` label plus
counts and summed token/cost totals. They never carry a source path,
a raw event or session record, a source session id, an opaque project
key, a model name, or a sub-granular (per-event) timestamp — none of
those fields exist anywhere in :class:`AggregateExportRow` for a
writer to accidentally populate. Every numeric total keeps the
``report_v1`` "``None`` means never observed" semantics: an absent
counter serializes as JSON ``null``, never a computed zero.

:func:`export_preview` renders the exact field-name allowlist plus one
representative sample row as plain text. Callers must render and
inspect that preview before calling :func:`write_export`, so the
schema and a concrete sample are visible before any byte reaches
disk.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

from .domain import NormalizedEvent
from .report_v1 import build_report

SCHEMA_VERSION: Final = 1
"""Version tag embedded in every export document."""

ROW_FIELDS: Final[tuple[str, ...]] = (
    "agent",
    "event_count",
    "session_count",
    "tokens_in",
    "tokens_out",
    "cache_read",
    "cache_write",
    "reasoning_tokens",
    "provider_cost_usd",
)
"""The complete, ordered allowlist of fields on every export row.

Nothing outside this tuple is ever written. There is no raw event id,
source session id, opaque project key, model name, free-text field, or
timestamp of any granularity among them.
"""


@dataclass(frozen=True, slots=True)
class AggregateExportRow:
    """One restricted export row: an agent's aggregate totals.

    Every field mirrors :data:`ROW_FIELDS` in declaration order. An
    ``int | None`` or ``float | None`` field is ``None`` only when no
    selected event ever reported that counter — never a computed
    zero standing in for "absent".
    """

    agent: str
    event_count: int
    session_count: int
    tokens_in: int | None
    tokens_out: int | None
    cache_read: int | None
    cache_write: int | None
    reasoning_tokens: int | None
    provider_cost_usd: float | None


@dataclass(frozen=True, slots=True)
class AggregateExportDocument:
    """The whole restricted export: a schema tag plus allowlisted rows."""

    schema_version: int
    rows: tuple[AggregateExportRow, ...]


def build_export(events: Sequence[NormalizedEvent]) -> AggregateExportDocument:
    """Aggregate ``events`` by agent into one restricted export document."""
    observed = build_report(events)
    rows = tuple(
        AggregateExportRow(
            agent=group.key,
            event_count=group.event_count,
            session_count=group.session_count,
            tokens_in=group.tokens.tokens_in,
            tokens_out=group.tokens.tokens_out,
            cache_read=group.tokens.cache_read,
            cache_write=group.tokens.cache_write,
            reasoning_tokens=group.tokens.reasoning_tokens,
            provider_cost_usd=group.provider_cost_usd,
        )
        for group in observed.by_agent
    )
    return AggregateExportDocument(schema_version=SCHEMA_VERSION, rows=rows)


def _row_dict(row: AggregateExportRow) -> dict[str, object]:
    return asdict(row)


def render_export(document: AggregateExportDocument) -> str:
    """Render ``document`` as the single documented JSON export schema."""
    payload = {
        "schema_version": document.schema_version,
        "rows": [_row_dict(row) for row in document.rows],
    }
    return json.dumps(payload, indent=2)


def export_preview(document: AggregateExportDocument) -> str:
    """Render the field allowlist plus one sample row, before any write.

    The sample is the document's first row, formatted exactly as it
    will be written. When there are no rows, the preview states that
    plainly instead of fabricating one.
    """
    lines = [
        f"schema_version: {document.schema_version}",
        f"fields: {', '.join(ROW_FIELDS)}",
    ]
    if document.rows:
        lines.append(f"sample: {json.dumps(_row_dict(document.rows[0]))}")
    else:
        lines.append("sample: (no rows)")
    return "\n".join(lines)


def write_export(document: AggregateExportDocument, out: Path) -> None:
    """Write ``document`` to ``out`` as the restricted JSON export schema."""
    out.write_text(render_export(document) + "\n")
