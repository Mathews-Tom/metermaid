"""Aggregate, text-free reporting over current normalized events.

A report is an observed aggregate over the events already committed to
the v1 store — never a legacy CSV snapshot, an estimate, or an
invented measurement. Every numeric total follows SQL ``SUM``
semantics: it is the sum of every non-``None`` contributing value, and
it stays ``None`` only when *no* selected event carries that field —
the distinction the pilot design requires between a genuinely observed
zero and a capability nothing supplied. Rendering must show ``None``
as exactly ``"unavailable"``, never a computed zero.

Grouping is limited to the three categorical dimensions the pilot
design names: agent, model, and opaque project key. ``model`` is
absent on some records (a source or record kind may not report one);
those events group under the ``UNAVAILABLE_MODEL`` label rather than
being silently dropped. ``project_key`` is a required field on every
``NormalizedEvent`` and therefore never needs that fallback. Every
output value here is a count, a sum, or a compact structural label —
never a path, a source record, a prompt, or free text.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from .domain import NormalizedEvent

UNAVAILABLE_MODEL = "unavailable"
"""Group label for events whose ``model`` field was not observed."""


@dataclass(frozen=True, slots=True)
class ReportFilter:
    """Optional current-event selection: range, agent, model, project key.

    ``since``/``until`` are both inclusive bounds on ``occurred_at`` and
    must be timezone-aware to compare against stored events. A ``None``
    bound leaves that side of the range open.
    """

    since: datetime | None = None
    until: datetime | None = None
    agent: str | None = None
    model: str | None = None
    project_key: str | None = None

    def __post_init__(self) -> None:
        for name, bound in (("since", self.since), ("until", self.until)):
            if bound is not None and bound.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if (
            self.since is not None
            and self.until is not None
            and self.since > self.until
        ):
            raise ValueError("since must not be after until")


@dataclass(frozen=True, slots=True)
class TokenTotals:
    """Summed token counters; ``None`` means never observed, not zero."""

    tokens_in: int | None
    tokens_out: int | None
    cache_read: int | None
    cache_write: int | None
    reasoning_tokens: int | None


@dataclass(frozen=True, slots=True)
class GroupAggregate:
    """One aggregate row keyed by agent, model, or opaque project key."""

    key: str
    event_count: int
    session_count: int
    tokens: TokenTotals
    provider_cost_usd: float | None


@dataclass(frozen=True, slots=True)
class ObservedReport:
    """Whole-selection totals plus per-agent/model/project breakdowns."""

    event_count: int
    session_count: int
    tokens: TokenTotals
    provider_cost_usd: float | None
    by_agent: tuple[GroupAggregate, ...]
    by_model: tuple[GroupAggregate, ...]
    by_project_key: tuple[GroupAggregate, ...]


def select_events(
    events: Sequence[NormalizedEvent], filter_: ReportFilter
) -> list[NormalizedEvent]:
    """Return events matching every populated dimension of ``filter_``."""
    selected = events
    if filter_.since is not None:
        since = filter_.since
        selected = [event for event in selected if event.occurred_at >= since]
    if filter_.until is not None:
        until = filter_.until
        selected = [event for event in selected if event.occurred_at <= until]
    if filter_.agent is not None:
        agent = filter_.agent
        selected = [event for event in selected if event.agent == agent]
    if filter_.model is not None:
        model = filter_.model
        selected = [event for event in selected if event.model == model]
    if filter_.project_key is not None:
        project_key = filter_.project_key
        selected = [event for event in selected if event.project_key == project_key]
    return list(selected)


def _sum_optional_int(values: Sequence[int | None]) -> int | None:
    observed = [value for value in values if value is not None]
    return sum(observed) if observed else None


def _sum_optional_float(values: Sequence[float | None]) -> float | None:
    observed = [value for value in values if value is not None]
    return sum(observed) if observed else None


def _token_totals(events: Sequence[NormalizedEvent]) -> TokenTotals:
    return TokenTotals(
        tokens_in=_sum_optional_int([event.tokens_in for event in events]),
        tokens_out=_sum_optional_int([event.tokens_out for event in events]),
        cache_read=_sum_optional_int([event.cache_read for event in events]),
        cache_write=_sum_optional_int([event.cache_write for event in events]),
        reasoning_tokens=_sum_optional_int(
            [event.reasoning_tokens for event in events]
        ),
    )


def _aggregate(key: str, events: Sequence[NormalizedEvent]) -> GroupAggregate:
    sessions = {event.source_session_id for event in events}
    return GroupAggregate(
        key=key,
        event_count=len(events),
        session_count=len(sessions),
        tokens=_token_totals(events),
        provider_cost_usd=_sum_optional_float(
            [event.provider_cost_usd for event in events]
        ),
    )


def _group_by(
    events: Sequence[NormalizedEvent], key_fn: Callable[[NormalizedEvent], str]
) -> tuple[GroupAggregate, ...]:
    keys: dict[str, list[NormalizedEvent]] = {}
    for event in events:
        keys.setdefault(key_fn(event), []).append(event)
    return tuple(_aggregate(key, group) for key, group in sorted(keys.items()))


def build_report(
    events: Sequence[NormalizedEvent], filter_: ReportFilter | None = None
) -> ObservedReport:
    """Build one observed-aggregate report from current normalized events."""
    selected = select_events(events, filter_ if filter_ is not None else ReportFilter())
    sessions = {event.source_session_id for event in selected}

    return ObservedReport(
        event_count=len(selected),
        session_count=len(sessions),
        tokens=_token_totals(selected),
        provider_cost_usd=_sum_optional_float(
            [event.provider_cost_usd for event in selected]
        ),
        by_agent=_group_by(selected, lambda event: event.agent),
        by_model=_group_by(selected, lambda event: event.model or UNAVAILABLE_MODEL),
        by_project_key=_group_by(selected, lambda event: event.project_key),
    )
