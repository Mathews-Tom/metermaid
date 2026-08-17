"""Pure per-agent record adapters for Metermaid v1's four pilot sources.

Each adapter maps one complete, already-decoded JSONL record to a
normalized usage event, using only the discriminator and nested shape
proven by its reviewed fixture (see ``tests/fixtures/m3`` and
``tests/test_source_schema_evidence.py``). A record whose top-level (or,
for Codex, nested) discriminator does not match the one reviewed,
enabled shape becomes an explicit ``unsupported`` diagnostic. A record
whose discriminator matches but that carries no usage payload at all —
for example an assistant turn or Codex event with no ``usage``/
``total_token_usage`` key anywhere along the reviewed path — is also an
explicit ``unsupported`` diagnostic: it is an unproven variant of a
known discriminator, not a broken instance of the reviewed one. Only a
record whose usage payload is present but broken — wrong-typed, or
missing a required counter, or whose mapped values violate a
``NormalizedEvent`` domain constraint such as a negative or non-finite
number — becomes an explicit ``malformed`` diagnostic. Neither
diagnostic path ever returns a zero-usage event: a diagnostic is
always a ``ParseOutcome``, never a ``NormalizedEvent`` with every
mapped field absent.

A present but unrepresentable optional *label* (``role``, ``model``,
OMP's ``toolName``) never invalidates an otherwise-parseable record: it
becomes ``None`` and every token counter still reaches the returned
event. Optional *counters* (cache reads/writes, reasoning tokens,
provider cost) are held to a stricter standard — a present value with
the wrong type, or a non-finite float such as ``NaN``/``Infinity`` (a
value JSON's own decoder can legally produce, and a value
``NormalizedEvent``'s own non-negative check cannot catch, since every
comparison against ``NaN`` is ``False``) — is treated as broken data
and reported ``malformed``, never silently dropped.

Every adapter is pure: ``parse`` derives ``occurred_at`` only from the
record's own top-level ``timestamp`` field — parsed by an anchored
ISO-8601 pattern that requires an explicit UTC or offset marker and is
always normalized to aware UTC, never a naive local time — and never
reads a file or a clock. ``occurred_at`` is combined with
``context.byte_start`` (see ``RecordContext``) to derive ``event_id``,
because no reviewed fixture proves a native, agent-issued per-record
identifier and two distinct records can otherwise share an identical
discriminator, session, and even timestamp. An adapter never derives
``source_session_id``/``project_key``/``byte_start`` itself — all
three arrive already opaque through ``RecordContext``. A field with no
reviewed evidence for a given agent (for example Claude Code's
provider cost, or Codex's role/model) is always ``None``, never
guessed or estimated; a field with reviewed evidence that is simply
absent from one record is also ``None`` rather than a defaulted zero.

Codex's reviewed ``total_token_usage`` counters are a session-scoped
running total, not a per-record delta: successive Codex events for the
same session report cumulative counts, not new usage since the last
event. This adapter maps each Codex record's totals as observed and
performs no delta computation; any per-event derivation belongs to a
later, evidence-backed stage, not to this pure mapping.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from ..adapters import AdapterOutcome, CompleteRecord, RecordContext
from ..domain import DiagnosticKind, NormalizedEvent, ParseOutcome
from ..state import event_identifier

_SAFE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
"""The same compact structural-label pattern ``domain.py`` enforces for a
discriminator and for a mapped role/model/tool-category label."""

_UNKNOWN_DISCRIMINATOR = "unknown"
_TIMESTAMP = re.compile(
    r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\Z"
)

_OMP_TOOL_CATEGORIES: frozenset[str] = frozenset(
    {
        "read",
        "write",
        "edit",
        "eval",
        "glob",
        "grep",
        "bash",
        "task",
        "hub",
        "web_search",
        "yield",
    }
)
"""OMP's own stable, conservative top-level tool identifiers.

