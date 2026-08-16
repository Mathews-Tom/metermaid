"""Mapping, diagnostic, and purity contracts for the M3 pilot record adapters."""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pytest import MonkeyPatch

from metermaid.adapters import CompleteRecord, RecordContext, SourceAdapter
from metermaid.domain import NormalizedEvent, ParseOutcome
from metermaid.parsers.pilot_adapters import (
    ClaudeCodeAdapter,
    CodexAdapter,
    OmpAdapter,
    PiAdapter,
)
from metermaid.state import event_identifier

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "m3"
_SECRET = b"fixture-secret-for-pilot-adapters"
_CONTEXT = RecordContext(source_session_id="a" * 64, project_key="b" * 64, byte_start=0)

_ADAPTERS: dict[str, SourceAdapter] = {
    "claude-code": ClaudeCodeAdapter(),
    "codex": CodexAdapter(),
    "pi": PiAdapter(),
    "omp": OmpAdapter(),
}


def _fixture(name: str) -> CompleteRecord:
    return cast(Mapping[str, object], json.loads((FIXTURE_ROOT / name).read_text()))


def _expected_event_id(agent: str, discriminator: str, occurred_at: datetime) -> str:
    return event_identifier(
        _SECRET,
        agent,
        discriminator,
        _CONTEXT.source_session_id,
        str(_CONTEXT.byte_start),
        occurred_at.isoformat(),
    )


def test_every_pilot_adapter_satisfies_the_source_adapter_protocol() -> None:
    for adapter in _ADAPTERS.values():
        assert isinstance(adapter, SourceAdapter)


def test_every_pilot_adapter_declares_its_own_pilot_agent() -> None:
    for agent, adapter in _ADAPTERS.items():
        assert adapter.agent == agent


# --- Mapping: every reviewed field, per agent -------------------------------


def test_claude_adapter_maps_the_reviewed_assistant_shape() -> None:
    record = _fixture("claude-assistant.jsonl")
    outcome = ClaudeCodeAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert isinstance(outcome, NormalizedEvent)
    occurred_at = datetime(2026, 8, 16, tzinfo=UTC)
    assert outcome.event_id == _expected_event_id(
        "claude-code", "assistant", occurred_at
    )
    assert outcome.agent == "claude-code"
    assert outcome.source_session_id == _CONTEXT.source_session_id
    assert outcome.project_key == _CONTEXT.project_key
    assert outcome.occurred_at == occurred_at
    assert outcome.record_kind == "usage"
    assert outcome.provenance == "claude-code.assistant"
    assert outcome.role == "assistant"
    assert outcome.model == "fixture-model"
    assert outcome.tokens_in == 100
    assert outcome.tokens_out == 20
    assert outcome.cache_read == 10
    assert outcome.cache_write == 5
    assert outcome.reasoning_tokens == 3
    assert outcome.provider_cost_usd is None
    assert outcome.safe_tool_category is None


def test_codex_adapter_maps_the_reviewed_token_count_shape() -> None:
    record = _fixture("codex-token-count.jsonl")
    outcome = CodexAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert isinstance(outcome, NormalizedEvent)
    occurred_at = datetime(2026, 8, 16, tzinfo=UTC)
    assert outcome.event_id == _expected_event_id("codex", "token_count", occurred_at)
    assert outcome.provenance == "codex.token_count"
    assert outcome.role is None
    assert outcome.model is None
    assert outcome.tokens_in == 100
    assert outcome.tokens_out == 20
    assert outcome.cache_read == 10
    assert outcome.cache_write is None
    assert outcome.reasoning_tokens == 3
    assert outcome.provider_cost_usd is None
    assert outcome.safe_tool_category is None


