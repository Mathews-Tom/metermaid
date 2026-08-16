"""Immutable, text-free contracts for Metermaid v1 persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

DiagnosticKind = Literal["malformed", "parsed", "unsupported"]
_AGENTS = frozenset({"claude-code", "codex", "omp", "pi"})
_OPAQUE_IDENTIFIER = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


def _require_opaque_identifier(name: str, value: str) -> None:
    if _OPAQUE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a 64-character lowercase hex identifier")


def _require_safe_label(name: str, value: str | None) -> None:
    if value is not None and _SAFE_LABEL.fullmatch(value) is None:
        raise ValueError(f"{name} must be a compact structural label")


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    """One observed source record with no source text or raw location."""

    event_id: str
    agent: str
    source_session_id: str
    project_key: str
    occurred_at: datetime
    record_kind: str
    provenance: str
    schema_version: int = 1
    role: str | None = None
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cache_read: int | None = None
    cache_write: int | None = None
    reasoning_tokens: int | None = None
    provider_cost_usd: float | None = None
    safe_tool_category: str | None = None

    def __post_init__(self) -> None:
        if self.agent not in _AGENTS:
            raise ValueError(f"Unsupported Metermaid agent: {self.agent}")
        for name, value in (
            ("event_id", self.event_id),
            ("source_session_id", self.source_session_id),
            ("project_key", self.project_key),
        ):
            _require_opaque_identifier(name, value)
        for name, label in (
            ("record_kind", self.record_kind),
            ("provenance", self.provenance),
            ("role", self.role),
            ("model", self.model),
            ("safe_tool_category", self.safe_tool_category),
        ):
            _require_safe_label(name, label)
        if self.schema_version < 1:
            raise ValueError("schema_version must be positive")
        for name in (
            "tokens_in",
            "tokens_out",
            "cache_read",
            "cache_write",
            "reasoning_tokens",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.provider_cost_usd is not None and self.provider_cost_usd < 0:
            raise ValueError("provider_cost_usd must be non-negative")


@dataclass(frozen=True, slots=True)
class ParseOutcome:
    """Countable result for a source-record discriminator."""

    agent: str
    discriminator: str
    kind: DiagnosticKind
    count: int = 1

    def __post_init__(self) -> None:
        if self.agent not in _AGENTS:
            raise ValueError(f"Unsupported Metermaid agent: {self.agent}")
        _require_safe_label("discriminator", self.discriminator)
        if self.count < 1:
            raise ValueError("Parse outcome count must be positive")


@dataclass(frozen=True, slots=True)
class FileWatermark:
    """Opaque incremental-read state ending at a complete newline."""

    source_locator: str
    file_identity: str
    observed_size: int
    modified_ns: int
    complete_offset: int

    def __post_init__(self) -> None:
        _require_opaque_identifier("source_locator", self.source_locator)
        _require_opaque_identifier("file_identity", self.file_identity)
        if min(self.observed_size, self.modified_ns, self.complete_offset) < 0:
            raise ValueError("Watermark counters must be non-negative")
        if self.complete_offset > self.observed_size:
            raise ValueError("complete_offset cannot exceed observed_size")


@dataclass(frozen=True, slots=True)
class LegacySnapshot:
    """One idempotently imported v0.2 CSV row — never a normalized event.

    Explicitly isolated from :class:`NormalizedEvent`: nothing here is
    inferred, converted, or ever merged into ``events`` aggregates. Every
    field is one of the eight columns DM-003 allow-lists directly from the
    legacy ``Snapshot`` CSV row it was mapped from (timestamp, provider,
    model, cumulative token/cache counters, and cost). The raw path,
    session identifier, source provenance, deltas, sidechain metadata, and
    context/timing columns are never read, so they cannot appear here.
    """

    legacy_id: str
    source_fingerprint: str
    row_fingerprint: str
    imported_at: datetime
    timestamp: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    cache_read: int
    cache_write: int
    cost_usd: float

    def __post_init__(self) -> None:
        for name, value in (
            ("legacy_id", self.legacy_id),
            ("source_fingerprint", self.source_fingerprint),
            ("row_fingerprint", self.row_fingerprint),
        ):
            _require_opaque_identifier(name, value)
        for name in ("timestamp", "provider", "model"):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")
        for name in ("tokens_in", "tokens_out", "cache_read", "cache_write"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.cost_usd < 0:
            raise ValueError("cost_usd must be non-negative")
