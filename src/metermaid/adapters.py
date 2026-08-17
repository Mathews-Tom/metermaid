"""Pure per-agent source-adapter protocol for Metermaid v1.

An adapter maps one complete, already-decoded JSONL record — plus a
pre-derived opaque record context — to either a normalized event or a
countable parse outcome. Adapters are pure: implementations must not
read a file, call a clock, or write to storage. An adapter never
derives a source session ID or a project key itself; the ingestion
service resolves each source file's identity and supplies both,
already opaque, through ``RecordContext``. Source traversal,
incremental byte offsets, and watermark state also belong to the
ingestion service, not to an adapter. This module defines the
structural contract only; concrete per-agent parsing logic is
implemented separately once each source has a reviewed fixture and a
passing contract test.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .domain import NormalizedEvent, ParseOutcome

CompleteRecord = Mapping[str, object]
"""One fully decoded JSON object for a single complete newline-terminated record."""

AdapterOutcome = NormalizedEvent | ParseOutcome
"""A mapped event, or a countable diagnostic for an unsupported/malformed record."""

_OPAQUE_IDENTIFIER = re.compile(r"[0-9a-f]{64}\Z")
"""The same opaque-identifier shape ``domain.py`` requires of every
``NormalizedEvent`` identifier field, enforced here too so an invalid
``RecordContext`` fails at construction rather than surfacing later as
a misleading per-record parse diagnostic."""


def _require_opaque_identifier(name: str, value: str) -> None:
    if _OPAQUE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a 64-character lowercase hex identifier")


@dataclass(frozen=True, slots=True)
class RecordContext:
    """Opaque per-file identity the ingestion service derives and supplies.

    Neither ``source_session_id`` nor ``project_key`` is derivable from
    a single record on its own: no reviewed fixture carries a project
    identifier, and only Claude Code's record shape carries an
    in-record session identifier. The ingestion service resolves both
    from the source file it is reading and passes them here already
    opaque, so an adapter can stay pure and never needs its own file,
    path, or project-name access.

    ``byte_start`` is the record's absolute byte offset within its
    source file — the same coordinate the ingestion service's own
    incremental byte-offset/watermark reader already tracks to resume
    a file, reused here rather than introducing a second, separate
    per-file counter. No reviewed fixture proves a native, agent-issued
    per-record identifier, so an adapter cannot derive a stable event
    identity from record content plus timestamp alone — two distinct
    records can legitimately share the same discriminator, session,
    and even the same recorded timestamp. ``byte_start`` is the
    identity material that keeps their derived ``event_id``s distinct;
    it carries no meaning outside identity derivation and is never a
    database primary key. It is required, not defaulted: a caller must
    always supply the real offset it read the record from.

    ``source_session_id`` and ``project_key`` are validated as opaque
    64-character lowercase hex identifiers at construction — the same
    format ``NormalizedEvent`` itself requires. Failing loud here, at
    the boundary where the ingestion service hands identity to an
    adapter, turns a caller bug (an unhashed session ID, a truncated
    key) into an immediate, attributable error instead of a record
    later being misreported as an unrelated ``malformed`` parse
    diagnostic.
    """

    source_session_id: str
    project_key: str
    byte_start: int

    def __post_init__(self) -> None:
        _require_opaque_identifier("source_session_id", self.source_session_id)
        _require_opaque_identifier("project_key", self.project_key)
        if self.byte_start < 0:
            raise ValueError("byte_start cannot be negative")


@runtime_checkable
class SourceAdapter(Protocol):
    """Structural contract every concrete per-agent adapter must satisfy."""

    @property
    def agent(self) -> str:
        """The fixed pilot agent this adapter parses records for.

        A read-only property so a conforming implementation may declare
        it as a class constant, an instance attribute, or a computed
        property; only readability is part of the contract.
        """
        ...

    @property
    def adapter_revision(self) -> int:
        """The positive semantic revision used to recover records with no event.

        A replay retains event identity, so it cannot restate a row that
        already exists with different mapped values.
        """
        ...

    def parse(
        self, record: CompleteRecord, *, context: RecordContext, secret: bytes
    ) -> AdapterOutcome:
        """Map one complete record to a normalized event or a parse outcome.

        Implementations must be pure and deterministic given ``record``,
        ``context``, and ``secret``: no filesystem access, no clock
        reads, no database writes. ``occurred_at`` on a returned event
        must derive from a timestamp field within ``record``, never from
        the current time.
        """
        ...