def test_pi_adapter_maps_the_reviewed_message_shape() -> None:
    record = _fixture("pi-message.jsonl")
    outcome = PiAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert isinstance(outcome, NormalizedEvent)
    occurred_at = datetime(2026, 8, 16, tzinfo=UTC)
    assert outcome.event_id == _expected_event_id("pi", "message", occurred_at)
    assert outcome.provenance == "pi.message"
    assert outcome.role == "assistant"
    assert outcome.model == "fixture-model"
    assert outcome.tokens_in == 100
    assert outcome.tokens_out == 20
    assert outcome.cache_read == 10
    assert outcome.cache_write == 5
    assert outcome.provider_cost_usd == 0.032
    assert outcome.reasoning_tokens is None
    assert outcome.safe_tool_category is None


def test_omp_adapter_maps_the_reviewed_message_shape() -> None:
    record = _fixture("omp-message.jsonl")
    outcome = OmpAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert isinstance(outcome, NormalizedEvent)
    occurred_at = datetime(2026, 8, 16, tzinfo=UTC)
    assert outcome.event_id == _expected_event_id("omp", "message", occurred_at)
    assert outcome.provenance == "omp.message"
    assert outcome.role == "assistant"
    assert outcome.model == "fixture-model"
    assert outcome.tokens_in == 100
    assert outcome.tokens_out == 20
    assert outcome.cache_read == 10
    assert outcome.cache_write == 5
    assert outcome.provider_cost_usd == 0.032
    assert outcome.reasoning_tokens is None
    assert outcome.safe_tool_category == "read"


# --- OMP conservative tool-category allowlist -------------------------------


def test_omp_adapter_maps_a_recognized_tool_name_to_its_own_category() -> None:
    record = _fixture("omp-message.jsonl")
    message = cast(dict[str, object], record["message"])
    record = {**record, "message": {**message, "toolName": "bash"}}
    outcome = OmpAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert isinstance(outcome, NormalizedEvent)
    assert outcome.safe_tool_category == "bash"


def test_omp_adapter_drops_an_unrecognized_tool_name_without_leaking_it() -> None:
    record = _fixture("omp-message.jsonl")
    message = cast(dict[str, object], record["message"])
    record = {
        **record,
        "message": {**message, "toolName": "some-unreviewed-provider-tool"},
    }
    outcome = OmpAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert isinstance(outcome, NormalizedEvent)
    assert outcome.safe_tool_category is None


def test_omp_adapter_leaves_tool_category_absent_when_the_field_is_missing() -> None:
    record = _fixture("omp-message.jsonl")
    message = cast(dict[str, object], record["message"])
    del message["toolName"]
    record = {**record, "message": message}
    outcome = OmpAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert isinstance(outcome, NormalizedEvent)
    assert outcome.safe_tool_category is None


# --- NEW-1: an unrepresentable optional label never costs the counters -----


def test_omp_adapter_drops_a_non_string_tool_name_but_keeps_the_event() -> None:
    """A non-string ``toolName`` is unrepresentable as a label, not a broken
    usage payload: it becomes ``None`` and every counter still reaches the
    returned event rather than the whole record failing malformed."""
    record = _fixture("omp-message.jsonl")
    message = cast(dict[str, object], record["message"])
    record = {**record, "message": {**message, "toolName": 7}}
    outcome = OmpAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert isinstance(outcome, NormalizedEvent)
    assert outcome.safe_tool_category is None
    assert outcome.tokens_in == 100
    assert outcome.tokens_out == 20


def test_claude_adapter_drops_an_unsafe_role_label_but_keeps_the_counters() -> None:
    record = _fixture("claude-assistant.jsonl")
    message = cast(dict[str, object], record["message"])
    record = {**record, "message": {**message, "role": "assistant (subagent)!"}}
    outcome = ClaudeCodeAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert isinstance(outcome, NormalizedEvent)
    assert outcome.role is None
    assert outcome.model == "fixture-model"
    assert outcome.tokens_in == 100
    assert outcome.tokens_out == 20


def test_claude_adapter_drops_a_non_string_model_label_but_keeps_the_counters() -> None:
    record = _fixture("claude-assistant.jsonl")
    message = cast(dict[str, object], record["message"])
    record = {**record, "message": {**message, "model": 42}}
    outcome = ClaudeCodeAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert isinstance(outcome, NormalizedEvent)
    assert outcome.model is None
    assert outcome.tokens_in == 100
    assert outcome.tokens_out == 20


