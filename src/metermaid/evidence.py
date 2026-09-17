"""Pure dogfood-evidence derivation over self-recorded operational facts.

Two consecutive dogfood periods were blocked because their operational
record was hand-written and incomplete, not because the runtime
misbehaved. This module derives the record instead:
:func:`diagnostic_state_fingerprint` binds an acknowledgement to the
exact diagnostic state a command displayed, and
:func:`build_evidence_coverage` labels each day from stored heartbeats,
acknowledgements, and observed events.

Everything here is a pure function over already-normalized values. It
opens no database, reads no clock, and touches no source file, so the
closeout criterion is testable without either. Its inputs are local
runtime facts and opaque derived identifiers only — never a source
record, path, project name, or session identifier.

A day is labeled exactly one of:

``idle``
    No tracked-agent events were observed. Genuine inactivity is not a
    failure, so an idle day never counts against coverage.
``qualified``
    Events were observed, at least one acknowledged review exists for
    that day, and no gap between consecutive heartbeats exceeds the
    largest interval recorded that day times :data:`GAP_TOLERANCE`.
``incomplete``
    Events were observed but the review or continuity condition is
    unmet.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Literal, Protocol

from .domain import DiagnosticReview, NormalizedEvent, WatchHeartbeat
from .state import event_identifier

GAP_TOLERANCE = 3
"""Multiple of a day's recorded poll interval tolerated between heartbeats.

A foreground loop can be delayed by a slow ingest pass or a busy machine
without that meaning the watcher stopped, so continuity allows several
missed polls before a day is called incomplete.
"""

DayStatus = Literal["idle", "qualified", "incomplete"]


class DiagnosticStateRow(Protocol):
    """One displayed diagnostic row, however the display layer names it.

    Both the store's parse outcomes and ``doctor``'s rendered counts
    satisfy this shape, so a fingerprint can bind to exactly what a
    command displayed without either module depending on the other's
    concrete type.
    """

    @property
    def agent(self) -> str: ...

    @property
    def discriminator(self) -> str: ...

    @property
    def kind(self) -> str: ...

    @property
    def count(self) -> int: ...


def diagnostic_state_fingerprint(
    secret: bytes, rows: Sequence[DiagnosticStateRow]
) -> str:
    """Derive the opaque fingerprint of one displayed diagnostic state.

    Folds every displayed ``(agent, discriminator, kind, count)`` row,
    in a canonical order, through the machine-local secret. Any change to
    a count, kind, discriminator, or the set of rows yields a different
    fingerprint, so an acknowledgement cannot be inherited by a later
    state; identical displayed state yields a stable one.
    """
    ordered = sorted(
        (row.agent, row.discriminator, row.kind, row.count) for row in rows
    )
    parts = [str(len(ordered))]
    for agent, discriminator, kind, count in ordered:
        parts.extend((agent, discriminator, kind, str(count)))
    return event_identifier(secret, "diagnostic-state", *parts)


@dataclass(frozen=True, slots=True)
class EvidenceDay:
    """One day's recorded dogfood evidence and its resulting label."""

    day: date
    event_count: int
    heartbeat_count: int
    review_count: int
    longest_gap_seconds: int | None
    status: DayStatus


@dataclass(frozen=True, slots=True)
class EvidenceCoverage:
    """A window of per-day evidence plus its qualifying-day counts."""

    days: tuple[EvidenceDay, ...]

    @property
    def qualified_days(self) -> int:
        return sum(day.status == "qualified" for day in self.days)

    @property
    def incomplete_days(self) -> int:
        return sum(day.status == "incomplete" for day in self.days)

    @property
    def idle_days(self) -> int:
        return sum(day.status == "idle" for day in self.days)


def _day_of(moment: datetime) -> date:
    return moment.astimezone(UTC).date()


def _longest_gap_seconds(moments: Sequence[datetime]) -> int | None:
    if len(moments) < 2:
        return None
    ordered = sorted(moment.astimezone(UTC) for moment in moments)
    return max(
        int((later - earlier).total_seconds()) for earlier, later in pairwise(ordered)
    )


def build_evidence_coverage(
    events: Sequence[NormalizedEvent],
    heartbeats: Sequence[WatchHeartbeat],
    reviews: Sequence[DiagnosticReview],
    *,
    window_days: int,
    until: date,
) -> EvidenceCoverage:
    """Label each day of a trailing window from its recorded evidence."""
    if window_days < 1:
        raise ValueError("window_days must be positive")

    window = tuple(
        until - timedelta(days=offset) for offset in reversed(range(window_days))
    )
    included = frozenset(window)

    event_counts: dict[date, int] = {}
    for event in events:
        day = _day_of(event.occurred_at)
        if day in included:
            event_counts[day] = event_counts.get(day, 0) + 1

    beats: dict[date, list[WatchHeartbeat]] = {}
    for heartbeat in heartbeats:
        day = _day_of(heartbeat.observed_at)
        if day in included:
            beats.setdefault(day, []).append(heartbeat)

    review_counts: dict[date, int] = {}
    for review in reviews:
        day = _day_of(review.reviewed_at)
        if day in included:
            review_counts[day] = review_counts.get(day, 0) + 1

    days: list[EvidenceDay] = []
    for day in window:
        event_count = event_counts.get(day, 0)
        day_beats = beats.get(day, [])
        review_count = review_counts.get(day, 0)
        longest_gap = _longest_gap_seconds(
            [heartbeat.observed_at for heartbeat in day_beats]
        )
        days.append(
            EvidenceDay(
                day=day,
                event_count=event_count,
                heartbeat_count=len(day_beats),
                review_count=review_count,
                longest_gap_seconds=longest_gap,
                status=_status(
                    event_count=event_count,
                    review_count=review_count,
                    heartbeats=day_beats,
                    longest_gap_seconds=longest_gap,
                ),
            )
        )
    return EvidenceCoverage(days=tuple(days))


def _status(
    *,
    event_count: int,
    review_count: int,
    heartbeats: Sequence[WatchHeartbeat],
    longest_gap_seconds: int | None,
) -> DayStatus:
    if event_count == 0:
        return "idle"
    if review_count == 0 or not heartbeats:
        return "incomplete"
    tolerated = max(heartbeat.interval_seconds for heartbeat in heartbeats)
    tolerated *= GAP_TOLERANCE
    if longest_gap_seconds is not None and longest_gap_seconds > tolerated:
        return "incomplete"
    return "qualified"