An OMP transcript's ``toolName`` names one of the harness's own fixed
top-level tools; this allowlist is exactly that fixed, reviewed set —
not a guess at every possible provider tool name. A ``toolName``
outside this set yields no ``safe_tool_category`` at all rather than
leaking an unreviewed raw tool identifier.
"""


class _RecordShapeError(Exception):
    """Raised internally when a present required field violates the
    reviewed shape (wrong type, missing required counter, or a
    non-finite number)."""


def _safe_discriminator(value: object) -> str:
    if isinstance(value, str) and _SAFE_LABEL.fullmatch(value):
        return value
    return _UNKNOWN_DISCRIMINATOR


def _diagnostic(
    agent: str, kind: DiagnosticKind, discriminator: object
) -> ParseOutcome:
    return ParseOutcome(
        agent=agent, discriminator=_safe_discriminator(discriminator), kind=kind
    )


def _require_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _RecordShapeError()
    return value


def _require_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _RecordShapeError()
    return value


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        raise _RecordShapeError()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise _RecordShapeError()
    return parsed.astimezone(UTC)


def _optional_label(container: Mapping[str, object], key: str) -> str | None:
    """Best-effort optional string label.

    A present value that cannot be represented as a safe label — the
    wrong type, empty, or containing characters ``NormalizedEvent``
    itself would reject — becomes ``None`` rather than invalidating the
    whole record: a formatting quirk in a secondary descriptive field
    must never cost an otherwise-parseable record its token counters.
    """
    value = container.get(key)
    if isinstance(value, str) and _SAFE_LABEL.fullmatch(value):
        return value
    return None


def _optional_int(container: Mapping[str, object], key: str) -> int | None:
    if key not in container:
        return None
    value = container[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise _RecordShapeError()
    return value


def _optional_number(container: Mapping[str, object], key: str) -> float | None:
    if key not in container:
        return None
    value = container[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _RecordShapeError()
    number = float(value)
    if not math.isfinite(number):
        raise _RecordShapeError()
    return number


def _optional_nested_int(
    container: Mapping[str, object], outer_key: str, inner_key: str
) -> int | None:
    if outer_key not in container:
        return None
    outer = container[outer_key]
    if outer is None:
        return None
    if not isinstance(outer, Mapping):
        raise _RecordShapeError()
    return _optional_int(outer, inner_key)


def _optional_nested_number(
    container: Mapping[str, object], outer_key: str, inner_key: str
) -> float | None:
    if outer_key not in container:
        return None
    outer = container[outer_key]
    if not isinstance(outer, Mapping):
        raise _RecordShapeError()
    return _optional_number(outer, inner_key)


def _omp_tool_category(tool_name: str | None) -> str | None:
    if tool_name is None or tool_name not in _OMP_TOOL_CATEGORIES:
        return None
    return tool_name


def _event_id(
    secret: bytes,
    agent: str,
    discriminator: str,
    context: RecordContext,
    occurred_at: datetime,
) -> str:
    # No reviewed fixture proves a native, agent-issued per-record
    # identifier, so `byte_start` — the record's absolute offset within
    # its source file, assigned by the ingestion service's own
    # byte-offset/watermark tracking — is the identity material that
    # keeps two records sharing the same discriminator, session, and
    # timestamp from colliding.
    return event_identifier(
        secret,
        agent,
        discriminator,
        context.source_session_id,
        str(context.byte_start),
        occurred_at.isoformat(),
    )


@dataclass(frozen=True, slots=True)
class ClaudeCodeAdapter:
    """Maps a reviewed Claude Code ``assistant`` record to a usage event."""

    agent: str = "claude-code"
    adapter_revision: int = 2

    def parse(
        self, record: CompleteRecord, *, context: RecordContext, secret: bytes
    ) -> AdapterOutcome:
        record_type = record.get("type")
        if record_type != "assistant":
            return _diagnostic(self.agent, "unsupported", record_type)

        message = record.get("message")
        message_map = message if isinstance(message, Mapping) else {}
        if "usage" not in message_map:
            return _diagnostic(self.agent, "unsupported", "assistant")

        try:
            occurred_at = _parse_timestamp(record.get("timestamp"))
            usage = _require_mapping(message_map.get("usage"))
            tokens_in = _require_int(usage.get("input_tokens"))
            tokens_out = _require_int(usage.get("output_tokens"))
            role = _optional_label(message_map, "role")
            model = _optional_label(message_map, "model")
            cache_read = _optional_int(usage, "cache_read_input_tokens")
            cache_write = _optional_int(usage, "cache_creation_input_tokens")
            reasoning_tokens = _optional_nested_int(
                usage, "output_tokens_details", "thinking_tokens"
            )
            return NormalizedEvent(
                event_id=_event_id(
                    secret, self.agent, "assistant", context, occurred_at
                ),
                agent=self.agent,
                source_session_id=context.source_session_id,
                project_key=context.project_key,
                occurred_at=occurred_at,
                record_kind="usage",
                provenance=f"{self.agent}.assistant",
                role=role,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cache_read=cache_read,
                cache_write=cache_write,
                reasoning_tokens=reasoning_tokens,
                provider_cost_usd=None,
                safe_tool_category=None,
            )
        except (_RecordShapeError, ValueError):
            return _diagnostic(self.agent, "malformed", "assistant")


@dataclass(frozen=True, slots=True)
class CodexAdapter:
    """Maps a reviewed Codex ``event_msg``/``token_count`` record to a usage event."""

    agent: str = "codex"
    adapter_revision: int = 1

    def parse(
        self, record: CompleteRecord, *, context: RecordContext, secret: bytes
    ) -> AdapterOutcome:
        record_type = record.get("type")
        if record_type != "event_msg":
            return _diagnostic(self.agent, "unsupported", record_type)

        payload = record.get("payload")
        payload_map = payload if isinstance(payload, Mapping) else {}
        payload_type = payload_map.get("type")
        if payload_type != "token_count":
            return _diagnostic(self.agent, "unsupported", payload_type)

        info = payload_map.get("info")
        info_map = info if isinstance(info, Mapping) else {}
        if "total_token_usage" not in info_map:
            return _diagnostic(self.agent, "unsupported", "token_count")

        try:
            occurred_at = _parse_timestamp(record.get("timestamp"))
            usage = _require_mapping(info_map.get("total_token_usage"))
            tokens_in = _require_int(usage.get("input_tokens"))
            tokens_out = _require_int(usage.get("output_tokens"))
            cache_read = _optional_int(usage, "cached_input_tokens")
            reasoning_tokens = _optional_int(usage, "reasoning_output_tokens")
            return NormalizedEvent(
                event_id=_event_id(
                    secret, self.agent, "token_count", context, occurred_at
                ),
                agent=self.agent,
                source_session_id=context.source_session_id,
                project_key=context.project_key,
                occurred_at=occurred_at,
                record_kind="usage",
                provenance=f"{self.agent}.token_count",
                role=None,
                model=None,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cache_read=cache_read,
                cache_write=None,
                reasoning_tokens=reasoning_tokens,
                provider_cost_usd=None,
                safe_tool_category=None,
            )
        except (_RecordShapeError, ValueError):
            return _diagnostic(self.agent, "malformed", "token_count")


@dataclass(frozen=True, slots=True)
class PiAdapter:
    """Maps a reviewed Pi ``message`` record to a usage event."""

    agent: str = "pi"
    adapter_revision: int = 1

    def parse(
        self, record: CompleteRecord, *, context: RecordContext, secret: bytes
    ) -> AdapterOutcome:
        record_type = record.get("type")
        if record_type != "message":
            return _diagnostic(self.agent, "unsupported", record_type)

        message = record.get("message")
        message_map = message if isinstance(message, Mapping) else {}
        if "usage" not in message_map:
            return _diagnostic(self.agent, "unsupported", "message")

        try:
            occurred_at = _parse_timestamp(record.get("timestamp"))
            usage = _require_mapping(message_map.get("usage"))
            tokens_in = _require_int(usage.get("input"))
            tokens_out = _require_int(usage.get("output"))
            role = _optional_label(message_map, "role")
            model = _optional_label(message_map, "model")
            cache_read = _optional_int(usage, "cacheRead")
            cache_write = _optional_int(usage, "cacheWrite")
            provider_cost_usd = _optional_nested_number(usage, "cost", "total")
            return NormalizedEvent(
                event_id=_event_id(secret, self.agent, "message", context, occurred_at),
                agent=self.agent,
                source_session_id=context.source_session_id,
                project_key=context.project_key,
                occurred_at=occurred_at,
                record_kind="usage",
                provenance=f"{self.agent}.message",
                role=role,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cache_read=cache_read,
                cache_write=cache_write,
                reasoning_tokens=None,
                provider_cost_usd=provider_cost_usd,
                safe_tool_category=None,
            )
        except (_RecordShapeError, ValueError):
            return _diagnostic(self.agent, "malformed", "message")


@dataclass(frozen=True, slots=True)
class OmpAdapter:
    """Maps a reviewed OMP ``message`` record to a usage event."""

    agent: str = "omp"
    adapter_revision: int = 1

    def parse(
        self, record: CompleteRecord, *, context: RecordContext, secret: bytes
    ) -> AdapterOutcome:
        record_type = record.get("type")
        if record_type != "message":
            return _diagnostic(self.agent, "unsupported", record_type)

        message = record.get("message")
        message_map = message if isinstance(message, Mapping) else {}
        if "usage" not in message_map:
            return _diagnostic(self.agent, "unsupported", "message")

        try:
            occurred_at = _parse_timestamp(record.get("timestamp"))
            usage = _require_mapping(message_map.get("usage"))
            tokens_in = _require_int(usage.get("input"))
            tokens_out = _require_int(usage.get("output"))
            role = _optional_label(message_map, "role")
            model = _optional_label(message_map, "model")
            cache_read = _optional_int(usage, "cacheRead")
            cache_write = _optional_int(usage, "cacheWrite")
            provider_cost_usd = _optional_nested_number(usage, "cost", "total")
            tool_name = _optional_label(message_map, "toolName")
            return NormalizedEvent(
                event_id=_event_id(secret, self.agent, "message", context, occurred_at),
                agent=self.agent,
                source_session_id=context.source_session_id,
                project_key=context.project_key,
                occurred_at=occurred_at,
                record_kind="usage",
                provenance=f"{self.agent}.message",
                role=role,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cache_read=cache_read,
                cache_write=cache_write,
                reasoning_tokens=None,
                provider_cost_usd=provider_cost_usd,
                safe_tool_category=_omp_tool_category(tool_name),
            )
        except (_RecordShapeError, ValueError):
            return _diagnostic(self.agent, "malformed", "message")