# --- NEW-2: usage entirely absent is unsupported; broken usage is malformed


def test_claude_adapter_reports_a_missing_message_as_unsupported() -> None:
    record = {"type": "assistant", "timestamp": "2026-08-16T00:00:00Z"}
    outcome = ClaudeCodeAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="claude-code", discriminator="assistant", kind="unsupported"
    )


def test_claude_adapter_reports_a_message_without_usage_as_unsupported() -> None:
    record = {
        "type": "assistant",
        "timestamp": "2026-08-16T00:00:00Z",
        "message": {"role": "assistant", "model": "fixture-model"},
    }
    outcome = ClaudeCodeAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="claude-code", discriminator="assistant", kind="unsupported"
    )


def test_codex_adapter_reports_a_non_mapping_payload_as_unsupported() -> None:
    record = {
        "type": "event_msg",
        "timestamp": "2026-08-16T00:00:00Z",
        "payload": "oops",
    }
    outcome = CodexAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="codex", discriminator="unknown", kind="unsupported"
    )


def test_codex_adapter_reports_a_missing_total_token_usage_as_unsupported() -> None:
    record = {
        "type": "event_msg",
        "timestamp": "2026-08-16T00:00:00Z",
        "payload": {"type": "token_count", "info": {}},
    }
    outcome = CodexAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="codex", discriminator="token_count", kind="unsupported"
    )


def test_pi_adapter_reports_a_missing_message_as_unsupported() -> None:
    record = {"type": "message", "timestamp": "2026-08-16T00:00:00Z"}
    outcome = PiAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="pi", discriminator="message", kind="unsupported"
    )


def test_omp_adapter_reports_a_missing_message_as_unsupported() -> None:
    record = {"type": "message", "timestamp": "2026-08-16T00:00:00Z"}
    outcome = OmpAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="omp", discriminator="message", kind="unsupported"
    )


# --- Unsupported: unknown/unreviewed discriminators -------------------------


def test_claude_adapter_reports_a_known_but_unreviewed_discriminator_as_unsupported() -> (
    None
):
    record = {"type": "user", "timestamp": "2026-08-16T00:00:00Z"}
    outcome = ClaudeCodeAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="claude-code", discriminator="user", kind="unsupported"
    )


def test_claude_adapter_falls_back_to_unknown_for_an_unsafe_discriminator_label() -> (
    None
):
    record = {"type": "bad type!", "timestamp": "2026-08-16T00:00:00Z"}
    outcome = ClaudeCodeAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="claude-code", discriminator="unknown", kind="unsupported"
    )


def test_codex_adapter_reports_an_unreviewed_top_level_type_as_unsupported() -> None:
    record = {"type": "response_item", "timestamp": "2026-08-16T00:00:00Z"}
    outcome = CodexAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="codex", discriminator="response_item", kind="unsupported"
    )


def test_codex_adapter_reports_an_unreviewed_nested_payload_type_as_unsupported() -> (
    None
):
    record = {
        "type": "event_msg",
        "timestamp": "2026-08-16T00:00:00Z",
        "payload": {"type": "agent_message"},
    }
    outcome = CodexAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="codex", discriminator="agent_message", kind="unsupported"
    )


def test_pi_adapter_reports_an_unreviewed_discriminator_as_unsupported() -> None:
    record = {"type": "tool_call", "timestamp": "2026-08-16T00:00:00Z"}
    outcome = PiAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="pi", discriminator="tool_call", kind="unsupported"
    )


def test_omp_adapter_reports_an_unreviewed_discriminator_as_unsupported() -> None:
    record = {"type": "heartbeat", "timestamp": "2026-08-16T00:00:00Z"}
    outcome = OmpAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="omp", discriminator="heartbeat", kind="unsupported"
    )


def test_claude_adapter_treats_null_thinking_details_as_unavailable() -> None:
    record = _fixture("claude-assistant-null-thinking.jsonl")
    outcome = ClaudeCodeAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert isinstance(outcome, NormalizedEvent)
    assert outcome.reasoning_tokens is None


