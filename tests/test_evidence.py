"""Contracts for self-recorded dogfood evidence (M5b).

Covers the fingerprint that binds an acknowledgement to the diagnostic
state a command actually displayed, and the per-day coverage criterion
that decides whether a dogfood day is idle, qualified, or incomplete.
Both are pure, so no database or clock participates here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from metermaid.domain import (
    DiagnosticReview,
    NormalizedEvent,
    ParseOutcome,
    WatchHeartbeat,
)
from metermaid.evidence import (
    GAP_TOLERANCE,
    EvidenceCoverage,
    build_evidence_coverage,
    diagnostic_state_fingerprint,
)

_SECRET = b"s" * 32
_DAY = date(2026, 9, 17)
_MIDDAY = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
_INTERVAL = 30


def _event(moment: datetime = _MIDDAY, event_id: str = "1" * 64) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=event_id,
        agent="omp",
        source_session_id="2" * 64,
        project_key="3" * 64,
        occurred_at=moment,
        record_kind="usage",
        provenance="omp.message",
    )


def _beats(
    count: int, *, spacing: int = _INTERVAL, poll_seconds: int | None = None
) -> list[WatchHeartbeat]:
    return [
        WatchHeartbeat(
            observed_at=_MIDDAY + timedelta(seconds=spacing * offset),
            run_id="4" * 64,
            interval_seconds=_INTERVAL,
            poll_seconds=poll_seconds,
        )
        for offset in range(count)
    ]


def _review(moment: datetime = _MIDDAY) -> DiagnosticReview:
    return DiagnosticReview(
        reviewed_at=moment, state_fingerprint="5" * 64, diagnostic_count=3
    )


def _coverage(
    events: list[NormalizedEvent],
    heartbeats: list[WatchHeartbeat],
    reviews: list[DiagnosticReview],
) -> EvidenceCoverage:
    return build_evidence_coverage(
        events, heartbeats, reviews, window_days=1, until=_DAY
    )


# --- acknowledgement binds to displayed diagnostic state --------------------


def test_fingerprint_is_stable_for_identical_displayed_state() -> None:
    first = [
        ParseOutcome(agent="omp", discriminator="message", kind="unsupported", count=4),
        ParseOutcome(
            agent="codex", discriminator="token_count", kind="parsed", count=9
        ),
    ]
    reordered = list(reversed(first))

    assert diagnostic_state_fingerprint(_SECRET, first) == diagnostic_state_fingerprint(
        _SECRET, reordered
    )


def test_fingerprint_changes_when_a_displayed_count_changes() -> None:
    before = [
        ParseOutcome(
            agent="omp", discriminator="model_usage", kind="unsupported", count=5
        )
    ]
    after = [
        ParseOutcome(
            agent="omp", discriminator="model_usage", kind="unsupported", count=6
        )
    ]

    assert diagnostic_state_fingerprint(
        _SECRET, before
    ) != diagnostic_state_fingerprint(_SECRET, after)


def test_fingerprint_changes_when_a_new_discriminator_appears() -> None:
    known = [
        ParseOutcome(agent="omp", discriminator="message", kind="unsupported", count=2)
    ]
    widened = known + [
        ParseOutcome(
            agent="omp", discriminator="model_usage", kind="unsupported", count=2
        )
    ]

    assert diagnostic_state_fingerprint(_SECRET, known) != diagnostic_state_fingerprint(
        _SECRET, widened
    )


# --- per-day coverage criterion ---------------------------------------------


def test_reviewed_and_continuously_watched_day_qualifies() -> None:
    coverage = _coverage([_event()], _beats(4), [_review()])

    day = coverage.days[0]
    assert day.status == "qualified"
    assert day.event_count == 1
    assert day.heartbeat_count == 4
    assert day.review_count == 1
    assert coverage.qualified_days == 1


def test_active_day_without_an_acknowledged_review_is_incomplete() -> None:
    coverage = _coverage([_event()], _beats(4), [])

    assert coverage.days[0].status == "incomplete"
    assert coverage.incomplete_days == 1


def test_heartbeat_gap_beyond_tolerance_makes_a_reviewed_day_incomplete() -> None:
    interrupted = _beats(2, spacing=_INTERVAL * (GAP_TOLERANCE + 1))

    coverage = _coverage([_event()], interrupted, [_review()])

    day = coverage.days[0]
    assert day.status == "incomplete"
    assert day.longest_gap_seconds == _INTERVAL * (GAP_TOLERANCE + 1)


def test_slow_ingest_pass_does_not_look_like_a_stopped_watcher() -> None:
    """A poll's real period is its interval plus its own ingest work.

    On a large corpus that work dominates the interval, so a gap far
    wider than the interval is normal cadence rather than downtime.
    """
    poll_seconds = _INTERVAL * 3
    cadence = _INTERVAL + poll_seconds
    busy = _beats(4, spacing=cadence, poll_seconds=poll_seconds)

    coverage = _coverage([_event()], busy, [_review()])

    day = coverage.days[0]
    assert day.longest_gap_seconds == cadence
    assert cadence > _INTERVAL * GAP_TOLERANCE
    assert day.status == "qualified"


def test_stopped_watcher_is_incomplete_even_with_slow_polls() -> None:
    poll_seconds = _INTERVAL * 3
    downtime = (_INTERVAL + poll_seconds) * GAP_TOLERANCE + 1
    interrupted = _beats(2, spacing=downtime, poll_seconds=poll_seconds)

    coverage = _coverage([_event()], interrupted, [_review()])

    assert coverage.days[0].status == "incomplete"


def test_heartbeat_without_a_measured_duration_falls_back_to_its_interval() -> None:
    legacy = _beats(2, spacing=_INTERVAL * GAP_TOLERANCE + 1)

    coverage = _coverage([_event()], legacy, [_review()])

    assert coverage.days[0].status == "incomplete"


def test_active_day_without_any_heartbeat_is_incomplete() -> None:
    coverage = _coverage([_event()], [], [_review()])

    assert coverage.days[0].status == "incomplete"


def test_day_without_tracked_activity_is_idle_not_a_failure() -> None:
    coverage = _coverage([], _beats(4), [_review()])

    day = coverage.days[0]
    assert day.status == "idle"
    assert coverage.idle_days == 1
    assert coverage.incomplete_days == 0


def test_window_covers_every_requested_day_in_order() -> None:
    coverage = build_evidence_coverage(
        [_event()], _beats(2), [_review()], window_days=3, until=_DAY
    )

    assert [day.day for day in coverage.days] == [
        date(2026, 9, 15),
        date(2026, 9, 16),
        _DAY,
    ]
    assert [day.status for day in coverage.days] == ["idle", "idle", "qualified"]


def test_evidence_outside_the_window_is_excluded() -> None:
    stale = _MIDDAY - timedelta(days=5)

    coverage = _coverage([_event(stale)], _beats(2), [_review(stale)])

    assert coverage.days[0].status == "idle"
    assert coverage.days[0].event_count == 0
    assert coverage.days[0].review_count == 0


def test_window_days_must_be_positive() -> None:
    with pytest.raises(ValueError, match="window_days must be positive"):
        build_evidence_coverage([], [], [], window_days=0, until=_DAY)
