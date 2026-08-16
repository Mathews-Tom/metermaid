"""Transactional incremental JSONL ingestion for Metermaid v1.

This module is the only place that opens a source transcript file, reads
bytes, decodes JSON, and drives a per-agent :mod:`metermaid.adapters`
implementation to normalized events. It never persists raw record text or
a raw filesystem path: every identifier handed to an adapter or written to
:class:`~metermaid.domain.FileWatermark` is derived through
:func:`metermaid.state.opaque_identifier`.

Discovery only considers the documented per-agent roots and glob patterns
from :mod:`metermaid.discover`; it never walks an undocumented directory,
and a resolved match that escapes its own root (for example through a
symlink) is skipped rather than ingested.

Reading is newline-safe and resumable: a file's :class:`FileWatermark`
records the absolute byte offset of the last *complete* (newline
terminated) record. An unterminated final line — a record still being
written — is never parsed and never advances the offset, so a later
ingest pass safely completes it instead of re-emitting or corrupting it.
A purely blank (whitespace-only) complete line is skipped without being
decoded or reported as a diagnostic, though it still advances the offset
like any other complete line.

A source file's *identity* folds its device and inode numbers together
with a fingerprint of its content, all through the machine-local secret,
derived entirely from one already-open file descriptor rather than a
separate, potentially racy path lookup (see :func:`read_increment`).
Identity is how a resume is validated against real content continuity
rather than size or device/inode alone: a rotated file (a different
inode — deleted and recreated, or a new session file reusing a stale
path) changes identity because its device/inode pair changes; a file
truncated and rewritten in place changes identity because its content
fingerprint almost always changes too, even when the rewritten content
happens to land on the same or a larger size than before. Either kind of
identity change forces a full, safe re-read from offset zero. A file
that merely shrinks below its previously observed size is treated the
same way, since its stored watermark could otherwise fail
``complete_offset <= observed_size``. Because identity captures which
generation of a path's content produced an event, it is folded into
that file's opaque source-session identifier too, so two different
generations at the same path can never collide onto the same derived
event identity even if they otherwise contain byte-identical records.

Reading one file in one ingest pass is bounded: at most
:data:`_MAX_READ_BYTES` is read per file per call, so a large backlog is
caught up incrementally over several passes instead of being loaded into
memory at once. A file whose read or stat raises ``OSError`` (removed
mid-scan, a permission change, a transient I/O error) is skipped for
that pass without affecting the counts already accumulated from other
files.

Every file is ingested through exactly one
:meth:`metermaid.store.EventStore.commit_ingest` call, so its normalized
events, parse diagnostics, and watermark update land together or not at
all.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from .adapters import RecordContext, SourceAdapter
from .discover import SourceRoot, documented_source_roots
from .domain import FileWatermark, NormalizedEvent, ParseOutcome
from .parsers import ClaudeCodeAdapter, CodexAdapter, OmpAdapter, PiAdapter
from .state import opaque_identifier
from .store import EventStore

_ADAPTERS: Mapping[str, SourceAdapter] = {
    "claude-code": ClaudeCodeAdapter(),
    "codex": CodexAdapter(),
    "pi": PiAdapter(),
    "omp": OmpAdapter(),
}
"""The pilot's four enabled, fixture-backed adapters, keyed by agent."""


def enabled_agents() -> frozenset[str]:
    """Return the pilot agents with a registered, fixture-backed adapter.

    ``doctor`` uses this to separate factual root discovery from an
    actually enabled parsing capability: a documented root existing on
    disk never implies its agent is enabled by itself.
    """
    return frozenset(_ADAPTERS)


_INVALID_JSON = "invalid-json"
"""Discriminator recorded when a line is not a decodable JSON object."""

_IDENTITY_SAMPLE_BYTES = 4096
"""Bytes sampled from a file's start to fingerprint its first complete line.

Appends only ever add bytes after this window, so the fingerprint is
stable for the life of one generation of content at a path.
"""

_MAX_READ_BYTES = 16 * 1024 * 1024
"""Upper bound on bytes read from one file in one ingest pass."""


@dataclass(frozen=True, slots=True)
class IngestSummary:
    """Aggregate, text-free result of one incremental ingest pass."""

    files_read: int
    events_inserted: int
    diagnostics_recorded: int


@dataclass(frozen=True, slots=True)
class CandidateFile:
    """One discovered concrete source file paired with its owning agent."""

    agent: str
    path: Path