# --- Malformed: usage present but broken ------------------------------------


def test_claude_adapter_reports_a_non_mapping_usage_as_malformed() -> None:
    record = {
        "type": "assistant",
        "timestamp": "2026-08-16T00:00:00Z",
        "message": {"usage": "oops"},
    }
    outcome = ClaudeCodeAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="claude-code", discriminator="assistant", kind="malformed"
    )


def test_claude_adapter_reports_a_missing_required_token_count_as_malformed() -> None:
    record = {
        "type": "assistant",
        "timestamp": "2026-08-16T00:00:00Z",
        "message": {"usage": {"output_tokens": 20}},
    }
    outcome = ClaudeCodeAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="claude-code", discriminator="assistant", kind="malformed"
    )


def test_claude_adapter_reports_a_wrong_typed_token_count_as_malformed() -> None:
    record = {
        "type": "assistant",
        "timestamp": "2026-08-16T00:00:00Z",
        "message": {"usage": {"input_tokens": "100", "output_tokens": 20}},
    }
    outcome = ClaudeCodeAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="claude-code", discriminator="assistant", kind="malformed"
    )


def test_claude_adapter_reports_an_unparseable_timestamp_as_malformed() -> None:
    record = {
        "type": "assistant",
        "timestamp": "not-a-timestamp",
        "message": {"usage": {"input_tokens": 100, "output_tokens": 20}},
    }
    outcome = ClaudeCodeAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="claude-code", discriminator="assistant", kind="malformed"
    )


def test_codex_adapter_reports_a_non_mapping_total_token_usage_as_malformed() -> None:
    record = {
        "type": "event_msg",
        "timestamp": "2026-08-16T00:00:00Z",
        "payload": {"type": "token_count", "info": {"total_token_usage": "oops"}},
    }
    outcome = CodexAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="codex", discriminator="token_count", kind="malformed"
    )


def test_codex_adapter_reports_a_missing_required_token_count_as_malformed() -> None:
    record = {
        "type": "event_msg",
        "timestamp": "2026-08-16T00:00:00Z",
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": {"output_tokens": 20}},
        },
    }
    outcome = CodexAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="codex", discriminator="token_count", kind="malformed"
    )


def test_pi_adapter_reports_a_wrong_typed_usage_field_as_malformed() -> None:
    record = {
        "type": "message",
        "timestamp": "2026-08-16T00:00:00Z",
        "message": {"usage": {"input": "100", "output": 20}},
    }
    outcome = PiAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="pi", discriminator="message", kind="malformed"
    )


def test_omp_adapter_reports_a_missing_output_tokens_as_malformed() -> None:
    record = {
        "type": "message",
        "timestamp": "2026-08-16T00:00:00Z",
        "message": {"usage": {"input": 100}},
    }
    outcome = OmpAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="omp", discriminator="message", kind="malformed"
    )


# --- I-2: non-finite / overflow cost is broken data, never silently kept ---


@pytest.mark.parametrize("bad_cost", [float("nan"), float("inf"), float("-inf")])
def test_pi_adapter_reports_a_non_finite_cost_as_malformed(bad_cost: float) -> None:
    record = {
        "type": "message",
        "timestamp": "2026-08-16T00:00:00Z",
        "message": {"usage": {"input": 100, "output": 20, "cost": {"total": bad_cost}}},
    }
    outcome = PiAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="pi", discriminator="message", kind="malformed"
    )


def test_omp_adapter_reports_an_overflowed_cost_literal_as_malformed() -> None:
    """A JSON numeric literal far beyond float range decodes to ``inf``
    without the decoder ever raising; the adapter must still catch it."""
    overflowed = json.loads("1e400")
    assert overflowed == float("inf")
    record = {
        "type": "message",
        "timestamp": "2026-08-16T00:00:00Z",
        "message": {
            "usage": {"input": 100, "output": 20, "cost": {"total": overflowed}}
        },
    }
    outcome = OmpAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="omp", discriminator="message", kind="malformed"
    )


