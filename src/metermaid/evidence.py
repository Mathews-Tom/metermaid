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

from .domain import DiagnosticReview, NormalizedEvent, WatchHeartbeat, WatchRun
from .state import event_identifier

GAP_TOLERANCE = 2
"""Multiple of a gap's own adjoining poll periods tolerated between beats.

A poll's real period is the configured interval plus the ingest work that
poll performed, which on a large corpus dominates the interval outright.
Each gap is judged against the slower of the two heartbeats bounding it,
never against the day's widest period: one slow pass must not silently
excuse an unrelated interruption hours later. Doubling that period
absorbs a busy machine while still exposing a watcher that stopped.
Heartbeats predating the measured duration fall back to the interval
alone, which is all the evidence they carry.
"""

MAX_INACTIVE_GAP_SECONDS = 30 * 60
"""Maximum duration of one event-free, tolerated gap on an active UTC day."""

MAX_TOLERATED_INACTIVE_GAPS_PER_DAY = 1
"""Event-free gaps an active UTC day may tolerate under the exploratory rule."""

MIN_QUALIFIED_DAYS = 10
"""Qualified days a 14-day window needs before a closeout may pass.

Zero incomplete days is the real assertion: no unexplained watcher gap
and no active day without an acknowledged review. This floor additionally
stops a window from passing while mostly idle, while still tolerating a
few genuinely inactive days such as a weekend.
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
    tolerated_inactive_gap_count: int
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

    @property
    def closeout_passes(self) -> bool:
        """Report whether this window satisfies the fixed closeout criterion."""
        return self.incomplete_days == 0 and self.qualified_days >= MIN_QUALIFIED_DAYS

    def closeout_passes_from(self, effective_date: date) -> bool:
        """Report whether this complete window qualifies after policy activation."""
        return (
            bool(self.days)
            and self.days[0].day >= effective_date
            and self.closeout_passes
        )


def _day_of(moment: datetime) -> date:
    return moment.astimezone(UTC).date()


def policy_effective_date(activated_at: datetime) -> date:
    """Start formal accounting on the full UTC day after activation."""
    return _day_of(activated_at) + timedelta(days=1)


def _real_period(heartbeat: WatchHeartbeat) -> int:
    """Return a poll's interval plus the ingest work it performed."""
    return heartbeat.interval_seconds + (heartbeat.poll_seconds or 0)


def _ordered(heartbeats: Sequence[WatchHeartbeat]) -> list[WatchHeartbeat]:
    return sorted(heartbeats, key=lambda heartbeat: heartbeat.observed_at)


def _longest_gap_seconds(heartbeats: Sequence[WatchHeartbeat]) -> int | None:
    if len(heartbeats) < 2:
        return None
    ordered = _ordered(heartbeats)
    return max(
        int((later.observed_at - earlier.observed_at).total_seconds())
        for earlier, later in pairwise(ordered)
    )


def _is_verified_reboot_boundary(
    earlier: WatchHeartbeat,
    later: WatchHeartbeat,
    runs_by_id: dict[str, WatchRun],
) -> bool:
    """Report whether a widened cross-run gap contains the later boot."""

    if earlier.run_id == later.run_id:
        return False
    resumed = runs_by_id.get(later.run_id)
    if resumed is None or resumed.booted_at is None:
        return False
    return (
        earlier.observed_at
        < resumed.booted_at
        <= resumed.started_at
        <= later.observed_at
    )


def _tolerated_inactive_gap_count(
    events: Sequence[NormalizedEvent],
    heartbeats: Sequence[WatchHeartbeat],
    runs_by_id: dict[str, WatchRun],
) -> int | None:
    """Return accepted inactive gaps, or ``None`` for unexplained continuity.

    Every over-tolerance gap remains represented by its two heartbeat
    endpoints. The exploratory rule accepts only one bounded interval per
    active UTC day and only when no normalized event occurred after the
    prior heartbeat and by the resumed heartbeat.
    """

    accepted = 0
    for earlier, later in pairwise(_ordered(heartbeats)):
        gap = (later.observed_at - earlier.observed_at).total_seconds()
        tolerated = GAP_TOLERANCE * max(_real_period(earlier), _real_period(later))
        if gap <= tolerated or _is_verified_reboot_boundary(earlier, later, runs_by_id):
            continue
        if gap > MAX_INACTIVE_GAP_SECONDS:
            return None
        if any(
            earlier.observed_at < event.occurred_at <= later.observed_at
            for event in events
        ):
            return None
        accepted += 1
        if accepted > MAX_TOLERATED_INACTIVE_GAPS_PER_DAY:
            return None
    return accepted


def build_evidence_coverage(
    events: Sequence[NormalizedEvent],
    heartbeats: Sequence[WatchHeartbeat],
    reviews: Sequence[DiagnosticReview],
    runs: Sequence[WatchRun] = (),
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
    events_by_day: dict[date, list[NormalizedEvent]] = {}
    for event in events:
        day = _day_of(event.occurred_at)
        if day in included:
            event_counts[day] = event_counts.get(day, 0) + 1
            events_by_day.setdefault(day, []).append(event)

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

    runs_by_id = {run.run_id: run for run in runs}

    days: list[EvidenceDay] = []
    for day in window:
        event_count = event_counts.get(day, 0)
        day_events = events_by_day.get(day, [])
        day_beats = beats.get(day, [])
        review_count = review_counts.get(day, 0)
        longest_gap = _longest_gap_seconds(day_beats)
        inactive_gaps = _tolerated_inactive_gap_count(day_events, day_beats, runs_by_id)
        days.append(
            EvidenceDay(
                day=day,
                event_count=event_count,
                heartbeat_count=len(day_beats),
                review_count=review_count,
                longest_gap_seconds=longest_gap,
                tolerated_inactive_gap_count=(
                    0 if inactive_gaps is None else inactive_gaps
                ),
                status=_status(
                    event_count=event_count,
                    review_count=review_count,
                    heartbeats=day_beats,
                    inactive_gaps=inactive_gaps,
                ),
            )
        )
    return EvidenceCoverage(days=tuple(days))


def _status(
    *,
    event_count: int,
    review_count: int,
    heartbeats: Sequence[WatchHeartbeat],
    inactive_gaps: int | None,
) -> DayStatus:
    if event_count == 0:
        return "idle"
    if review_count == 0 or not heartbeats or inactive_gaps is None:
        return "incomplete"
    return "qualified"
