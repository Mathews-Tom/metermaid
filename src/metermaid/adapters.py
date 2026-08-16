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

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .domain import NormalizedEvent, ParseOutcome

CompleteRecord = Mapping[str, object]
"""One fully decoded JSON object for a single complete newline-terminated record."""

AdapterOutcome = NormalizedEvent | ParseOutcome
"""A mapped event, or a countable diagnostic for an unsupported/malformed record."""


@dataclass(frozen=True, slots=True)
class RecordContext:
    """Opaque per-file identity the ingestion service derives and supplies.

    Neither field is derivable from a single record on its own: no
    reviewed fixture carries a project identifier, and only Claude
    Code's record shape carries an in-record session identifier. The
    ingestion service resolves both from the source file it is reading
    and passes them here already opaque, so an adapter can stay pure
    and never needs its own file, path, or project-name access.
    """

    source_session_id: str
    project_key: str


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
