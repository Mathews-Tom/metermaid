"""Contracts for `metermaid report`'s current-event aggregation (M4 PR1).

Covers grouping by agent/model/opaque project key, range/agent/model/
project-key filtering, missing-vs-observed-zero null semantics, and
privacy-safe CLI rendering with no raw path, session id, or free text.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from metermaid.cli import main
from metermaid.domain import NormalizedEvent
from metermaid.report_v1 import (
    UNAVAILABLE_MODEL,
    ReportFilter,
    build_report,
    select_events,
)
from metermaid.state import load_or_create_secret, resolve_state_paths
from metermaid.store import EventStore

_T0 = datetime(2026, 8, 16, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 8, 16, 6, 0, tzinfo=UTC)
_T2 = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def _event(
    *,
    event_id: str = "1" * 64,
    agent: str = "codex",
    source_session_id: str = "a" * 64,
    project_key: str = "b" * 64,
    occurred_at: datetime = _T0,
    model: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cache_read: int | None = None,
    cache_write: int | None = None,
    reasoning_tokens: int | None = None,
    provider_cost_usd: float | None = None,
) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=event_id,
        agent=agent,
        source_session_id=source_session_id,
        project_key=project_key,
        occurred_at=occurred_at,
        record_kind="usage",
        provenance=f"{agent}.message",
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_read=cache_read,
        cache_write=cache_write,
        reasoning_tokens=reasoning_tokens,
        provider_cost_usd=provider_cost_usd,
    )


# --- aggregation: null vs observed-zero semantics ---------------------------


def test_build_report_sums_only_observed_values_and_ignores_missing_ones() -> None:
    events = [
        _event(event_id="1" * 64, tokens_in=100, tokens_out=None),
        _event(event_id="2" * 64, tokens_in=None, tokens_out=20),
    ]

    observed = build_report(events)

    assert observed.tokens.tokens_in == 100
    assert observed.tokens.tokens_out == 20


def test_build_report_leaves_a_totally_unobserved_counter_as_none() -> None:
    events = [_event(event_id="1" * 64, cache_read=None)]

    observed = build_report(events)

    assert observed.tokens.cache_read is None


def test_build_report_distinguishes_an_observed_zero_from_missing() -> None:
    events = [_event(event_id="1" * 64, tokens_out=0)]

    observed = build_report(events)

    assert observed.tokens.tokens_out == 0


def test_build_report_sums_provider_cost_and_leaves_it_none_when_unobserved() -> None:
    priced = _event(event_id="1" * 64, provider_cost_usd=0.5)
    unpriced = _event(event_id="2" * 64, provider_cost_usd=None)

    assert build_report([priced]).provider_cost_usd == 0.5
    assert build_report([unpriced]).provider_cost_usd is None


def test_build_report_on_no_events_has_zero_counts_and_no_groups() -> None:
    observed = build_report([])

    assert observed.event_count == 0
    assert observed.session_count == 0
    assert observed.tokens.tokens_in is None
    assert observed.provider_cost_usd is None
    assert observed.by_agent == ()
    assert observed.by_model == ()
    assert observed.by_project_key == ()


def test_session_count_deduplicates_by_source_session_id() -> None:
    events = [
        _event(event_id="1" * 64, source_session_id="a" * 64),
        _event(event_id="2" * 64, source_session_id="a" * 64),
        _event(event_id="3" * 64, source_session_id="c" * 64),
    ]

    observed = build_report(events)

    assert observed.event_count == 3
    assert observed.session_count == 2


# --- grouping: agent / model / opaque project key ----------------------------


def test_group_by_agent_partitions_and_sums_per_agent() -> None:
    events = [
        _event(event_id="1" * 64, agent="codex", tokens_in=10),
        _event(event_id="2" * 64, agent="codex", tokens_in=5),
        _event(event_id="3" * 64, agent="omp", tokens_in=1),
    ]

    observed = build_report(events)
    by_agent = {row.key: row for row in observed.by_agent}

    assert by_agent["codex"].event_count == 2
    assert by_agent["codex"].tokens.tokens_in == 15
    assert by_agent["omp"].event_count == 1


def test_group_by_model_labels_missing_model_as_unavailable() -> None:
    events = [
        _event(event_id="1" * 64, model="claude-opus"),
        _event(event_id="2" * 64, model=None),
    ]

    observed = build_report(events)
    keys = {row.key for row in observed.by_model}

    assert keys == {"claude-opus", UNAVAILABLE_MODEL}


def test_group_by_project_key_never_falls_back_since_it_is_required() -> None:
    events = [
        _event(event_id="1" * 64, project_key="1" * 64),
        _event(event_id="2" * 64, project_key="2" * 64),
    ]

    observed = build_report(events)
    keys = {row.key for row in observed.by_project_key}

    assert keys == {"1" * 64, "2" * 64}
    assert UNAVAILABLE_MODEL not in keys


# --- filtering: range / agent / model / project key --------------------------


def test_select_events_range_bounds_are_both_inclusive() -> None:
    events = [
        _event(event_id="1" * 64, occurred_at=_T0),
        _event(event_id="2" * 64, occurred_at=_T1),
        _event(event_id="3" * 64, occurred_at=_T2),
    ]

    selected = select_events(events, ReportFilter(since=_T0, until=_T1))

    assert {event.event_id for event in selected} == {"1" * 64, "2" * 64}


def test_select_events_filters_by_agent() -> None:
    events = [
        _event(event_id="1" * 64, agent="codex"),
        _event(event_id="2" * 64, agent="pi"),
    ]

    selected = select_events(events, ReportFilter(agent="pi"))

    assert [event.event_id for event in selected] == ["2" * 64]


def test_select_events_filters_by_model() -> None:
    events = [
        _event(event_id="1" * 64, model="claude-opus"),
        _event(event_id="2" * 64, model="claude-sonnet"),
    ]

    selected = select_events(events, ReportFilter(model="claude-sonnet"))

    assert [event.event_id for event in selected] == ["2" * 64]


def test_select_events_filters_by_opaque_project_key() -> None:
    events = [
        _event(event_id="1" * 64, project_key="1" * 64),
        _event(event_id="2" * 64, project_key="2" * 64),
    ]

    selected = select_events(events, ReportFilter(project_key="2" * 64))

    assert [event.event_id for event in selected] == ["2" * 64]


def test_select_events_combines_every_populated_filter_dimension() -> None:
    events = [
        _event(event_id="1" * 64, agent="codex", occurred_at=_T0),
        _event(event_id="2" * 64, agent="codex", occurred_at=_T2),
        _event(event_id="3" * 64, agent="pi", occurred_at=_T0),
    ]

    selected = select_events(events, ReportFilter(agent="codex", since=_T1))

    assert [event.event_id for event in selected] == ["2" * 64]


def test_report_filter_rejects_a_naive_range_bound() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ReportFilter(since=datetime(2026, 8, 16))


def test_report_filter_rejects_since_after_until() -> None:
    with pytest.raises(ValueError, match="since must not be after until"):
        ReportFilter(since=_T2, until=_T0)


# --- CLI: `metermaid report` -------------------------------------------------


def _seed_store(tmp_path: Path) -> Path:
    data_dir = tmp_path / "state"
    paths = resolve_state_paths(data_dir)
    load_or_create_secret(paths)
    store = EventStore(paths.database)
    store.initialize()
    store.commit_ingest(
        [
            _event(
                event_id="1" * 64,
                agent="codex",
                source_session_id="a" * 64,
                project_key="b" * 64,
                model="claude-opus",
                occurred_at=_T0,
                tokens_in=100,
                tokens_out=20,
                provider_cost_usd=0.25,
            ),
            _event(
                event_id="2" * 64,
                agent="pi",
                source_session_id="c" * 64,
                project_key="d" * 64,
                model=None,
                occurred_at=_T1,
                tokens_in=None,
                tokens_out=None,
            ),
        ],
        [],
        None,
    )
    return data_dir


def test_report_command_renders_unavailable_for_missing_counters(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = _seed_store(tmp_path)
    monkeypatch.setattr(
        "sys.argv", ["metermaid", "report", "--data-dir", str(data_dir)]
    )

    main()

    out = capsys.readouterr().out
    assert "unavailable" in out
    assert "Observed: 2" in out


def test_report_command_never_prints_a_raw_path_session_or_project_id(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = _seed_store(tmp_path)
    monkeypatch.setattr(
        "sys.argv", ["metermaid", "report", "--data-dir", str(data_dir)]
    )

    main()

    out = capsys.readouterr().out
    assert str(data_dir) not in out
    assert "a" * 64 not in out
    assert "c" * 64 not in out


def test_report_command_filters_by_agent(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = _seed_store(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["metermaid", "report", "--data-dir", str(data_dir), "--agent", "pi"],
    )

    main()

    out = capsys.readouterr().out
    assert "Observed: 1" in out


def test_report_command_filters_by_opaque_project_key(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = _seed_store(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "metermaid",
            "report",
            "--data-dir",
            str(data_dir),
            "--project-key",
            "b" * 64,
        ],
    )

    main()

    out = capsys.readouterr().out
    assert "Observed: 1" in out


def test_report_command_rejects_a_since_after_until_range(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = _seed_store(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "metermaid",
            "report",
            "--data-dir",
            str(data_dir),
            "--since",
            "2026-08-16T12:00:00+00:00",
            "--until",
            "2026-08-16T00:00:00+00:00",
        ],
    )

    with pytest.raises(ValueError, match="since must not be after until"):
        main()


def test_report_command_rejects_a_malformed_range_bound(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = _seed_store(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["metermaid", "report", "--data-dir", str(data_dir), "--since", "not-a-date"],
    )

    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 2