def test_no_diagnostic_outcome_ever_carries_token_usage_fields() -> None:
    """A malformed/unsupported outcome is always a countable diagnostic, never
    a disguised zero-usage event: ``ParseOutcome`` declares no token fields."""
    field_names = {field.name for field in dataclasses.fields(ParseOutcome)}
    assert field_names == {"agent", "discriminator", "kind", "count"}


def test_two_records_sharing_a_timestamp_get_distinct_event_ids_by_byte_start() -> None:
    """Two records can legitimately share a discriminator, session, and even
    an identical recorded timestamp; only ``context.byte_start`` keeps their
    derived ``event_id``s apart."""
    record = _fixture("pi-message.jsonl")
    first_context = RecordContext(
        source_session_id=_CONTEXT.source_session_id,
        project_key=_CONTEXT.project_key,
        byte_start=0,
    )
    second_context = RecordContext(
        source_session_id=_CONTEXT.source_session_id,
        project_key=_CONTEXT.project_key,
        byte_start=128,
    )

    first = PiAdapter().parse(record, context=first_context, secret=_SECRET)
    second = PiAdapter().parse(record, context=second_context, secret=_SECRET)

    assert isinstance(first, NormalizedEvent)
    assert isinstance(second, NormalizedEvent)
    assert first.occurred_at == second.occurred_at
    assert first.event_id != second.event_id


def test_record_context_rejects_a_negative_byte_start() -> None:
    with pytest.raises(ValueError, match="byte_start"):
        RecordContext(source_session_id="a" * 64, project_key="b" * 64, byte_start=-1)


# --- R-1: RecordContext fails loud on an invalid opaque identifier --------


def test_record_context_rejects_a_non_hex_source_session_id() -> None:
    with pytest.raises(ValueError, match="source_session_id"):
        RecordContext(source_session_id="s" * 64, project_key="b" * 64, byte_start=0)


def test_record_context_rejects_a_wrong_length_project_key() -> None:
    with pytest.raises(ValueError, match="project_key"):
        RecordContext(source_session_id="a" * 64, project_key="b" * 63, byte_start=0)


def test_record_context_rejects_an_uppercase_hex_identifier() -> None:
    with pytest.raises(ValueError, match="source_session_id"):
        RecordContext(source_session_id="A" * 64, project_key="b" * 64, byte_start=0)


# --- R-2: wrong-typed/boolean/negative counters; timestamp normalization --


def test_claude_adapter_reports_a_wrong_typed_optional_counter_as_malformed() -> None:
    record = {
        "type": "assistant",
        "timestamp": "2026-08-16T00:00:00Z",
        "message": {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": "10",
            }
        },
    }
    outcome = ClaudeCodeAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="claude-code", discriminator="assistant", kind="malformed"
    )


def test_claude_adapter_reports_a_boolean_required_counter_as_malformed() -> None:
    record = {
        "type": "assistant",
        "timestamp": "2026-08-16T00:00:00Z",
        "message": {"usage": {"input_tokens": True, "output_tokens": 20}},
    }
    outcome = ClaudeCodeAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="claude-code", discriminator="assistant", kind="malformed"
    )


def test_claude_adapter_reports_a_boolean_optional_counter_as_malformed() -> None:
    record = {
        "type": "assistant",
        "timestamp": "2026-08-16T00:00:00Z",
        "message": {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": True,
            }
        },
    }
    outcome = ClaudeCodeAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="claude-code", discriminator="assistant", kind="malformed"
    )


def test_claude_adapter_reports_a_negative_required_counter_as_malformed() -> None:
    record = {
        "type": "assistant",
        "timestamp": "2026-08-16T00:00:00Z",
        "message": {"usage": {"input_tokens": -1, "output_tokens": 20}},
    }
    outcome = ClaudeCodeAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="claude-code", discriminator="assistant", kind="malformed"
    )


def test_omp_adapter_reports_a_negative_optional_counter_as_malformed() -> None:
    record = {
        "type": "message",
        "timestamp": "2026-08-16T00:00:00Z",
        "message": {"usage": {"input": 100, "output": 20, "cacheRead": -1}},
    }
    outcome = OmpAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="omp", discriminator="message", kind="malformed"
    )