@dataclass(frozen=True, slots=True)
class CompleteLine:
    """One complete, newline-terminated record and its absolute offset."""

    byte_start: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class ReadResult:
    """A file's newly read complete lines and its refreshed watermark."""

    watermark: FileWatermark
    lines: tuple[CompleteLine, ...]


def discover_candidate_files(
    roots: Sequence[SourceRoot],
) -> tuple[CandidateFile, ...]:
    """Resolve every documented root to its concrete, existing source files.

    Only a root already reported ``exists`` is globbed; discovery never
    lists an undocumented directory or applies an undocumented pattern. A
    match whose resolved, symlink-followed path is not actually contained
    within its own root directory is skipped: a root may only ever
    contribute files that really live under it. A path matched by more
    than one root (for example both a current and a legacy Claude Code
    project root resolving to the same file) is ingested once, keeping
    the first agent that claimed it.
    """
    seen: dict[Path, str] = {}
    for root in roots:
        if not root.exists:
            continue
        root_resolved = root.path.resolve()
        for match in sorted(root.path.glob(root.glob_pattern)):
            if not match.is_file():
                continue
            resolved = match.resolve()
            if not resolved.is_relative_to(root_resolved):
                continue
            seen.setdefault(resolved, root.agent)
    return tuple(CandidateFile(agent=agent, path=path) for path, agent in seen.items())


def _content_fingerprint(handle: IO[bytes]) -> str:
    """Fingerprint this generation's content from an already-open handle.

    Once the file has at least one complete line, the fingerprint is
    anchored to exactly that line's bytes — a boundary a later append
    never touches, so the fingerprint is stable for the rest of this
    generation's life. Before a first complete line exists, there is no
    such stable boundary yet, so the fingerprint instead covers whatever
    prefix bytes are currently available (up to
    :data:`_IDENTITY_SAMPLE_BYTES`). That value is not yet stable across
    polls, but it cannot matter: no complete line — and therefore no
    event — exists in this state either, so nothing can be resumed from
    or duplicated. Hashing the real available bytes here, rather than a
    single constant placeholder, only prevents two genuinely different
    partial files from colliding onto the same identity while both are
    still in this state.
    """
    handle.seek(0)
    sample = handle.read(_IDENTITY_SAMPLE_BYTES)
    newline_index = sample.find(b"\n")
    if newline_index != -1:
        sample = sample[: newline_index + 1]
    return hashlib.sha256(sample).hexdigest()


def _file_identity(
    secret: bytes, stat_result: os.stat_result, content_fingerprint: str
) -> str:
    material = f"{stat_result.st_dev}:{stat_result.st_ino}:{content_fingerprint}"
    return opaque_identifier(secret, "file-identity", material)


def _start_offset(
    previous: FileWatermark | None, identity: str, observed_size: int
) -> int:
    if previous is None:
        return 0
    if previous.file_identity != identity:
        return 0
    if observed_size < previous.observed_size:
        return 0
    return previous.complete_offset


def _split_complete_lines(data: bytes, start_offset: int) -> tuple[CompleteLine, ...]:
    last_newline = data.rfind(b"\n")
    if last_newline == -1:
        return ()
    complete = data[: last_newline + 1]
    lines: list[CompleteLine] = []
    offset = start_offset
    for segment in complete.split(b"\n")[:-1]:
        lines.append(CompleteLine(byte_start=offset, payload=segment))
        offset += len(segment) + 1
    return tuple(lines)


