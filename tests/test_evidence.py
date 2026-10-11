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
    WatchRun,
)
from metermaid.evidence import (
    GAP_TOLERANCE,
    MAX_INACTIVE_GAP_SECONDS,
    MIN_QUALIFIED_DAYS,
    EvidenceCoverage,
    build_evidence_coverage,
    diagnostic_state_fingerprint,
    policy_effective_date,
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


def _run(
    run_id: str,
    *,
    started_at: datetime,
    booted_at: datetime | None,
) -> WatchRun:
    return WatchRun(run_id=run_id, started_at=started_at, booted_at=booted_at)


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
    assert day.tolerated_inactive_gap_count == 0
    assert day.review_count == 1
    assert coverage.qualified_days == 1


def test_active_day_without_an_acknowledged_review_is_incomplete() -> None:
    coverage = _coverage([_event()], _beats(4), [])

    assert coverage.days[0].status == "incomplete"
    assert coverage.incomplete_days == 1


def test_heartbeat_gap_with_tracked_activity_is_incomplete() -> None:
    interrupted = _beats(2, spacing=_INTERVAL * (GAP_TOLERANCE + 1))

    coverage = _coverage(
        [_event(), _event(_MIDDAY + timedelta(seconds=1), "7" * 64)],
        interrupted,
        [_review()],
    )

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


def test_gap_longer_than_the_inactive_budget_is_incomplete() -> None:
    poll_seconds = _INTERVAL * 3
    interrupted = _beats(
        2,
        spacing=MAX_INACTIVE_GAP_SECONDS + 1,
        poll_seconds=poll_seconds,
    )

    coverage = _coverage([_event()], interrupted, [_review()])

    assert coverage.days[0].status == "incomplete"


def test_one_slow_pass_does_not_excuse_an_unrelated_interruption() -> None:
    """Tolerance follows each gap's own polls, not the day's widest one.

    A single slow pass early in the day must not silently license a
    stoppage hours later, which a day-level maximum would have done.
    """
    slow = WatchHeartbeat(
        observed_at=_MIDDAY,
        run_id="4" * 64,
        interval_seconds=_INTERVAL,
        poll_seconds=_INTERVAL * 10,
    )
    fast_before = WatchHeartbeat(
        observed_at=_MIDDAY + timedelta(seconds=_INTERVAL),
        run_id="4" * 64,
        interval_seconds=_INTERVAL,
        poll_seconds=1,
    )
    fast_after = WatchHeartbeat(
        observed_at=_MIDDAY
        + timedelta(seconds=_INTERVAL + MAX_INACTIVE_GAP_SECONDS + 1),
        run_id="4" * 64,
        interval_seconds=_INTERVAL,
        poll_seconds=1,
    )

    coverage = _coverage([_event()], [slow, fast_before, fast_after], [_review()])

    assert coverage.days[0].status == "incomplete"


def test_verified_reboot_gap_keeps_a_reviewed_day_qualified() -> None:
    before = WatchHeartbeat(
        observed_at=_MIDDAY,
        run_id="4" * 64,
        interval_seconds=_INTERVAL,
        poll_seconds=0,
    )
    after = WatchHeartbeat(
        observed_at=_MIDDAY + timedelta(seconds=_INTERVAL * 4),
        run_id="6" * 64,
        interval_seconds=_INTERVAL,
        poll_seconds=0,
    )
    resumed = _run(
        "6" * 64,
        started_at=_MIDDAY + timedelta(seconds=_INTERVAL * 3),
        booted_at=_MIDDAY + timedelta(seconds=_INTERVAL * 2),
    )

    coverage = build_evidence_coverage(
        [_event()], [before, after], [_review()], [resumed], window_days=1, until=_DAY
    )

    assert coverage.days[0].status == "qualified"


def test_process_restart_with_tracked_activity_in_gap_is_incomplete() -> None:
    before = WatchHeartbeat(
        observed_at=_MIDDAY,
        run_id="4" * 64,
        interval_seconds=_INTERVAL,
        poll_seconds=0,
    )
    after = WatchHeartbeat(
        observed_at=_MIDDAY + timedelta(seconds=_INTERVAL * 4),
        run_id="6" * 64,
        interval_seconds=_INTERVAL,
        poll_seconds=0,
    )
    resumed = _run(
        "6" * 64,
        started_at=_MIDDAY + timedelta(seconds=_INTERVAL * 3),
        booted_at=_MIDDAY - timedelta(seconds=1),
    )

    coverage = build_evidence_coverage(
        [_event(), _event(_MIDDAY + timedelta(seconds=1), "7" * 64)],
        [before, after],
        [_review()],
        [resumed],
        window_days=1,
        until=_DAY,
    )

    assert coverage.days[0].status == "incomplete"


def test_heartbeat_without_a_measured_duration_falls_back_to_its_interval() -> None:
    legacy = _beats(2, spacing=MAX_INACTIVE_GAP_SECONDS + 1)

    coverage = _coverage([_event()], legacy, [_review()])

    assert coverage.days[0].status == "incomplete"


def test_one_event_free_gap_within_the_budget_is_qualified_and_visible() -> None:
    interrupted = _beats(2, spacing=MAX_INACTIVE_GAP_SECONDS)

    coverage = _coverage([_event()], interrupted, [_review()])

    day = coverage.days[0]
    assert day.status == "qualified"
    assert day.tolerated_inactive_gap_count == 1


def test_second_event_free_gap_in_one_day_is_incomplete() -> None:
    interrupted = _beats(3, spacing=MAX_INACTIVE_GAP_SECONDS)

    coverage = _coverage([_event()], interrupted, [_review()])

    assert coverage.days[0].status == "incomplete"
    assert coverage.days[0].tolerated_inactive_gap_count == 0


# --- precommitted closeout rule ---------------------------------------------


def _window(statuses: list[str]) -> EvidenceCoverage:
    """Build a window whose days land on the requested labels."""
    events: list[NormalizedEvent] = []
    heartbeats: list[WatchHeartbeat] = []
    reviews: list[DiagnosticReview] = []
    for offset, status in enumerate(statuses):
        moment = _MIDDAY - timedelta(days=len(statuses) - 1 - offset)
        if status == "idle":
            continue
        events.append(_event(moment, event_id=f"{offset:064d}"))
        if status == "qualified":
            reviews.append(_review(moment))
            heartbeats.extend(
                WatchHeartbeat(
                    observed_at=moment + timedelta(seconds=_INTERVAL * tick),
                    run_id="4" * 64,
                    interval_seconds=_INTERVAL,
                    poll_seconds=0,
                )
                for tick in range(3)
            )
    return build_evidence_coverage(
        events,
        heartbeats,
        reviews,
        window_days=len(statuses),
        until=_DAY,
    )


def test_closeout_passes_with_no_incomplete_day_and_enough_qualified_days() -> None:
    coverage = _window(["qualified"] * MIN_QUALIFIED_DAYS + ["idle"] * 4)

    assert coverage.qualified_days == MIN_QUALIFIED_DAYS
    assert coverage.incomplete_days == 0
    assert coverage.idle_days == 4
    assert coverage.closeout_passes is True


def test_a_single_incomplete_day_fails_the_closeout() -> None:
    coverage = _window(
        ["qualified"] * MIN_QUALIFIED_DAYS + ["incomplete"] + ["idle"] * 3
    )

    assert coverage.incomplete_days == 1
    assert coverage.closeout_passes is False


def test_closeout_requires_an_entire_window_after_policy_activation() -> None:
    coverage = _window(["qualified"] * MIN_QUALIFIED_DAYS + ["idle"] * 4)
    first_day = coverage.days[0].day

    assert coverage.closeout_passes_from(first_day) is True
    assert coverage.closeout_passes_from(first_day + timedelta(days=1)) is False


def test_policy_effective_date_is_the_next_full_utc_day() -> None:
    activated_at = datetime(2026, 9, 17, 23, 59, tzinfo=UTC)

    assert policy_effective_date(activated_at) == date(2026, 9, 18)


def test_a_mostly_idle_window_fails_the_closeout() -> None:
    coverage = _window(["qualified"] * (MIN_QUALIFIED_DAYS - 1) + ["idle"] * 5)

    assert coverage.incomplete_days == 0
    assert coverage.qualified_days < MIN_QUALIFIED_DAYS
    assert coverage.closeout_passes is False


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