def test_pi_adapter_reports_a_negative_provider_cost_as_malformed() -> None:
    record = {
        "type": "message",
        "timestamp": "2026-08-16T00:00:00Z",
        "message": {"usage": {"input": 100, "output": 20, "cost": {"total": -0.01}}},
    }
    outcome = PiAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert outcome == ParseOutcome(
        agent="pi", discriminator="message", kind="malformed"
    )


def test_non_utc_offset_timestamp_normalizes_to_the_same_utc_event_as_z() -> None:
    """A timestamp expressed in a non-UTC offset must normalize to the exact
    same aware-UTC ``occurred_at`` — and therefore the same ``event_id`` — as
    the equivalent instant expressed with a literal ``Z`` suffix."""
    utc_record = _fixture("pi-message.jsonl")
    offset_record = {**utc_record, "timestamp": "2026-08-16T05:30:00+05:30"}

    utc_outcome = PiAdapter().parse(utc_record, context=_CONTEXT, secret=_SECRET)
    offset_outcome = PiAdapter().parse(offset_record, context=_CONTEXT, secret=_SECRET)

    assert isinstance(utc_outcome, NormalizedEvent)
    assert isinstance(offset_outcome, NormalizedEvent)
    assert utc_outcome.occurred_at == offset_outcome.occurred_at
    assert utc_outcome.occurred_at.tzinfo == UTC
    assert utc_outcome.event_id == offset_outcome.event_id


# --- Purity: no record maps trigger a file, clock, or database call --------


class _NoNowDatetime(datetime):
    """A ``datetime`` stand-in whose ``now``/``utcnow`` refuse to run.

    ``datetime.datetime`` is an immutable C type: PR1's own probe test
    noted its classmethods cannot be monkeypatched directly. This
    subclass instead swaps in for the module-level ``datetime`` name
    each adapter module imports; every other classmethod — in
    particular ``fromisoformat``, which every adapter actually calls —
    is inherited unchanged.
    """

    @classmethod
    def now(cls, tz: object = None) -> "_NoNowDatetime":
        raise AssertionError("adapter.parse must not read the current time")

    @classmethod
    def utcnow(cls) -> "_NoNowDatetime":
        raise AssertionError("adapter.parse must not read the current time")


@pytest.mark.parametrize(
    ("adapter", "fixture_name"),
    [
        (ClaudeCodeAdapter(), "claude-assistant.jsonl"),
        (CodexAdapter(), "codex-token-count.jsonl"),
        (PiAdapter(), "pi-message.jsonl"),
        (OmpAdapter(), "omp-message.jsonl"),
    ],
)
def test_each_real_pilot_adapter_parses_purely(
    adapter: SourceAdapter, fixture_name: str, monkeypatch: MonkeyPatch
) -> None:
    """Exercises the same purity guard PR1 could only prove against a
    synthetic probe, this time against every real PR2 adapter."""
    record = _fixture(fixture_name)

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("adapter.parse must not perform this operation")

    monkeypatch.setattr(Path, "open", _forbidden)
    monkeypatch.setattr("builtins.open", _forbidden)
    monkeypatch.setattr(sqlite3, "connect", _forbidden)
    monkeypatch.setattr(time, "time", _forbidden)
    monkeypatch.setattr(time, "monotonic", _forbidden)
    monkeypatch.setattr(time, "time_ns", _forbidden)
    monkeypatch.setattr(os, "stat", _forbidden)
    monkeypatch.setattr("metermaid.parsers.pilot_adapters.datetime", _NoNowDatetime)

    outcome = adapter.parse(record, context=_CONTEXT, secret=_SECRET)

    assert isinstance(outcome, NormalizedEvent)


def test_parsing_the_same_record_twice_is_deterministic() -> None:
    record = _fixture("pi-message.jsonl")
    first = PiAdapter().parse(record, context=_CONTEXT, secret=_SECRET)
    second = PiAdapter().parse(record, context=_CONTEXT, secret=_SECRET)

    assert first == second