def read_increment(
    path: Path, previous: FileWatermark | None, source_locator: str, secret: bytes
) -> ReadResult:
    """Read up to one bounded batch of complete records newly written to
    ``path`` since ``previous``.

    A trailing, unterminated line is left unread: it neither appears in
    the returned lines nor advances ``complete_offset``, so a later call
    reads it once it gains its terminating newline.

    Identity is validated against content, not just size: it folds the
    file's device and inode together with a fingerprint of its content
    (see :func:`_content_fingerprint`), so a truncate-and-rewrite that
    happens to land on the same or a larger size than before — which a
    size-only check would miss — is still caught because its content
    differs, forcing a safe re-read from offset zero instead of a
    corrupt resume.

    Every piece of metadata — device, inode, size, modification time —
    and the fingerprint and data read below all come from
    ``os.fstat``/reads on the one file descriptor this call opens, never
    from a separate ``path.stat()`` taken before opening the file. That
    avoids a race where the path is replaced (rotated or rewritten)
    between a preliminary stat and the open: every value used here is
    guaranteed to describe the exact bytes this call actually reads,
    never a different file the path briefly pointed to.

    ``observed_size`` is the number of bytes this call actually saw
    (``start_offset`` plus the bytes read, capped at
    :data:`_MAX_READ_BYTES`), never a value from a separate, possibly
    racy ``stat`` call, keeping ``complete_offset <= observed_size`` true
    by construction. A backlog larger than the cap is caught up over
    multiple calls.
    """
    with path.open("rb") as handle:
        stat_result = os.fstat(handle.fileno())
        fingerprint = _content_fingerprint(handle)
        identity = _file_identity(secret, stat_result, fingerprint)
        start_offset = _start_offset(previous, identity, stat_result.st_size)
        handle.seek(start_offset)
        data = handle.read(_MAX_READ_BYTES)

    lines = _split_complete_lines(data, start_offset)
    complete_offset = start_offset + sum(len(line.payload) + 1 for line in lines)
    observed_size = start_offset + len(data)

    watermark = FileWatermark(
        source_locator=source_locator,
        file_identity=identity,
        observed_size=observed_size,
        modified_ns=stat_result.st_mtime_ns,
        complete_offset=complete_offset,
    )
    return ReadResult(watermark=watermark, lines=lines)


def _decode_line(
    agent: str, payload: bytes
) -> Mapping[str, object] | ParseOutcome | None:
    """Decode one complete line, or ``None`` for a whitespace-only line."""
    if not payload.strip():
        return None
    try:
        decoded = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ParseOutcome(agent=agent, discriminator=_INVALID_JSON, kind="malformed")
    if not isinstance(decoded, Mapping):
        return ParseOutcome(agent=agent, discriminator=_INVALID_JSON, kind="malformed")
    return decoded


def ingest_file(
    store: EventStore,
    candidate: CandidateFile,
    secret: bytes,
) -> IngestSummary:
    """Incrementally ingest one candidate file through one atomic commit."""
    adapter = _ADAPTERS[candidate.agent]
    resolved = str(candidate.path)
    source_locator = opaque_identifier(secret, "source-locator", resolved)
    project_key = opaque_identifier(secret, "project", str(candidate.path.parent))

    previous = store.watermark(source_locator)
    result = read_increment(candidate.path, previous, source_locator, secret)
    # Folding the file's identity into the session id keeps two different
    # generations of the same path (a truncation or a rotation) from ever
    # deriving the same event identity, even for byte-identical records.
    source_session_id = opaque_identifier(
        secret, "source-session", f"{resolved}:{result.watermark.file_identity}"
    )

    events: list[NormalizedEvent] = []
    outcomes: list[ParseOutcome] = []
    for line in result.lines:
        decoded = _decode_line(candidate.agent, line.payload)
        if decoded is None:
            continue
        if isinstance(decoded, ParseOutcome):
            outcomes.append(decoded)
            continue
        context = RecordContext(
            source_session_id=source_session_id,
            project_key=project_key,
            byte_start=line.byte_start,
        )
        outcome = adapter.parse(decoded, context=context, secret=secret)
        if isinstance(outcome, NormalizedEvent):
            events.append(outcome)
        else:
            outcomes.append(outcome)

    commit = store.commit_ingest(events, outcomes, result.watermark)
    return IngestSummary(
        files_read=1,
        events_inserted=commit.inserted_events,
        diagnostics_recorded=sum(outcome.count for outcome in outcomes),
    )


def ingest_once(
    store: EventStore,
    secret: bytes,
    *,
    roots: Sequence[SourceRoot] | None = None,
) -> IngestSummary:
    """Discover every documented source and ingest each incrementally.

    ``roots`` overrides the scanned roots for tests; the default reuses
    :func:`metermaid.discover.documented_source_roots`. A file that
    raises ``OSError`` while being read (removed mid-scan, a permission
    change, a transient I/O error) is skipped on its own: the counts
    already accumulated from files read before it are kept, and files
    discovered after it are still attempted.
    """
    resolved_roots = tuple(roots) if roots is not None else documented_source_roots()
    candidates = discover_candidate_files(resolved_roots)

    files_read = 0
    events_inserted = 0
    diagnostics_recorded = 0
    for candidate in candidates:
        try:
            summary = ingest_file(store, candidate, secret)
        except OSError:
            continue
        files_read += summary.files_read
        events_inserted += summary.events_inserted
        diagnostics_recorded += summary.diagnostics_recorded

    return IngestSummary(
        files_read=files_read,
        events_inserted=events_inserted,
        diagnostics_recorded=diagnostics_recorded,
    )
